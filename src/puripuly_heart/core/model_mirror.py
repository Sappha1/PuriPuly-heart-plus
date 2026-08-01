"""Reach the model host from networks that cannot (r354).

The Whisper recogniser downloads its weights through huggingface_hub, which is
unreachable from mainland China -- a user there had every download time out,
leaving the local Qwen recogniser as their only option. On a CPU without VNNI
that recogniser's int8 arithmetic is unreliable (see cpu_int8_support), so
"the one that works on your hardware" and "the one you can actually download"
were disjoint sets and the app offered no way out.

huggingface_hub reads HF_ENDPOINT once, at import, so the choice has to be
made before anything touches it -- which is why this is called from main()
before the rest of the app loads.

Nothing is hosted by us: hf-mirror.com is the long-standing community mirror
of the same repositories, so a mirrored download fetches identical bytes and
still passes the checksum verification the installer already performs.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_MIRROR = "https://hf-mirror.com"
_ENV = "HF_ENDPOINT"


def _looks_like_prc_locale() -> bool:
    """Best-effort read of whether this machine sits behind the firewall.

    Deliberately weak evidence, used only to pick a DEFAULT that the user can
    override. Being wrong costs a mirrored download of identical, checksummed
    bytes; being wrong the other way costs the user every model in the app.
    """
    try:
        import time

        zones = {(time.tzname[0] or ""), (time.tzname[1] or "")}
        if any("China" in z or "CST" == z.strip() for z in zones):
            return True
    except Exception:
        pass
    try:
        import locale

        code = (locale.getdefaultlocale()[0] or "").lower()
        if code.startswith("zh_cn") or code.startswith("zh-hans"):
            return True
    except Exception:
        pass
    return False


def configure_model_downloads(preference: str = "auto") -> str:
    """Point model downloads at a reachable host. Returns what was chosen.

    `preference` is "auto", "mirror", or "direct". An HF_ENDPOINT already set
    in the environment always wins -- if the user configured a host, that is
    an explicit instruction and not ours to second-guess.
    """
    existing = os.environ.get(_ENV, "").strip()
    if existing:
        logger.info("[Models] HF_ENDPOINT already set to %s - leaving it", existing)
        return existing

    if preference == "direct":
        return ""
    if preference == "mirror" or (preference == "auto" and _looks_like_prc_locale()):
        os.environ[_ENV] = DEFAULT_MIRROR
        logger.info(
            "[Models] routing model downloads via %s (huggingface.co is not "
            "reachable from some networks; the mirror serves the same files "
            "and downloads are checksum-verified either way)",
            DEFAULT_MIRROR,
        )
        return DEFAULT_MIRROR
    return ""
