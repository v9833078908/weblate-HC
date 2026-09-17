# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import time
from contextlib import ExitStack, contextmanager, suppress
from datetime import datetime, timedelta
from functools import wraps
from glob import glob
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from celery import current_task
from celery.exceptions import MaxRetriesExceededError
from celery.schedules import crontab
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.translation import gettext, ngettext, override

from weblate.accounts.utils import remove_user
from weblate.addons.events import AddonActivityLogReason, AddonActivityLogStatus
from weblate.auth.models import AuthenticatedHttpRequest, User, get_anonymous
from weblate.lang.models import Language
from weblate.logger import LOGGER
from weblate.machinery.base import MACHINERY_DEFAULT_THRESHOLD
from weblate.trans.actions import ActionEvents
from weblate.trans.autotranslate import BatchAutoTranslate
from weblate.trans.component_copy import copy_component_addons
from weblate.trans.exceptions import FileParseError
from weblate.trans.fix_check import (
    refresh_fix_check_lock,
    release_fix_check_lock,
    resolve_fix_policy,
)
from weblate.trans.inherited_settings import apply_create_inheritance_defaults
from weblate.trans.judge import JudgeError
from weblate.trans.judge_loop import DEFAULT_CANDIDATE_SEVERITIES
from weblate.trans.models import (
    Category,
    Change,
    Comment,
    Component,
    ComponentList,
    PendingUnitChange,
    Project,
    Report,
    Suggestion,
    Translation,
    Unit,
)
from weblate.trans.removal import RemovalBatch, removal_batch_context
from weblate.utils.celery import (
    INTERACTIVE_TASK_PRIORITY,
    app,
    delete_task_liveness,
    heartbeat_task,
    register_task_liveness,
    touch_task_liveness,
)
from weblate.utils.data import data_dir
from weblate.utils.errors import report_error
from weblate.utils.files import VCS_METADATA_DIRS, remove_tree
from weblate.utils.lock import WeblateLock, WeblateLockTimeoutError
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED
from weblate.utils.stats import ProjectLanguage, prefetch_stats
from weblate.vcs.base import RepositoryError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from loc_kit_ingest.model import StringUnit
    from weblate.trans.models.change import RevertUserEditsResult
    from weblate.trans.models.unit import UnitQuerySet
    from weblate.workspaces.models import Workspace


class JudgeExecutionGuardError(Exception):
    """A duplicate delivery found its producer execution already running."""


def _requires_producer_execution_guard(
    *, mode: str, auto_source: Literal["mt", "others"], task_id: str
) -> bool:
    return bool(task_id) and (mode == "judge" or auto_source == "mt")


@contextmanager
def producer_execution_guard(*, producer_run_id: str | None, task_id: str):
    """Acquire a file-only guard for one Celery delivery's producer run."""
    lock = WeblateLock(
        scope="producer-execution",
        key=producer_run_id or task_id,
        slug="judge",
        timeout=0,
        file_only=True,
    )
    with ExitStack() as stack:
        try:
            stack.enter_context(lock)
        except WeblateLockTimeoutError as error:
            raise JudgeExecutionGuardError from error
        yield


def _fail_stalled_producer_run(
    *, producer_run_id: str | None, task_id: str
) -> str | None:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import ProducerRun

    run = (
        ProducerRun.objects.filter(pk=producer_run_id).first()
        if producer_run_id
        else ProducerRun.objects.filter(task_id=task_id).first()
    )
    if run is None or (
        run.task_id != task_id and str(run.dispatch_task_id or "") != task_id
    ):
        return None
    if run.status in {ProducerRun.Status.QUEUED, ProducerRun.Status.RUNNING}:
        run.status = ProducerRun.Status.FAILED
        run.finished = timezone.now()
        run.failure = gettext("The execution lock was not released.")
        run.save(update_fields=["status", "finished", "failure"])
    return str(run.pk)


def guard_producer_execution(function):
    """Serialize one task delivery before it can create or adopt a run."""

    @wraps(function)
    def wrapper(self, *args, **kwargs):
        task_id = self.request.id or ""
        mode = kwargs["mode"]
        auto_source = kwargs["auto_source"]
        producer_run_id = kwargs.get("producer_run_id")
        if not _requires_producer_execution_guard(
            mode=mode, auto_source=auto_source, task_id=task_id
        ):
            return function(self, *args, **kwargs)
        try:
            with producer_execution_guard(
                producer_run_id=producer_run_id, task_id=task_id
            ):
                return function(self, *args, **kwargs)
        except JudgeExecutionGuardError as error:
            try:
                return self.retry(
                    exc=error,
                    countdown=60,
                    max_retries=settings.JUDGE_GUARD_WAIT_RETRIES,
                )
            except (MaxRetriesExceededError, JudgeExecutionGuardError):
                run_id = _fail_stalled_producer_run(
                    producer_run_id=producer_run_id, task_id=task_id
                )
                result = {
                    "message": gettext("The execution lock was not released."),
                    "warnings": [],
                }
                if run_id:
                    result["report_url"] = reverse("judge-run", kwargs={"pk": run_id})
                return result

    return wrapper


def commit_lock_retries_exhausted() -> bool:
    """Check whether a commit lock timeout will no longer be retried."""
    if not current_task:
        return True

    if current_task.max_retries is None:
        return False
    return current_task.request.retries >= current_task.max_retries


def schedule_deferred_commit(component: Component) -> None:
    followup = component.finish_commit_task()
    if followup:
        component.queue_commit_pending(
            followup["reason"],
            user_id=followup["user_id"],
            force_scan=followup["force_scan"],
            previous_head=followup["previous_head"],
            # A payload stored before this field existed stays background.
            user_waiting=followup.get("user_waiting", False),
        )


def perform_component_commit(
    component: Component,
    reason: str,
    user: User | None,
    *,
    force_scan: bool = False,
    previous_head: str | None = None,
) -> None:
    with component.repository.lock:
        component.commit_pending(reason, user=user)
        if force_scan:
            component.trigger_post_update(
                previous_head=previous_head,
                skip_push=False,
                user=user,
                parse_after_update=True,
            )
            component.create_translations(force=True)


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
)
def perform_update(
    cls: Literal["Project", "Component"],
    pk: int,
    auto: bool = False,
    obj=None,
    user_id: int = 0,
) -> None:
    request: AuthenticatedHttpRequest | None = None
    if user_id:
        request = AuthenticatedHttpRequest()
        request.user = User.objects.get(pk=user_id)
    # This is stored as alert, so we can silently ignore some exceptions here
    with suppress(FileParseError, RepositoryError, FileNotFoundError):
        if obj is None:
            if cls == "Project":
                obj = Project.objects.get(pk=pk)
            else:
                obj = Component.objects.get(pk=pk)
        obj.log_info("Updating remote repository")
        if settings.AUTO_UPDATE in {"full", True} or not auto:
            obj.do_update(request)
        else:
            obj.update_remote_branch(user=obj.get_update_user(request))


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
)
def perform_load(
    pk: int,
    *,
    force: bool = False,
    force_scan: bool = False,
    langs: list[str] | None = None,
    changed_template: bool = False,
    from_link: bool = False,
    change: int | None = None,
    preserve_pending_units: bool = False,
    loc_kit_explanations: dict[str, str] | None = None,
    user_id: int | None = None,
) -> None:
    request: AuthenticatedHttpRequest | None = None
    user: User | None = None
    if user_id:
        user = User.objects.get(pk=user_id)
        request = AuthenticatedHttpRequest()
        request.user = user
    try:
        component = Component.objects.get(pk=pk)
    except Component.DoesNotExist:
        # Component was removed
        return
    component.create_translations_immediate(
        force=force,
        force_scan=force_scan,
        langs=langs,
        changed_template=changed_template,
        from_link=from_link,
        change=change,
        preserve_pending_units=preserve_pending_units,
        loc_kit_explanations=loc_kit_explanations,
        request=request,
        user=user,
    )


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
)
def perform_commit(
    pk,
    reason: str,
    *,
    user_id: int | None = None,
    force_scan: bool = False,
    previous_head: str | None = None,
) -> None:
    component = Component.objects.get(pk=pk)
    try:
        user = User.objects.get(pk=user_id) if user_id else None
        perform_component_commit(
            component,
            reason,
            user,
            force_scan=force_scan,
            previous_head=previous_head,
        )
    except WeblateLockTimeoutError:
        if commit_lock_retries_exhausted():
            schedule_deferred_commit(component)
        raise
    except Exception:
        component.delete_commit_task(require_match=True)
        raise
    schedule_deferred_commit(component)


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
)
def perform_push(pk, *args, **kwargs) -> None:
    component = Component.objects.get(pk=pk)
    component.do_push(*args, **kwargs)


@app.task(trail=False)
def commit_pending(
    hours: int | None = None,
    pks: set[int] | None = None,
    logger: Callable[[str], None] | None = None,
) -> None:
    components = PendingUnitChange.objects.find_committable_components(
        pks=list(pks) if pks else None, hours=hours
    )

    if not components:
        return

    components = prefetch_stats(components)

    for component in components:
        if logger:
            logger(f"Committing {component}")

        component.queue_commit_pending("commit_pending")


@app.task(trail=False)
def revert_user_edits(
    target_user_id: int,
    acting_user_id: int,
    *,
    project_id: int | None = None,
    sitewide: bool = False,
) -> RevertUserEditsResult:
    if project_id is None and not sitewide:
        msg = "Either project_id or sitewide must be provided"
        raise ValueError(msg)

    target_user = User.objects.get(pk=target_user_id)
    acting_user = User.objects.get(pk=acting_user_id)
    project = Project.objects.get(pk=project_id) if project_id is not None else None
    return Change.objects.revert_user_edits(
        target_user,
        acting_user,
        project=project,
    )


@app.task(trail=False)
@transaction.atomic
def cleanup_user_contributions(
    target_user_id: int,
    acting_user_id: int,
    *,
    project_id: int | None = None,
    sitewide: bool = False,
    reject_suggestions: bool = False,
    delete_comments: bool = False,
) -> dict[str, int]:
    if project_id is None and not sitewide:
        msg = "Either project_id or sitewide must be provided"
        raise ValueError(msg)

    target_user = User.objects.get(pk=target_user_id)
    acting_user = User.objects.get(pk=acting_user_id)

    rejected_suggestions = 0
    deleted_comments = 0

    if reject_suggestions:
        suggestions = Suggestion.objects.filter(user=target_user).select_related("unit")
        if project_id is not None:
            suggestions = suggestions.filter(
                unit__translation__component__project_id=project_id
            )
        for suggestion in suggestions.iterator(chunk_size=100):
            suggestion.delete_log(acting_user, old=suggestion.unit.target)
            rejected_suggestions += 1

    if delete_comments:
        comments = Comment.objects.filter(user=target_user).select_related("unit")
        if project_id is not None:
            comments = comments.filter(
                unit__translation__component__project_id=project_id
            )
        for comment in comments.iterator(chunk_size=100):
            comment.delete(user=acting_user)
            deleted_comments += 1

    return {
        "comments": deleted_comments,
        "suggestions": rejected_suggestions,
    }


