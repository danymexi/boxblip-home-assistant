"""Setup, polling and error handling."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.api import Parcel, ParcelsResponse, Summary
from custom_components.boxblip.const import EVENT_PARCEL_UPDATED
from custom_components.boxblip.coordinator import api_language
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .conftest import TOKEN, make_body, make_parcel, mock_parcels, setup_entry

SENSOR = "sensor.boxblip_name_p1"


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int = 5):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_and_request(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Setup polls with the token, language, time zone and window."""
    await hass.config.async_update(language="it-IT", time_zone="Europe/Rome")
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")], on_the_way=1))
    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert url.query == {"lang": "it", "delivered_days": "3", "tz": "Europe/Rome"}
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept-Language"] == "it"
    assert "If-None-Match" not in headers
    assert hass.states.get(SENSOR).state == "in_transit"

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (401, ConfigEntryState.SETUP_ERROR),
        (503, ConfigEntryState.SETUP_RETRY),
        (500, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    status: int,
    state: ConfigEntryState,
) -> None:
    """401 at setup starts reauth; 5xx retries later."""
    mock_parcels(aioclient_mock, status=status)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is state
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows) is (
        status == 401
    )


async def test_etag_304_keeps_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The ETag is sent back and a 304 keeps the previous data."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]), etag='"abc"')
    await setup_entry(hass, mock_config_entry)

    mock_parcels(aioclient_mock, status=304, etag=None)
    await _poll(hass, freezer)
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["If-None-Match"] == '"abc"'
    state = hass.states.get(SENSOR)
    assert state.state == "in_transit"
    assert mock_config_entry.runtime_data.last_update_success


async def test_401_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A revoked token while running starts the reauth flow."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)

    mock_parcels(aioclient_mock, status=401)
    await _poll(hass, freezer)
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE


async def test_429_backs_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """429 marks the data stale and waits Retry-After before the next call."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    mock_parcels(aioclient_mock, status=429, headers={"Retry-After": "1800"})
    await _poll(hass, freezer)
    assert not coordinator.last_update_success
    assert coordinator.last_exception.retry_after == 1800
    assert aioclient_mock.call_count == 1

    # The normal 5 minute interval passes: no request, still backing off.
    await _poll(hass, freezer, minutes=10)
    assert aioclient_mock.call_count == 1

    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await _poll(hass, freezer, minutes=25)
    assert aioclient_mock.call_count == 1
    assert coordinator.last_update_success


async def test_429_short_retry_after_never_faster_than_interval(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A tiny Retry-After does not make polling faster than five minutes."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    mock_parcels(aioclient_mock, status=429, headers={"Retry-After": "5"})
    await _poll(hass, freezer)
    assert mock_config_entry.runtime_data.last_exception.retry_after == 300


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_server_errors_make_entities_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    status: int,
) -> None:
    """5xx (503 included) is transient: unavailable, no reauth, then recovery."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)

    mock_parcels(aioclient_mock, status=status)
    await _poll(hass, freezer)
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.boxblip_on_the_way").state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress()

    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await _poll(hass, freezer)
    assert hass.states.get(SENSOR).state == "in_transit"


async def test_timeout_is_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Timeouts mark entities unavailable."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        "https://api-boxblip.daniele-messi.com/v1/ha/parcels", exc=TimeoutError()
    )
    await _poll(hass, freezer)
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE


async def test_bad_body_is_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A body without a parcels list is an error, not empty data."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    mock_parcels(aioclient_mock, {"oops": True})
    await _poll(hass, freezer)
    assert not mock_config_entry.runtime_data.last_update_success


async def test_events_on_status_change(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Events fire on changes after the first refresh only."""
    events = async_capture_events(hass, EVENT_PARCEL_UPDATED)
    mock_parcels(
        aioclient_mock,
        make_body([make_parcel("p1"), make_parcel("p2", archived=True)]),
    )
    await setup_entry(hass, mock_config_entry)
    assert events == []

    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel(
                    "p1",
                    "out_for_delivery",
                    eta={
                        "start": "2026-11-18T08:00:00Z",
                        "end": "2026-11-18T12:00:00Z",
                        "has_time": True,
                    },
                ),
                make_parcel("p2", "delivered", archived=True),
                make_parcel("p3", "info_received"),
            ]
        ),
        etag='"v2"',
    )
    await _poll(hass, freezer)
    assert len(events) == 2
    by_id = {e.data["parcel_id"]: e.data for e in events}
    assert by_id["p1"] == {
        "config_entry_id": mock_config_entry.entry_id,
        "parcel_id": "p1",
        "name": "Name p1",
        "display_name": "Name p1",
        "tracking_number": "TRKp1",
        "carrier": "BRT",
        "carrier_code": "brt",
        "old_status": "in_transit",
        "new_status": "out_for_delivery",
        "status_text": "Out For Delivery",
        "last_event": {
            "time": "2026-11-18T08:00:00Z",
            "text": "Arrived at depot",
            "location": "Milano",
        },
        "eta": {
            "start": "2026-11-18T08:00:00Z",
            "end": "2026-11-18T12:00:00Z",
            "has_time": True,
        },
        "tracking_url": "https://carrier.example/track/p1",
        "outgoing": False,
        # A server that does not send the account (or a parcel without one).
        "account": None,
    }
    assert by_id["p3"]["old_status"] is None
    assert by_id["p3"]["new_status"] == "info_received"

    # Same statuses again: nothing new.
    await _poll(hass, freezer)
    assert len(events) == 2


def test_api_language() -> None:
    """HA languages map to what the server supports."""
    assert api_language("it") == "it"
    assert api_language("en-GB") == "en"
    assert api_language("pt-BR") == "en"
    assert api_language("de_CH") == "de"
    assert api_language(None) == "en"


def test_parsing_is_defensive() -> None:
    """Junk in the body does not break parsing."""
    response = ParcelsResponse.from_dict(
        {
            "parcels": [
                {"id": "x", "status": "teleported", "carrier": "nope", "eta": 3},
                {"no_id": True},
                "junk",
            ],
            "summary": {"on_the_way": -1, "problems": "2"},
        },
        None,
    )
    parcel = response.parcels["x"]
    assert isinstance(parcel, Parcel)
    assert parcel.status is None
    assert parcel.display_name == "x"
    assert parcel.carrier_name is None
    assert response.summary == Summary()
    assert response.delivered_days is None
