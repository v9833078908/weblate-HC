# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def release():
    path = Path(__file__).parents[1] / "deploy/check-import-capacity.py"
    spec = importlib.util.spec_from_file_location("release_capacity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, 8 * 1024**3), (2 * 1024**3, 8 * 1024**3), (4 * 1024**3, 11 * 1024**3)],
)
def test_import_budget_includes_unpacking_and_reserved_space(release, size, expected):
    assert release.required_import_free_bytes(size) == expected


@pytest.mark.parametrize("size", [-1, True, "100", 1.5, None])
def test_invalid_sizes_cannot_bypass_admission(release, size):
    with pytest.raises(ValueError):
        release.required_import_free_bytes(size)


def test_import_checks_both_filesystems_and_image_identity(release, tmp_path):
    kwargs = {
        "image_size": 4 * 1024**3,
        "storage": tmp_path,
        "temporary": tmp_path / "temp",
        "image_id": "sha256:" + "a" * 64,
        "expected_image_id": "sha256:" + "a" * 64,
        "commit": "b" * 40,
        "expected_commit": "b" * 40,
    }
    with pytest.raises(ValueError, match="space"):
        release.check_import_capacity(
            **kwargs, free_space=lambda path: (12 if path == tmp_path else 10) * 1024**3
        )
    assert (
        release.check_import_capacity(**kwargs, free_space=lambda _path: 11 * 1024**3)[
            "required_bytes"
        ]
        == 11 * 1024**3
    )
    with pytest.raises(ValueError, match="identity"):
        release.check_import_capacity(
            **{**kwargs, "image_id": "sha256:" + "c" * 64},
            free_space=lambda _path: 100 * 1024**3,
        )


@pytest.mark.parametrize(
    ("c", "d", "used", "running"),
    [
        (89, 100, 0, False),
        (100, 49, 0, False),
        (49, 100, 1, True),
        (100, 100, 41, True),
    ],
)
def test_virtual_wsl_capacity_never_substitutes_for_physical_disk_budget(
    release, c, d, used, running
):
    with pytest.raises(ValueError, match=r"space|budget"):
        release.check_build_capacity(
            {"C": c * 1024**3, "D": d * 1024**3},
            used_bytes=used * 1024**3,
            running=running,
        )


def archive(path, name, body=b"clean", link=None):
    with tarfile.open(path, "w") as tar:
        item = tarfile.TarInfo(name)
        if link:
            item.type, item.linkname = tarfile.SYMTYPE, link
        else:
            item.size = len(body)
        tar.addfile(item, None if link else io.BytesIO(body))


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        ".env",
        "deploy/.env",
        ".superpowers/private.txt",
        "secrets/key.txt",
        "data/wizard.sqlite",
    ],
)
def test_private_files_and_path_traversal_never_enter_build_context(
    release, tmp_path, name
):
    path = tmp_path / "source.tar"
    archive(path, name)
    with pytest.raises(ValueError):
        release.validate_source_archive(path)


def test_unknown_credentials_and_escaping_links_fail_before_extraction(
    release, tmp_path
):
    path = tmp_path / "source.tar"
    archive(path, "new.py", b'token="sk-or-v1-' + b"a" * 64 + b'"')
    with pytest.raises(ValueError, match="credential"):
        release.validate_source_archive(path)
    archive(path, "link", link="../../outside")
    with pytest.raises(ValueError, match="link"):
        release.validate_source_archive(path)
    archive(path, "CLAUDE.md", link="AGENTS.md")
    assert release.validate_source_archive(path)["members"] == 1


def test_dry_run_does_not_invoke_docker_or_touch_a_running_stack(
    release, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        msg = "Dry run cannot invoke Docker"
        raise AssertionError(msg)

    monkeypatch.setattr(release, "docker", forbidden)
    result = release.build_release(
        repo=tmp_path,
        commit="a" * 40,
        output=tmp_path / "out",
        mode="dry-run",
        capacity={"C": 100 * 1024**3, "D": 100 * 1024**3},
    )
    assert result["build_started"] is False
    assert result["production_contacted"] is False


def test_budget_overrun_terminates_only_the_new_build_client(release):
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    ) as process:
        with pytest.raises(ValueError, match="budget"):
            release.monitor_build(
                process,
                {"C": 100 * 1024**3},
                read_capacity=lambda: {"C": 59 * 1024**3, "D": 100 * 1024**3},
            )
        assert process.poll() is not None


def test_base_or_numpy_drift_is_rejected_before_image_build(release, tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/Dockerfile").write_text(
        "FROM weblate/weblate:latest\nARG NUMPY_VERSION=2.4.0\n"
    )
    with pytest.raises(ValueError, match="identity"):
        release.validate_build_contract(tmp_path)


def test_second_unpinned_base_cannot_bypass_build_contract(release, tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/Dockerfile").write_text(
        "FROM weblate/weblate:2026.8.0.0@sha256:81acc89b2a9742015f2ffdee77130d1c5fcdbf3eeae203b3ff665bc6d2f7a8aa\n"
        "ARG NUMPY_VERSION=2.3.5\nFROM unpinned:latest\n"
    )
    with pytest.raises(ValueError, match="identity"):
        release.validate_build_contract(tmp_path)


def test_image_smoke_checks_packaging_without_loading_django_models(release, tmp_path):
    # A cold interpreter has no Django registry or runtime credentials. Packaging
    # must be checkable even when machinery cannot be imported before setup.
    for name in ("weblate", "loc_kit_ingest", "weblate_customization"):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
    machinery = tmp_path / "weblate_customization/machinery.py"
    machinery.write_text(
        "raise RuntimeError('Django registry has not been initialized')\n"
    )
    (tmp_path / "numpy.py").write_text("__version__ = '2.3.5'\n")
    command = release.image_smoke_command("sha256:" + "a" * 64)
    script = command[command.index("-c") + 1]
    subprocess.run([sys.executable, "-B", "-c", script], cwd=tmp_path, check=True)
    machinery.unlink()
    assert (
        subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=tmp_path, check=False
        ).returncode
        != 0
    )
    machinery.write_text("")
    (tmp_path / "numpy.py").write_text("__version__ = '2.4.0'\n")
    assert (
        subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=tmp_path, check=False
        ).returncode
        != 0
    )