def get_bulk_accept_user_suggestions_message(
    *, accepted: int, failed: int, total: int, username: str
) -> str:
    """Build the completion message for accepting suggestions from a user."""
    if total == 0:
        return gettext("No suggestions found.")

    if failed == 0:
        return ngettext(
            "Accepted %(count)d suggestion from %(user)s.",
            "Accepted %(count)d suggestions from %(user)s.",
            accepted,
        ) % {
            "count": accepted,
            "user": username,
        }

    return ngettext(
        "Accepted %(accepted)d of %(total)d suggestion from %(user)s. %(failed)d failed due to permissions or checks.",
        "Accepted %(accepted)d of %(total)d suggestions from %(user)s. %(failed)d failed due to permissions or checks.",
        total,
    ) % {
        "accepted": accepted,
        "total": total,
        "failed": failed,
        "user": username,
    }


def get_bulk_accept_user_suggestions_message_level(
    *, accepted: int, failed: int
) -> str:
    """Return the message level for a bulk accept result."""
    if accepted > 0:
        if failed == 0:
            return "success"
        return "warning"
    if failed > 0:
        return "error"
    return "info"


def report_bulk_accept_user_suggestions_progress(processed: int, total: int) -> None:
    """Report bulk suggestion acceptance progress to Celery."""
    if current_task and current_task.request.id:
        current_task.update_state(
            state="PROGRESS",
            meta={"progress": 100 * processed // total if total else 100},
        )


@app.task(trail=False)
def bulk_accept_user_suggestions(
    *,
    translation_id: int,
    target_user_id: int,
    user_id: int,
    approve: bool = False,
    return_url: str = "",
) -> dict[str, dict[str, str] | int | str]:
    """Accept all suggestions from a specific user for a translation."""
    translation = Translation.objects.get(pk=translation_id)
    target_user = User.objects.get(pk=target_user_id)
    user = User.objects.get(pk=user_id)

    request = AuthenticatedHttpRequest()
    request.user = user

    # Bulk accept is a per-human-author convenience action: it never
    # touches automation-authored judge repair candidates, which have
    # their own guarded acceptance path (invariant 5). The namespace check
    # must happen in Python: a JSON key exclusion in the query would also
    # drop every row where "kind" is absent, i.e. every human suggestion
    # (matches the same trap SuggestionManager.add's dedup check avoids).
    base_suggestions = Suggestion.objects.filter(
        unit__translation=translation, user=target_user
    ).select_related("unit")
    # Counted with two positive queries rather than by materializing the
    # backlog: a large translation must not be loaded into memory at once,
    # and matching "kind" positively avoids the negation trap above.
    total = (
        base_suggestions.count()
        - base_suggestions.filter(userdetails__kind="judge-repair").count()
    )
    accepted = 0
    failed = 0
    processed = 0

    report_bulk_accept_user_suggestions_progress(processed, total)

    for suggestion in base_suggestions.iterator(chunk_size=100):
        if suggestion.is_judge_candidate:
            continue
        processed += 1

        if (
            not user.has_perm("suggestion.accept", suggestion.unit)
            or (approve and not user.has_perm("unit.review", suggestion.unit))
            or list(suggestion.get_checks())
        ):
            failed += 1
        else:
            suggestion.accept(
                request,
                state=STATE_APPROVED if approve else STATE_TRANSLATED,
            )
            accepted += 1

        report_bulk_accept_user_suggestions_progress(processed, total)

    with override(user.profile.language if user else "en"):
        message_level = get_bulk_accept_user_suggestions_message_level(
            accepted=accepted, failed=failed
        )
        message = get_bulk_accept_user_suggestions_message(
            accepted=accepted,
            failed=failed,
            total=total,
            username=target_user.username,
        )
    result: dict[str, dict[str, str] | int | str] = {
        "accepted": accepted,
        "failed": failed,
        "total": total,
        "message": message,
        "completion_message": {
            "level": message_level,
            "text": message,
        },
    }
    if return_url:
        result["url"] = return_url
    return result


@app.task(trail=False)
def cleanup_component(pk: int) -> None:
    """
    Perform cleanup of component models.

    - Remove stale source Unit objects.
    - Update variants.
    """
    try:
        component = Component.objects.get(pk=pk)
    except Component.DoesNotExist:
        return

    # Skip monolingual components, these handle cleanups based on the template
    if component.template:
        return

    # Remove stale variants
    with transaction.atomic():
        component.update_variants()

    translation = component.source_translation
    # Skip translations with a filename (eg. when POT file is present)
    if translation.filename:
        return

    # Remove all units where there is just one referenced unit (self)
    with transaction.atomic():
        referenced_units = Unit.objects.filter(source_unit=OuterRef("pk")).exclude(
            pk=OuterRef("pk")
        )
        deleted, details = (
            translation.unit_set.alias(has_references=Exists(referenced_units))
            .filter(has_references=False)
            .delete()
        )
        if deleted:
            translation.log_info("removed leaf units: %s", details)


@app.task(trail=False)
def cleanup_suggestions() -> None:
    # Process suggestions
    anonymous_user = get_anonymous()
    suggestions = Suggestion.objects.prefetch_related("unit")
    for suggestion in suggestions:
        with transaction.atomic():
            # Remove suggestions with same text as real translation
            if (
                suggestion.unit.target == suggestion.target
                and suggestion.unit.translated
            ):
                suggestion.delete_log(
                    anonymous_user, change=ActionEvents.SUGGESTION_CLEANUP
                )
                continue

            # Remove duplicate suggestions
            if (
                Suggestion.objects.filter(
                    unit=suggestion.unit, target=suggestion.target
                )
                .exclude(id=suggestion.id)
                .exists()
            ):
                suggestion.delete_log(
                    anonymous_user, change=ActionEvents.SUGGESTION_CLEANUP
                )


@app.task(trail=False)
def update_remotes() -> None:
    """Update all remote branches (without attempt to merge)."""
    if settings.AUTO_UPDATE not in {"full", "remote", True, False}:
        return

    now = timezone.now()
    components = (
        Component.objects.with_repo()
        .annotate(hourmod=F("id") % 24)
        .filter(hourmod=now.hour)
    )
    for component in components.prefetch().iterator(chunk_size=100):
        perform_update("Component", -1, auto=True, obj=component)


@app.task(trail=False)
def cleanup_repos() -> None:
    """Cleanup of all internal repositories."""
    now = timezone.now()

    components = (
        Component.objects.with_repo()
        .annotate(id_mod=F("id") % (30 * 24))
        .filter(id_mod=(now.day - 1) * 24 + now.hour)
    )
    for component in components.prefetch().iterator(chunk_size=100):
        try:
            with component.repository.lock:
                component.log_info("Performing repository maintenance")
                component.repository.maintenance()
        except (RepositoryError, WeblateLockTimeoutError):
            report_error("Repository maintenance failed", project=component.project)


def _is_project_or_category_path(parts: tuple[str, ...]) -> bool:
    if len(parts) == 1:
        return Project.objects.filter(slug__iexact=parts[0]).exists()

    if len(parts) < 2:
        return False

    project, *categories = parts
    category = categories[-1]
    kwargs: dict[str, str | None] = {}
    prefix = ""
    for parent in reversed(categories[:-1]):
        kwargs[f"{prefix}category__slug"] = parent
        prefix = f"category__{prefix}"
    if not kwargs:
        kwargs["category"] = None

    return Category.objects.filter(
        slug__iexact=category, project__slug__iexact=project, **kwargs
    ).exists()


def _get_component_by_vcs_path(parts: tuple[str, ...]) -> Component | None:
    if len(parts) < 2:
        return None
    with suppress(Component.DoesNotExist):
        return Component.objects.get_by_path("/".join(parts))
    return None


@app.task(trail=False)
def cleanup_stale_repos(root: Path | None = None) -> bool:
    vcs_root = Path(data_dir("vcs"))
    if root is None:
        root = vcs_root

    yesterday = time.time() - 86400
    root_is_known_container = root == vcs_root or _is_project_or_category_path(
        root.relative_to(vcs_root).parts
    )

    empty_dir = True
    for path in root.glob("*"):
        if not path.is_dir():
            empty_dir = False
            # Possibly a lock file
            continue
        if root_is_known_container and path.name in VCS_METADATA_DIRS:
            empty_dir = False
            continue

        git_dir = path / ".git"
        mercurial_dir = path / ".hg"
        relative_parts = path.relative_to(vcs_root).parts

        if _is_project_or_category_path(relative_parts):
            # Project/category dir, regardless of stale VCS metadata.
            if not cleanup_stale_repos(path):
                empty_dir = False
            continue

        component = _get_component_by_vcs_path(relative_parts)
        if component is not None and not component.is_repo_link:
            empty_dir = False
            continue

        if not git_dir.exists() and not mercurial_dir.exists():
            # Possible project/category dir not present in the database.
            if not cleanup_stale_repos(path):
                empty_dir = False
            continue

        # Skip recently modified paths
        if path.stat().st_mtime > yesterday:
            empty_dir = False
            continue

        if component is None:
            LOGGER.info("removing stale VCS path (not found): %s", path)
            remove_tree(path)
        elif component.is_repo_link:
            LOGGER.info("removing stale VCS path (uses link): %s", root)
            remove_tree(path)
        else:
            empty_dir = False

    if empty_dir and root != vcs_root:
        if root_is_known_container:
            empty_dir = False
        else:
            LOGGER.info("removing stale VCS path (not found): %s", root)
            root.rmdir()
    return empty_dir


@app.task(trail=False)
def repository_alerts(threshold: int = settings.REPOSITORY_ALERT_THRESHOLD) -> None:
    non_linked = Component.objects.with_repo()
    for component in non_linked.iterator():
        try:
            update_repository_alerts(component, threshold)
        except RepositoryError as error:
            report_error("Could not check repository status", project=component.project)
            component.add_alert(
                "MergeFailure", **component.get_repository_alert_details(error)
            )


def update_repository_alerts(component: Component, threshold: int) -> None:
    if component.repository.count_missing() > threshold:
        component.add_alert("RepositoryOutdated")
    else:
        component.delete_alert("RepositoryOutdated")
    if component.repository.count_outgoing() > threshold:
        component.add_alert("RepositoryChanges")
    else:
        component.delete_alert("RepositoryChanges")


@app.task(trail=False)
def component_alerts(component_ids=None) -> None:
    if component_ids:
        components = Component.objects.filter(pk__in=component_ids)
    else:
        now = timezone.now()
        components = Component.objects.annotate(hourmod=F("id") % 24).filter(
            hourmod=now.hour
        )
    for component in components.order_by("id").prefetch().iterator(chunk_size=100):
        with transaction.atomic():
            component.update_alerts()


@app.task(
    trail=False,
    autoretry_for=(Component.DoesNotExist, WeblateLockTimeoutError),
    retry_backoff=60,
)
@transaction.atomic
def component_after_save(  # ruff: ignore[too-many-arguments]
    pk: int,
    *,
    changed_git: bool,
    changed_setup: bool,
    changed_template: bool,
    changed_variant: bool,
    changed_enforced_checks: bool,
    skip_push: bool,
    create: bool,
    seed_source_component_id: int | None = None,
    copy_seed_addons: bool = False,
    seed_author: str | None = None,
    acting_user_id: int | None = None,
    loc_kit_exact: bool = False,
    loc_kit_explanations: dict[str, str] | None = None,
) -> dict[Literal["component"], int]:
    component = Component.objects.get(pk=pk)
    if acting_user_id is not None:
        component.acting_user = User.objects.get(pk=acting_user_id)
    component.after_save(
        changed_git=changed_git,
        changed_setup=changed_setup,
        changed_template=changed_template,
        changed_variant=changed_variant,
        changed_enforced_checks=changed_enforced_checks,
        skip_push=skip_push,
        create=create,
        seed_source_component_id=seed_source_component_id,
        copy_seed_addons=copy_seed_addons,
        seed_author=seed_author,
        loc_kit_exact=loc_kit_exact,
        loc_kit_explanations=loc_kit_explanations,
    )
    return {"component": pk}


@app.task(
    trail=False,
    autoretry_for=(Component.DoesNotExist, WeblateLockTimeoutError),
    retry_backoff=60,
)
@transaction.atomic
def update_enforced_checks(component: int | Component) -> None:
    if isinstance(component, int):
        component = Component.objects.get(pk=component)
    component.update_enforced_checks()


@app.task(trail=False)
@transaction.atomic
def component_removal(pk: int, uid: int) -> None:
    user = User.objects.get(pk=uid)
    try:
        component = Component.objects.get(pk=pk)
    except Component.DoesNotExist:
        return

    _component_removal(component, user)


def _component_removal(
    component: Component, user: User, batch: RemovalBatch | None = None
) -> None:
    if batch is not None:
        component.removal_batch = batch
    with component.repository.lock:
        component.acting_user = user
        component.project.change_set.create(
            action=ActionEvents.REMOVE_COMPONENT,
            target=component.slug,
            user=user,
            author=user,
        )
        component.delete()
        if component.allow_translation_propagation:
            components = component.project.component_set.filter(
                allow_translation_propagation=True
            ).exclude(pk=component.pk)
            if batch is not None:
                components = components.exclude(pk__in=batch.removed_component_ids)
            for current in components.iterator():
                current.schedule_update_checks()


def _collect_removal_targets(category: Category, batch: RemovalBatch) -> None:
    _collect_linked_removal_targets(
        category.component_set.values_list("id", flat=True).iterator(chunk_size=1000),
        batch,
    )

    for child in category.category_set.all():
        _collect_removal_targets(child, batch)


def _collect_linked_removal_targets(
    component_ids: Iterable[int], batch: RemovalBatch
) -> None:
    linked_frontier: set[int] = set()
    for component_id in component_ids:
        batch.mark_component(component_id)
        linked_frontier.add(component_id)

    while linked_frontier:
        children = Component.objects.filter(
            linked_component_id__in=linked_frontier
        ).values_list("id", flat=True)
        next_frontier = set()
        for component_id in children.iterator(chunk_size=1000):
            if component_id in batch.removed_component_ids:
                continue
            batch.mark_component(component_id)
            next_frontier.add(component_id)
        linked_frontier = next_frontier


def _category_removal(
    category: Category, user: User, batch: RemovalBatch | None = None
) -> None:
    for child in category.category_set.all():
        _category_removal(child, user, batch)
    for component in category.component_set.iterator():
        _component_removal(component, user, batch)
    category.project.change_set.create(
        action=ActionEvents.REMOVE_CATEGORY,
        target=category.slug,
        user=user,
        author=user,
    )
    category.delete()


@app.task(trail=False)
@transaction.atomic
def category_removal(pk: int, uid: int) -> None:
    user = User.objects.get(pk=uid)
    try:
        category = Category.objects.get(pk=pk)
    except Category.DoesNotExist:
        return
    batch = RemovalBatch()
    _collect_removal_targets(category, batch)
    with removal_batch_context(batch):
        _category_removal(category, user, batch)
    transaction.on_commit(batch.flush)


def cleanup_project_tokens(project: Project, user: User | None) -> None:
    """Remove project-scoped tokens before project groups are deleted."""
    other_project_groups = User.groups.through.objects.filter(
        user_id=OuterRef("pk"),
        group__defining_project__isnull=False,
    ).exclude(group__defining_project=project)
    project_tokens = (
        User.objects.filter(
            groups__defining_project=project,
            is_bot=True,
            username__startswith="bot-",
            email__endswith="@bots.noreply.weblate.org",
        )
        .exclude(username__contains=":")
        .exclude(full_name="Deleted User")
        .annotate(has_other_project_groups=Exists(other_project_groups))
        .filter(has_other_project_groups=False)
        .distinct()
        .order_by("pk")
    )
    username = user.username if user is not None else None
    for token_user in project_tokens.iterator():
        remove_user(
            token_user,
            None,
            activity="token-removed",
            project=project.name,
            username=username,
        )


@app.task(
    trail=False,
    autoretry_for=(IntegrityError,),
    retry_backoff=600,
    retry_backoff_max=3600,
)
def actual_project_removal(pk: int, uid: int | None) -> None:
    """
    Remove project.

    This is separated from project_removal to allow retry on integrity errors.
    """
    with transaction.atomic():
        user = get_anonymous() if uid is None else User.objects.get(pk=uid)
        try:
            project = Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            return
        Change.objects.create(
            action=ActionEvents.REMOVE_PROJECT,
            target=project.slug,
            user=user,
            author=user,
        )
        cleanup_project_tokens(project, user)
        batch = RemovalBatch()
        with removal_batch_context(batch):
            project.delete()
        transaction.on_commit(batch.flush)


@app.task(trail=False)
def project_removal(pk: int, uid: int | None) -> None:
    """Backup project and schedule actual removal."""
    create_project_backup(pk)
    actual_project_removal.delay(pk, uid)


def store_auto_translate_activity_log(
    activity_log_id: int | None,
    result: dict[str, Any],
    *,
    status: AddonActivityLogStatus | None = None,
    reason: AddonActivityLogReason | None = None,
    task_count: int | None = None,
) -> dict[str, Any]:
    if activity_log_id is None:
        return result

    # ruff: ignore[import-outside-top-level]
    from weblate.addons.tasks import update_addon_activity_log

    update_addon_activity_log(
        activity_log_id,
        result,
        status=(status if status is not None else AddonActivityLogStatus.SUCCESS),
        reason=reason,
        task_count=task_count,
    )
    return result


def get_auto_translate_target(
    *,
    translation_id: int | None,
    component_id: int | None,
    category_id: int | None,
    project_id: int | None,
    language_id: int | None,
    workspace_id: str | None = None,
) -> tuple[
    Translation | Component | Category | Project | ProjectLanguage | Workspace,
    dict[str, int | str],
]:
    if translation_id is not None:
        translation = Translation.objects.get(pk=translation_id)
        return translation, {"translation": translation.id}
    if component_id is not None:
        component = Component.objects.get(pk=component_id)
        return component, {"component": component.id}
    if category_id is not None:
        category = Category.objects.get(pk=category_id)
        return category, {"category": category.id}
    if project_id is not None:
        project = Project.objects.get(pk=project_id)
        if language_id is None:
            return project, {"project": project.id}
        project_language = ProjectLanguage(
            project=project,
            language=Language.objects.get(pk=language_id),
        )
        return project_language, {
            "project": project_language.project.id,
            "language": project_language.language.id,
        }
    if workspace_id is not None:
        # ruff: ignore[import-outside-top-level]
        from weblate.workspaces.models import Workspace

        workspace = Workspace.objects.get(pk=workspace_id)
        return workspace, {"workspace": str(workspace.pk)}
    msg = (
        "One of translation_id, component_id, category_id, project_id, "
        "or workspace_id must be provided"
    )
    raise ValueError(msg)


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
    acks_late=True,
    reject_on_worker_lost=True,
    bind=True,
)
@guard_producer_execution
# ruff: ignore[too-many-arguments]
def auto_translate(
    self,
    *,
    user_id: int | None,
    mode: str,
    q: str,
    auto_source: Literal["mt", "others"],
    source_component_id: int | None,
    engines: list[str],
    threshold: int,
    component_wide: bool = False,
    unit_ids: list[int] | None = None,
    translation_id: int | None = None,
    component_id: int | None = None,
    category_id: int | None = None,
    project_id: int | None = None,
    language_id: int | None = None,
    workspace_id: str | None = None,
    activity_log_id: int | None = None,
    activity_log_task_count: int | None = None,
    enforce_permissions: bool = True,
    overwrite_existing: bool = False,
    producer_run_id: str | None = None,
    judge_pretranslate: bool = True,
    judge_mutating_repairs: bool = True,
    judge_candidate_severities: tuple[str, ...] = ("critical", "major"),
    judge_proposal_only: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {"warnings": []}
    heartbeat_task(current_task.request.id if current_task else None)
    user = User.objects.get(pk=user_id) if user_id else None
    with override(user.profile.language if user else "en"):
        try:
            obj, target_result = get_auto_translate_target(
                translation_id=translation_id,
                component_id=component_id,
                category_id=category_id,
                project_id=project_id,
                language_id=language_id,
                workspace_id=workspace_id,
            )
        except ObjectDoesNotExist:
            result["message"] = gettext(
                "Automatic translation skipped because the target no longer exists."
            )
            # A pre-created re-check run would otherwise stay QUEUED forever
            # and keep suppressing every replacement re-check for that unit.
            if producer_run_id:
                from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
                    ProducerRun,
                )

                ProducerRun.objects.filter(
                    pk=producer_run_id,
                    status__in=[ProducerRun.Status.QUEUED, ProducerRun.Status.RUNNING],
                ).update(
                    status=ProducerRun.Status.FAILED,
                    finished=timezone.now(),
                    failure=result["message"],
                )
            return store_auto_translate_activity_log(
                activity_log_id,
                result,
                status=AddonActivityLogStatus.SKIPPED,
                reason=AddonActivityLogReason.TARGET_MISSING,
                task_count=activity_log_task_count,
            )
        result.update(target_result)
        auto = BatchAutoTranslate(
            obj,
            user=user,
            q=q,
            mode=mode,
            component_wide=component_wide,
            unit_ids=unit_ids,
            enforce_permissions=enforce_permissions,
            overwrite_existing=overwrite_existing,
            producer_run_id=producer_run_id,
            judge_pretranslate=judge_pretranslate,
            judge_mutating_repairs=judge_mutating_repairs,
            judge_candidate_severities=judge_candidate_severities,
            judge_proposal_only=judge_proposal_only,
        )
        try:
            message = auto.perform(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=(
                    [source_component_id] if source_component_id is not None else None
                ),
            )
        except PermissionDenied as error:
            if auto.active_producer_run is not None:
                result["report_url"] = reverse(
                    "judge-run", kwargs={"pk": auto.active_producer_run.id}
                )
            result.update({"message": str(error), "warnings": auto.get_warnings()})
            return store_auto_translate_activity_log(
                activity_log_id,
                result,
                status=AddonActivityLogStatus.ERROR,
                task_count=activity_log_task_count,
            )
        except JudgeError as error:
            if auto.active_producer_run is not None:
                result["report_url"] = reverse(
                    "judge-run", kwargs={"pk": auto.active_producer_run.id}
                )
            result.update(
                {
                    "message": gettext("Automatic translation failed: %s") % error,
                    "warnings": auto.get_warnings(),
                }
            )
            return store_auto_translate_activity_log(
                activity_log_id,
                result,
                status=AddonActivityLogStatus.ERROR,
                task_count=activity_log_task_count,
            )
        result.update({"message": message, "warnings": auto.get_warnings()})
        if auto.active_producer_run is not None:
            result["report_url"] = reverse(
                "judge-run", kwargs={"pk": auto.active_producer_run.id}
            )
        return store_auto_translate_activity_log(
            activity_log_id,
            result,
            task_count=activity_log_task_count,
        )


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
    acks_late=True,
    reject_on_worker_lost=True,
    bind=True,
)
@guard_producer_execution
def auto_translate_component(
    self,
    component_id: int,
    *,
    mode: str,
    q: str,
    auto_source: Literal["mt", "others"],
    engines: list[str],
    threshold: int,
    source_component_id: int | None = None,
    user_id: int | None = None,
    activity_log_id: int | None = None,
    enforce_permissions: bool = True,
    overwrite_existing: bool = False,
) -> dict[str, Any]:
    heartbeat_task(current_task.request.id if current_task else None)
    component_obj = Component.objects.get(pk=component_id)
    user = User.objects.get(pk=user_id) if user_id else None
    auto = BatchAutoTranslate(
        component_obj,
        user=user,
        q=q,
        mode=mode,
        component_wide=True,
        enforce_permissions=enforce_permissions,
        overwrite_existing=overwrite_existing,
    )
    try:
        message = auto.perform(
            auto_source=auto_source,
            engines=engines,
            threshold=threshold,
            source_component_ids=(
                [source_component_id] if source_component_id is not None else None
            ),
        )
    except JudgeError as error:
        message = gettext("Automatic translation failed: %s") % error
        auto.add_warning(message)
        result = {
            "component": component_obj.id,
            "message": message,
            "warnings": auto.get_warnings(),
        }
        if auto.active_producer_run is not None:
            result["report_url"] = reverse(
                "judge-run", kwargs={"pk": auto.active_producer_run.id}
            )
        return store_auto_translate_activity_log(
            activity_log_id,
            result,
            status=AddonActivityLogStatus.ERROR,
        )
    component_obj.run_batched_checks()
    result = {
        "component": component_obj.id,
        "message": message,
        "warnings": auto.get_warnings(),
    }
    if auto.active_producer_run is not None:
        result["report_url"] = reverse(
            "judge-run", kwargs={"pk": auto.active_producer_run.id}
        )
    return store_auto_translate_activity_log(activity_log_id, result)


