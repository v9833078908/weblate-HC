# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math
import time
from email.utils import parsedate_to_datetime


def retry_after_seconds(value: str | None) -> float | None:
    """Parse delta-seconds or an HTTP date without retaining the raw header."""
    if value is None or len(value) > 128:
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdigit():
            delay = float(value)
        else:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                return None
            delay = max(0.0, stamp.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return None
    return delay if math.isfinite(delay) else None


def retry_delay(attempt: int, *, ceiling: int = 300) -> int:
    """Bounded exponential backoff; the persisted attempt survives restarts."""
    return min(ceiling, 5 * (2 ** max(0, attempt - 1)))
