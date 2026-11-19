"""Fixtures for BoxBlip tests."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.const import CONF_WEBHOOK_ID, DEFAULT_URL, DOMAIN
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

TOKEN = "bbha_" + "A" * 43
OTHER_TOKEN = "bbha_" + "B" * 43
WEBHOOK_ID = "0123456789abcdef" * 4
PARCELS_URL = f"{DEFAULT_URL}/v1/ha/parcels"
SUMMARY_URL = f"{DEFAULT_URL}/v1/ha/summary"
WEBHOOK_URL = f"{DEFAULT_URL}/v1/ha/webhook"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


def make_parcel(parcel_id: str, status: str = "in_transit", **extra: Any) -> dict:
    """Build one parcel as the API returns it."""
    active = status in {
        "pending",
        "info_received",
        "in_transit",
        "out_for_delivery",
        "available_for_pickup",
        "exception",
    }
    parcel: dict[str, Any] = {
        "id": parcel_id,
        "name": f"Name {parcel_id}",
        "display_name": f"Name {parcel_id}",
        "tracking_number": f"TRK{parcel_id}",
        "carrier": {"code": "brt", "name": "BRT"},
        "status": status,
        "status_text": status.replace("_", " ").title(),
        "active": active,
        "last_event": {
            "time": "2026-11-18T08:00:00Z",
            "text": "Arrived at depot",
            "location": "Milano",
        },
        "eta": None,
        "added_at": "2026-11-15T10:00:00Z",
        "updated_at": "2026-11-18T08:00:00Z",
        "delivered_at": None,
        "tracking_url": f"https://carrier.example/track/{parcel_id}",
        "outgoing": False,
        "archived": False,
        "shared": False,
    }
    parcel.update(extra)
    return parcel


def make_body(parcels: list[dict], **summary: int) -> dict:
    """Build a /v1/ha/parcels body."""
    counts = {
        "on_the_way": 0,
        "out_for_delivery": 0,
        "to_pick_up": 0,
        "delivered_today": 0,
        "problems": 0,
    }
    counts.update(summary)
    return {"parcels": parcels, "summary": counts, "delivered_days": 3}


def mock_parcels(
    aioclient_mock: AiohttpClientMocker,
    body: dict | None = None,
    *,
    status: int = 200,
    etag: str | None = '"v1"',
    headers: dict[str, str] | None = None,
) -> None:
    """Replace every mocked response with one for /v1/ha/parcels."""
    aioclient_mock.clear_requests()
    all_headers = {"content-type": "application/json"}
    if etag:
        all_headers["ETag"] = etag
    all_headers.update(headers or {})
    aioclient_mock.get(
        PARCELS_URL,
        status=status,
        json=body if body is not None and status == 200 else None,
        headers=all_headers,
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured BoxBlip entry, push disabled unless a test enables it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="BoxBlip",
        data={CONF_TOKEN: TOKEN, CONF_URL: DEFAULT_URL, CONF_WEBHOOK_ID: WEBHOOK_ID},
        options={"push": False, "delivered_days": 3},
        entry_id="01BOXBLIPTESTENTRY",
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
