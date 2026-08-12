# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

from weblate.auth.models import User, get_anonymous
from weblate.checks.flags import Flags
from weblate.lang.models import Language
from weblate.trans.models import Component, Project, Translation
from weblate.utils.celery import app
from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.stats import prefetch_stats

if TYPE_CHECKING:
    from weblate.trans.models.translation import TranslationQuerySet


@app.task(
    trail=False,
    autoretry_for=(Component.DoesNotExist, WeblateLockTimeoutError),
    retry_backoff=60,
)
def sync_glossary_languages(pk: int, component: Component | None = None) -> None:
    """Add missing glossary languages."""
    if component is None:
        component = Component.objects.get(pk=pk)

    language_ids = set(component.translation_set.values_list("language_id", flat=True))
    missing = (
        Language.objects.filter(translation__component__project=component.project)
        .exclude(pk__in=language_ids)
        .distinct()
    )
    if not missing:
        return
    with component.repository.lock:
        component.log_info("Adding glossary languages: %s", missing)
        # Make sure changes are committed, create_translations might discard pending ones
        component.commit_pending("glossary languages", None)
        needs_create = False
        for language in missing:
            added = component.add_new_language(
                language, None, create_translations=False
            )
            if added is not None:
                needs_create = True

        if needs_create:
            # force_scan needed, see add_new_language
            component.create_translations_immediate(force_scan=True)


def get_stale_glossary_translations(
    project: Project, component: Component | None = None
) -> TranslationQuerySet:
    """
    Return glossary translations for languages unused by regular components.

    The source translation is excluded because it is expected to exist even if
    there are no matching regular component translations.
    """
    languages_in_non_glossary_components: set[int] = set(
        Translation.objects.filter(
            component__project=project, component__is_glossary=False
        ).values_list("language_id", flat=True)
    )
    if not languages_in_non_glossary_components:
        return Translation.objects.none()

    stale_glossaries = Translation.objects.filter(
        component__project=project, component__is_glossary=True
    )
    if component is not None:
        stale_glossaries = stale_glossaries.filter(component=component)

    return (
        stale_glossaries.prefetch()
        .exclude(language__id__in=languages_in_non_glossary_components)
        .exclude_source()
    )


@app.task(trail=False, autoretry_for=(Project.DoesNotExist, WeblateLockTimeoutError))
def cleanup_stale_glossaries(project: int | Project) -> None:
    """
    Delete stale glossaries.

    A glossary translation is considered stale when it meets the following conditions:
    - glossary.language is not used in any other non-glossary components
    - glossary.language is different from glossary.component.source_language
    - It has no translation

    Stale glossary is not removed if:
    - the component only has one glossary component
    - if is managed outside weblate (i.e repo != 'local:')
    """
    if isinstance(project, int):
        project = Project.objects.get(pk=project)

    glossary_translations = prefetch_stats(get_stale_glossary_translations(project))

    component_to_check = []

    for glossary in glossary_translations:
        if glossary.can_be_deleted():
            glossary.remove(get_anonymous())
            if glossary.component not in component_to_check:
                component_to_check.append(glossary.component)

    for component in component_to_check:
        transaction.on_commit(component.schedule_update_checks)


@app.task(
    trail=False,
    autoretry_for=(Component.DoesNotExist, WeblateLockTimeoutError),
    retry_backoff=60,
)
def sync_terminology(pk: int, component: Component | None = None):
    """Sync terminology and add missing glossary languages."""
    if component is None:
        component = Component.objects.get(pk=pk)

    sync_glossary_languages(pk, component)

    component.source_translation.sync_terminology()

    return {"component": pk}


@app.task(
    trail=False,
    autoretry_for=(Component.DoesNotExist, WeblateLockTimeoutError),
    retry_backoff=60,
    bind=True,
)
def flag_glossary_terminology(self, pk: int) -> None:
    """
    Mark every source string of an imported glossary as terminology.

    Terms written straight into TBX carry no flags, and `sync_terminology`
    copies only flagged strings into the other languages, so without this the
    glossary exists in the source pair alone. The component is created by a
    background task, so the source strings may not exist yet: retry rather
    than flag nothing.
    """
    component = Component.objects.get(pk=pk)
    source = component.source_translation
    if not source.unit_set.exists():
        raise self.retry(countdown=10, max_retries=30)

    author = User.objects.get_or_create_bot(
        scope="glossary", name="sync", verbose="Glossary sync"
    )
    with transaction.atomic():
        for unit in source.unit_set.select_for_update().order_by("id"):
            flags = Flags(unit.extra_flags)
            if "terminology" in flags:
                continue
            flags.merge("terminology")
            unit.update_extra_flags(flags.format(), author)

    component.schedule_sync_terminology()
