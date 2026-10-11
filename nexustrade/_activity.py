"""Advisory liveness evidence for a runner that explicitly configures it."""

from __future__ import annotations

import os


def touch_activity() -> None:
    """Record real activity without making reporting a prerequisite for work."""
    path = os.environ.get("NEXUSTRADE_HOST_ACTIVITY_FILE", "").strip()
    if not path:
        return
    try:
        with open(path, "ab"):
            pass
        os.utime(path, None)
    except OSError:
        pass
