# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from copy import copy
from enum import StrEnum
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.postgres import indexes as postgres_indexes
from django.db import models, transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext

from weblate.checks.models import CHECKS, Check
from weblate.trans.actions import ActionEvents
from weblate.trans.autofixes import fix_target
from weblate.trans.exceptions import (
    SuggestionSimilarToTranslationError,
    SuggestionTooLongError,
)
from weblate.trans.mixins import UserDisplayMixin
from weblate.trans.util import join_plural, split_plural
from weblate.trans.validators import get_translation_text_max_length
from weblate.utils import messages
from weblate.utils.antispam import report_spam
from weblate.utils.request import get_ip_address, get_user_agent_raw
from weblate.utils.state import STATE_TRANSLATED

if TYPE_CHECKING:
    from weblate.auth.models import AuthenticatedHttpRequest, User
    from weblate.trans.models.unit import Unit


class SuggestionAddResult(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    VOTED = "voted"
    SIMILAR = "similar"
    TOO_LONG = "too_long"


class SuggestionManager(models.Manager["Suggestion"]):
    def add(  # ruff: ignore[complex-structure]
        self,
        unit: Unit,
        target: list[str],
        request: AuthenticatedHttpRequest | None,
        vote: bool = False,
        user: User | None = None,
        raise_exception: bool = True,
        userdetails: dict[str, object] | None = None,
        change_details: dict[str, object] | None = None,
    ) -> tuple[Suggestion | None, SuggestionAddResult]:
        """
        Create new suggestion for this unit.

        ``userdetails`` and ``change_details`` are trusted internal-only
        arguments: they are never taken from user input. A mapping passed
        as ``userdetails`` opts the suggestion into the judge namespace
        (dedup scoped to judge candidates, replacement of the previous
        active candidate for the same verdict).
        """
        # ruff: ignore[import-outside-top-level]
        from weblate.auth.models import get_anonymous
        from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
            JudgeCandidateError,
            JudgeCandidateMetadata,
        )

        judge_metadata = JudgeCandidateMetadata.parse(userdetails)

        max_length = get_translation_text_max_length(unit)
        if any(len(text) > max_length for text in target):
            if raise_exception:
                raise SuggestionTooLongError
            return None, SuggestionAddResult.TOO_LONG

        target_merged = join_plural(target)
        if len(target_merged) > max_length:
            if raise_exception:
                raise SuggestionTooLongError
            return None, SuggestionAddResult.TOO_LONG

        # Apply fixups
        fixups: list[str] = []
        if not unit.translation.is_template:
            target, fixups = fix_target(target, unit)

        target_merged = join_plural(target)
        if len(target_merged) > max_length:
            if raise_exception:
                raise SuggestionTooLongError
            return None, SuggestionAddResult.TOO_LONG

        if user is None:
            user = request.user if request else get_anonymous()
        if judge_metadata is not None:
            # Automation authorship: the launcher never owns the candidate
            # (invariant 7); provenance renders from the metadata kind.
            user = get_anonymous()

        if unit.translated and unit.target == target_merged:
            if raise_exception:
                raise SuggestionSimilarToTranslationError
            return None, SuggestionAddResult.SIMILAR

        # Dedup is scoped to the namespace: a judge candidate only collides
        # with another judge candidate, never with a human suggestion.
        # The namespace of an existing row is decided in Python: a JSON
        # key negation would also drop rows where the key is absent.
        same_suggestion = self.filter(target=target_merged, unit=unit).first()
        same_judge_metadata = None
        if same_suggestion is not None:
            try:
                same_judge_metadata = JudgeCandidateMetadata.parse(
                    same_suggestion.userdetails
                )
            except JudgeCandidateError:
                # A malformed persisted row must never abort an unrelated
                # suggestion: treat it as opaque here, never as a live
                # candidate. `active_judge_candidate` skips it too.
                same_judge_metadata = None
        same_is_judge = same_judge_metadata is not None
        if judge_metadata is not None:
            if (
                same_is_judge
                and same_judge_metadata.verdict_id == judge_metadata.verdict_id
            ):
                return same_suggestion, SuggestionAddResult.DUPLICATE
            # One verdict has at most one active candidate (invariant 8):
            # a new verdict replaces the previous judge candidate. Identity
            # is the verdict, not the text: a fresh verdict whose repair is
            # byte-identical to the superseded one must still end up bound
            # to the current verdict, or no candidate is active at all.
            self.filter(unit=unit, userdetails__kind="judge-repair").delete()
        elif same_suggestion is not None and not same_is_judge:
            if same_suggestion.user == user or not vote:
                return same_suggestion, SuggestionAddResult.DUPLICATE
            same_suggestion.add_vote(request, Vote.POSITIVE)
            return same_suggestion, SuggestionAddResult.VOTED

        # Create the suggestion
        stored_details: dict[str, object] = {
            "address": get_ip_address(request),
            "agent": get_user_agent_raw(request),
        }
        if judge_metadata is not None:
            stored_details = judge_metadata.as_dict()
        suggestion = self.create(
            target=target_merged,
            unit=unit,
            user=user,
            userdetails=stored_details,
        )
        suggestion.fixups = fixups

        # Record in change
        change = unit.generate_change(
            user, user, ActionEvents.SUGGESTION, check_new=False, save=False
        )
        change.suggestion = suggestion
        change.target = target_merged
        if change_details:
            change.details.update(change_details)
        change.save()

        # Add unit vote
        if vote:
            suggestion.add_vote(request, Vote.POSITIVE)

        # Update suggestion stats: candidates are automation-authored and
        # must not move a human profile.
        if judge_metadata is None:
            user.profile.watch_on_contribution(unit.translation.component.project)
            user.profile.increase_count("suggested")

        unit.invalidate_related_cache()

        return suggestion, SuggestionAddResult.CREATED


