# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit

USERNAMES = ("producer-e2e", "producer-e2e-denied")
DATABASE_NAME = "producer_console_e2e"
ALLOWED_PROJECT_COUNT = 51
ALLOWED_PROJECT_SLUGS = (
    "e2e-allowed-project",
    *(f"e2e-allowed-project-{index}" for index in range(2, ALLOWED_PROJECT_COUNT + 1)),
)
PROJECT_SLUGS = (
    *ALLOWED_PROJECT_SLUGS,
    "e2e-restricted-project",
    "e2e-created-project",
    "e2e-forbidden-project",
    "e2e-expired-session",
    "e2e-missing-csrf",
)
GROUP_NAME = "Producer Console E2E project creation"
ROLE_NAME = "Producer Console E2E project creation"
BILLING_PLAN_SLUG = "producer-console-e2e"
PASSWORD = "producer-e2e-password"  # ruff: ignore[hardcoded-password-string]


def install_optional_native_stubs() -> None:
    """Provide imports which the isolated Producer E2E flow never invokes."""
    if "tesserocr" in sys.modules:
        return
    module = ModuleType("tesserocr")
    module.OEM = SimpleNamespace(LSTM_ONLY=1)
    module.PSM = SimpleNamespace(SPARSE_TEXT_OSD=1)
    module.RIL = SimpleNamespace(TEXTLINE=1)
    module.PyTessBaseAPI = object
    module.iterate_level = lambda *_args, **_kwargs: ()
    sys.modules["tesserocr"] = module


def validate_environment() -> None:
    if os.environ.get("PRODUCER_CONSOLE_E2E") != "1":
        msg = "Set PRODUCER_CONSOLE_E2E=1 to authorize E2E seeding."
        raise RuntimeError(msg)
    base_url = urlsplit(os.environ.get("PRODUCER_CONSOLE_E2E_BASE_URL", ""))
    if base_url.scheme != "http" or base_url.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        msg = "Producer Console E2E only accepts an HTTP loopback URL."
        raise RuntimeError(msg)
    if not base_url.path.endswith("/"):
        msg = "Producer Console E2E base URL must end with a slash."
        raise RuntimeError(msg)
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "weblate.settings_test":
        msg = "Producer Console E2E requires weblate.settings_test."
        raise RuntimeError(msg)
    if os.environ.get("CI_DB_HOST") not in {"127.0.0.1", "localhost", "::1"}:
        msg = "Producer Console E2E only accepts a loopback PostgreSQL host."
        raise RuntimeError(msg)
    if os.environ.get("CI_DB_NAME") != DATABASE_NAME:
        msg = f"Producer Console E2E requires the isolated {DATABASE_NAME} database."
        raise RuntimeError(msg)


def configure_django() -> None:
    install_optional_native_stubs()
    import django  # ruff: ignore[import-outside-top-level]

    django.setup()


def cleanup() -> None:
    from weblate.billing.models import (  # ruff: ignore[import-outside-top-level]
        Billing,
        Plan,
    )
    from weblate.auth.models import (  # ruff: ignore[import-outside-top-level]
        Group,
        Role,
        User,
    )
    from weblate.trans.models import Project  # ruff: ignore[import-outside-top-level]
    from weblate.workspaces.models import (  # ruff: ignore[import-outside-top-level]
        Workspace,
    )

    Project.objects.filter(slug__in=PROJECT_SLUGS).delete()
    User.objects.filter(username__in=USERNAMES).delete()
    Group.objects.filter(name=GROUP_NAME).delete()
    Role.objects.filter(name=ROLE_NAME).delete()
    billings = Billing.objects.filter(plan__slug=BILLING_PLAN_SLUG)
    workspace_ids = list(billings.values_list("workspace_id", flat=True))
    billings.delete()
    Workspace.objects.filter(pk__in=workspace_ids).delete()
    Plan.objects.filter(slug=BILLING_PLAN_SLUG).delete()


def create() -> None:
    from weblate.billing.models import (  # ruff: ignore[import-outside-top-level]
        Billing,
        Plan,
    )
    from weblate.auth.data import (  # ruff: ignore[import-outside-top-level]
        SELECTION_ALL,
    )
    from weblate.auth.models import (  # ruff: ignore[import-outside-top-level]
        Group,
        Permission,
        Role,
        User,
    )
    from weblate.trans.models import Project  # ruff: ignore[import-outside-top-level]

    cleanup()
    plan = Plan.objects.create(
        name="Producer Console E2E",
        slug=BILLING_PLAN_SLUG,
        limit_projects=0,
        display_limit_projects=0,
    )
    billing = Billing.objects.create(plan=plan)
    allowed = [
        Project.objects.create(
            name=(
                "E2E Allowed Project" if index == 1 else f"E2E Allowed Project {index}"
            ),
            slug=slug,
            access_control=Project.ACCESS_PRIVATE,
            machinery_settings={
                "openrouter": {"key": "e2e", "routing": {"*": "e2e/offline"}}
            },
        )
        for index, slug in enumerate(ALLOWED_PROJECT_SLUGS, start=1)
    ]
    for project in allowed:
        billing.add_project(project)
    Project.objects.create(
        name="E2E Restricted Project",
        slug="e2e-restricted-project",
        access_control=Project.ACCESS_PRIVATE,
    )
    creator = User.objects.create_user(
        "producer-e2e", "producer-e2e@example.invalid", PASSWORD
    )
    creator.profile.language = "ru"
    creator.profile.save(update_fields=["language"])
    denied = User.objects.create_user(
        "producer-e2e-denied", "producer-e2e-denied@example.invalid", PASSWORD
    )
    denied.profile.language = "ru"
    denied.profile.save(update_fields=["language"])

    group = Group.objects.create(name=GROUP_NAME, language_selection=SELECTION_ALL)
    group.projects.add(*allowed)
    role = Role.objects.create(name=ROLE_NAME)
    role.permissions.add(
        *Permission.objects.filter(
            codename__in=(
                "project.add",
                "project.edit",
                "translation.add",
                "translation.auto",
                "translation.download",
                "upload.perform",
                "glossary.add",
            )
        )
    )
    group.roles.add(role)
    creator.groups.add(group)
    creator.clear_permissions_cache()


def main() -> None:
    validate_environment()
    if len(sys.argv) != 2 or sys.argv[1] not in {"create", "cleanup"}:
        msg = "usage: seed.py create|cleanup"
        raise SystemExit(msg)
    configure_django()
    if sys.argv[1] == "create":
        create()
    else:
        cleanup()


if __name__ == "__main__":
    main()