def _release_fix_check_lock_reporting(lock_key: str, token: str) -> bool:
    """
    Release a mass-fix reservation, recording a lease that already lapsed.

    `release_fix_check_lock` compares the stored token before deleting, so
    a false return means the key belongs to a newer run. Task 4 step 5
    requires that to be recorded and never retried: the run still reports
    the outcome it actually produced.
    """
    released = release_fix_check_lock(lock_key, token)
    if not released:
        LOGGER.warning(
            "mass fix reservation %s was no longer held by %s", lock_key, token
        )
    return released


def _resolve_fix_check_scope(
    *,
    user_id: int | None,
    translation_id: int | None,
    component_id: int | None,
    project_id: int | None,
) -> tuple[User | None, UnitQuerySet, Project | None]:
    """
    Resolve the actor and the scope query for one mass-fix run.

    Raises when the actor or the scope object no longer exists, or when no
    scope id was passed at all; the task turns that into a returned
    `{"status": "failed"}` payload (Task 4 step 2) rather than letting it
    become a Celery exception result.
    """
    user = User.objects.get(pk=user_id) if user_id else None
    if translation_id is not None:
        translation = Translation.objects.get(pk=translation_id)
        return user, translation.unit_set.all(), translation.component.project
    if component_id is not None:
        component_obj = Component.objects.get(pk=component_id)
        return (
            user,
            Unit.objects.filter(translation__component=component_obj),
            component_obj.project,
        )
    if project_id is not None:
        project = Project.objects.get(pk=project_id)
        return (
            user,
            Unit.objects.filter(translation__component__project=project),
            project,
        )
    msg = "One of translation_id, component_id, or project_id must be provided"
    raise ValueError(msg)


