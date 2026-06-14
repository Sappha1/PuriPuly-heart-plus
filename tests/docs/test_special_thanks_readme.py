from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README_FILES = ["README.md", "README.ko.md", "README.ja.md", "README.zh-CN.md"]
SPECIAL_THANKS_TEXT = (
    "SUI\\_32C, Nagikokoro, motoka96, \\_Ykol魚, kascr\\_, "
    "Just Monika V, FLUVIA, Han โชเล่ย์, EA\\_PE, Ephedrine"
)


@pytest.mark.parametrize("readme_name", README_FILES)
def test_readme_special_thanks_lists_expected_names_in_order(readme_name: str) -> None:
    readme_text = (ROOT / readme_name).read_text(encoding="utf-8")

    assert SPECIAL_THANKS_TEXT in readme_text


def test_all_contributors_contains_ea_pe_special_thanks_entry() -> None:
    config = json.loads((ROOT / ".all-contributorsrc").read_text(encoding="utf-8"))

    contributor = next(
        (entry for entry in config["contributors"] if entry["login"] == "ea-pe"),
        None,
    )

    assert contributor == {
        "login": "ea-pe",
        "name": "EA_PE",
        "avatar_url": "https://ui-avatars.com/api/?name=EA&size=160&background=F0FFF4&color=2F6B45&rounded=true&bold=true",
        "contributions": ["thanks"],
    }


def test_all_contributors_contains_ephedrine_special_thanks_entry() -> None:
    config = json.loads((ROOT / ".all-contributorsrc").read_text(encoding="utf-8"))

    contributor = next(
        (entry for entry in config["contributors"] if entry["login"] == "ephedrine"),
        None,
    )

    assert contributor == {
        "login": "ephedrine",
        "name": "Ephedrine",
        "avatar_url": "https://ui-avatars.com/api/?name=EPH&size=160&background=F4F0FF&color=4C3B7A&rounded=true&bold=true",
        "contributions": ["thanks"],
    }
