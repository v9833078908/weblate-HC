# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Packaging guards for data files loaded through :mod:`importlib.resources`.

A non-Python file next to the code that reads it is invisible to
``packages.find``: it reaches the built artifacts only when a
``[tool.setuptools.package-data]`` glob names it, because the project sets
``include-package-data = false``. Nothing in the source tree notices the
omission - the dev container imports from the bind-mounted checkout and the
test suite runs from the same tree, so the file is always there. The failure
appears only in an installed wheel, at the first runtime read.

The two tests below close that gap from both sides: the declaration is checked
offline on every run, and the built wheel is checked whenever a build backend
is available.
"""

from __future__ import annotations

import ast
import fnmatch
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SOURCE_PACKAGE = "weblate"


def _resource_loads(tree: ast.AST) -> list[tuple[str, str]]:
    """Find ``resources.files("pkg").joinpath("name")`` pairs in one module."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) != 1:
            continue
        joinpath = node.func
        if not isinstance(joinpath, ast.Attribute) or joinpath.attr != "joinpath":
            continue
        files_call = joinpath.value
        if not isinstance(files_call, ast.Call) or len(files_call.args) != 1:
            continue
        files_attr = files_call.func
        if not isinstance(files_attr, ast.Attribute) or files_attr.attr != "files":
            continue
        package, name = files_call.args[0], node.args[0]
        if (
            isinstance(package, ast.Constant)
            and isinstance(package.value, str)
            and isinstance(name, ast.Constant)
            and isinstance(name.value, str)
        ):
            found.append((package.value, name.value))
    return found


def discover_resource_data_files() -> set[Path]:
    """
    Return the data files the shipped code reads, relative to the repository.

    Test modules are skipped: they are excluded from the distribution by
    ``[tool.setuptools.packages.find]``, so their fixtures are not expected
    in an artifact.
    """
    discovered: set[Path] = set()
    for module in sorted((REPO_ROOT / SOURCE_PACKAGE).rglob("*.py")):
        if "tests" in module.relative_to(REPO_ROOT).parts:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for package, name in _resource_loads(tree):
            if package.split(".")[0] != SOURCE_PACKAGE:
                continue
            discovered.add(Path(*package.split(".")) / name)
    return discovered


def package_data_globs() -> dict[Path, list[str]]:
    """``package-data`` globs keyed by the directory they are relative to."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    package_data = config["tool"]["setuptools"]["package-data"]
    return {Path(*package.split(".")): globs for package, globs in package_data.items()}


class ResourceDataPackagingTest(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.expected = discover_resource_data_files()
        # A silent empty set would make every assertion below vacuous.
        self.assertTrue(self.expected, "no importlib.resources data files discovered")

    def test_discovered_files_exist(self) -> None:
        for path in sorted(self.expected):
            with self.subTest(path=path):
                self.assertTrue(
                    (REPO_ROOT / path).is_file(), f"{path} is read but does not exist"
                )

    def test_declared_as_package_data(self) -> None:
        """Every runtime data file is named by a package-data glob."""
        globs = package_data_globs()
        for path in sorted(self.expected):
            with self.subTest(path=path):
                matched = any(
                    fnmatch.fnmatchcase(path.relative_to(directory).as_posix(), pattern)
                    for directory, patterns in globs.items()
                    if path.is_relative_to(directory)
                    for pattern in patterns
                )
                self.assertTrue(
                    matched,
                    f"{path} is read at runtime but no [tool.setuptools.package-data]"
                    f" glob matches it, so it is missing from the built wheel",
                )

    def test_built_wheel_contains_them(self) -> None:
        """Ground truth: the same files are inside a freshly built wheel."""
        uv = shutil.which("uv")
        if uv is None:
            msg = "uv is required to build the wheel"
            raise self.skipTest(msg)
        # setuptools copies sources into build/lib and reuses whatever is
        # already there. A leftover tree from an earlier build carries the
        # data file even when the current package-data no longer declares it,
        # which makes this test pass against the very defect it guards. The
        # build base is gitignored and regenerated, so clearing it is safe.
        shutil.rmtree(REPO_ROOT / "build", ignore_errors=True)
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [uv, "build", "--wheel", "--out-dir", tmp],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            wheels = list(Path(tmp).glob("*.whl"))
            self.assertEqual(len(wheels), 1, f"expected one wheel, got {wheels}")
            with zipfile.ZipFile(wheels[0]) as archive:
                shipped = set(archive.namelist())
        for path in sorted(self.expected):
            with self.subTest(path=path):
                self.assertIn(path.as_posix(), shipped)