@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
    acks_late=True,
    reject_on_worker_lost=True,
)
def fix_failing_checks(
    *,
    user_id: int | None,
    check_id: str,
    lock_key: str,
    translation_id: int | None = None,
    component_id: int | None = None,
    project_id: int | None = None,
    unit_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Mass-fix a failing check over one scope (Task 4 step 1).

    Metadata is scope-shaped per decision 10: `translation_id` for a
    translation scope, `component_id` for a component scope,
    `project_id`/`user_id` alone for a project scope. `lock_key` is the
    concurrency-guard reservation this run's caller already holds
    (`fix_check_lock_key()`), keyed to `current_task.request.id`.

    A terminal failure - the lock lease is lost, the scope or check cannot
    be resolved, or any other exception - is returned as
    `{"status": "failed", ...}`, never raised, so a dead run can never
    surface as a Celery exception result the poller cannot render (Task 4
    step 2). `WeblateLockTimeoutError` while retries remain is refreshed
    and re-raised, letting `autoretry_for` retry it - unless the refresh
    reports a lost lease, which is terminal and is never retried.
    """
    token: str = (current_task.request.id if current_task else None) or ""
    heartbeat_task(current_task.request.id if current_task else None)
    project = None
    zero_counts: dict[str, Any] = {
        "fixed": 0,
        "denied": 0,
        "manual": 0,
        "stale_or_no_change": 0,
        "verdicts_no_longer_current": 0,
    }

    def failed(message: str, counts: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"status": "failed", "message": message, **(counts or zero_counts)}

    try:
        user, unit_set, project = _resolve_fix_check_scope(
            user_id=user_id,
            translation_id=translation_id,
            component_id=component_id,
            project_id=project_id,
        )
    except Exception as error:
        report_error("Mass fix could not resolve its scope")
        _release_fix_check_lock_reporting(lock_key, token)
        return failed(gettext("Mass fix failed: %s") % error)

    policy = resolve_fix_policy(check_id)
    if policy is None:
        _release_fix_check_lock_reporting(lock_key, token)
        return failed(gettext("This check is no longer eligible for a mass fix."))

    def progress_callback(done: int, total: int) -> bool:
        touch_task_liveness(current_task.request.id if current_task else None)
        if current_task and current_task.request.id:
            current_task.update_state(
                state="PROGRESS",
                meta={
                    "progress": 100 * done // total if total else 100,
                    "done": done,
                    "total": total,
                },
            )
        return refresh_fix_check_lock(lock_key, token)

    try:
        result = policy.perform(
            user,
            unit_set,
            project,
            unit_ids=unit_ids,
            progress_callback=progress_callback,
        )
    except WeblateLockTimeoutError:
        if commit_lock_retries_exhausted():
            _release_fix_check_lock_reporting(lock_key, token)
            report_error(
                "Mass fix could not acquire the component lock in time",
                project=project,
            )
            return failed(
                gettext("Mass fix could not complete: the component stayed locked.")
            )
        # Retries remain: keep the reservation alive across the backoff -
        # which can outlast the lease - and let `autoretry_for` requeue
        # this run. A refusal means another run owns the reservation now,
        # so this run is terminal: it neither retries nor releases a key
        # that is no longer its own (Task 4 step 5).
        if not refresh_fix_check_lock(lock_key, token):
            return failed(gettext("Another run took over this fix."))
        raise
    except Exception as error:
        report_error("Mass fix failed", project=project)
        _release_fix_check_lock_reporting(lock_key, token)
        return failed(gettext("Mass fix failed: %s") % error)

    counts = {
        "fixed": result.fixed,
        "denied": result.denied,
        "manual": result.manual,
        "stale_or_no_change": result.stale_or_no_change,
        "verdicts_no_longer_current": result.verdicts_no_longer_current,
    }

    if result.aborted:
        # The lock refresh failed mid-run: another run owns the
        # reservation now, so this run must not release it.
        return failed(gettext("Another run took over this fix."), counts)

    _release_fix_check_lock_reporting(lock_key, token)
    return {
        "status": "completed",
        "message": ngettext(
            "Mass fix completed, %d string was fixed.",
            "Mass fix completed, %d strings were fixed.",
            result.fixed,
        )
        % result.fixed,
        **counts,
    }


@app.task(trail=False)
def create_component(copy_from=None, copy_addons=False, in_task=False, **kwargs):
    explicit_fields = set(kwargs)
    kwargs["project"] = Project.objects.get(pk=kwargs["project"])
    kwargs["source_language"] = Language.objects.get(pk=kwargs["source_language"])
    if "secondary_language" in kwargs and kwargs["secondary_language"] is not None:
        kwargs["secondary_language"] = Language.objects.get(
            pk=kwargs["secondary_language"]
        )
    apply_create_inheritance_defaults(kwargs, explicit_fields)
    component = Component(**kwargs)
    # Perform validation to avoid creating duplicate components via background
    # tasks in discovery
    component.full_clean()
    component.save(force_insert=True)
    component.change_set.create(action=ActionEvents.CREATE_COMPONENT)
    if copy_from:
        source_component = Component.objects.filter(pk=copy_from).first()
        # Copy non-automatic component lists
        for clist in ComponentList.objects.filter(
            components__id=copy_from, autocomponentlist__isnull=True
        ):
            clist.components.add(component)
        # Copy add-ons
        if copy_addons and source_component is not None:
            copy_component_addons(
                component,
                source_component,
                same_project_only=False,
            )
    if in_task:
        return {"component": component.id}
    return component


@app.task(trail=False)
@transaction.atomic
def update_checks(pk: int, update_token: str, update_state: bool = False) -> None:
    try:
        component = Component.objects.select_related("source_language").get(pk=pk)
    except Component.DoesNotExist:
        return

    # Skip when further updates are scheduled
    latest_token = cache.get(component.update_checks_key)
    if latest_token and update_token != latest_token:
        return

    component.start_batched_checks()
    source_translation = component.source_translation
    # Source translation as last
    translations = (
        *component.translation_set.exclude(pk=source_translation.pk).select_related(
            "language", "plural"
        ),
        source_translation,
    )
    for translation in translations:
        units = translation.unit_set.prefetch_all_checks()
        if update_state:
            units = units.select_for_update()
        for unit in units:
            # Reuse object to avoid fetching from the database
            unit.source_unit.translation = source_translation
            # Mark this as a batch update to avoid stats update on each unit
            unit.is_batch_update = True
            if update_state:
                unit.update_state()
            unit.run_checks()
    component.run_batched_checks()
    component.invalidate_cache()


@app.task(trail=False)
def daily_update_checks() -> None:
    if settings.BACKGROUND_TASKS == "never":
        return
    today = timezone.now()
    components = Component.objects.annotate(hourmod=F("id") % 24).filter(
        hourmod=today.hour
    )
    if settings.BACKGROUND_TASKS == "monthly":
        components = components.annotate(idmod=F("id") % 30).filter(idmod=today.day)
    elif settings.BACKGROUND_TASKS == "weekly":
        components = components.annotate(idmod=F("id") % 7).filter(
            idmod=today.weekday()
        )
    for component in components.iterator():
        component.schedule_update_checks()


@app.task(trail=False)
def cleanup_project_backups() -> None:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.backups import PROJECTBACKUP_PREFIX

    # This intentionally does not use Project objects to remove stale backups
    # for removed projects as well.
    rootdir = data_dir(PROJECTBACKUP_PREFIX)
    backup_cutoff = timezone.now() - timedelta(days=settings.PROJECT_BACKUP_KEEP_DAYS)
    for projectdir in glob(os.path.join(rootdir, "*")):
        if not os.path.isdir(projectdir):
            continue
        if projectdir.endswith("import"):
            # Keep imports for shorter time, but more of them
            cutoff = timezone.now() - timedelta(days=1)
            max_count = 30
        else:
            cutoff = backup_cutoff
            max_count = settings.PROJECT_BACKUP_KEEP_COUNT
        backups = sorted(
            (
                (
                    path,
                    make_aware(
                        # ruff: ignore[call-datetime-fromtimestamp]
                        datetime.fromtimestamp(int(path.split(".")[0]))
                    ),
                )
                for path in os.listdir(projectdir)
                if path.endswith((".zip", ".zip.part"))
            ),
            key=itemgetter(1),
            reverse=True,
        )
        while len(backups) > max_count:
            remove = backups.pop()
            os.unlink(os.path.join(projectdir, remove[0]))

        for backup in backups:
            if backup[1] < cutoff:
                os.unlink(os.path.join(projectdir, backup[0]))


@app.task(trail=False)
def create_project_backup(pk: int, uid: int | None = None) -> None:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.backups import ProjectBackup

    project = Project.objects.get(pk=pk)
    user = User.objects.get(pk=uid) if uid else None
    backup = ProjectBackup()
    backup.backup_project(project, user)


def report_task_progress(progress: int) -> None:
    if current_task and current_task.request.id:
        current_task.update_state(state="PROGRESS", meta={"progress": progress})


@app.task(trail=False)
def generate_report(
    *,
    kind: str,
    parameters: dict[str, Any],
    user_id: int,
    scope_type: str = "",
    scope_id: str = "",
    target: str = "api",
) -> dict[str, str]:
    # Importing here avoids loading report views in every Celery process at startup.
    # ruff: ignore[import-outside-top-level]
    from django.urls import reverse

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.views.reports import collect_report_data, load_report_scope

    user = User.objects.get(pk=user_id)
    scope = load_report_scope(scope_type, scope_id)
    report_task_progress(10)
    with override(user.profile.language or "en"):
        data = collect_report_data(kind, parameters, user, scope)
        message = gettext("Report generated.")
    report_task_progress(90)
    scope_values = {scope_type: scope} if scope_type else {}
    report = Report.objects.create(
        creator=user,
        kind=kind,
        parameters=parameters,
        data=data,
        **scope_values,
    )
    if target == "web":
        url = reverse("report", kwargs={"pk": report.pk})
    else:
        url = reverse("api:report-detail", kwargs={"pk": report.pk})
    return {"message": message, "url": url}


@app.task(trail=False)
def cleanup_reports() -> None:
    cutoff = timezone.now() - timedelta(days=settings.REPORT_EXPIRY)
    Report.objects.filter(created__lt=cutoff).delete()


def _judge_observability_retention_days(setting_name: str) -> int:
    """Read an optional retention setting without making older settings fail."""
    value = getattr(settings, setting_name, 90)
    return value if isinstance(value, int) and value >= 0 else 90


@app.task(trail=False)
def cleanup_judge_observability() -> None:
    """
    Remove bounded-retention Judge transport records.

    Verdicts and run reports are intentionally retained. Foreign keys from
    verdict and usage rows are nullable, so attempt cleanup preserves their
    audit value while removing high-volume request diagnostics.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import JudgeDeferral, JudgeRequestAttempt, LLMUsageLog

    now = timezone.now()
    JudgeRequestAttempt.objects.filter(
        created_at__lt=now
        - timedelta(
            days=_judge_observability_retention_days(
                "JUDGE_REQUEST_ATTEMPT_RETENTION_DAYS"
            )
        )
    ).delete()
    LLMUsageLog.objects.filter(
        operation=LLMUsageLog.Operation.JUDGE,
        created_at__lt=now
        - timedelta(
            days=_judge_observability_retention_days("LLM_USAGE_LOG_RETENTION_DAYS")
        ),
    ).delete()
    # Closed deferrals are history; queued and slow rows are live work and are
    # never deleted automatically, no matter their age.
    JudgeDeferral.objects.filter(
        state=JudgeDeferral.State.CLOSED,
        closed_at__lt=now
        - timedelta(
            days=_judge_observability_retention_days(
                "JUDGE_DEFERRAL_CLOSED_RETENTION_DAYS"
            )
        ),
    ).delete()


@app.task(trail=False)
def drain_judge_deferrals() -> int:
    """Run durable judge retries only when the operator enabled the queue."""
    if not settings.JUDGE_DEFERRAL_ENABLED:
        return 0
    # Importing lazily avoids pulling judge orchestration into every worker
    # process while this module initializes its task registry.
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.judge_loop import drain_judge_deferrals as drain

    return drain()


@app.task(trail=False)
def generate_judge_candidate(
    *, unit_id: int, verdict_id: int, user_id: int | None, replace: bool
) -> str:
    """
    Generate one repair candidate for a held unit's current verdict.

    Runs outside the page request: a provider call takes seconds and must
    never happen during GET (invariant 4). The result is stored as a
    candidate suggestion, never as an applied target.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.judge_loop import generate_candidate_for_verdict

    user = User.objects.get(pk=user_id) if user_id else None
    return generate_candidate_for_verdict(
        unit_id=unit_id, verdict_id=verdict_id, user=user, replace=replace
    )


# How many persistent broker failures one dispatch intent may record before
# the generation enters the existing retryable FAILED path. A user retry then
# reserves a fresh UUID and intent; payload, cursor and counters are never
# touched by dispatch bookkeeping.
LOC_KIT_DISPATCH_MAX_ATTEMPTS = 5

# How many persistent broker failures a queued producer run's dispatch
# intent may record before it is failed outright, mirroring the loc-kit
# ledger's own bound. A transient broker hiccup alone must not fail a run
# the periodic drain (`drain_producer_run_dispatches`) would have retried.
PRODUCER_RUN_DISPATCH_MAX_ATTEMPTS = 5


def _producer_run_dispatch_kwargs(run) -> dict[str, Any] | None:
    """
    Build the ``auto_translate`` kwargs for a run's dispatch phase.

    Returns ``None`` when the run's recorded scope/mode does not match the
    phase it claims to own - never publish a generation the row does not
    actually describe.
    """
    common = {
        "user_id": run.actor_id,
        "mode": "judge",
        "auto_source": "mt",
        "source_component_id": None,
        "engines": [],
        "threshold": MACHINERY_DEFAULT_THRESHOLD,
        "producer_run_id": str(run.pk),
        "judge_pretranslate": False,
        "judge_mutating_repairs": False,
    }
    scope_type = type(run).ScopeType
    if run.dispatch_phase == "judge-recheck":
        if (
            run.scope_type != scope_type.TRANSLATION
            or run.requested_mode != "recheck"
            or not run.requested_query.startswith("id:")
        ):
            return None
        return {
            **common,
            "q": run.requested_query,
            "translation_id": int(run.scope_id),
            "unit_ids": [int(run.requested_query.removeprefix("id:"))],
            "judge_candidate_severities": ("critical",),
        }
    if run.dispatch_phase == "judge-project":
        if run.scope_type != scope_type.PROJECT or run.requested_mode != "judge":
            return None
        return {
            **common,
            "q": run.requested_query,
            "project_id": int(run.scope_id),
            "unit_ids": run.scope_snapshot or None,
            "judge_proposal_only": True,
            "judge_candidate_severities": DEFAULT_CANDIDATE_SEVERITIES,
        }
    return None


def publish_producer_run_dispatch(*, run_id, skip_locked: bool = False) -> bool:
    """
    Publish a queued producer run's reserved task UUID.

    The durable intent is committed with the run. Re-publishing after a crash
    uses the same UUID; the worker's run claim fences duplicate deliveries.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import ProducerRun

    with transaction.atomic():
        queryset = ProducerRun.objects.select_for_update(
            of=("self",), skip_locked=skip_locked
        )
        run = queryset.filter(pk=run_id).first()
        if run is None or run.dispatch_task_id is None:
            return False
        if run.dispatch_published_at is not None:
            return True
        if run.status != ProducerRun.Status.QUEUED or run.actor_id is None:
            return False
        task_id = run.dispatch_task_id
        kwargs = _producer_run_dispatch_kwargs(run)
        if kwargs is None:
            return False

    register_task_liveness(str(task_id))
    try:
        auto_translate.apply_async(
            kwargs=kwargs,
            task_id=str(task_id),
            priority=INTERACTIVE_TASK_PRIORITY,
        )
    except Exception:
        delete_task_liveness(str(task_id))
        LOGGER.exception("Failed to dispatch producer run %s", run_id)
        with transaction.atomic():
            run = ProducerRun.objects.select_for_update().get(pk=run_id)
            if run.dispatch_task_id == task_id and run.dispatch_published_at is None:
                run.dispatch_attempts += 1
                run.dispatch_error = "The background task could not be queued."
                fields = ["dispatch_attempts", "dispatch_error"]
                if run.dispatch_attempts >= PRODUCER_RUN_DISPATCH_MAX_ATTEMPTS:
                    run.status = ProducerRun.Status.FAILED
                    run.finished = timezone.now()
                    run.failure = gettext("The background task could not be queued.")
                    fields.extend(["status", "finished", "failure"])
                run.save(update_fields=fields)
        return False

    ProducerRun.objects.filter(
        pk=run_id, dispatch_task_id=task_id, dispatch_published_at__isnull=True
    ).update(
        task_id=str(task_id),
        dispatch_published_at=timezone.now(),
        dispatch_error="",
    )
    return True


def _loc_kit_dispatch_task(phase: str):
    """Return the task class for a dispatch phase."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    if phase == LocKitImportDraft.DispatchPhase.PREPARE:
        return prepare_loc_kit_string_update
    if phase == LocKitImportDraft.DispatchPhase.APPLY:
        return apply_loc_kit_string_update_draft
    return None


def _publish_loc_kit_dispatch(*, draft_id: int, skip_locked: bool = False) -> bool:
    """
    Publish the draft's current unpublished dispatch intent exactly once.

    The intent (``dispatch_task_id`` + ``dispatch_phase``) was reserved in
    the same DB transaction that assigned ``prepare_task_id`` or
    ``apply_task_id``. This dispatcher claims it under the draft's row lock,
    verifies it still matches the current phase UUID and state, publishes
    the broker message with the reserved UUID and interactive priority, and
    only then records ``dispatch_published_at``. A crash between the broker
    publish and that record leaves the intent unpublished; the periodic
    drain re-claims it and republishes the *same* UUID - a duplicate
    delivery that is safe only because every row portion commits under its
    fencing token (``weblate.trans.loc_kit.apply_loc_kit_portion``). No
    caller may publish a prepare/apply message past this dispatcher.

    ``skip_locked`` is used by the periodic drain: another claim (the
    on-commit fast path or a concurrent drain sweep) is already handling
    the intent, so the sweep must not block on its row.

    Returns ``True`` when the reserved task is (or already was) published,
    ``False`` when the intent was superseded or invalid and nothing was
    queued.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    task_id = None
    task = None
    with transaction.atomic():
        queryset = LocKitImportDraft.objects.select_for_update(
            of=("self",), skip_locked=skip_locked
        )
        if skip_locked:
            # A genuinely locked row yields no result under SKIP LOCKED;
            # the lock owner is already handling the intent.
            draft = queryset.filter(pk=draft_id).first()
            if draft is None:
                return False
        else:
            draft = queryset.get(pk=draft_id)
        if draft.dispatch_task_id is None:
            return False
        if draft.dispatch_published_at is not None:
            return True
        phase = draft.dispatch_phase
        task = _loc_kit_dispatch_task(phase)
        if task is None:
            draft.dispatch_task_id = None
            draft.dispatch_phase = ""
            draft.dispatch_published_at = None
            draft.dispatch_error = ""
            draft.save(
                update_fields=[
                    "dispatch_task_id",
                    "dispatch_phase",
                    "dispatch_published_at",
                    "dispatch_error",
                ]
            )
            return False
        phase_field = (
            "prepare_task_id"
            if phase == LocKitImportDraft.DispatchPhase.PREPARE
            else "apply_task_id"
        )
        expected_state = (
            LocKitImportDraft.State.PREPARING
            if phase == LocKitImportDraft.DispatchPhase.PREPARE
            else LocKitImportDraft.State.APPLYING
        )
        task_id = draft.dispatch_task_id
        if getattr(draft, phase_field) != task_id or draft.state != expected_state:
            # A retry or state change superseded this intent; never publish a
            # generation that no longer owns the phase UUID.
            return False
    # Publish outside the row lock: a slow broker must not hold the draft row.
    try:
        task.apply_async(
            kwargs={"draft_id": draft_id},
            task_id=str(task_id),
            priority=INTERACTIVE_TASK_PRIORITY,
        )
    except Exception as error:
        _record_loc_kit_dispatch_failure(
            draft_id=draft_id, task_id=task_id, error=error
        )
        return False
    _record_loc_kit_dispatch_published(draft_id=draft_id, task_id=task_id)
    return True


def _record_loc_kit_dispatch_published(*, draft_id: int, task_id) -> None:
    """Mark the intent published unless a newer generation superseded it."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    with transaction.atomic():
        draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
            pk=draft_id
        )
        if draft.dispatch_task_id != task_id or draft.dispatch_published_at is not None:
            return
        draft.dispatch_published_at = timezone.now()
        draft.dispatch_error = ""
        draft.save(update_fields=["dispatch_published_at", "dispatch_error"])


def _record_loc_kit_dispatch_failure(
    *, draft_id: int, task_id, error: Exception
) -> None:
    """
    Record a persistent broker failure on the current dispatch intent.

    Bounded by ``LOC_KIT_DISPATCH_MAX_ATTEMPTS``: exhaustion flips the draft
    into the existing retryable FAILED path (``retry_phase`` set, cursor,
    payload and counters untouched). Only a fixed, user-safe message is ever
    stored on the draft - a broker exception may embed endpoint or
    credential text, so the real error goes to the error report alone - and
    only while the intent still owns the phase UUID, so a superseding retry
    cannot be failed by an old dispatch.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    with transaction.atomic():
        draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
            pk=draft_id
        )
        if draft.dispatch_task_id != task_id or draft.dispatch_published_at is not None:
            return
        draft.dispatch_attempts += 1
        draft.dispatch_error = "The background task could not be queued."
        draft.last_activity_at = timezone.now()
        draft.expires_at = draft.last_activity_at + timedelta(hours=1)
        fields = [
            "dispatch_attempts",
            "dispatch_error",
            "last_activity_at",
            "expires_at",
        ]
        if draft.dispatch_attempts >= LOC_KIT_DISPATCH_MAX_ATTEMPTS:
            draft.state = LocKitImportDraft.State.FAILED
            draft.error_code = "dispatch-failed"
            draft.error_details = draft.dispatch_error
            draft.retry_phase = draft.dispatch_phase
            fields.extend(["state", "error_code", "error_details", "retry_phase"])
        draft.save(update_fields=fields)
        project = draft.project
    report_error(
        "loc-kit task dispatch failed",
        project=project,
        exception=error,
        skip_error_reporting=draft.dispatch_attempts < LOC_KIT_DISPATCH_MAX_ATTEMPTS,
    )


