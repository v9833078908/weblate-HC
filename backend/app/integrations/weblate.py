# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import io
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx
from translate.storage.pypo import pofile

from app.db import DomainError
from app.services.retry_policy import retry_after_seconds
from app.settings import Settings, read_secret_file


def segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Invalid remote identifier")
    return value


class WeblateRejected(DomainError):
    def __init__(self, remote_status: int, response=None):
        self.retry_after = (
            retry_after_seconds(response.headers.get("Retry-After"))
            if remote_status == 429 and response is not None
            else None
        )
        self.safe_reasons = []
        labels = {
            "already_exists": "значение уже существует",
            "required": "обязательное поле",
            "invalid": "некорректное значение",
            "permission": "нет разрешения",
            "rejected": "значение отклонено",
        }
        allowed = {
            "slug",
            "name",
            "source_language",
            "file_format",
            "template",
            "new_base",
            "q",
            "engines",
            "language_code",
            "configuration",
            "value",
            "source",
            "target",
            "context",
            "non_field_errors",
            "detail",
        }
        if response is not None and len(response.content) <= 16384:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                for field in sorted(allowed & payload.keys()):
                    values = (
                        payload[field] if isinstance(payload[field], list) else [payload[field]]
                    )
                    text = " ".join(v[:500] for v in values[:5] if isinstance(v, str)).casefold()
                    reason = next(
                        (
                            code
                            for word, code in (
                                ("already exists", "already_exists"),
                                ("required", "required"),
                                ("invalid", "invalid"),
                                ("permission", "permission"),
                            )
                            if word in text
                        ),
                        "rejected",
                    )
                    self.safe_reasons.append({"field": field, "reason": reason})
        detail = f"Внешний Weblate вернул HTTP {remote_status}."
        if self.safe_reasons:
            detail += " " + "; ".join(
                r["field"] + ": " + labels[r["reason"]] for r in self.safe_reasons
            )
        super().__init__("weblate_rejected", detail, 502)
        self.remote_status = remote_status


