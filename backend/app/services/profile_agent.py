# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from app.db import DomainError, revision
from app.services.drafts import get_draft
from app.services.profile_context import get_context_snapshot
from app.services.profiles import get_profile, validate_generated

SKILLS = Path(__file__).parents[1] / "skills"
ENGINE_RULES = (
    "Не добавляй новых фактов и не меняй игровую механику. Соблюдай утверждённую "
    "терминологию глоссария. Сохраняй игровые теги, переменные, плейсхолдеры, числа "
    "и разделители строк, включая $, и идентификаторы игровых подстановок."
)
TOKEN_PATTERN = re.compile(r"<[^>\n]+>|\{[^}\n]*\}|%[A-Za-z0-9_]+%|%[sd]|\b\w+(?=\[[^\]\n]*\|)")


def load_profile_skills():
    text = "\n\n".join(
        (SKILLS / name).read_text(encoding="utf-8")
        for name in ("game-context.md", "translation-prompts.md", "locale-instructions.md")
    )
    return text, revision(text)


def build_profile_input(store, owner, slug, request):
    project = store.get("project", slug, owner)
    profile = get_profile(store, owner, slug, "openrouter")
    if request.revision != profile["revision"]:
        raise DomainError("stale-revision", "Profile changed", 409)
    draft = get_draft(store, request.upload_token, owner)
    if draft["project"] != slug or draft.get("kind") != "string":
        raise DomainError("not_found", "Not found", 404)
    if request.upload_revision != draft["revision"]:
        raise DomainError("stale-revision", "Language selection changed", 409)
    manifest = draft.get("manifest")
    if not manifest:
        raise DomainError("profile_languages", "Save source and target languages first")
    source = draft["answers"].get("source_language")
    targets = list(dict.fromkeys(draft["answers"].get("included_target_languages", [])))
    if not source or not targets or source in targets:
        raise DomainError("profile_languages", "Select source and distinct target languages")
    if (manifest.get("source", source), manifest.get("targets", targets)) != (source, targets):
        raise DomainError("stale-revision", "Loc-kit languages changed", 409)
    snapshot = {}
    if request.context_snapshot_id:
        snapshot = get_context_snapshot(store, owner, slug, request.context_snapshot_id)
        if (snapshot["upload_token"], snapshot["upload_revision"]) != (
            request.upload_token,
            request.upload_revision,
        ):
            raise DomainError("context_stale", "Resolve context for this loc-kit version", 409)
    answers = request.answers.model_dump()
    answer_origins = {}
    for key, supplied in answers.items():
        old = profile["answers"].get(key)
        suggested = snapshot.get("answers", {}).get(key)
        origin = profile.get("field_origins", {}).get("answers." + key)
        if "answers." + key in request.edited_fields or (
            supplied
            and (
                (old and origin not in {"agent", "template", "bdhc"})
                or (supplied != old and supplied != suggested)
            )
        ):
            answer_origins[key] = "manual"
        else:
            answer_origins[key] = "bdhc" if snapshot.get("answers", {}).get(key) else "template"
    # Empty answers can receive game defaults; explicit values always win.
    for key, value in snapshot.get("answers", {}).items():
        if not answers.get(key) or (
            answers.get(key) == profile["answers"].get(key)
            and profile.get("field_origins", {}).get("answers." + key)
            in {"bdhc", "agent", "template"}
        ):
            answers[key] = value
    defaults = {
        "register_ui": "Кратко и понятно",
        "register_dialogue": "Естественно, по контексту персонажа",
        "profanity": "Не усиливать ненормативную лексику оригинала",
    }
    for key, value in defaults.items():
        if not answers.get(key):
            answers[key] = value
    rules = json.loads((SKILLS / "locales.json").read_text(encoding="utf-8"))
    guides, warnings = {}, list(snapshot.get("warnings", []))
    for code in targets:
        guides[code] = rules["guides"].get(
            code, f"{code}: проверенной локальной справки нет; особенности нужно проверить вручную."
        )
        if code not in rules["guides"]:
            warnings.append(f"{code}: no verified local locale guide")
    from app.services.manifest import component_source

    manifest = {**manifest, "source": source}
    source_languages = list(
        dict.fromkeys(component_source(manifest, c) for c in manifest.get("components", []))
    )
    evidence, tokens, has_separator = [], set(), False
    # Invariants scan ALL selected source units, examples only the first ten.
    for component in manifest.get("components", []):
        for unit in component.get("units", []):
            actual = component_source(manifest, component)
            text = str(unit.get("values", {}).get(actual, ""))
            tokens.update(TOKEN_PATTERN.findall(text))
            has_separator |= "$" in text
            if text and len(evidence) < 10:
                evidence.append(
                    {
                        "key": str(unit.get("key", ""))[:200],
                        "source": text[:1000],
                        "source_language": actual,
                    }
                )
    value = {
        "project_slug": slug,
        "project_name": project["name"],
        "game_title": snapshot.get("title"),
        "source_language": source,
        "source_languages": source_languages,
        "target_languages": targets,
        "upload_token": request.upload_token,
        "upload_revision": request.upload_revision,
        "profile_revision": profile["revision"],
        "context_snapshot_id": request.context_snapshot_id,
        "context_hash": snapshot.get("context_hash"),
        "answers": answers,
        "answer_origins": answer_origins,
        "facts": copy.deepcopy(snapshot.get("facts", {})),
        "locale_rules": copy.deepcopy(snapshot.get("locale_rules", {})),
        "locale_guides": guides,
        "evidence": evidence,
        "manifest_hash": revision(manifest),
        "protected_tokens": {
            "examples": sorted(tokens)[:100],
            "has_dollar_separator": has_separator,
            "rules": ENGINE_RULES,
        },
        "skills_version": load_profile_skills()[1],
        "locale_rules_version": revision(rules),
        "warnings": warnings,
    }
    if len(json.dumps(value, ensure_ascii=False)) > 24000:
        raise DomainError(
            "context_too_large", "Agent input exceeds the context limit; review context manually"
        )
    return value