def _chain_loc_kit_apply_continuation(*, draft_id: int, task_id) -> bool:
    """Fence the current delivery and reserve exactly one fresh continuation."""
    # ruff: ignore[import-outside-top-level]
    from uuid import uuid4

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    continuation_id = uuid4()
    with transaction.atomic():
        draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
            pk=draft_id
        )
        component = draft.target_component
        if (
            draft.state != LocKitImportDraft.State.APPLYING
            or draft.apply_task_id != task_id
            or component is None
        ):
            return False
        draft.apply_task_id = continuation_id
        draft.last_activity_at = timezone.now()
        draft.expires_at = draft.last_activity_at + timedelta(hours=1)
        draft.dispatch_task_id = continuation_id
        draft.dispatch_phase = LocKitImportDraft.DispatchPhase.APPLY
        draft.dispatch_requested_at = draft.last_activity_at
        draft.dispatch_published_at = None
        draft.dispatch_attempts = 0
        draft.dispatch_error = ""
        draft.save(
            update_fields=[
                "apply_task_id",
                "last_activity_at",
                "expires_at",
                "dispatch_task_id",
                "dispatch_phase",
                "dispatch_requested_at",
                "dispatch_published_at",
                "dispatch_attempts",
                "dispatch_error",
            ]
        )
        transaction.on_commit(lambda: _publish_loc_kit_dispatch(draft_id=draft_id))
    return True


