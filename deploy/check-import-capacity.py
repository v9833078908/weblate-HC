# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
# Standalone operator CLI: fixed argument arrays, explicit local Docker socket.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath

GIB = 1024**3
BASE_DIGEST = "sha256:81acc89b2a9742015f2ffdee77130d1c5fcdbf3eeae203b3ff665bc6d2f7a8aa"
PLATFORM = "linux/amd64"
NUMPY_VERSION = "2.3.5"
_FIXTURES = {
    "docs/admin/auth.rst": "ef6790fcd4ae26f93e50502bd81cac98aa107576dc355d7025ce96aa228e56d6",
    "weblate/addons/tests.py": "e434e1e4b5d619d1e21c7be2b04ae71d3e53b6c51ac2b09d88aaf7f987bfcdcd",
    "weblate/machinery/tests.py": "ce8496831a7b51a34d19ed40f9749ebc7c74be3e7c8922e8a4b26d7cedc1ea00",
    "weblate/trans/tests/data/googlev3.json": "a00f19e967b0b0e92e7b0d9fd15c4c6790a1667af7d0e3186197f7cf206c3dd5",
    "weblate/vcs/tests/test_github.py": "ffb4f591fcc73ef0d8194c3425ac0171ff3fe1b792a5a32bf0322c08a8b283be",
    "weblate/vcs/tests/test_github_views.py": "81c55efc7593365a3eea872bf921e7340590685b98a0a90ca28baf5dece15323",
}
_CREDENTIAL = re.compile(
    rb"sk-or-v1-[A-Za-z0-9]{48,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def required_import_free_bytes(image_size: int) -> int:
    if type(image_size) is not int or image_size < 0:
        msg = "Image size must be a non-negative integer"
        raise ValueError(msg)
    return max(8 * GIB, 2 * image_size + 3 * GIB)


def check_import_capacity(
    *,
    image_size,
    storage,
    temporary,
    image_id,
    expected_image_id,
    commit,
    expected_commit,
    free_space=None,
):
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or image_id != expected_image_id
        or commit != expected_commit
    ):
        msg = "Image identity differs from the approved manifest"
        raise ValueError(msg)
    required = required_import_free_bytes(image_size)
    free_space = free_space or (lambda path: shutil.disk_usage(path).free)
    observations = []
    for path in (Path(storage), Path(temporary)):
        available = free_space(path)
        if type(available) is not int or available < required:
            msg = "Insufficient import space; no image is loaded"
            raise ValueError(msg)
        observations.append({"path": str(path), "free_bytes": available})
    return {"required_bytes": required, "filesystems": observations}


def check_build_capacity(capacity, *, used_bytes=0, running=False):
    if type(used_bytes) is not int or used_bytes < 0 or used_bytes > 40 * GIB:
        msg = "Build storage budget exceeded"
        raise ValueError(msg)
    minimum_c = (50 if running else 90) * GIB
    if (
        any(type(capacity.get(drive)) is not int for drive in ("C", "D"))
        or capacity["C"] < minimum_c
        or capacity["D"] < 50 * GIB
    ):
        msg = "Insufficient physical Windows disk space"
        raise ValueError(msg)
    return {"C": capacity["C"], "D": capacity["D"], "used_bytes": used_bytes}


def executable(name):
    path = shutil.which(name)
    if path is None:
        message = "Required local executable is missing: " + name
        raise ValueError(message)
    return path


def windows_capacity():
    program = "powershell.exe" if os.name != "nt" else "powershell"
    code = "@{C=(Get-PSDrive -Name C).Free;D=(Get-PSDrive -Name D).Free}|ConvertTo-Json -Compress"
    result = subprocess.run(
        [executable(program), "-NoProfile", "-NonInteractive", "-Command", code],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return json.loads(result.stdout.decode("utf-8-sig"))


def validate_source_archive(path):
    """Inspect every tracked member before extracting; never log credential values."""
    forbidden_roots = {
        ".git",
        ".superpowers",
        ".worktrees",
        ".venv",
        "node_modules",
        "data",
        "data-test",
        "secrets",
        "backups",
    }
    members = 0
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.rstrip("/")
            parts = PurePosixPath(name).parts
            if (
                not parts
                or ".." in parts
                or name.startswith(("/", "dev-docker/data/"))
                or "\\" in name
                or parts[0] in forbidden_roots
                or PurePosixPath(name).name
                in {
                    ".env",
                    ".env.local",
                    "bd_hc_ssh_git.md",
                    "for_ssh_for_weblate.txt",
                    "weblate_HC.md",
                }
                or name.endswith((".sqlite", ".sqlite3", ".dump"))
            ):
                raise ValueError("Private or unsafe build-context path: " + name)
            if member.issym() or member.islnk():
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), member.linkname)
                )
                if (
                    member.linkname.startswith("/")
                    or target == ".."
                    or target.startswith("../")
                    or "\\" in member.linkname
                ):
                    msg = "Escaping source archive link"
                    raise ValueError(msg)
            elif member.isfile():
                data = archive.extractfile(member).read()
                if _CREDENTIAL.search(data) and hashlib.sha256(
                    data
                ).hexdigest() != _FIXTURES.get(name):
                    raise ValueError("Unreviewed credential candidate in " + name)
            elif not member.isdir():
                msg = "Unsupported source archive member"
                raise ValueError(msg)
            members += 1
    return {"members": members}


def docker(*args, **kwargs):
    # A caller's context or environment cannot redirect this tool to production.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DOCKER_HOST", "DOCKER_CONTEXT"}
    }
    env["DOCKER_BUILDKIT"] = "0"
    return subprocess.run(
        [executable("docker"), "--host", "unix:///var/run/docker.sock", *args],
        env=env,
        check=True,
        **kwargs,
    )


