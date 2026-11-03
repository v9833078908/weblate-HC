# Copyright © Weblate contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Monthly report of observed Weblate usage and the entire LiteLLM key spend."""

import csv
import hashlib
import io
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from email.message import MIMEPart
from operator import itemgetter
from typing import Any
from urllib.parse import urlsplit

import httpx2
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db.models import Count, Q, Sum
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext as _

from weblate.trans.models import Component, LLMUsageLog, Project
from weblate.utils.icons import load_icon
from weblate.utils.requests import stream_validated_url
from weblate.utils.site import get_site_url

MAX_SPEND_BYTES = 2 * 1024 * 1024
# Read-only corporate LiteLLM /model/info, 2026-11-03: public aliases and
# their provider model IDs. Unknown names are never matched by substring.
MODEL_ALIASES = {
    "deepseek-v4-pro": "deepseek-v4-pro",
    "openai/deepseek-ai/deepseek-v4-pro": "deepseek-v4-pro",
    "atlas/qwen3.8-max": "atlas/qwen3.8-max",
    "qwen/qwen3.8-max": "atlas/qwen3.8-max",
}
MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


class LLMReportError(Exception):
    """Base class for safe-to-report failures."""


class LLMReportConfigurationError(LLMReportError):
    """Invalid or missing configuration."""


class LLMReportDataError(LLMReportError):
    """Missing or invalid source data."""


class LLMReportTemporaryError(LLMReportError):
    """Retryable data retrieval failure before SMTP."""


def previous_utc_month(now):
    if now.tzinfo is None:
        msg = "A timezone-aware datetime is required"
        raise ValueError(msg)
    now = now.astimezone(UTC)
    end = datetime(now.year, now.month, 1, tzinfo=UTC)
    start = datetime(
        end.year - (end.month == 1),
        12 if end.month == 1 else end.month - 1,
        1,
        tzinfo=UTC,
    )
    return start, end


def _check_period(start, end):
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or start.tzinfo is None
        or end.tzinfo is None
    ):
        msg = "Invalid UTC month"
        raise LLMReportConfigurationError(msg)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if (
        start.day != 1
        or start.hour
        or start.minute
        or start.second
        or start.microsecond
        or previous_utc_month(end) != (start, end)
    ):
        msg = "Invalid UTC month"
        raise LLMReportConfigurationError(msg)
    return start, end


def collect_usage_rows(start, end):
    start, end = _check_period(start, end)
    fields = (
        "service",
        "model",
        "project_id_snapshot",
        "project_slug",
        "component_id_snapshot",
        "component_slug",
        "target_language_code",
        "operation",
    )
    rows = list(
        LLMUsageLog.objects.filter(created_at__gte=start, created_at__lt=end)
        .values(*fields)
        .annotate(
            requests=Count("id"),
            strings=Sum("batch_size"),
            prompt=Sum("prompt_tokens"),
            completion=Sum("completion_tokens"),
            total=Sum("total_tokens"),
            cost=Sum("cost_usd"),
            unpriced=Count("id", filter=Q(cost_usd__isnull=True)),
        )
        .order_by()
    )
    projects = dict(
        Project.objects.filter(
            pk__in={r["project_id_snapshot"] for r in rows if r["project_id_snapshot"]}
        ).values_list("pk", "slug")
    )
    components = dict(
        Component.objects.filter(
            pk__in={
                r["component_id_snapshot"] for r in rows if r["component_id_snapshot"]
            }
        ).values_list("pk", "slug")
    )
    for row in rows:
        row["project"] = projects.get(row["project_id_snapshot"], row["project_slug"])
        row["component"] = components.get(
            row["component_id_snapshot"], row["component_slug"]
        )
        row["attribution_complete"] = bool(
            row["project_id_snapshot"]
            and row["component_id_snapshot"]
            and row["target_language_code"]
        )
    return rows


def _spend_config():
    key = getattr(settings, "JUDGE_API_KEY", "")
    base = getattr(settings, "JUDGE_BASE_URL", "")
    if not isinstance(key, str) or not key.strip() or not isinstance(base, str):
        msg = "LiteLLM spend configuration is missing"
        raise LLMReportConfigurationError(msg)
    try:
        parsed = urlsplit(base.strip().rstrip("/"))
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "hcbifrost.herocraft.com"
            and parsed.port in {None, 443}
            and parsed.path == "/litellm/v1"
            and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment
            )
        )
    except ValueError:
        valid = False
    if not valid:
        msg = "LiteLLM spend endpoint is not the verified endpoint"
        raise LLMReportConfigurationError(msg)
    return "https://hcbifrost.herocraft.com/litellm/spend/logs", key


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        msg = "Invalid LiteLLM spend data"
        raise LLMReportDataError(msg)
    value = Decimal(value)
    if (
        not value.is_finite()
        or value < 0
        or value.adjusted() > 20
        or value.adjusted() < -30
    ):
        msg = "Invalid LiteLLM spend data"
        raise LLMReportDataError(msg)
    return value