def mark_loc_kit_draft_failed(
    draft_id: int,
    *,
    task_id_field: str,
    expected_state: str,
    task_id,
    error_code: str,
    message: str,
    retry_phase: str,
) -> None:
    """Flip a draft to FAILED only if it still belongs to this task attempt."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    with transaction.atomic():
        draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
            pk=draft_id
        )
        if draft.state != expected_state or getattr(draft, task_id_field) != task_id:
            return
        draft.state = LocKitImportDraft.State.FAILED
        draft.error_code = error_code
        draft.error_details = message[:1000]
        draft.retry_phase = retry_phase
        draft.last_activity_at = timezone.now()
        # FAILED keeps a full hour for view/retry, same as COMPLETED.
        draft.expires_at = draft.last_activity_at + timedelta(hours=1)
        draft.save(
            update_fields=[
                "state",
                "error_code",
                "error_details",
                "retry_phase",
                "last_activity_at",
                "expires_at",
            ]
        )


def _flush_loc_kit_pending_changes(
    *,
    draft_id: int,
    task_id,
    component: Component,
    owner: User,
    retry_phase: str,
) -> bool:
    """
    Commit only this draft's owned pending changes, under repository lock.

    Enters ``Component.locked_for_update()`` before the draft row lock -
    the same ``repository -> component -> draft`` order the atomic portion
    coordinator uses - so finalization serializes against every other
    component writer: a concurrent manual edit, another import, or a
    duplicate same-UUID delivery of this draft. ``commit_pending_subset``
    is never called without holding this lock.
    """
    with component.locked_for_update() as locked_component:
        return _flush_loc_kit_pending_changes_locked(
            draft_id=draft_id,
            task_id=task_id,
            component=locked_component,
            owner=owner,
            retry_phase=retry_phase,
        )


def _flush_loc_kit_pending_changes_locked(
    *,
    draft_id: int,
    task_id,
    component: Component,
    owner: User,
    retry_phase: str,
) -> bool:
    """
    Loop body of the finalizer; the caller holds the repository/component lock.

    The owned set is re-derived from
    ``PendingUnitChange.metadata["loc_kit_draft_id"]`` on every attempt -
    ``pending_change_ids`` is written here purely as audit/recovery data,
    never read back as ownership authority. Looping (not recursing) lets a
    duplicate same-UUID delivery's freshly owned rows, added while an
    earlier attempt's commit ran, be swept in without re-entering the lock.
    Only an empty owned set may report success.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    while True:
        with transaction.atomic():
            draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
                pk=draft_id
            )
            if (
                draft.state != LocKitImportDraft.State.APPLYING
                or draft.apply_task_id != task_id
            ):
                return False
            owned_ids = set(
                PendingUnitChange.objects.filter(
                    metadata__loc_kit_draft_id=str(draft.pk)
                ).values_list("pk", flat=True)
            )
            draft.progress = {**draft.progress, "phase": "finalizing"}
            draft.pending_change_ids = sorted(owned_ids)
            draft.last_activity_at = timezone.now()
            draft.expires_at = draft.last_activity_at + timedelta(hours=1)
            draft.save(
                update_fields=[
                    "progress",
                    "pending_change_ids",
                    "last_activity_at",
                    "expires_at",
                ]
            )
        if not owned_ids:
            return True

        still_authorized = owner.has_perm(
            "source.edit", component.source_translation
        ) or (
            owner.has_perm("upload.perform", component)
            and owner.has_perm("unit.add", component.source_translation)
        )
        if not still_authorized:
            # Authorization is rechecked here because a portion may have
            # been staged minutes or hours before the repository commit
            # happens; permission loss stops finalization before any
            # commit is attempted.
            mark_loc_kit_draft_failed(
                draft_id,
                task_id_field="apply_task_id",
                expected_state=LocKitImportDraft.State.APPLYING,
                task_id=task_id,
                error_code="finalize-forbidden",
                message="The owner no longer holds permission to apply this table.",
                retry_phase=retry_phase,
            )
            return False

        if not component.commit_pending_subset(
            f"loc-kit table update ({component.slug})", owner, owned_ids
        ):
            mark_loc_kit_draft_failed(
                draft_id,
                task_id_field="apply_task_id",
                expected_state=LocKitImportDraft.State.APPLYING,
                task_id=task_id,
                error_code="finalize-failed",
                message="Could not commit the applied strings to the repository.",
                retry_phase=retry_phase,
            )
            return False
        # Loop again under the same lock: re-query the owned set to confirm
        # the empty-set transition, or to sweep in rows a duplicate
        # same-UUID delivery added while this commit ran.


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def prepare_loc_kit_string_update(  # ruff: ignore[too-many-locals]
    self, *, draft_id: int
) -> None:
    """Parse a staged string-update table into a private canonical packet."""
    # ruff: ignore[import-outside-top-level]
    import json

    # ruff: ignore[import-outside-top-level]
    from hashlib import sha256

    # ruff: ignore[import-outside-top-level]
    from uuid import UUID

    # ruff: ignore[import-outside-top-level]
    from django.core.files.base import ContentFile

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.infer import InferenceError, infer_profile

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.model import Severity

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.parser import parse_component

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.profile import ProfileError, parse_profile

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.reader import ReaderError, read_sheets

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.loc_kit import (
        PREVIEW_WARNING_LIMIT,
        classify_kit_explanations,
        count_judge_stale_after_explanations,
        find_changed_sources,
        string_unit_to_json,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    task_id = UUID(self.request.id)
    with transaction.atomic():
        draft = (
            LocKitImportDraft.objects.select_for_update(of=("self",))
            .select_related("target_component__source_language")
            .get(pk=draft_id)
        )
        component = draft.target_component
        if (
            draft.kind != LocKitImportDraft.Kind.STRING
            or draft.state != LocKitImportDraft.State.PREPARING
            or draft.prepare_task_id != task_id
            or component is None
        ):
            return
        filename = draft.uploaded.path

    try:  # ruff: ignore[too-many-statements-in-try-clause]
        sheets = read_sheets(
            Path(filename), max_bytes=settings.TRANSLATION_UPLOAD_MAX_SIZE
        )
        if len(sheets) != 1:
            msg = "The workbook must contain exactly one worksheet."
            raise ValueError(msg)  # ruff: ignore[raise-within-try]
        sheet_name, rows = next(iter(sheets.items()))
        document, _notes = infer_profile(
            {sheet_name: rows},
            kit_stem=component.slug,
            component=component.slug,
            source_lang=component.source_language.code,
            min_fill=0,
        )
        profile = parse_profile(document)
        parsed_component = profile.components[0]
        if parsed_component.kind != "po":
            msg = "The table maps to a glossary layout, not strings."
            raise ValueError(msg)  # ruff: ignore[raise-within-try]
        result = parse_component(parsed_component, rows)
        errors = [
            f"row {diagnostic.row}: {diagnostic.message}"
            for diagnostic in result.diagnostics
            if diagnostic.severity is Severity.ERROR
        ]
        if errors:
            raise ValueError("; ".join(errors[:10]))  # ruff: ignore[raise-within-try]
        # The ``kind != "po"`` guard above proves every parsed record is a
        # StringUnit; ParseResult only knows the wider ParsedUnit protocol.
        string_units = cast("tuple[StringUnit, ...]", result.units)
        rows_json = [string_unit_to_json(unit) for unit in string_units]
        # Only the keys the table actually carries matter: a component-wide
        # baseline would both inflate the packet past its size ceiling and
        # do component-sized work for a one-row upload.
        incoming_keys = {unit.key for unit in string_units}
        baseline = dict(
            component.source_translation.unit_set.filter(
                context__in=incoming_keys
            ).values_list("context", "explanation")
        )
        packet = {"version": 1, "rows": rows_json, "baseline": baseline}
        encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > settings.TRANSLATION_UPLOAD_MAX_SIZE:
            msg = "The prepared table exceeds the configured size limit."
            raise ValueError(msg)  # ruff: ignore[raise-within-try]
    except (InferenceError, ProfileError, ReaderError, ValueError) as error:
        mark_loc_kit_draft_failed(
            draft_id,
            task_id_field="prepare_task_id",
            expected_state=LocKitImportDraft.State.PREPARING,
            task_id=task_id,
            error_code="prepare-failed",
            message=str(error),
            retry_phase="prepare",
        )
        return
    except Exception as error:
        # Anything else (I/O, JSON, storage) is still an owned-task failure:
        # the durable draft must never sit in PREPARING forever because an
        # unanticipated exception skipped the specific-error branch above.
        report_error("loc-kit string update prepare failed", project=component.project)
        mark_loc_kit_draft_failed(
            draft_id,
            task_id_field="prepare_task_id",
            expected_state=LocKitImportDraft.State.PREPARING,
            task_id=task_id,
            error_code="prepare-failed",
            message=str(error),
            retry_phase="prepare",
        )
        raise

    with transaction.atomic():
        draft = (
            LocKitImportDraft.objects.select_for_update(of=("self",))
            .select_related("target_component")
            .get(pk=draft_id)
        )
        preview_component = draft.target_component
        if (
            draft.state != LocKitImportDraft.State.PREPARING
            or draft.prepare_task_id != task_id
            or preview_component is None
        ):
            return
        existing = set(
            preview_component.source_translation.unit_set.filter(
                context__in={row["key"] for row in rows_json}
            ).values_list("context", flat=True)
        )
        new_count = sum(row["key"] not in existing for row in rows_json)
        explanations = classify_kit_explanations(
            component=preview_component,
            units=string_units,
            overwrite=False,
        )
        changed_sources = find_changed_sources(
            component=preview_component, units=string_units
        )
        draft.prepared_payload.save(
            f"{draft.token}.json", ContentFile(encoded), save=False
        )
        draft.preview_json = json.dumps(
            {
                "total_rows": len(rows_json),
                "new_count": new_count,
                "existing_count": len(rows_json) - new_count,
                "sample_keys": [row["key"] for row in rows_json[:10]],
                "explanations": explanations.__dict__,
                # Only a bounded sample is stored and rendered; the full
                # divergence is reported by ``changed_source_count``.
                "changed_sources": [
                    {
                        "key": row.key,
                        "old_source": row.old_source,
                        "new_source": row.new_source,
                    }
                    for row in changed_sources[:PREVIEW_WARNING_LIMIT]
                ],
                "changed_source_count": len(changed_sources),
                # Worst case, the confirm that overwrites: how many current
                # verdicts this table would outdate.
                "judge_stale_count": count_judge_stale_after_explanations(
                    component=preview_component,
                    units=string_units,
                    overwrite=True,
                ),
            },
            ensure_ascii=False,
        )
        draft.payload_checksum = sha256(encoded).hexdigest()
        draft.payload_size = len(encoded)
        draft.state = LocKitImportDraft.State.PREVIEW_READY
        draft.progress = {"phase": "preview", "processed_rows": len(rows_json)}
        draft.last_activity_at = timezone.now()
        # PREVIEW_READY awaits the user's confirm; give it the same
        # one-hour retention as an active task heartbeat.
        draft.expires_at = draft.last_activity_at + timedelta(hours=1)
        draft.uploaded.delete(save=False)
        draft.save()


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def apply_loc_kit_string_update_draft(  # ruff: ignore[complex-structure]
    self, *, draft_id: int
) -> None:
    """
    Apply a prepared string-update packet in bounded, resumable portions.

    Each portion is one call to ``apply_loc_kit_portion``, which locks
    ``repository -> component -> draft`` in one transaction, re-checks the
    fencing token (``state``, ``apply_task_id``) and the durable cursor
    before any mutation, and commits the adds, Explanations, owned pending
    tagging, cursor, counters and heartbeat together. A redelivery resumes
    from the next unprocessed row; two duplicate deliveries of the same
    UUID commit at most one portion, because the second observes the
    advanced cursor inside the lock and returns ``None`` without mutating
    anything. Finalizing commits exactly this draft's owned pending changes
    with ``commit_pending_subset``, never the component's ambient
    ``commit_pending()`` - a concurrent manual edit or another import's
    pending changes are never swept in.
    """
    # ruff: ignore[import-outside-top-level]
    from uuid import UUID

    # ruff: ignore[import-outside-top-level]
    from celery.exceptions import Retry

    # ruff: ignore[import-outside-top-level]
    from django.core.exceptions import ValidationError

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.loc_kit import (
        LOC_KIT_STRING_UPDATE_PORTION_SIZE,
        apply_loc_kit_portion,
        load_prepared_string_units,
        validate_loc_kit_string_update_size,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.loc_kit import LOC_KIT_STRING_UPDATE_APPLY_TIME_LIMIT

    task_id = UUID(self.request.id)
    delivery_started_at = time.monotonic()
    delivery_budget = max(
        1.0, float(getattr(settings, "CELERY_VISIBILITY_TIMEOUT", 4 * 3600)) / 2
    )
    with transaction.atomic():
        draft = (
            LocKitImportDraft.objects.select_for_update(of=("self",))
            .select_related("target_component", "owner")
            .get(pk=draft_id)
        )
        component = draft.target_component
        if (
            draft.kind != LocKitImportDraft.Kind.STRING
            or draft.state != LocKitImportDraft.State.APPLYING
            or draft.apply_task_id != task_id
            or component is None
        ):
            return
        owner = draft.owner
        overwrite_explanations = bool(
            draft.confirmed_options.get("overwrite_explanations")
        )

    try:
        units, explanation_baseline = load_prepared_string_units(draft)
    except ValidationError as error:
        mark_loc_kit_draft_failed(
            draft_id,
            task_id_field="apply_task_id",
            expected_state=LocKitImportDraft.State.APPLYING,
            task_id=task_id,
            error_code="apply-failed",
            message="; ".join(error.messages),
            retry_phase="apply",
        )
        return

    total_rows = len(units)
    try:
        validate_loc_kit_string_update_size(units)
    except ValidationError as error:
        mark_loc_kit_draft_failed(
            draft_id,
            task_id_field="apply_task_id",
            expected_state=LocKitImportDraft.State.APPLYING,
            task_id=task_id,
            error_code="apply-failed",
            message="; ".join(error.messages),
            retry_phase="apply",
        )
        return

    while True:
        with transaction.atomic():
            draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
                pk=draft_id
            )
            if (
                draft.state != LocKitImportDraft.State.APPLYING
                or draft.apply_task_id != task_id
            ):
                return
            cursor = draft.next_row
        if cursor >= total_rows:
            break
        if (
            draft.apply_started_at is not None
            and timezone.now() - draft.apply_started_at
            >= LOC_KIT_STRING_UPDATE_APPLY_TIME_LIMIT
        ):
            if not _flush_loc_kit_pending_changes(
                draft_id=draft_id,
                task_id=task_id,
                component=component,
                owner=owner,
                retry_phase="finalize",
            ):
                return
            mark_loc_kit_draft_failed(
                draft_id,
                task_id_field="apply_task_id",
                expected_state=LocKitImportDraft.State.APPLYING,
                task_id=task_id,
                error_code="apply-time-limit",
                message="The update reached its 24-hour limit after partial completion.",
                retry_phase="",
            )
            return
        portion = units[cursor : cursor + LOC_KIT_STRING_UPDATE_PORTION_SIZE]
        try:
            result = apply_loc_kit_portion(
                user=owner,
                component=component,
                units=portion,
                overwrite_explanations=overwrite_explanations,
                pending_owner=str(draft_id),
                explanation_baseline=explanation_baseline,
                draft_id=draft_id,
                task_id=task_id,
                expected_cursor=cursor,
                total_rows=total_rows,
            )
        except WeblateLockTimeoutError as error:
            retry_delays = (1, 2, 4, 8, 16, 32, 64, 128)
            retry_count = self.request.retries
            if retry_count >= len(retry_delays):
                mark_loc_kit_draft_failed(
                    draft_id,
                    task_id_field="apply_task_id",
                    expected_state=LocKitImportDraft.State.APPLYING,
                    task_id=task_id,
                    error_code="lock-timeout",
                    message=str(error),
                    retry_phase="apply",
                )
                return
            retry_delay = retry_delays[retry_count]
            with transaction.atomic():
                current = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
                    pk=draft_id
                )
                if (
                    current.state != LocKitImportDraft.State.APPLYING
                    or current.apply_task_id != task_id
                ):
                    return
                current.progress = {
                    **current.progress,
                    "phase": "applying",
                    "processed_rows": cursor,
                    "total_rows": total_rows,
                    "lock_retry_attempt": retry_count + 1,
                    "next_retry_at": (
                        timezone.now() + timedelta(seconds=retry_delay)
                    ).isoformat(),
                }
                current.last_activity_at = timezone.now()
                current.expires_at = current.last_activity_at + timedelta(hours=1)
                current.save(
                    update_fields=["progress", "last_activity_at", "expires_at"]
                )
            touch_task_liveness(str(task_id))
            raise self.retry(
                exc=error,
                countdown=retry_delay,
                max_retries=len(retry_delays),
            ) from error
        except Retry:
            raise
        except (ValidationError, ValueError) as error:
            mark_loc_kit_draft_failed(
                draft_id,
                task_id_field="apply_task_id",
                expected_state=LocKitImportDraft.State.APPLYING,
                task_id=task_id,
                error_code="apply-failed",
                message=str(error),
                retry_phase="apply",
            )
            return
        except Exception as error:
            report_error(
                "loc-kit string update apply failed", project=component.project
            )
            mark_loc_kit_draft_failed(
                draft_id,
                task_id_field="apply_task_id",
                expected_state=LocKitImportDraft.State.APPLYING,
                task_id=task_id,
                error_code="apply-failed",
                message=str(error),
                retry_phase="apply",
            )
            raise

        if result is None:
            # The fence check inside the coordinator's lock found a
            # replaced UUID or an already-advanced cursor: another
            # generation owns this portion. Stop this delivery quietly.
            return
        touch_task_liveness(str(task_id))
        if time.monotonic() - delivery_started_at >= delivery_budget:
            _chain_loc_kit_apply_continuation(draft_id=draft_id, task_id=task_id)
            return

    if not _flush_loc_kit_pending_changes(
        draft_id=draft_id,
        task_id=task_id,
        component=component,
        owner=owner,
        retry_phase="finalize",
    ):
        return

    with transaction.atomic():
        draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
            pk=draft_id
        )
        if (
            draft.state != LocKitImportDraft.State.APPLYING
            or draft.apply_task_id != task_id
        ):
            return
        # The coordinator merges each portion's counters into the durable
        # ``progress`` as it commits, so the completed summary is read from
        # there rather than re-accumulated locally - it must reflect every
        # portion applied across every delivery, not only this one.
        final_progress = draft.progress if isinstance(draft.progress, dict) else {}
        draft.state = LocKitImportDraft.State.COMPLETED
        draft.progress = {
            "phase": "completed",
            "added": int(final_progress.get("added", 0)),
            "existing": int(final_progress.get("existing", 0)),
            "explanations_set": int(final_progress.get("explanations_set", 0)),
            "explanations_unchanged": int(
                final_progress.get("explanations_unchanged", 0)
            ),
            "explanations_would_overwrite": int(
                final_progress.get("explanations_would_overwrite", 0)
            ),
            "explanations_unavailable": int(
                final_progress.get("explanations_unavailable", 0)
            ),
            "explanations_source_changed": int(
                final_progress.get("explanations_source_changed", 0)
            ),
            "explanations_baseline_changed": int(
                final_progress.get("explanations_baseline_changed", 0)
            ),
            "created_languages": sorted(final_progress.get("created_languages", ())),
            "unavailable_languages": sorted(
                final_progress.get("unavailable_languages", ())
            ),
        }
        draft.finished_at = timezone.now()
        draft.last_activity_at = draft.finished_at
        # COMPLETED keeps a full hour for the owner to find and view the
        # result after navigating away.
        draft.expires_at = draft.finished_at + timedelta(hours=1)
        draft.error_code = ""
        draft.error_details = ""
        draft.retry_phase = ""
        draft.pending_change_ids = []
        draft.delete_storage()
        draft.save()


