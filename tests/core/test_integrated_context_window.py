from __future__ import annotations

import dataclasses

from puripuly_heart.core.orchestrator.context import ContextResolver
from puripuly_heart.core.orchestrator.hub import ClientHub


def test_integrated_context_covers_sixty_seconds_and_five_entries() -> None:
    resolver = ContextResolver()
    assert (resolver.integrated_time_window_s, resolver.integrated_max_entries) == (60.0, 5)

    defaults = {f.name: f.default for f in dataclasses.fields(ClientHub)}
    assert defaults["integrated_context_time_window_s"] == 60.0
    assert defaults["integrated_context_max_entries"] == 5
    # the per-channel window is unchanged
    assert (defaults["context_time_window_s"], defaults["context_max_entries"]) == (30.0, 3)