def _read_spend_response(response, deadline):
    if response.status_code in {408, 429} or response.status_code >= 500:
        msg = "LiteLLM spend service temporarily unavailable"
        raise LLMReportTemporaryError(msg)
    if response.status_code != 200:
        msg = "LiteLLM spend service rejected the request"
        raise LLMReportDataError(msg)
    body = bytearray()
    for chunk in response.iter_bytes():
        if time.monotonic() > deadline:
            msg = "LiteLLM spend response deadline exceeded"
            raise LLMReportTemporaryError(msg)
        if len(body) + len(chunk) > MAX_SPEND_BYTES:
            msg = "LiteLLM spend response exceeds size limit"
            raise LLMReportDataError(msg)
        body.extend(chunk)
    if time.monotonic() > deadline:
        msg = "LiteLLM spend response deadline exceeded"
        raise LLMReportTemporaryError(msg)
    return body


def _request_spend(url, key, digest, start, end):
    deadline = time.monotonic() + 30.0
    with stream_validated_url(
        "get",
        url,
        follow_redirects=False,
        timeout=30.0,
        headers={"Authorization": f"Bearer {key}"},
        params={
            "api_key": digest,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "summarize": "true",
        },
    ) as response:
        return _read_spend_response(response, deadline)


def _spend_day(item, start, end, digest, days):
    day = date.fromisoformat(item["startTime"])
    models = item["models"]
    spend = _amount(item["spend"])
    if (
        not isinstance(models, dict)
        or day in days
        or not start.date() <= day <= end.date()
    ):
        msg = "Invalid LiteLLM spend data"
        raise LLMReportDataError(msg)
    days.add(day)
    amounts = [
        (name, _amount(amount))
        for name, amount in models.items()
        if isinstance(name, str) and name
    ]
    difference = spend - sum((amount for _, amount in amounts), Decimal(0))
    # LiteLLM aggregates binary floats independently by day and model.
    tolerance = max(Decimal("1e-12"), spend * Decimal("1e-12"))
    if (
        len(amounts) != len(models)
        or abs(difference) > tolerance
        or (spend and digest not in item)
        or (digest in item and abs(_amount(item[digest]) - spend) > tolerance)
        or (day == end.date() and spend)
    ):
        msg = "Invalid LiteLLM spend data"
        raise LLMReportDataError(msg)
    if day == end.date():
        return []
    rows = [{"day": day, "model": name, "cost": amount} for name, amount in amounts]
    if difference:
        rows.append(
            {"day": day, "model": "", "cost": difference, "reconciliation": True}
        )
    return rows


def _decode_spend(body, start, end, digest):
    data = json.loads(
        body,
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(data, list) or not data:
        msg = "No LiteLLM spend data for this month"
        raise LLMReportDataError(msg)
    rows = []
    days: set[date] = set()
    for item in data:
        rows.extend(_spend_day(item, start, end, digest, days))
    return rows


def fetch_litellm_key_spend(start, end):
    start, end = _check_period(start, end)
    url, key = _spend_config()
    digest = hashlib.sha256(key.encode()).hexdigest()
    try:
        body = _request_spend(url, key, digest, start, end)
    except (httpx2.HTTPError, OSError):
        msg = "LiteLLM spend service temporarily unavailable"
        raise LLMReportTemporaryError(msg) from None
    except ValidationError:
        msg = "LiteLLM outbound policy rejected the endpoint"
        raise LLMReportConfigurationError(msg) from None
    try:
        return _decode_spend(body, start, end, digest)
    except (ValueError, UnicodeError, InvalidOperation, KeyError, TypeError):
        msg = "Invalid LiteLLM spend response"
        raise LLMReportDataError(msg) from None


def _safe_cell(value):
    if value is None:
        return ""
    if isinstance(value, (Decimal, int)):
        return str(value)
    text = str(value)
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
        else text
    )


def _csv(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])
    return output.getvalue()