def validate_agent_result(value, input, *, generated_proposal=True):
    result = validate_generated(value, input["target_languages"])
    if (
        not result["persona"].strip()
        or not result["style"].strip()
        or set(result["language_instructions"]) != set(input["target_languages"])
    ):
        raise DomainError(
            "profile_instructions", "Generate nonempty instructions for every selected language"
        )
    if ENGINE_RULES not in result["style"]:
        result["style"] += "\n" + ENGINE_RULES
    if generated_proposal and input.get("source_languages", [input["source_language"]]) != [
        input["source_language"]
    ]:
        result["persona"] = actual_source_persona(input)
    return validate_generated(result, input["target_languages"])


def actual_source_persona(input):
    name = input.get("game_title") or input["project_name"]
    role = f"Ты профессиональный локализатор видеоигр. Переводи тексты {name} с фактического исходного языка, указанного в текущем запросе Weblate, на его целевой язык. Возможные источники: {', '.join(input.get('source_languages', []))}. Локализуй интерфейс, диалоги и игровые описания."
    context = "; ".join(
        f"{key}: {input['answers'][key]}"
        for key in ("genre", "setting", "audience")
        if input["answers"].get(key)
    )
    return role + (" Контекст игры: " + context + "." if context else "")


def template_profile(input):
    answers = input["answers"]
    name = input.get("game_title") or input["project_name"]
    persona = f"Ты профессиональный локализатор видеоигр. Переводи тексты {name} с языка {input['source_language']} на {', '.join(input['target_languages'])}: интерфейс, диалоги и игровые описания."
    context = "; ".join(
        f"{key}: {answers[key]}" for key in ("genre", "setting", "audience") if answers.get(key)
    )
    if context:
        persona += " Контекст игры: " + context + "."
    style = f"Интерфейс: {answers.get('register_ui', 'кратко и понятно')}. Диалоги: {answers.get('register_dialogue', 'естественно, по контексту')}. Правила ненормативной лексики: {answers.get('profanity', 'не усиливать оригинал')}."
    if any("profiles require" in warning for warning in input["warnings"]):
        raise DomainError(
            "profile_context_conflict", "BDHC language rules conflict; select rules manually"
        )
    instructions = {}
    for code, guide in input["locale_guides"].items():
        rules = input["locale_rules"].get(code, {})
        text = guide
        if rules:
            text += " Правила игры: " + json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
        if len(text) > 1000:
            raise DomainError(
                "profile_instructions",
                "Game language rules exceed the instruction limit; review manually",
            )
        instructions[code] = text
    return validate_agent_result(
        {"persona": persona, "style": style, "language_instructions": instructions}, input
    )