@app.task(trail=False)
def cleanup_loc_kit_drafts() -> None:
    """
    Delete expired loc-kit import drafts and their uploaded files.

    Row-locked and re-validated per draft, not a bulk delete of a
    previously selected set: an active task's heartbeat extends
    ``expires_at`` concurrently with this scan, and deleting that draft's
    files out from under it (leaving the row behind, referencing storage
    that no longer exists) would corrupt a still-running import.
    ``skip_locked=True`` steps over a row an active task (or a concurrent
    cleanup run) currently holds instead of blocking on it. Idempotent:
    running it twice, or a file already gone, is a no-op.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    candidate_ids = list(
        LocKitImportDraft.objects.filter(expires_at__lt=timezone.now()).values_list(
            "pk", flat=True
        )
    )
    for draft_id in candidate_ids:
        with transaction.atomic():
            draft = (
                LocKitImportDraft.objects.select_for_update(skip_locked=True)
                .filter(pk=draft_id, expires_at__lt=timezone.now())
                .first()
            )
            if draft is None:
                continue
            draft.delete_storage()
            draft.delete()


@app.task(trail=False)
def drain_loc_kit_dispatches() -> None:
    """
    Reclaim every unpublished loc-kit dispatch intent.

    ``transaction.on_commit`` normally publishes a reserved intent
    immediately; this drain covers the crash between the reserving DB commit
    and that callback, or a lost broker publication. Each intent is claimed
    under the draft's row lock and verified against the current phase UUID
    before ``apply_async`` runs, so a superseded generation is never
    published. Duplicate same-UUID deliveries are safe: every row portion
    commits under its fencing token exactly once.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    candidate_ids = list(
        LocKitImportDraft.objects.filter(
            dispatch_task_id__isnull=False, dispatch_published_at__isnull=True
        ).values_list("pk", flat=True)
    )
    for draft_id in candidate_ids:
        _publish_loc_kit_dispatch(draft_id=draft_id, skip_locked=True)