def validate_build_contract(context):
    dockerfile = (context / "deploy/Dockerfile").read_text()
    bases = [
        line
        for line in dockerfile.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    if (
        len(bases) != 1
        or not any(
            line.startswith("FROM weblate/weblate:")
            and line.endswith("@" + BASE_DIGEST)
            for line in dockerfile.splitlines()
        )
        or "ARG NUMPY_VERSION=" + NUMPY_VERSION not in dockerfile.splitlines()
    ):
        msg = "Base digest or NumPy identity changed"
        raise ValueError(msg)


def file_sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def monitor_build(process, capacity, *, read_capacity=None):
    read_capacity = read_capacity or windows_capacity
    try:
        while process.poll() is None:
            current = read_capacity()
            check_build_capacity(
                current, used_bytes=max(0, capacity["C"] - current["C"]), running=True
            )
            time.sleep(2)
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        raise
    if process.returncode:
        message = "Image build failed; see private local build log"
        raise ValueError(message)


def build_release(*, repo, commit, output, mode="dry-run", capacity=None):
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or mode not in {
        "dry-run",
        "prepare",
        "build",
    }:
        msg = "An exact source commit and explicit mode are required"
        raise ValueError(msg)
    capacity = windows_capacity() if capacity is None else capacity
    check_build_capacity(capacity)
    result = {
        "schema": 1,
        "source_commit": commit,
        "base_digest": BASE_DIGEST,
        "platform": PLATFORM,
        "numpy": NUMPY_VERSION,
        "build_started": False,
        "production_contacted": False,
        "capacity": capacity,
        "mode": mode,
    }
    if mode == "dry-run":
        return result
    if not str(output).lower().replace("\\", "/").startswith(("d:/", "/mnt/d/")):
        msg = "Build context and image archive must be on drive D"
        raise ValueError(msg)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source.tar"
    with source.open("wb") as stream:
        subprocess.run(
            [
                executable("git"),
                "-C",
                str(Path(repo).resolve()),
                "archive",
                "--format=tar",
                commit,
            ],
            stdout=stream,
            check=True,
            timeout=120,
        )
    result["source_sha256"] = file_sha256(source)
    result["context"] = validate_source_archive(source)
    context = output / "context"
    context.mkdir()
    with tarfile.open(source) as archive:
        archive.extractall(context, filter="data")
    validate_build_contract(context)
    (output / "manifest.json").write_text(json.dumps(result, indent=2))
    if mode == "prepare":
        return result
    if os.name == "nt":
        msg = "Image build requires the approved WSL Linux Docker runtime"
        raise ValueError(msg)
    tag = "hcgameloc-validated:" + commit
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DOCKER_HOST", "DOCKER_CONTEXT"}
    }
    env["DOCKER_BUILDKIT"] = "0"
    command = [
        executable("docker"),
        "--host",
        "unix:///var/run/docker.sock",
        "build",
        "--platform",
        PLATFORM,
        "--build-arg",
        "GIT_SHA=" + commit,
        "--build-arg",
        "NUMPY_VERSION=" + NUMPY_VERSION,
        "-f",
        str(context / "deploy/Dockerfile"),
        "-t",
        tag,
        str(context),
    ]
    result["build_started"] = True
    with (
        (output / "build.log").open("wb") as log,
        subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT
        ) as process,
    ):
        monitor_build(process, capacity)
    metadata = json.loads(docker("image", "inspect", tag, capture_output=True).stdout)[
        0
    ]
    if (
        metadata["Config"]["Labels"].get("org.opencontainers.image.revision") != commit
        or metadata["Architecture"] != "amd64"
        or metadata["Os"] != "linux"
    ):
        msg = "Built image identity differs from approved contract"
        raise ValueError(msg)
    docker(*image_smoke_command(metadata["Id"]))
    if windows_capacity()["D"] < metadata["Size"] + 3 * GIB:
        msg = "Insufficient export space"
        raise ValueError(msg)
    archive = output / "image.tar"
    docker("image", "save", "-o", str(archive), metadata["Id"])
    result.update(
        image_id=metadata["Id"],
        image_size=metadata["Size"],
        archive_size=archive.stat().st_size,
        archive_sha256=file_sha256(archive),
        import_required_bytes=required_import_free_bytes(metadata["Size"]),
    )
    (output / "manifest.json").write_text(json.dumps(result, indent=2))
    return result


def image_smoke_command(image_id):
    # Packaging check only: machinery imports Django models and requires a real
    # application registry. Never load runtime settings or credentials here.
    return (
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/app/venv/bin/python",
        image_id,
        "-c",
        (
            "import importlib.util as u, numpy, weblate, loc_kit_ingest; "
            "assert u.find_spec('weblate_customization.machinery'); "
            "assert numpy.__version__ == '2.3.5'"
        ),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Offline release preparation and read-only capacity checks"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build")
    build.add_argument("--repo", required=True, type=Path)
    build.add_argument("--commit", required=True)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument(
        "--mode", choices=("dry-run", "prepare", "build"), default="dry-run"
    )
    check = sub.add_parser("check-import")
    check.add_argument("--manifest", required=True, type=Path)
    check.add_argument("--storage", required=True, type=Path)
    check.add_argument("--temporary", required=True, type=Path)
    check.add_argument("--image-id", required=True)
    check.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            build_release(
                repo=args.repo, commit=args.commit, output=args.output, mode=args.mode
            )
        else:
            manifest = json.loads(args.manifest.read_text())
            check_import_capacity(
                image_size=manifest["image_size"],
                storage=args.storage,
                temporary=args.temporary,
                image_id=args.image_id,
                expected_image_id=manifest["image_id"],
                commit=args.commit,
                expected_commit=manifest["source_commit"],
            )
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
