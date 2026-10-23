# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

from psycopg import connect, sql
from seed import install_optional_native_stubs, validate_environment


def ensure_database() -> None:
    database_name = os.environ["CI_DB_NAME"]
    connection = connect(
        dbname="postgres",
        host=os.environ.get("CI_DB_HOST", ""),
        port=os.environ.get("CI_DB_PORT", ""),
        user=os.environ.get("CI_DB_USER", ""),
        password=os.environ.get("CI_DB_PASSWORD", ""),
        autocommit=True,
    )
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


def configure_e2e_runtime() -> None:
    """Install deterministic, process-local seams for the isolated browser run."""
    from django.db.models import F  # ruff: ignore[import-outside-top-level]

    from weblate.trans import producer_run  # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit  # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
        ProducerRun,
    )
    from weblate.utils.state import (  # ruff: ignore[import-outside-top-level]
        STATE_TRANSLATED,
    )
    from weblate.vcs.git import (  # ruff: ignore[import-outside-top-level]
        LocalRepository,
    )
    from weblate.vcs.models import (  # ruff: ignore[import-outside-top-level]
        VCS_REGISTRY,
    )

    VCS_REGISTRY.data["local"] = LocalRepository
    original_getenv = LocalRepository._getenv  # ruff: ignore[private-member-access]

    def isolated_git_env(_cls, environment=None, *, cwd=None):
        result = original_getenv(environment, cwd=cwd)
        result.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "Producer Console E2E",
                "GIT_AUTHOR_EMAIL": "producer-e2e@example.invalid",
                "GIT_COMMITTER_NAME": "Producer Console E2E",
                "GIT_COMMITTER_EMAIL": "producer-e2e@example.invalid",
            }
        )
        return result

    LocalRepository._getenv = classmethod(isolated_git_env)  # type: ignore[method-assign]

    def offline_translation(*, run, project, unit_ids, **_kwargs):
        if run.input_snapshot.get("glossary"):
            current = ProducerRun.objects.get(pk=run.pk)
            if current.stages["glossary"]["status"] != "completed":
                msg = "E2E translation started before glossary publication."
                raise RuntimeError(msg)
            if not Unit.objects.filter(
                translation__component__project=project,
                translation__component__is_glossary=True,
                translation__language__code="ru",
                source="Start",
                target="Начать",
            ).exists():
                msg = "E2E translation cannot observe the prepared glossary."
                raise RuntimeError(msg)
        updated = Unit.objects.filter(pk__in=unit_ids, target="").update(
            target=F("source"), state=STATE_TRANSLATED
        )
        return updated, []

    producer_run._translate_localization_language = offline_translation


def main() -> None:
    validate_environment()
    ensure_database()
    target = urlsplit(os.environ["PRODUCER_CONSOLE_E2E_BASE_URL"])
    os.environ["WEBLATE_TEST_URL_PREFIX"] = target.path.rstrip("/")

    install_optional_native_stubs()
    import django  # ruff: ignore[import-outside-top-level]

    django.setup()
    configure_e2e_runtime()
    from django.conf import settings  # ruff: ignore[import-outside-top-level]
    from django.core.management import (  # ruff: ignore[import-outside-top-level]
        call_command,
        execute_from_command_line,
    )

    settings.SITE_DOMAIN = target.netloc
    settings.SITE_URL = os.environ["PRODUCER_CONSOLE_E2E_BASE_URL"].rstrip("/")
    settings.LOGIN_URL = f"{settings.URL_PREFIX}/accounts/login/"
    settings.LOGOUT_URL = f"{settings.URL_PREFIX}/accounts/logout/"
    settings.LOGIN_REDIRECT_URL = f"{settings.URL_PREFIX}/"
    settings.STATIC_URL = f"{settings.URL_PREFIX}/static/"
    settings.ALLOWED_HOSTS = [target.hostname]
    call_command("migrate", interactive=False, verbosity=1)
    url_host = "127.0.0.1"
    port = "18081"

    if target.hostname:
        url_host = target.hostname
    if target.port:
        port = str(target.port)
    host = f"[{url_host}]" if ":" in url_host else url_host
    execute_from_command_line(
        [
            sys.argv[0],
            "runserver",
            "--noreload",
            "--insecure",
            "--skip-checks",
            f"{host}:{port}",
        ]
    )


if __name__ == "__main__":
    main()
