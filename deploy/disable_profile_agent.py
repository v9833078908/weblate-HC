# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def disable(path, recovery=None):
    path = Path(path).resolve()
    current = path.read_text(encoding="utf-8")
    metadata = path.stat()
    if recovery is not None:
        recovery = Path(recovery)
        recovery.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if recovery.exists():
            raise ValueError("Recovery configuration already exists")
        shutil.copy2(path, recovery)
        recovery.chmod(0o600)
    keys = {
        "WIZARD_PROFILE_AGENT_ENABLED",
        "PROFILE_LLM_MODEL",
        "PROFILE_LLM_TOKEN_FILE",
        "WIZARD_EXTERNAL_WRITES",
    }
    lines = [line for line in current.splitlines() if line.split("=", 1)[0].strip() not in keys]
    lines += [
        "WIZARD_PROFILE_AGENT_ENABLED=0",
        "PROFILE_LLM_MODEL=",
        "PROFILE_LLM_TOKEN_FILE=",
        "WIZARD_EXTERNAL_WRITES=0",
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(stat.S_IMODE(metadata.st_mode))
        if hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    disable(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("Profile calls and external writes disabled; credentials not printed")
