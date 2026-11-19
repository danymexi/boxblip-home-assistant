"""The ready-made dashboard: valid YAML, and its templates group parcels right."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from .conftest import make_body, make_parcel, mock_parcels, setup_entry

DASHBOARD = Path(__file__).parent.parent / "dashboards" / "boxblip.yaml"
GROUP_TITLES = (
    "In consegna oggi",
    "In viaggio",
    "Da ritirare",
    "Problemi",
    "Consegnati di recente",
)


def _cards() -> list[dict[str, Any]]:
    view = yaml.safe_load(DASHBOARD.read_text(encoding="utf-8"))["views"][0]
    assert view["type"] == "sections"
    return [card for section in view["sections"] for card in section["cards"]]


def test_only_built_in_cards() -> None:
    """No custom cards, and only language-independent entity ids."""
    cards = _cards()
    assert {card["type"] for card in cards} <= {
        "heading",
        "markdown",
        "tile",
        "calendar",
    }
    named = {card["entity"] for card in cards if "entity" in card}
    named |= {e for card in cards for e in card.get("entities", [])}
    assert named == {"calendar.boxblip_deliveries"}


async def _render(hass: HomeAssistant) -> list[str]:
    out = []
    for card in _cards():
        if card["type"] == "markdown":
            out.append(
                Template(card["content"], hass).async_render(
                    parse_result=False, strict=True
                )
            )
    return out


@pytest.mark.freeze_time("2026-11-18T10:00:00+00:00")
async def test_groups(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Every parcel lands in exactly one group; empty groups render nothing."""
    await hass.config.async_update(time_zone="Europe/Rome")
    today_eta = {
        "start": "2026-11-18T11:00:00Z",
        "end": "2026-11-18T11:00:00Z",
        "has_time": False,
    }
    later_eta = {
        "start": "2026-11-20T11:00:00Z",
        "end": "2026-11-21T11:00:00Z",
        "has_time": False,
    }
    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel("a1", "out_for_delivery"),
                make_parcel("a2", "in_transit", eta=today_eta),
                make_parcel("b1", "in_transit", eta=later_eta),
                make_parcel("b2", "pending", outgoing=True),
                make_parcel("c1", "available_for_pickup"),
                make_parcel(
                    "e1",
                    "delivered",
                    delivered_at="2026-11-18T08:00:00Z",
                ),
            ]
        ),
    )
    await setup_entry(hass, mock_config_entry)

    summary, today, transit, pickup, problems, delivered = await _render(hass)
    assert "## 5 pacchi in arrivo" in summary
    assert "In consegna **1**" in summary
    assert "Da ritirare **1**" in summary
    assert "Problemi **0**" in summary
    assert "Consegnati oggi **1**" in summary

    assert "In consegna oggi (2)" in today
    assert "Name a1" in today
    assert "Name a2" in today
    assert "In viaggio (2)" in transit
    assert "Name b1" in transit
    assert "entro ven 20/11" not in transit
    assert "entro sab 21/11" in transit
    assert "in uscita" in transit
    assert "Da ritirare (1)" in pickup
    assert "Name c1" in pickup
    assert problems == ""
    assert "Consegnati di recente (1)" in delivered
    assert "Name e1" in delivered
    # Each row links to the parcel's device page and the carrier's page.
    assert "](/config/devices/device/" in today
    assert "[traccia](https://carrier.example/track/a1)" in today
    assert "<small>Arrived at depot · Milano · oggi 09:00</small>" in today
    assert "<small>In Transit · Arrived at depot" in transit


async def test_no_parcels(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With nothing on the way the summary still renders and every group is empty."""
    mock_parcels(aioclient_mock, make_body([]))
    await setup_entry(hass, mock_config_entry)
    summary, *groups = await _render(hass)
    assert "## 0 pacchi in arrivo" in summary
    assert "Prossima consegna" not in summary
    assert groups == [""] * len(GROUP_TITLES)
