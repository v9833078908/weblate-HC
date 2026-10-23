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

    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    def request(self, method: str, path: str, *, allow_missing=False, raw=False, **kwargs):
        if (
            method not in {"GET", "HEAD"}
            and not self.settings.external_writes_enabled
            and not isinstance(self.transport, httpx.MockTransport)
        ):
            raise DomainError(
                "external_read_only", "Запись во внешний Weblate отключена оператором", 403
            )
        base = "https://l10n.herocraft.com/api/"
        url = urljoin(base, path)
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc) != (
            "https",
            "l10n.herocraft.com",
        ) or not parts.path.startswith("/api/"):
            raise ValueError("Unsafe external Weblate URL")
        if parts.username or parts.password or parts.fragment:
            raise ValueError("Unsafe external Weblate URL")
        try:
            token = read_secret_file(self.settings.weblate_token_file)
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = "Token " + token
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(
                    connect=self.settings.weblate_connect_timeout,
                    read=(
                        self.settings.weblate_get_read_timeout
                        if method in {"GET", "HEAD"}
                        else self.settings.weblate_write_read_timeout
                    ),
                    write=self.settings.weblate_write_timeout,
                    pool=self.settings.weblate_pool_timeout,
                ),
                follow_redirects=False,
            ) as client:
                response = client.request(method, url, headers=headers, **kwargs)
        except (OSError, ValueError) as error:
            raise DomainError(
                "weblate_configuration", "Weblate token is not configured correctly", 503
            ) from error
        except httpx.HTTPError as error:
            code = "weblate_unknown" if method not in {"GET", "HEAD"} else "weblate_unavailable"
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
            raise WeblateRejected(response.status_code, response)
        if raw:
            return response.content
        try:
            value = response.json()
        except ValueError as error:
            code = "weblate_unknown" if method not in {"GET", "HEAD"} else "weblate_schema"
            raise DomainError(code, "Weblate returned an unexpected response", 502) from error
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            raise DomainError("weblate_schema", "Weblate returned an unexpected response", 502)
        value["_http_status"] = response.status_code
        return value

    def get_project(self, slug):
        return self.request("GET", f"projects/{segment(slug)}/", allow_missing=True)

    def producer_me(self):
        value = self.request("GET", "producer/me/")
        if not isinstance(value, dict) or not isinstance(value.get("capabilities"), dict):
            raise DomainError("weblate_schema", "Invalid producer capabilities", 502)
        return value

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
            "GET", f"producer/projects/{segment(project)}/mt/batches/{key}/", allow_missing=True
        )
        return None if value is None else self._require_mt_receipt(value, key)

    @staticmethod
    def _require_mt_receipt(value, key):
        if (
            not isinstance(value, dict)
            or value.get("key") != key
            or not isinstance(value.get("run_id"), str)
            or not value["run_id"]
            or value.get("state")
            not in {"accepted", "running", "completed", "partial", "failed", "provider_unknown"}
            or not isinstance(value.get("units"), list)
        ):
            raise DomainError("weblate_schema", "Invalid MT operation receipt", 502)
        return value

    def collect(self, path, *, params=None):
        result, visited = [], set()
        deadline = time.monotonic() + 180
        params = {"page_size": 1000, **(params or {})}
        for _ in range(500):
            parts = urlsplit(path)
            if parts.scheme == "http" and parts.netloc == "l10n.herocraft.com":
                path = parts._replace(scheme="https").geturl()
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

    def create_project(self, name, slug):
        return self.request("POST", "projects/", json={"name": name, "slug": segment(slug)})

    def get_component(self, project, component):
        return self.request(
            "GET", f"components/{segment(project)}/{segment(component)}/", allow_missing=True
        )

    def create_component(self, project, component, source, archive, *, glossary=False):
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
