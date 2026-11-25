"""Entities: parcel sensors, counts, delivery today, refresh action."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.const import DOMAIN
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import make_body, make_parcel, mock_parcels, setup_entry


def _parcel_entities(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    registry = er.async_get(hass)
    return {
        e.unique_id.removeprefix(f"{entry.entry_id}_parcel_")
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if "_parcel_" in e.unique_id and "-" not in e.unique_id
    }


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_parcel_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """One enum sensor per parcel: the main entity of the parcel's own device."""
    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel(
                    "p1",
                    "available_for_pickup",
                    eta={
                        "start": "2026-11-18T08:00:00Z",
                        "end": "2026-11-19T18:00:00Z",
                        "has_time": False,
                    },
                    shared=True,
                    account="Amazon · Casa",
                )
            ]
        ),
    )
    await setup_entry(hass, mock_config_entry)

    state = hass.states.get("sensor.boxblip_name_p1")
    assert state.state == "available_for_pickup"
    assert state.attributes["options"][0] == "pending"
    assert len(state.attributes["options"]) == 9
    assert state.attributes["friendly_name"] == "Name p1"
    assert state.attributes["carrier"] == "BRT"
    assert state.attributes["carrier_code"] == "brt"
    assert state.attributes["tracking_number"] == "TRKp1"
    assert state.attributes["last_event"] == "Arrived at depot"
    assert state.attributes["last_event_location"] == "Milano"
    assert state.attributes["last_event_time"].isoformat() == (
        "2026-11-18T08:00:00+00:00"
    )
    assert state.attributes["eta_end"].isoformat() == "2026-11-19T18:00:00+00:00"
    assert state.attributes["shared"] is True
    assert state.attributes["account"] == "Amazon · Casa"
    assert state.attributes["outgoing"] is False
    assert state.attributes["delivered_at"] is None

    registry = er.async_get(hass)
    entity = registry.async_get("sensor.boxblip_name_p1")
    assert entity.unique_id == f"{mock_config_entry.entry_id}_parcel_p1"
    devices = dr.async_get(hass)
    device = devices.async_get(entity.device_id)
    assert device.entry_type is None
    assert device.name == "Name p1"
    account = devices.async_get(device.via_device_id)
    assert account.entry_type is dr.DeviceEntryType.SERVICE
    assert account.name == "BoxBlip"


async def test_parcel_lifecycle(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Parcels appear, get archived or leave the list, and their sensors follow."""
    mock_parcels(
        aioclient_mock,
        make_body([make_parcel("p1"), make_parcel("p2", archived=True)]),
    )
    await setup_entry(hass, mock_config_entry)
    assert _parcel_entities(hass, mock_config_entry) == {"p1"}

    # p3 appears, p1 is delivered (stays while in the window), renamed.
    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel("p1", "delivered", display_name="Shoes"),
                make_parcel("p3"),
            ]
        ),
        etag='"v2"',
    )
    await _poll(hass, freezer)
    assert _parcel_entities(hass, mock_config_entry) == {"p1", "p3"}
    state = hass.states.get("sensor.boxblip_name_p1")
    assert state.state == "delivered"
    assert state.attributes["friendly_name"] == "Shoes"
    assert hass.states.get("sensor.boxblip_name_p3").state == "in_transit"

    # p1 leaves the delivered window, p3 gets archived.
    mock_parcels(
        aioclient_mock, make_body([make_parcel("p3", archived=True)]), etag='"v3"'
    )
    await _poll(hass, freezer)
    assert _parcel_entities(hass, mock_config_entry) == set()
    assert hass.states.get("sensor.boxblip_name_p1") is None
    assert hass.states.get("sensor.boxblip_name_p3") is None

    # Unarchived again: a sensor comes back.
    mock_parcels(aioclient_mock, make_body([make_parcel("p3")]), etag='"v4"')
    await _poll(hass, freezer)
    assert _parcel_entities(hass, mock_config_entry) == {"p3"}
    assert hass.states.get("sensor.boxblip_name_p3").state == "in_transit"


async def test_stale_entities_removed_at_startup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sensors for parcels that vanished while Home Assistant was off are removed."""
    mock_config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_parcel_gone",
        config_entry=mock_config_entry,
    )
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _parcel_entities(hass, mock_config_entry) == {"p1"}


