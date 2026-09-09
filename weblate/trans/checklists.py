# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections import UserList
from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol
from urllib.parse import quote

from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy

from weblate.checks.models import CHECKS
from weblate.trans.filter import FILTERS

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

    from weblate.trans.models.project import Project
    from weblate.utils.stats import BaseStats


class TranslationProtocol(Protocol):
    project: Project
    stats: BaseStats
    enable_review: bool
    is_readonly: bool
    is_source: bool

    def get_translate_url(self) -> str: ...


class EngageTask(NamedTuple):
    url: str
    label: StrOrPromise
    total: int


class TranslationChecklistItem(NamedTuple):
    """
    One row of `TranslationChecklistMixin.list_translation_checks`.

    `check_id`/`mass_fixup` are set only by the per-`CHECKS` loop in
    `list_translation_checks`; every aggregate bucket (`all`, `translated`,
    labels, ...) leaves them `None` (docs/product/plans/
    2026-08-25-mass-fix-failing-checks.md, Task 5 step 1). `fix_url_name`
    is the `fix-check` URL name a "Fix" link should use - the policy Task
    A/B's `CHECK_TO_POLICY_ID` maps this check to, if any, else `check_id`
    itself - resolved down to `None` when no `FixPolicy` is actually
    available (docs/product/plans/2026-09-09-producer-bulk-punctuation-
    repair.md, Task D: "available by supported operation, not merely by
    `get_fixup` presence"). `begin_space`/`end_space` carry no `mass_fixup`
    of their own - they are only ever fixable through the mapped
    `edge-space-remove` mechanical group - so `mass_fixup` alone is not a
    reliable gate for whether a "Fix" link belongs here; `fix_url_name` is.
    """

    query: str
    name: StrOrPromise
    total: int
    color: str
    words: int
    characters: int
    check_id: str | None = None
    mass_fixup: Literal["safe", "review"] | None = None
    fix_url_name: str | None = None


class TranslationChecklistMixin:
    @cached_property
    def list_engage_tasks(self: TranslationProtocol) -> EngageChecklist:
        """Return list of non-empty task buckets for the engage page."""
        result = EngageChecklist(self.get_translate_url())

        result.add_if(self.stats, "nottranslated", gettext_lazy("Untranslated"))
        result.add_if(self.stats, "translated_checks", gettext_lazy("Failing checks"))
        result.add_if(
            self.stats,
            "suggestions",
            gettext_lazy("Suggestions pending"),
            "suggestions",
        )
        result.add_if(self.stats, "fuzzy", gettext_lazy("Needs editing"))
        if self.enable_review:
            result.add_if(self.stats, "unapproved", gettext_lazy("Needs review"))

        return result

    @cached_property
    def list_translation_checks(self: TranslationProtocol) -> TranslationChecklist:
        """Return list of failing checks on current translation."""
        # Deferred: `weblate.trans.fix_check` imports `weblate.trans.models`,
        # which (via `weblate.utils.stats`) imports this module - a
        # module-level import here would be circular.
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.fix_check import (
            fix_check_policy_id_for_check,
            resolve_fix_policy,
        )

        result = TranslationChecklist()

        # All strings
        result.add(self.stats, "all", "")

        result.add_if(
            self.stats, "readonly", "primary" if self.enable_review else "success"
        )

        if not self.is_readonly:
            if self.enable_review:
                result.add_if(self.stats, "approved", "primary")

            # Count of translated strings
            result.add_if(self.stats, "translated", "success")

            # To approve
            if self.enable_review:
                result.add_if(self.stats, "unapproved", "success")

                # Approved with suggestions
                result.add_if(self.stats, "approved_suggestions", "primary")

            # Unfinished strings
            result.add_if(self.stats, "todo", "")

            # Untranslated strings
            result.add_if(self.stats, "nottranslated", "")

            # Fuzzy strings
            result.add_if(self.stats, "fuzzy", "")

            # Translations with suggestions
            if result.add_if(self.stats, "suggestions", ""):
                result.add_if(self.stats, "nosuggestions", "")

        # All checks
        result.add_if(self.stats, "allchecks", "")

        # Translated strings with checks
        if not self.is_source:
            result.add_if(self.stats, "translated_checks", "")

        # Dismissed checks
        result.add_if(self.stats, "dismissed_checks", "")

        # Process specific checks
        for check in CHECKS:
            check_obj = CHECKS[check]
            mapped_id = fix_check_policy_id_for_check(check_obj.check_id)
            fix_url_name = (
                mapped_id if resolve_fix_policy(mapped_id) is not None else None
            )
            result.add_if(
                self.stats,
                check_obj.url_id,
                "",
                check_id=check_obj.check_id,
                mass_fixup=check_obj.mass_fixup,
                fix_url_name=fix_url_name,
            )

        # Grab comments
        result.add_if(self.stats, "comments", "")

        # Include labels
        labels = self.project.label_set.order()
        if labels:
            has_label = False
            for label in labels:
                has_label |= result.add_if(
                    self.stats,
                    f"label:{label.name}",
                    f"label label-{label.color}",
                )
            if has_label:
                result.add_if(self.stats, "unlabeled", "")

        return result


class TranslationChecklist(UserList):
    """Simple list wrapper for translation checklist."""

    def add_if(
        self,
        stats,
        name,
        level,
        *,
        check_id: str | None = None,
        mass_fixup: Literal["safe", "review"] | None = None,
        fix_url_name: str | None = None,
    ) -> bool:
        """Add to list if there are matches."""
        if getattr(stats, name) > 0:
            self.add(
                stats,
                name,
                level,
                check_id=check_id,
                mass_fixup=mass_fixup,
                fix_url_name=fix_url_name,
            )
            return True
        return False

    def add(
        self,
        stats,
        name,
        level,
        *,
        check_id: str | None = None,
        mass_fixup: Literal["safe", "review"] | None = None,
        fix_url_name: str | None = None,
    ) -> None:
        """Add item to the list."""
        self.append(
            TranslationChecklistItem(
                query=FILTERS.get_filter_query(name),
                name=FILTERS.get_filter_name(name),
                total=getattr(stats, name),
                color=level,
                words=getattr(stats, f"{name}_words"),
                characters=getattr(stats, f"{name}_chars"),
                check_id=check_id,
                mass_fixup=mass_fixup,
                fix_url_name=fix_url_name,
            )
        )


class EngageChecklist(UserList):
    """Simple list wrapper for engage page tasks."""

    def __init__(self, translate_url: str) -> None:
        super().__init__()
        self.translate_url = translate_url

    def add_if(self, stats, name, label, fragment: str = "") -> bool:
        """Add to list if there are matches."""
        count = getattr(stats, name)
        if count <= 0:
            return False

        query = quote(FILTERS.get_filter_query(name), safe=":=")
        url = f"{self.translate_url}?q={query}"
        if fragment:
            url = f"{url}#{fragment}"

        self.append(EngageTask(url, label, count))
        return True
