"""Pin the `translators` library's server region before its first import.

Without `translators_default_region` set, the library geo-detects the region
at first use by calling geolocation.onetrust.com / httpbin.org / ip-api.com —
all blocked or unreliable inside China. When detection fails it defaults to
the international backends (www.bing.com), which ARE blocked there, so Bing
"times out after 10s" for exactly the users who need the CN backends
(cn.bing.com). Pinning the region also skips the geo lookup entirely, making
first translation faster and deterministic for everyone.

Must run BEFORE `from translators import ...` anywhere in the process; both
import sites (free_web provider, OCR overlay) call it right before importing.
"""

from __future__ import annotations

import os
import subprocess

_CREATE_NO_WINDOW = 0x08000000


def ensure_translators_region() -> str:
    existing = os.environ.get("translators_default_region")
    if existing:
        return existing
    region = "EN"
    try:
        # Same China signal as the model-download mirror choice: timezone.
        result = subprocess.run(
            ["tzutil", "/g"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=_CREATE_NO_WINDOW,
        )
        if "China Standard Time" in (result.stdout or ""):
            region = "CN"
    except Exception:
        pass
    os.environ["translators_default_region"] = region
    return region
