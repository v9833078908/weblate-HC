# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import signal
import time
from threading import Event

from app.db import Store
from app.services.profile_jobs import process_profile_once
from app.settings import Settings


def main():
    settings = Settings()
    store = Store(settings.data_dir)
    stopping = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    while not stopping.is_set():
        (settings.data_dir / "profile-worker-heartbeat").write_text(str(time.time()))
        if not settings.profile_agent_enabled or not process_profile_once(store, settings):
            stopping.wait(2)


if __name__ == "__main__":
    main()