class WeblateClient:
    """Fixed-host REST client. Mutating requests are never automatically retried."""

    def __init__(self, settings: Settings, transport=None, *, admission_store=None):
        self.settings = settings
        self.transport = transport
        self.admission_store = admission_store

    def _base_url(self) -> str:
        base = self.settings.weblate_url.rstrip("/") + "/"
        parts = urlsplit(base)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or not parts.path.startswith("/api/")
        ):
            raise ValueError("Unsafe external Weblate URL")
        return base

    def _safe_url(self, path: str) -> str:
        base = self._base_url()
        base_parts = urlsplit(base)
        candidate = urlsplit(path)
        if (
            candidate.scheme == "http"
            and base_parts.scheme == "https"
            and candidate.netloc == base_parts.netloc
        ):
            path = candidate._replace(scheme="https").geturl()
        url = urljoin(base, path)
        parts = urlsplit(url)
        if (
            (parts.scheme, parts.netloc) != (base_parts.scheme, base_parts.netloc)
            or not parts.path.startswith(base_parts.path)
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise ValueError("Unsafe external Weblate URL")
        return url

    def request(self, method: str, path: str, *, allow_missing=False, raw=False, **kwargs):
        if (
            method not in {"GET", "HEAD"}
            and not self.settings.external_writes_enabled
            and not isinstance(self.transport, httpx.MockTransport)
        ):
            raise DomainError(
                "external_read_only", "Запись во внешний Weblate отключена оператором", 403
            )
        url = self._safe_url(path)
        read_only = method in {"GET", "HEAD"}
        attempts = 3 if read_only else 1
        admission = None
        try:
            token = read_secret_file(self.settings.weblate_token_file)
            if self.admission_store is not None:
                from app.services.producer_admission import admission_key, reserve_admission

                admission = admission_key(self._base_url(), token)
                paid_route = bool(
                    re.fullmatch(
                        r"producer/projects/[^/]+/(?:runs/|mt/batches/|quality/recheck/)", path
                    )
                )
                if method == "POST" and paid_route:
                    delay = reserve_admission(
                        self.admission_store,
                        admission,
                        self.settings.producer_post_interval_seconds,
                    )
                    if delay:
                        deferred = WeblateRejected(429)
                        deferred.retry_after = delay
                        raise deferred
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = "Token " + token
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(
                    connect=self.settings.weblate_connect_timeout,
                    read=(
                        self.settings.weblate_get_read_timeout
                        if read_only
                        else self.settings.weblate_write_read_timeout
                    ),
                    write=self.settings.weblate_write_timeout,
                    pool=self.settings.weblate_pool_timeout,
                ),
                follow_redirects=False,
            ) as client:
                for attempt in range(attempts):
                    try:
                        response = client.request(
                            method,
                            url,
                            headers=headers,
                            **kwargs,
                        )
                    except httpx.HTTPError:
                        if not read_only or attempt + 1 == attempts:
                            raise
                    else:
                        # Let the worker persist a cooldown instead of
                        # immediately spending the server's request allowance.
                        if response.status_code == 429 or response.status_code < 500:
                            break
                        if not read_only or attempt + 1 == attempts:
                            break
                    if not isinstance(self.transport, httpx.MockTransport):
                        time.sleep(0.2 * (2**attempt))
        except (OSError, ValueError) as error:
            raise DomainError(
                "weblate_configuration", "Weblate token is not configured correctly", 503
            ) from error
        except httpx.HTTPError as error:
            code = "weblate_unknown" if not read_only else "weblate_unavailable"
            raise DomainError(
                code, "External Weblate response was not received; no automatic retry was made", 503
            ) from error
        if response.status_code == 404 and allow_missing:
            return None
        if response.status_code >= 500 and method not in {"GET", "HEAD"}:
            raise DomainError(
                "weblate_unknown",
                "Weblate did not confirm the operation; inspect its result before retrying",
                503,
            )
        if not 200 <= response.status_code < 300:
            if response.status_code == 429 and admission is not None:
                from app.services.producer_admission import defer_admission

                defer_admission(
                    self.admission_store,
                    admission,
                    retry_after_seconds(response.headers.get("Retry-After")) or 75,
                )
            raise WeblateRejected(response.status_code, response)
        if raw:
            return response.content
        try:
            value = response.json()
        except ValueError as error:
            code = "weblate_unknown" if not read_only else "weblate_schema"
            raise DomainError(code, "Weblate returned an unexpected response", 502) from error
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            raise DomainError("weblate_schema", "Weblate returned an unexpected response", 502)
        value["_http_status"] = response.status_code
        return value

    def get_project(self, slug):
        return self.request("GET", f"projects/{segment(slug)}/", allow_missing=True)

    def start_mt_batch(self, project, payload, key):
        from uuid import UUID

        key = str(UUID(key))
        return self._require_mt_receipt(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/mt/batches/",
                json=payload,
                headers={"Idempotency-Key": key},
            ),
            key,
        )

    def get_mt_batch(self, project, key):
        from uuid import UUID

        key = str(UUID(key))
        value = self.request(
            "GET",
            f"producer/projects/{segment(project)}/mt/batches/{key}/",
            allow_missing=True,
        )
        return None if value is None else self._require_mt_receipt(value, key)

    def reconcile_mt_batch(self, project, key):
        from uuid import UUID

        key = str(UUID(key))
        return self._require_mt_receipt(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/mt/batches/{key}/reconcile/",
                json={},
            ),
            key,
        )

    @staticmethod
    def _require_mt_receipt(value, key):
        WeblateClient._require_mapping(value, {"key", "run_id", "state", "units"})
        if value["key"] != key or not isinstance(value["run_id"], str) or not value["run_id"]:
            raise DomainError("weblate_schema", "MT operation identity mismatch", 502)
        if value["state"] not in {
            "accepted",
            "running",
            "completed",
            "partial",
            "failed",
            "provider_unknown",
        } or not isinstance(value["units"], list):
            raise DomainError("weblate_schema", "Unknown MT operation state", 502)
        return value

    @staticmethod
    def _require_mapping(value, fields):
        if not isinstance(value, dict) or any(field not in value for field in fields):
            raise DomainError("weblate_schema", "Weblate returned an unexpected response", 502)
        return value

    @staticmethod
    def _require_run(value):
        WeblateClient._require_mapping(value, {"id", "status"})
        statuses = {
            "queued",
            "running",
            "cancel_requested",
            "partial",
            "cancelled",
            "completed",
            "failed",
        }
        if value["status"] not in statuses:
            raise DomainError("weblate_schema", "Weblate returned an unknown run status", 502)
        return value

    def producer_me(self):
        return self._require_mapping(
            self.request("GET", "producer/me/"), {"username", "capabilities"}
        )

    def judge_estimate(self, project, scope, *, execution_mode="repair"):
        return self._require_mapping(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/runs/estimate/",
                json={"kind": "judge", "scope": scope, "execution_mode": execution_mode},
            ),
            {"estimate_id"},
        )

    def start_judge(self, project, estimate_id, scope):
        return self._require_run(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/runs/",
                json={
                    "kind": "judge",
                    "scope": scope,
                    "estimate_id": estimate_id,
                },
            )
        )

    def get_producer_run(self, run_id):
        return self._require_run(self.request("GET", f"producer/runs/{segment(run_id)}/"))

    def resume_producer_run(self, run_id, attempt):
        if type(attempt) is not int or attempt < 1:
            raise DomainError("quality_scope", "Invalid resume attempt", 409)
        return self._require_run(
            self.request(
                "POST",
                f"producer/runs/{segment(run_id)}/resume/",
                json={"attempt": attempt},
            )
        )

    def list_decisions(self, project, run_id):
        rows = self.collect(
            f"producer/projects/{segment(project)}/decisions/",
            params={"kind": "judge", "run": segment(run_id)},
        )
        applicability = {
            "ready",
            "no_candidate",
            "not_verified",
            "stale",
            "needs_individual_confirmation",
            "forbidden",
        }
        for row in rows:
            self._require_mapping(
                row,
                {
                    "unit_id",
                    "revision",
                    "seat_results",
                    "candidate_verified",
                    "applicability",
                },
            )
            if row["applicability"] not in applicability:
                raise DomainError(
                    "weblate_schema",
                    "Weblate returned an unknown applicability",
                    502,
                )
        return rows

    def list_quality_issues(self, project, language, kinds=None):
        value = self.request(
            "GET",
            f"producer/projects/{segment(project)}/quality/issues/",
            params={
                "language": segment(language),
                "kind": ",".join(kinds or ("repeat-drift", "glossary")),
            },
        )
        return self._require_mapping(value, {"snapshot_hash", "issues"})

    def recheck_quality(self, project, items):
        value = self.request(
            "POST",
            f"producer/projects/{segment(project)}/quality/recheck/",
            json={"items": items},
        )
        return self._require_mapping(value, {"snapshot_hash", "items"})

    def bulk_apply_candidates(self, project, items):
        value = self.request(
            "POST",
            f"producer/projects/{segment(project)}/decisions/apply/",
            json={"items": items},
        )
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise DomainError("weblate_schema", "Weblate returned an unexpected response", 502)
        return value

    def quality_snapshot(self, project, language, scope):
        from app.services.quality_contract import validate_snapshot

        return validate_snapshot(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/quality/snapshot/",
                json={"language": language, "scope": scope},
            ),
            scope,
        )

    def plan_repeat_quality(self, project, language, scope, snapshot_hash, application_ids, key):
        from app.services.quality_contract import RepeatPlan, validate_contract

        return validate_contract(
            RepeatPlan,
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/quality/repeats/plan/",
                headers={"Idempotency-Key": segment(key)},
                json={
                    "language": language,
                    "scope": scope,
                    "issue_snapshot_hash": snapshot_hash,
                    "application_ids": application_ids,
                },
            ),
        )

    def get_repeat_plan(self, token):
        from app.services.quality_contract import RepeatPlan, validate_contract

        return validate_contract(
            RepeatPlan,
            self.request(
                "GET",
                f"producer/repeat-plans/{segment(token)}/",
            ),
        )

    def apply_repeat_plan(self, token, key):
        from app.services.quality_contract import RepeatRun, validate_contract

        return validate_contract(
            RepeatRun,
            self.request(
                "POST",
                f"producer/repeat-plans/{segment(token)}/apply/",
                headers={"Idempotency-Key": segment(key)},
                json={},
            ),
        )

    def get_repeat_run(self, token):
        from app.services.quality_contract import RepeatRun, validate_contract

        return validate_contract(
            RepeatRun,
            self.request(
                "GET",
                f"producer/repeat-runs/{segment(token)}/",
            ),
        )

    def observe_quality_operation(self, project, key):
        from app.services.quality_contract import require_text_free

        value = self.request(
            "GET",
            f"producer/projects/{segment(project)}/quality/operations/{segment(key)}/",
            allow_missing=True,
        )
        if value is not None:
            self._require_mapping(value, {"state"})
            require_text_free(value)
            if value["state"] not in {"pending", "confirmed", "failed"}:
                raise DomainError("weblate_schema", "Unknown quality operation state", 502)
        return value

    def start_quality_judge(self, project, estimate_id, scope, key, *, execution_mode="repair"):
        return self._require_run(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/runs/",
                headers={"Idempotency-Key": segment(key)},
                json={
                    "kind": "judge",
                    "scope": scope,
                    "estimate_id": estimate_id,
                    "execution_mode": execution_mode,
                },
            )
        )

    def apply_quality_candidates(self, project, items, key):
        value = self.request(
            "POST",
            f"producer/projects/{segment(project)}/decisions/apply/",
            headers={"Idempotency-Key": segment(key)},
            json={"items": items},
        )
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise DomainError("weblate_schema", "Unexpected candidate apply results", 502)
        return value

    def recheck_full_quality(self, project, items, key):
        return self._require_mapping(
            self.request(
                "POST",
                f"producer/projects/{segment(project)}/quality/recheck/",
                headers={"Idempotency-Key": segment(key)},
                json={"items": items},
            ),
            {"items", "affected_unit_ids", "affected_repeat_group_ids"},
        )

    def collect(self, path, *, params=None):
        result, visited = [], set()
        deadline = time.monotonic() + 180
        params = {"page_size": 1000, **(params or {})}
        for _ in range(500):
            if time.monotonic() > deadline:
                raise DomainError(
                    "remote_incomplete", "Время загрузки снимка Weblate превышено", 502
                )
            if path in visited:
                raise DomainError(
                    "remote_incomplete", "Повтор страницы Weblate; снимок неполон", 502
                )
            visited.add(path)
            page = self.request("GET", path, **({"params": params} if params is not None else {}))
            params = None
            rows = page if isinstance(page, list) else page.get("results")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise DomainError("weblate_schema", "Некорректный список Weblate", 502)
            result.extend(rows)
            if len(result) > 100000:
                break
            path = None if isinstance(page, list) else page.get("next")
            if not path:
                return result
        raise DomainError("remote_incomplete", "Превышен предел снимка Weblate", 502)

    def list_units(self, project, component, language, query=None):
        return self.collect(
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/units/",
            params={"q": query} if query else None,
        )

    def create_project(self, name, slug, *, translation_review: bool = False):
        body = {"name": name, "slug": segment(slug)}
        if translation_review:
            body["translation_review"] = True
        created = self.request("POST", "projects/", json=body)
        if translation_review and (
            not isinstance(created, dict) or created.get("translation_review") is not True
        ):
            raise DomainError(
                "quality_configuration",
                "New Weblate project did not enable translation review",
                503,
            )
        return created

    def get_component(self, project, component):
        return self.request(
            "GET", f"components/{segment(project)}/{segment(component)}/", allow_missing=True
        )

    def create_component(
        self,
        project,
        component,
        source,
        archive,
        *,
        glossary=False,
        allow_translation_propagation: bool | None = None,
    ):
        extension = "tbx" if glossary else "po"
        fields = {
            "name": component,
            "slug": segment(component),
            "source_language": segment(source),
            "filemask": "*." + extension,
            "file_format": "tbx" if glossary else "po-mono",
            "disable_autoshare": "true",
            "is_glossary": str(glossary).lower(),
        }
        if not glossary:
            fields.update(template=source + ".po", new_base=source + ".po", manage_units="true")
            if allow_translation_propagation is not None:
                fields["allow_translation_propagation"] = str(allow_translation_propagation).lower()
        return self.request(
            "POST",
            f"projects/{segment(project)}/components/",
            data=fields,
            files={"zipfile": ("kit.zip", archive, "application/zip")},
        )

    def set_profile(self, project, generated):
        if self.settings.engine not in {"openrouter", "litellm"}:
            raise DomainError(
                "engine_configuration", "Select openrouter or litellm on the server", 503
            )
        # This endpoint replaces the whole service configuration. Preserve server-only
        # credentials, routing and unknown infrastructure fields in memory, never in UI.
        current = self.request("GET", f"projects/{segment(project)}/machinery_settings/")
        if not isinstance(current, dict):
            raise DomainError(
                "profile_unavailable", "Нельзя безопасно прочитать настройки сервиса", 409
            )
        configuration = current.get(self.settings.engine, {})
        if not isinstance(configuration, dict):
            raise DomainError(
                "profile_unavailable", "Нельзя безопасно объединить настройки сервиса", 409
            )
        configuration = {**configuration, **generated}
        return self.request(
            "PATCH",
            f"projects/{segment(project)}/machinery_settings/",
            json={"service": self.settings.engine, "configuration": configuration},
        )

    def get_translation_status(self, project, component, language):
        return self.request(
            "GET",
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/",
            allow_missing=True,
        )

    def start_translation(self, project, component, language):
        return self.request(
            "POST",
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/autotranslate/",
            json={
                "mode": "translate",
                "auto_source": "mt",
                "q": "state:empty",
                "engines": [self.settings.engine],
                "threshold": 80,
            },
        )

    def start_scoped_translation(self, project, component, language, unit_ids):
        ids = sorted(set(unit_ids))
        if not ids or len(ids) > 100 or any(type(i) is not int or i < 1 for i in ids):
            raise DomainError("mt_scope", "Некорректный набор строк перевода", 409)
        query = "(" + " OR ".join(f"id:{i}" for i in ids) + ") AND state:empty"
        actual = self.list_units(project, component, language, query)
        if {row["id"] for row in actual} != set(ids) or len(actual) != len(ids):
            raise DomainError(
                "mt_scope", "Weblate выбрал другой набор строк; перевод не отправлен", 409
            )
        return self.request(
            "POST",
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/autotranslate/",
            json={
                "mode": "translate",
                "auto_source": "mt",
                "q": query,
                "engines": [self.settings.engine],
                "threshold": 80,
            },
        )

    def add_source_unit(self, project, component, language, key, source, *, monolingual=True):
        body = (
            {"key": key, "value": [source]} if monolingual else {"context": key, "source": [source]}
        )
        return self.request(
            "POST",
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/units/",
            json=body,
        )

    def set_unit_explanation(self, unit_id, text):
        if not isinstance(unit_id, int) or unit_id <= 0 or not isinstance(text, str):
            raise DomainError("unit_context", "Некорректный контекст строки", 409)
        return self.request("PATCH", f"units/{unit_id}/", json={"explanation": text})

    def set_unit_target(self, unit_id, value):
        if type(unit_id) is not int or unit_id < 1:
            raise DomainError("remote_identity", "Некорректный ID строки", 409)
        return self.request("PATCH", f"units/{unit_id}/", json={"target": [value]})

    def create_translation(self, project, component, language):
        try:
            return self.request(
                "POST",
                f"components/{segment(project)}/{segment(component)}/translations/",
                json={"language_code": segment(language)},
            )
        except WeblateRejected as error:
            # Component import can create the language after the worker's GET.
            # Observe the result once; never repeat the mutating request.
            if error.remote_status == 400:
                existing = self.get_translation_status(project, component, language)
                if existing is not None:
                    return existing
            raise

    def download_translation(self, project, component, language):
        payload = self.request(
            "GET",
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/file/",
            raw=True,
        )
        if len(payload) > 100 * 1024 * 1024:
            raise DomainError("remote_file_size", "Weblate translation is too large", 502)
        try:
            store = pofile(io.BytesIO(payload))
            return {
                str(unit.source): str(unit.target) for unit in store.units if not unit.isheader()
            }
        except (ValueError, TypeError) as error:
            raise DomainError(
                "remote_file_format", "Weblate returned an invalid PO file", 502
            ) from error

    def upload_translation(self, project, component, language, payload):
        return self.request(
            "POST",
            f"translations/{segment(project)}/{segment(component)}/{segment(language)}/file/",
            data={"method": "translate", "conflicts": "ignore"},
            files={"file": (language + ".xlf", payload, "application/x-xliff+xml")},
        )

    def get_task(self, url):
        return self.request("GET", url)
