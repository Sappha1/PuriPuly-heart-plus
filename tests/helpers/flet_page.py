from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class DummyPage:
    opened: list[object] = field(default_factory=list)
    closed: list[object] = field(default_factory=list)

    def open(self, dialog: object) -> None:
        self.opened.append(dialog)

    def close(self, dialog: object) -> None:
        self.closed.append(dialog)


def attach_dummy_page(
    monkeypatch: pytest.MonkeyPatch,
    control: object,
    page: object | None = None,
) -> object:
    attached_page = object() if page is None else page
    control_type = type(control)
    isolated_control_type = type(
        f"_AttachedDummyPage{control_type.__name__}",
        (control_type,),
        {"__module__": control_type.__module__},
    )
    monkeypatch.setattr(control, "__class__", isolated_control_type)
    monkeypatch.setattr(
        type(control),
        "page",
        property(lambda _self: attached_page),
    )
    return attached_page