def render_usage_csv(rows):
    headers = (
        "service",
        "model",
        "project_id",
        "project",
        "project_slug_snapshot",
        "component_id",
        "component",
        "component_slug_snapshot",
        "target_language",
        "operation",
        "requests",
        "strings_asked",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_usd",
        "unpriced",
        "priced_complete",
        "attribution_complete",
    )
    return _csv(
        headers,
        (
            (
                r["service"],
                r["model"],
                r["project_id_snapshot"],
                r["project"],
                r["project_slug"],
                r["component_id_snapshot"],
                r["component"],
                r["component_slug"],
                r["target_language_code"],
                r["operation"],
                r["requests"],
                r["strings"],
                r["prompt"],
                r["completion"],
                r["total"],
                r["cost"],
                r["unpriced"],
                "no" if r["unpriced"] else "yes",
                "yes" if r["attribution_complete"] else "no",
            )
            for r in rows
        ),
    )


def render_litellm_spend_csv(rows):
    return _csv(
        ("day", "model", "cost_usd", "scope", "row_type"),
        (
            (
                r["day"].isoformat(),
                r["model"],
                r["cost"],
                "entire-key",
                "reconciliation-difference" if r.get("reconciliation") else "model",
            )
            for r in rows
        ),
    )


def _amount_label(amount, unpriced=0):
    if amount is None:
        return _("неизвестно") + (
            f" ({unpriced} " + _("без стоимости") + ")" if unpriced else ""
        )
    result = f"${amount:.2f}"
    return result + (
        f" ({_('неполная сумма')}, {unpriced} {_('без стоимости')})" if unpriced else ""
    )


def _cost_groups(rows, key):
    groups: dict[Any, dict[str, Any]] = {}
    for row in rows:
        name = key(row)
        group = groups.setdefault(name, {"cost": None, "unpriced": 0})
        if row["cost"] is not None:
            group["cost"] = (group["cost"] or Decimal(0)) + row["cost"]
        group["unpriced"] += row["unpriced"]
    return groups


@dataclass(frozen=True)
class MonthlyReport:
    start: datetime
    end: datetime
    usage: list
    spend: list
    context: dict


def build_monthly_report(start, end):
    start, end = _check_period(start, end)
    usage = collect_usage_rows(start, end)
    spend = fetch_litellm_key_spend(start, end)
    with translation.override("ru"):
        title = f"{_('Расходы Weblate на ИИ')} — {MONTHS[start.month - 1]} {start.year}"
        openrouter = [r for r in usage if r["service"] == "openrouter"]
        project_names = {
            r["project_id_snapshot"]: r["project"] or r["project_slug"]
            for r in openrouter
            if r["project_id_snapshot"]
        }

        def project_key(r):
            identity = r["project_id_snapshot"]
            slug = project_names.get(identity, "") if identity else r["project_slug"]
            if not identity:
                return (_("Без атрибуции"), None, slug)
            if slug.startswith("wizard-"):
                return ("Wizard", "wizard", "")
            if slug.startswith("test-"):
                return (_("Технические проекты"), "test", "")
            return (slug or _("Неизвестный проект"), identity, "")

        projects = _cost_groups(openrouter, project_key)
        models = _cost_groups(
            openrouter, lambda r: r["model"] or _("Неизвестная модель")
        )
        other_services = _cost_groups(
            (r for r in usage if r["service"] not in {"openrouter", "litellm"}),
            lambda r: r["service"] or _("Неизвестный сервис"),
        )
        other_rows = [
            {"name": name, "cost": _amount_label(value["cost"], value["unpriced"])}
            for name, value in sorted(other_services.items())
        ]
        total = _cost_groups(openrouter, lambda _: "total").get(
            "total", {"cost": Decimal(0), "unpriced": 0}
        )
        project_rows = [
            {
                "name": key[0]
                if key[1] is not None or not key[2]
                else f"{key[0]}: {key[2]}",
                "cost": _amount_label(value["cost"], value["unpriced"]),
                "sort": value["cost"] or Decimal(0),
            }
            for key, value in projects.items()
        ]
        project_rows.sort(key=itemgetter("sort"), reverse=True)
        model_rows = []
        for name, value in models.items():
            share = (
                f"{value['cost'] / total['cost'] * 100:.1f}%"
                if value["cost"] is not None and total["cost"]
                else "—"
            )
            model_rows.append(
                {
                    "name": name,
                    "cost": _amount_label(value["cost"], value["unpriced"]),
                    "share": share,
                    "sort": value["cost"] or Decimal(0),
                }
            )
        model_rows.sort(key=itemgetter("sort"), reverse=True)
        judge = [
            r for r in usage if r["service"] == "litellm" and r["operation"] == "judge"
        ]
        combined: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"prompt": None, "completion": None, "total": None, "cost": None}
        )
        for row in judge:
            group = combined[MODEL_ALIASES.get(row["model"], row["model"])]
            for field in ("prompt", "completion", "total"):
                group[field] = (group[field] or 0) + (row[field] or 0)
        for row in spend:
            if row.get("reconciliation"):
                continue
            group = combined[MODEL_ALIASES.get(row["model"], row["model"])]
            group["cost"] = (group["cost"] or Decimal(0)) + row["cost"]
        key_rows: list[dict[str, Any]] = [
            {
                "name": name,
                "prompt": value["prompt"] if value["prompt"] is not None else "—",
                "completion": value["completion"]
                if value["completion"] is not None
                else "—",
                "total": value["total"] if value["total"] is not None else "—",
                "cost": _amount_label(value["cost"])
                if value["cost"] is not None
                else "—",
                "sort": value["cost"] or Decimal(0),
            }
            for name, value in combined.items()
        ]
        key_rows.sort(key=itemgetter("sort"), reverse=True)
        context = {
            "subject": title,
            "projects": project_rows,
            "models": model_rows,
            "openrouter_total": _amount_label(total["cost"], total["unpriced"]),
            "judge_prompt": sum(r["prompt"] or 0 for r in judge),
            "judge_completion": sum(r["completion"] or 0 for r in judge),
            "judge_total": sum(r["total"] or 0 for r in judge),
            "key_models": key_rows,
            "key_total": _amount_label(sum((r["cost"] for r in spend), Decimal(0))),
            "top_openrouter": ", ".join(r["name"] for r in model_rows[:2]) or "—",
            "top_key": ", ".join(r["name"] for r in key_rows if r["sort"] > 0) or "—",
        }
        context["other_services"] = other_rows
        context["top_key"] = (
            ", ".join(r["name"] for r in key_rows if r["sort"] > 0)
            if len(key_rows) <= 2
            else ", ".join(r["name"] for r in key_rows[:2])
        )
        context["top_key"] = context["top_key"] or "—"
    return MonthlyReport(start, end, usage, spend, context)