class SuggestionQuerySet(models.QuerySet["Suggestion", "Suggestion"]):
    def order(self):
        return self.order_by("-timestamp")

    def filter_access(self, user: User):
        result = self
        if user.needs_project_filter:
            result = result.filter(
                user.get_project_access_query("unit__translation__component__project")
            )
        if user.needs_component_restrictions_filter:
            result = result.filter(
                Q(unit__translation__component__restricted=False)
                | Q(unit__translation__component_id__in=user.component_permissions)
            )
        return result

    def load_votes(self):
        return self.annotate(
            num_votes=Coalesce(
                Sum("vote__value"), Value(0), output_field=models.IntegerField()
            )
        )


class Suggestion(models.Model, UserDisplayMixin):
    unit = models.ForeignKey("trans.Unit", on_delete=models.deletion.CASCADE)
    target = models.TextField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.deletion.CASCADE,
    )
    userdetails = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)

    votes = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Vote", related_name="user_votes"
    )  # type: ignore[var-annotated]

    objects = SuggestionManager.from_queryset(SuggestionQuerySet)()

    class Meta:
        required_db_vendor = "postgresql"
        app_label = "trans"
        verbose_name = "string suggestion"
        verbose_name_plural = "string suggestions"
        # ruff: ignore[mutable-class-default]
        indexes = [
            postgres_indexes.GinIndex(
                postgres_indexes.OpClass(models.F("target"), name="gin_trgm_ops"),
                models.F("unit"),
                name="suggestion_target_fulltext",
            ),
        ]

    def __str__(self) -> str:
        return f"suggestion for {self.unit} by {self.user.username if self.user else 'unknown'}"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fixups: list[str] = []

    @property
    def is_judge_candidate(self) -> bool:
        """
        Whether this row is a stored judge repair candidate.

        A lenient, presentation-only check: any ``kind``-tagged row is
        treated as a candidate even if the rest of its metadata turns out to
        be malformed, so a broken row never falls back to rendering generic
        human-suggestion controls (clone/vote/accept/edit).
        """
        return (
            isinstance(self.userdetails, dict)
            and self.userdetails.get("kind") == "judge-repair"
        )

    @transaction.atomic
    def accept(
        self,
        request: AuthenticatedHttpRequest,
        permission: str = "suggestion.accept",
        state=STATE_TRANSLATED,
    ) -> None:
        from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
            JudgeCandidateMetadata,
        )

        # A judge repair candidate carries its own, stronger guard
        # (unit.review + translation.auto, freshness, single active
        # candidate) that overrides whatever permission/state the caller
        # asked for (invariant 5). This is the one choke point every
        # acceptance surface goes through: the card, the classic suggestion
        # list, the API, vote autoaccept, and bulk accept.
        if JudgeCandidateMetadata.parse(self.userdetails) is not None:
            from weblate.trans.judge_loop import (  # ruff: ignore[import-outside-top-level]
                accept_judge_candidate,
            )

            accept_judge_candidate(self, request)
            return

        if not request.user.has_perm(permission, self.unit):
            messages.error(request, gettext("Could not accept suggestion!"))
            return

        # Skip if there is no change
        if self.unit.target != self.target or self.unit.state < STATE_TRANSLATED:
            if self.user and not self.user.is_anonymous:
                author = self.user
            else:
                author = request.user
            self.unit.translate(
                request.user,
                split_plural(self.target),
                state,
                author=author,
                change_action=ActionEvents.ACCEPT,
            )

        # Delete the suggestion
        self.delete()

    def delete_log(
        self,
        user: User,
        change=ActionEvents.SUGGESTION_DELETE,
        is_spam: bool = False,
        rejection_reason: str = "",
        old: str = "",
    ) -> None:
        """Delete with logging change."""
        # Judge candidates carry closed metadata with no reporter address or
        # agent, so they are never spam-reportable; a plain key lookup here
        # would raise KeyError and 500 the delete endpoint.
        if is_spam and self.userdetails and not self.is_judge_candidate:
            report_spam(
                self.userdetails.get("address", ""),
                self.userdetails.get("agent", ""),
                self.target,
            )
        self.unit.change_set.create(
            action=change,
            user=user,
            target=self.target,
            author=user,
            details={"rejection_reason": rejection_reason},
            old=old,
        )
        self.delete()

    def delete(self, using=None, keep_parents=False):
        result = super().delete(using=using, keep_parents=keep_parents)
        self.unit.invalidate_related_cache()
        return result

    def get_num_votes(self, *, override: bool = False):
        """Return number of votes."""
        if not override and hasattr(
            self, "num_votes"
        ):  # annotation added via `load_votes``
            return self.num_votes
        return self.vote_set.aggregate(Sum("value"))["value__sum"] or 0

    def add_vote(self, request: AuthenticatedHttpRequest | None, value: int) -> None:
        """Add (or updates) vote for a suggestion."""
        if request is None or not request.user.is_authenticated:
            return

        vote, created = Vote.objects.get_or_create(
            suggestion=self, user=request.user, defaults={"value": value}
        )
        if not created or vote.value != value:
            vote.value = value
            vote.save()

        # Automatic accepting. A judge repair candidate never autoaccepts
        # from votes: acceptance is always an explicit producer decision
        # made through the guarded card/API path (invariant 5).
        if self.is_judge_candidate:
            return
        required_votes = self.unit.translation.suggestion_autoaccept
        if required_votes and self.get_num_votes(override=True) >= required_votes:
            self.accept(request, "suggestion.vote")

    def get_checks(self):
        # Build fake unit to run checks
        fake_unit = copy(self.unit)
        fake_unit.target = self.target
        fake_unit.state = STATE_TRANSLATED
        fake_unit.check_cache = {}
        source = fake_unit.get_source_plurals()
        target = fake_unit.get_target_plurals()

        result = []
        for check, check_obj in CHECKS.target.items():
            if check_obj.skip_suggestions:
                continue
            if check_obj.check_target(source, target, fake_unit):
                result.append(Check(unit=fake_unit, dismissed=False, name=check))
        return result

    @property
    def target_list(self) -> list[str]:
        """
        Target split into a list of plurals.

        Used for populating the translation widgets in the frontend.
        """
        return split_plural(self.target)

    def get_target_plurals(self) -> list[str]:
        return self.target_list


class Vote(models.Model):
    """Suggestion voting."""

    suggestion = models.ForeignKey(
        Suggestion, on_delete=models.deletion.CASCADE, db_index=False
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.deletion.CASCADE
    )
    value = models.SmallIntegerField(default=0)

    POSITIVE = 1
    NEGATIVE = -1

    class Meta:
        required_db_vendor = "postgresql"
        # ruff: ignore[mutable-class-default]
        unique_together = [("suggestion", "user")]
        app_label = "trans"
        verbose_name = "suggestion vote"
        verbose_name_plural = "suggestion votes"

    def __str__(self) -> str:
        return f"{self.value:+d} for {self.suggestion} by {self.user.username}"
