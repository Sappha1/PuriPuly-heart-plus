"""Fetch VRChat's CURRENT mute state via OSCQuery instead of waiting for a change.

VRChat only SENDS /avatar/parameters/MuteSelf over OSC when the state CHANGES,
so mute sync stayed grey until the user toggled their in-game mic once — and
with push-to-talk the mute toggle never changes at all, making sync impossible.
VRChat also runs an OSCQuery HTTP service (random per-session port, advertised
over mDNS as _oscjson._tcp.local.) that answers with the CURRENT value:

    GET http://127.0.0.1:<port>/avatar/parameters/MuteSelf
    -> {"FULL_PATH": ..., "TYPE": "T", "VALUE": [false]}

This poller runs while the mute state is unsynced (None), fetches the initial
value, and exits — subsequent changes flow through the normal OSC receiver.
Verified live against VRChat (service VRChat-Client-*, VALUE parsed as bool).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request

logger = logging.getLogger(__name__)

_OSCQUERY_SERVICE_TYPE = "_oscjson._tcp.local."
_VRCHAT_SERVICE_PREFIX = "VRChat-Client"
_MUTE_PATH = "/avatar/parameters/MuteSelf"
_DISCOVERY_WINDOW_S = 4.0
_RETRY_INTERVAL_S = 10.0


def _fetch_mute_via_oscquery() -> bool | None:
    """Blocking: discover VRChat's OSCQuery service and read MuteSelf.
    Returns the bool state, or None when VRChat isn't running / unreachable."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except Exception:
        return None

    endpoints: list[tuple[str, int]] = []

    class _Listener(ServiceListener):
        def add_service(self, zc, type_, name) -> None:  # noqa: ANN001
            if _VRCHAT_SERVICE_PREFIX not in name:
                return
            info = zc.get_service_info(type_, name, timeout=2000)
            if info and info.port:
                for addr in info.parsed_addresses():
                    endpoints.append((addr, info.port))

        def update_service(self, *args) -> None:  # noqa: ANN002
            pass

        def remove_service(self, *args) -> None:  # noqa: ANN002
            pass

    zc = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, _OSCQUERY_SERVICE_TYPE, _Listener())
        deadline = time.monotonic() + _DISCOVERY_WINDOW_S
        while time.monotonic() < deadline and not endpoints:
            time.sleep(0.25)
    except Exception:
        return None
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass

    for addr, port in endpoints:
        try:
            url = f"http://{addr}:{port}{_MUTE_PATH}"
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            value = data.get("VALUE")
            if isinstance(value, list) and value:
                return bool(value[0])
        except Exception:
            continue
    return None


async def run_initial_mute_sync(state, *, is_active) -> None:  # noqa: ANN001
    """Poll until the mute state is synced or the receiver goes away.

    state: VrcMicState (checked/updated on the event loop, never from the
    discovery thread). is_active: callable() -> bool, False stops the loop.
    """
    try:
        logger.info(
            "[OSCQuery] Starting VRChat service discovery for mute sync — "
            "Windows may ask ONCE to allow the app through the firewall. "
            "This only listens for local-network service announcements; "
            "nothing is sent to the internet."
        )
        while is_active() and state.muted is None:
            muted = await asyncio.to_thread(_fetch_mute_via_oscquery)
            if not is_active() or state.muted is not None:
                return
            if muted is not None:
                state.update(muted)
                logger.info(
                    "[OSCQuery] Initial VRChat mute state fetched: muted=%s "
                    "(no mic toggle needed — works with push-to-talk)",
                    muted,
                )
                return
            await asyncio.sleep(_RETRY_INTERVAL_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("[OSCQuery] mute sync poller stopped: %s", exc)