def send_monthly_llm_usage_report(start, end, *, report=None, recipients=None):
    start, end = _check_period(start, end)
    recipients = (
        getattr(settings, "LLM_COST_REPORT_RECIPIENTS", [])
        if recipients is None
        else recipients
    )
    if not isinstance(recipients, (list, tuple)) or not recipients:
        msg = "Report recipients are not configured"
        raise LLMReportConfigurationError(msg)
    try:
        for recipient in recipients:
            validate_email(recipient)
    except (ValidationError, TypeError):
        msg = "Invalid report recipient"
        raise LLMReportConfigurationError(msg) from None
    _spend_config()
    if report is None:
        report = build_monthly_report(start, end)
    if report.start != start or report.end != end:
        msg = "Report month does not match requested month"
        raise LLMReportConfigurationError(msg)
    with translation.override("ru"):
        context = {
            **report.context,
            "LANGUAGE_CODE": translation.get_language(),
            "LANGUAGE_BIDI": translation.get_language_bidi(),
            "current_site_url": get_site_url(),
            "site_title": settings.SITE_TITLE,
            "change": None,
            "request": None,
            "translation": None,
            "component": None,
            "project": None,
            "unsubscribe_url": None,
            "unsubscribe_note": None,
        }
        text = render_to_string("mail/llm_cost_report.txt", context)
        html = render_to_string("mail/llm_cost_report.html", context)
        message = EmailMultiAlternatives(
            subject=f"{settings.EMAIL_SUBJECT_PREFIX}{report.context['subject']}",
            body=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[],
            bcc=list(recipients),
        )
        message.attach_alternative(html, "text/html")
        for name, domain in (
            ("email-logo.png", "weblate.org"),
            ("email-logo-footer.png", "hcgameloc"),
        ):
            image = MIMEPart()
            image.set_content(
                load_icon(name, auto_prefix=False),
                maintype="image",
                subtype="png",
                disposition="inline",
                filename=name,
                cid=f"<{name}@cid.{domain}>",
            )
            message.attach(image)
        month = start.strftime("%Y-%m")
        message.attach(
            f"llm-usage-{month}.csv", render_usage_csv(report.usage), "text/csv"
        )
        message.attach(
            f"litellm-key-spend-{month}.csv",
            render_litellm_spend_csv(report.spend),
            "text/csv",
        )
        message.send(fail_silently=False)