@app.task(trail=False)
def drain_producer_run_dispatches() -> None:
    """Re-publish every committed producer-run intent missing its broker mark."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import ProducerRun

    run_ids = ProducerRun.objects.filter(
        status=ProducerRun.Status.QUEUED,
        dispatch_task_id__isnull=False,
        dispatch_published_at__isnull=True,
    ).values_list("pk", flat=True)
    for run_id in run_ids.iterator():
        publish_producer_run_dispatch(run_id=run_id, skip_locked=True)


@app.task(trail=False)
def cleanup_component_spreadsheet_import_drafts() -> None:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import ComponentSpreadsheetImportDraft

    qs = ComponentSpreadsheetImportDraft.objects.filter(expires_at__lt=timezone.now())
    for draft in qs.iterator():
        draft.delete_storage()
    qs.delete()


def report_restore_component_progress(completed: int, total: int) -> None:
    if total:
        report_task_progress(30 + (60 * completed // total))


def restore_project_backup(
    project_name: str,
    project_slug: str,
    user_id: int,
    filename: str,
    billing_id: int | None,
    workspace_id: str | None = None,
) -> tuple[Project, list[str]]:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.backups import ProjectBackup

    report_task_progress(5)
    user = User.objects.get(pk=user_id)
    billing = None
    if billing_id is not None:
        # ruff: ignore[import-outside-top-level]
        from weblate.billing.models import Billing

        billing = Billing.objects.get(pk=billing_id)
    workspace = None
    if workspace_id is not None:
        # ruff: ignore[import-outside-top-level]
        from weblate.workspaces.models import Workspace

        workspace = Workspace.objects.get(pk=workspace_id)
    restore = ProjectBackup(filename)
    report_task_progress(10)
    restore.validate()
    report_task_progress(30)
    project = restore.restore(
        project_name=project_name,
        project_slug=project_slug,
        user=user,
        billing=billing,
        workspace=workspace,
        progress_callback=report_restore_component_progress,
    )
    report_task_progress(95)
    return project, restore.skipped_components.copy()


@app.task(trail=False)
def import_project_backup(
    project_name: str,
    project_slug: str,
    user_id: int,
    filename: str,
    billing_id: int | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    try:
        project, skipped_components = restore_project_backup(
            project_name,
            project_slug,
            user_id,
            filename,
            billing_id,
            workspace_id,
        )
    finally:
        with suppress(OSError):
            os.unlink(filename)

    if skipped_components:
        return {
            "message": gettext(
                "Project backup import completed with skipped components."
            ),
            "warnings": [
                gettext(
                    "Component %(component)s was skipped because its linked repository is unavailable."
                )
                % {"component": component}
                for component in skipped_components
            ],
        }

    return {
        "message": gettext("Project backup import completed."),
        "url": project.get_absolute_url(),
    }


@app.task(trail=False)
def remove_project_backup_download(name: str) -> None:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.backups import get_project_backup_download_storage

    storage = get_project_backup_download_storage()
    if storage.exists(name):
        storage.delete(name)


@app.task(trail=False)
def cleanup_project_backup_download() -> None:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.backups import (
        PROJECTBACKUP_PREFIX,
        get_project_backup_download_storage,
    )

    storage = get_project_backup_download_storage()
    if not storage.exists(PROJECTBACKUP_PREFIX):
        return
    cutoff = timezone.now() - timedelta(hours=2)
    for name in storage.listdir(PROJECTBACKUP_PREFIX)[1]:
        full_name = os.path.join(PROJECTBACKUP_PREFIX, name)
        if storage.get_created_time(full_name) < cutoff:
            storage.delete(full_name)


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs) -> None:
    sender.add_periodic_task(3600, commit_pending.s(), name="commit-pending")
    sender.add_periodic_task(3600, update_remotes.s(), name="update-remotes")
    sender.add_periodic_task(3600, cleanup_repos.s(), name="cleanup-repos")
    sender.add_periodic_task(
        crontab(minute=30), daily_update_checks.s(), name="daily-update-checks"
    )
    sender.add_periodic_task(
        crontab(hour=3, minute=45), repository_alerts.s(), name="repository-alerts"
    )
    sender.add_periodic_task(3600, component_alerts.s(), name="component-alerts")
    sender.add_periodic_task(
        crontab(hour=0, minute=40), cleanup_suggestions.s(), name="suggestions-cleanup"
    )
    sender.add_periodic_task(
        crontab(hour=0, minute=40), cleanup_stale_repos.s(), name="cleanup-stale-repos"
    )
    sender.add_periodic_task(
        crontab(hour=2, minute=30),
        cleanup_project_backups.s(),
        name="cleanup-project-backups",
    )
    sender.add_periodic_task(
        3600,
        cleanup_project_backup_download.s(),
        name="cleanup-project-backup-download",
    )
    sender.add_periodic_task(
        crontab(hour=0, minute=50), cleanup_reports.s(), name="reports-cleanup"
    )
    judge_cleanup_interval = getattr(
        settings, "JUDGE_OBSERVABILITY_CLEANUP_INTERVAL", 86_400
    )
    if isinstance(judge_cleanup_interval, int) and judge_cleanup_interval > 0:
        sender.add_periodic_task(
            judge_cleanup_interval,
            cleanup_judge_observability.s(),
            name="judge-observability-cleanup",
        )
    if settings.JUDGE_DEFERRAL_ENABLED:
        sender.add_periodic_task(
            max(1, settings.JUDGE_DEFERRAL_MIN_INTERVAL),
            drain_judge_deferrals.s(),
            name="judge-deferral-drain",
        )
    sender.add_periodic_task(
        900, cleanup_loc_kit_drafts.s(), name="cleanup-loc-kit-drafts"
    )
    sender.add_periodic_task(
        60, drain_loc_kit_dispatches.s(), name="drain-loc-kit-dispatches"
    )
    sender.add_periodic_task(
        60, drain_producer_run_dispatches.s(), name="drain-producer-run-dispatches"
    )
    sender.add_periodic_task(
        900,
        cleanup_component_spreadsheet_import_drafts.s(),
        name="cleanup-component-spreadsheet-import-drafts",
    )
