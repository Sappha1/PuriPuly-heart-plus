"""r340: the speaker-tag registries must not outlive the entries they point at.

Chat entries disappear two ways — the Clear button and the CHAT_MAX_ENTRIES
trim (oldest 20 dropped past 200) — and the registries used to hold strong
references to their tags for the whole session, so a relabel walked controls
that were no longer on screen.

These exercise the real methods rather than grepping the source, because the
thing being asserted is lifetime behaviour.
"""
from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace

import flet as ft

from puripuly_heart.ui.views import dashboard as dashboard_module


def _view() -> object:
    """A DashboardView with only the attributes these methods touch."""
    view = dashboard_module.DashboardView.__new__(dashboard_module.DashboardView)
    view._speaker_tag_controls = {}
    view._speaker_tag_controls_by_name = {}
    view.on_speaker_clusters_for_name = lambda _name: []
    return view


def test_relabel_updates_tags_held_by_cluster_and_by_name() -> None:
    view = _view()
    by_cluster = ft.Text("Old")
    by_name = ft.Text("Old")
    view._speaker_tag_controls = {7: [weakref.ref(by_cluster)]}
    view._speaker_tag_controls_by_name = {"Old": [weakref.ref(by_name)]}

    view._retro_label_speaker_tags(7, "New", also_named="Old")

    assert by_cluster.value == "New"
    assert by_name.value == "New"
    # the identity index follows the rename
    assert "Old" not in view._speaker_tag_controls_by_name
    assert len(view._speaker_tag_controls_by_name["New"]) == 1


def test_a_collected_tag_is_skipped_and_pruned() -> None:
    """A trimmed entry's tag is gone; relabelling must not trip over it."""
    view = _view()
    alive = ft.Text("Old")
    dead = ft.Text("Old")
    view._speaker_tag_controls = {7: [weakref.ref(dead), weakref.ref(alive)]}

    del dead
    gc.collect()

    view._retro_label_speaker_tags(7, "New")

    assert alive.value == "New"
    # the dead reference is dropped rather than kept forever
    assert len(view._speaker_tag_controls[7]) == 1
    assert view._speaker_tag_controls[7][0]() is alive


def test_registries_do_not_keep_trimmed_entries_alive() -> None:
    """The registry must hold a WEAK reference — a strong one would defeat
    the CHAT_MAX_ENTRIES trim entirely."""
    view = _view()
    tag = ft.Text("Old")
    ref = weakref.ref(tag)
    view._speaker_tag_controls = {7: [ref]}
    view._speaker_tag_controls_by_name = {"Old": [ref]}

    del tag
    gc.collect()

    assert ref() is None, "registry is pinning controls that should be collectable"


def test_clearing_the_log_empties_both_registries() -> None:
    view = _view()
    tag = ft.Text("Old")
    view._speaker_tag_controls = {7: [weakref.ref(tag)]}
    view._speaker_tag_controls_by_name = {"Old": [weakref.ref(tag)]}
    view._chat_list_view = SimpleNamespace(
        controls=[ft.Text("entry")], update=lambda: None
    )

    view._on_chat_clear(None)

    assert view._speaker_tag_controls == {}
    assert view._speaker_tag_controls_by_name == {}
    assert view._chat_list_view.controls == []


def test_relabel_is_safe_with_no_registry_at_all() -> None:
    view = dashboard_module.DashboardView.__new__(dashboard_module.DashboardView)

    view._retro_label_speaker_tags(3, "New")  # must not raise
