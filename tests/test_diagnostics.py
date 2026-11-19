"""Diagnostics redact personal data."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.diagnostics import async_get_config_entry_diagnostics
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from .conftest import (
    TOKEN,
    WEBHOOK_ID,
    make_body,
    make_parcel,
    mock_parcels,
    setup_entry,
)


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Token, webhook id, names, tracking data, event texts and URLs are hidden."""
    mock_parcels(
        aioclient_mock,
        make_body(
            [make_parcel("p1", name="Scarpe", display_name="Scarpe")], problems=1
        ),
    )
    await setup_entry(hass, mock_config_entry)

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    dumped = json.dumps(diag, default=str)
    for secret in (
        TOKEN,
        WEBHOOK_ID,
        "Scarpe",
        "TRKp1",
        "Arrived at depot",
        "Milano",
        "carrier.example",
    ):
        assert secret not in dumped, secret

    parcel = diag["data"]["parcels"][0]
    assert parcel["id"] == "p1"
    assert parcel["status"] == "in_transit"
    assert parcel["carrier"] == {"code": "brt", "name": "BRT"}
    assert parcel["name"] == "**REDACTED**"
    assert parcel["last_event"]["time"] == "2026-11-18T08:00:00Z"
    assert diag["data"]["summary"]["problems"] == 1
    assert diag["entry"]["data"][CONF_URL] == "https://api-boxblip.daniele-messi.com"
    assert diag["push"] == {
        "enabled": False,
        "active": False,
        "via_cloud": False,
        "refused_reason": None,
    }
    assert diag["coordinator"]["last_update_success"] is True


async def test_diagnostics_redacts_custom_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A self-hosted server address is redacted too."""
    entry = MockConfigEntry(
        domain="boxblip",
        data={"token": TOKEN, CONF_URL: "http://127.0.0.1:8000", "webhook_id": "x"},
        options={"push": False},
    )
    aioclient_mock.get(
        "http://127.0.0.1:8000/v1/ha/parcels", json=make_body([]), headers={}
    )
    await setup_entry(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_URL] == "**REDACTED**"
