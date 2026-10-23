# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root
    ).strip():
        raise SystemExit("Commit verified source changes before packaging")
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=root, text=True
    ).strip()
    source = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=root)
    output = root / "artifacts" / f"weblate-wizard-{sha}.zip"
    output.parent.mkdir(exist_ok=True)
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_zip,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for name in source_zip.namelist():
            if not name.endswith("/"):
                target.writestr(name, source_zip.read(name))
        for path in (root / "console/dist").rglob("*"):
            if path.is_file():
                target.write(path, path.relative_to(root).as_posix())
    with zipfile.ZipFile(output) as bundle:
        assert (
            "requirements.txt" in bundle.namelist()
            and "console/dist/index.html" in bundle.namelist()
        )
        forbidden = {".env", "bd_hc_ssh_git.md", "WS.Localizations.xlsx"}
        assert not any(
            Path(name).name in forbidden
            or name.startswith(("secrets/", "data/", ".superpowers/", ".venv/"))
            for name in bundle.namelist()
        )
    print(output)
    print("SHA256 " + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