async def test_unknown_status_is_unknown_state(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A status this version does not know becomes 'unknown', not an error."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1", "teleported")]))
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("sensor.boxblip_name_p1").state == "unknown"


async def test_summary_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Counts come from the server summary."""
    mock_parcels(
        aioclient_mock,
        make_body(
            [],
            on_the_way=2,
            out_for_delivery=1,
            to_pick_up=3,
            delivered_today=4,
            problems=5,
        ),
    )
    await setup_entry(hass, mock_config_entry)
    expected = {
        "on_the_way": "2",
        "out_for_delivery": "1",
        "to_pick_up": "3",
        "delivered_today": "4",
        "problems": "5",
    }
    for key, value in expected.items():
        state = hass.states.get(f"sensor.boxblip_{key}")
        assert state.state == value, key
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "parcels"
        assert state.attributes["state_class"] == "measurement"
    assert hass.states.get("sensor.boxblip_on_the_way").attributes["by_account"] == {}


async def test_parcels_by_account(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Parcels on the way per store account; parcels without one keep working."""
    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel("c1", account="Amazon · Casa"),
                make_parcel("c2", "out_for_delivery", account="Amazon · Casa"),
                make_parcel("u1", account="Amazon · Ufficio"),
                make_parcel("u2", "delivered", account="Amazon · Ufficio"),
                make_parcel("u3", account="Amazon · Ufficio", archived=True),
                make_parcel("h1"),
                make_parcel("h2", account=""),
            ],
            on_the_way=4,
        ),
    )
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("sensor.boxblip_on_the_way")
    assert state.state == "4"
    assert state.attributes["by_account"] == {
        "Amazon · Casa": 2,
        "Amazon · Ufficio": 1,
    }
    assert hass.states.get("sensor.boxblip_name_u2").attributes["account"] == (
        "Amazon · Ufficio"
    )
    assert hass.states.get("sensor.boxblip_name_h1").attributes["account"] is None
    assert hass.states.get("sensor.boxblip_name_h2").attributes["account"] is None


@pytest.mark.parametrize(
    ("parcels", "expected"),
    [
        ([], "off"),
        ([make_parcel("p1")], "off"),
        ([make_parcel("p1", "out_for_delivery")], "on"),
        ([make_parcel("p1", "out_for_delivery", outgoing=True)], "off"),
        (
            [
                make_parcel(
                    "p1",
                    "in_transit",
                    eta={
                        "start": "2026-11-18T07:00:00Z",
                        "end": "2026-11-18T17:00:00Z",
                        "has_time": False,
                    },
                )
            ],
            "on",
        ),
        (
            [
                make_parcel(
                    "p1",
                    "in_transit",
                    eta={
                        "start": "2026-11-19T07:00:00Z",
                        "end": "2026-11-19T17:00:00Z",
                        "has_time": False,
                    },
                )
            ],
            "off",
        ),
        (
            [
                make_parcel(
                    "p1",
                    "available_for_pickup",
                    eta={
                        "start": "2026-11-18T07:00:00Z",
                        "end": "2026-11-18T17:00:00Z",
                        "has_time": False,
                    },
                )
            ],
            "off",
        ),
        ([make_parcel("p1", "out_for_delivery", archived=True)], "off"),
    ],
)
@pytest.mark.freeze_time("2026-11-18T10:00:00+00:00")
async def test_delivery_today(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    parcels: list[dict],
    expected: str,
) -> None:
    """On for out_for_delivery or an ETA ending today (HA time zone)."""
    await hass.config.async_update(time_zone="Europe/Rome")
    mock_parcels(aioclient_mock, make_body(parcels))
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("binary_sensor.boxblip_delivery_today").state == expected


async def test_refresh_action(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """boxblip.refresh polls now, for all entries or one."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    assert aioclient_mock.call_count == 1

    mock_parcels(aioclient_mock, make_body([make_parcel("p1", "delivered")]))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert aioclient_mock.call_count == 1
    assert hass.states.get("sensor.boxblip_name_p1").state == "delivered"

    await hass.services.async_call(
        DOMAIN,
        "refresh",
        {"config_entry_id": mock_config_entry.entry_id},
        blocking=True,
    )
    assert aioclient_mock.call_count == 2

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "refresh", {"config_entry_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "entry_not_found"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "refresh",
            {"config_entry_id": mock_config_entry.entry_id},
            blocking=True,
        )
    assert err.value.translation_key == "entry_not_loaded"
