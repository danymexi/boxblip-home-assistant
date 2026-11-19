"""Parcel devices, their entities, the upgrade from one-device versions, the calendar."""

from __future__ import annotations

from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip import async_remove_config_entry_device
from custom_components.boxblip.const import DOMAIN
from custom_components.boxblip.entity import parcel_model
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import make_body, make_parcel, mock_parcels, setup_entry

ENTRY = "01BOXBLIPTESTENTRY"
DAY_ETA = {
    "start": "2026-11-19T11:00:00Z",
    "end": "2026-11-20T11:00:00Z",
    "has_time": False,
}
TIMED_ETA = {
    "start": "2026-11-18T09:15:00Z",
    "end": "2026-11-18T11:15:00Z",
    "has_time": True,
}


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _parcel_devices(hass: HomeAssistant) -> dict[str, dr.DeviceEntry]:
    """Parcel devices by name."""
    return {
        device.name: device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), ENTRY)
        if device.entry_type is None
    }


def _entity_ids(hass: HomeAssistant, device: dr.DeviceEntry) -> set[str]:
    return {
        e.entity_id for e in er.async_entries_for_device(er.async_get(hass), device.id)
    }


@pytest.mark.freeze_time("2026-11-18T10:00:00+00:00")
async def test_parcel_device_and_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A parcel is a device under the account, with one entity per known detail."""
    await hass.config.async_update(time_zone="Europe/Rome")
    caplog.set_level(logging.WARNING)
    mock_parcels(aioclient_mock, make_body([make_parcel("p1", eta=DAY_ETA)]))
    await setup_entry(hass, mock_config_entry)

    devices = _parcel_devices(hass)
    device = devices["Name p1"]
    assert device.manufacturer == "BRT"
    assert device.model == "Parcel"
    assert device.configuration_url == "https://carrier.example/track/p1"
    account = dr.async_get(hass).async_get(device.via_device_id)
    assert account.identifiers == {(DOMAIN, ENTRY)}
    assert account.entry_type is dr.DeviceEntryType.SERVICE

    assert _entity_ids(hass, device) == {
        "sensor.boxblip_name_p1",
        "sensor.boxblip_name_p1_expected_delivery",
        "sensor.boxblip_name_p1_last_update",
        "sensor.boxblip_name_p1_last_event",
        "sensor.boxblip_name_p1_location",
        "sensor.boxblip_name_p1_carrier",
        "sensor.boxblip_name_p1_tracking_number",
    }
    expected = hass.states.get("sensor.boxblip_name_p1_expected_delivery")
    assert expected.state == "2026-11-20"
    assert expected.attributes["friendly_name"] == "Name p1 Expected by"
    assert expected.attributes["device_class"] == "date"
    assert expected.attributes["has_time"] is False
    last_update = hass.states.get("sensor.boxblip_name_p1_last_update")
    assert last_update.state == "2026-11-18T08:00:00+00:00"
    assert last_update.attributes["device_class"] == "timestamp"
    assert hass.states.get("sensor.boxblip_name_p1_last_event").state == (
        "Arrived at depot"
    )
    assert hass.states.get("sensor.boxblip_name_p1_location").state == "Milano"
    assert hass.states.get("sensor.boxblip_name_p1_carrier").state == "BRT"
    tracking = hass.states.get("sensor.boxblip_name_p1_tracking_number")
    assert tracking.state == "TRKp1"
    assert tracking.attributes["tracking_url"] == "https://carrier.example/track/p1"

    registry = er.async_get(hass)
    for key in ("carrier", "tracking_number"):
        entry = registry.async_get(f"sensor.boxblip_name_p1_{key}")
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert entry.unique_id == f"{ENTRY}_parcel_p1-{key}"
    assert (
        registry.async_get("sensor.boxblip_name_p1").unique_id == f"{ENTRY}_parcel_p1"
    )

    # Nothing BoxBlip does should make Home Assistant complain.
    # (The loader's generic "custom integration" notice aside.)
    assert not [
        r
        for r in caplog.records
        if "boxblip" in r.getMessage().lower() and r.name != "homeassistant.loader"
    ]


async def test_details_appear_only_once_known(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No entities for details the server has no data for; they come when it does."""
    mock_parcels(
        aioclient_mock,
        make_body(
            [make_parcel("p1", "pending", last_event=None, carrier={}, eta=None)]
        ),
    )
    await setup_entry(hass, mock_config_entry)
    device = _parcel_devices(hass)["Name p1"]
    assert device.manufacturer == "BoxBlip"
    assert _entity_ids(hass, device) == {
        "sensor.boxblip_name_p1",
        "sensor.boxblip_name_p1_tracking_number",
    }

    mock_parcels(
        aioclient_mock,
        make_body([make_parcel("p1", eta=DAY_ETA)]),
        etag='"v2"',
    )
    await _poll(hass, freezer)
    assert len(_entity_ids(hass, device)) == 7
    assert _parcel_devices(hass)["Name p1"].manufacturer == "BRT"

    # The estimate goes away (e.g. delivered): the entity stays, without a value.
    mock_parcels(
        aioclient_mock, make_body([make_parcel("p1", "delivered")]), etag='"v3"'
    )
    await _poll(hass, freezer)
    assert len(_entity_ids(hass, device)) == 7
    assert hass.states.get("sensor.boxblip_name_p1_expected_delivery").state == (
        "unknown"
    )


async def test_device_follows_the_parcel(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Renamed parcels rename their device; parcels that leave remove it."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1"), make_parcel("p2")]))
    await setup_entry(hass, mock_config_entry)
    assert set(_parcel_devices(hass)) == {"Name p1", "Name p2"}

    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel(
                    "p1",
                    display_name="Shoes",
                    carrier={"code": "gls", "name": "GLS"},
                    outgoing=True,
                )
            ]
        ),
        etag='"v2"',
    )
    await _poll(hass, freezer)
    devices = _parcel_devices(hass)
    assert set(devices) == {"Shoes"}
    assert devices["Shoes"].manufacturer == "GLS"
    assert devices["Shoes"].model == "Outgoing parcel"
    assert hass.states.get("sensor.boxblip_name_p1").attributes["friendly_name"] == (
        "Shoes"
    )
    assert (
        hass.states.get("sensor.boxblip_name_p1_last_event").attributes["friendly_name"]
        == "Shoes Last event"
    )
    assert hass.states.get("sensor.boxblip_name_p2") is None
    assert hass.states.get("sensor.boxblip_name_p2_carrier") is None
    assert not [
        e
        for e in er.async_entries_for_config_entry(er.async_get(hass), ENTRY)
        if "_parcel_p2" in e.unique_id
    ]

    mock_parcels(aioclient_mock, make_body([]), etag='"v3"')
    await _poll(hass, freezer)
    assert _parcel_devices(hass) == {}
    # The account device and its entities stay.
    assert hass.states.get("sensor.boxblip_on_the_way").state == "0"


async def test_stale_devices_removed_at_startup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Parcels that left the list while Home Assistant was off lose their device."""
    mock_config_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    gone = devices.async_get_or_create(
        config_entry_id=ENTRY,
        identifiers={(DOMAIN, f"{ENTRY}_parcel_gone")},
        name="Gone",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{ENTRY}_parcel_gone-carrier",
        config_entry=mock_config_entry,
        device_id=gone.id,
    )
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await hass.config_entries.async_setup(ENTRY)
    await hass.async_block_till_done()
    assert set(_parcel_devices(hass)) == {"Name p1"}
    assert not [
        e
        for e in er.async_entries_for_config_entry(er.async_get(hass), ENTRY)
        if "gone" in e.unique_id
    ]


async def test_upgrade_keeps_entity_ids(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Parcel sensors made by 0.1 (one device for everything) keep their ids.

    They move to their parcel's device; nothing is duplicated and the counters keep
    the ids they had in the user's language.
    """
    mock_config_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    account = devices.async_get_or_create(
        config_entry_id=ENTRY,
        identifiers={(DOMAIN, ENTRY)},
        entry_type=dr.DeviceEntryType.SERVICE,
        manufacturer="BoxBlip",
        model="Parcel tracker",
        name="BoxBlip",
    )
    registry = er.async_get(hass)
    old = {
        f"{ENTRY}_parcel_p1": ("sensor", "boxblip_scarpe"),
        f"{ENTRY}_on_the_way": ("sensor", "boxblip_in_arrivo"),
        f"{ENTRY}_delivery_today": ("binary_sensor", "boxblip_consegna_oggi"),
    }
    for unique_id, (domain, object_id) in old.items():
        registry.async_get_or_create(
            domain,
            DOMAIN,
            unique_id,
            config_entry=mock_config_entry,
            device_id=account.id,
            suggested_object_id=object_id,
            has_entity_name=True,
        )
    before = {e.entity_id for e in er.async_entries_for_config_entry(registry, ENTRY)}

    mock_parcels(aioclient_mock, make_body([make_parcel("p1", display_name="Scarpe")]))
    await hass.config_entries.async_setup(ENTRY)
    await hass.async_block_till_done()

    after = {e.entity_id for e in er.async_entries_for_config_entry(registry, ENTRY)}
    assert before <= after
    assert not [e for e in after if e.endswith("_2")]
    status = registry.async_get("sensor.boxblip_scarpe")
    parcel_device = devices.async_get(status.device_id)
    assert parcel_device.name == "Scarpe"
    assert parcel_device.via_device_id == account.id
    assert hass.states.get("sensor.boxblip_scarpe").state == "in_transit"
    assert hass.states.get("sensor.boxblip_scarpe").attributes["friendly_name"] == (
        "Scarpe"
    )
    assert registry.async_get("sensor.boxblip_in_arrivo").device_id == account.id
    assert hass.states.get("binary_sensor.boxblip_consegna_oggi") is not None
    # Exactly one status sensor for the parcel.
    assert [
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, ENTRY)
        if e.unique_id == f"{ENTRY}_parcel_p1"
    ] == ["sensor.boxblip_scarpe"]
    # One account device, one parcel device.
    assert len(dr.async_entries_for_config_entry(devices, ENTRY)) == 2


async def test_user_can_delete_only_stale_parcel_devices(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The account device and current parcels cannot be deleted by hand."""
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    devices = dr.async_get(hass)
    parcel = _parcel_devices(hass)["Name p1"]
    account = devices.async_get(parcel.via_device_id)
    orphan = devices.async_get_or_create(
        config_entry_id=ENTRY, identifiers={(DOMAIN, f"{ENTRY}_parcel_old")}
    )
    assert not await async_remove_config_entry_device(hass, mock_config_entry, account)
    assert not await async_remove_config_entry_device(hass, mock_config_entry, parcel)
    assert await async_remove_config_entry_device(hass, mock_config_entry, orphan)


async def test_italian_model(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The device model follows Home Assistant's language."""
    await hass.config.async_update(language="it")
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    await setup_entry(hass, mock_config_entry)
    assert _parcel_devices(hass)["Name p1"].model == "Pacco"
    assert parcel_model("it", outgoing=True) == "Pacco in uscita"
    assert parcel_model("pt-BR", outgoing=False) == "Parcel"


@pytest.mark.freeze_time("2026-11-18T10:00:00+00:00")
async def test_next_delivery_and_last_read(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Next delivery is the soonest incoming window; last read moves on 304 too."""
    await hass.config.async_update(time_zone="Europe/Rome")
    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel("p1", eta=DAY_ETA),
                make_parcel("p2", eta=TIMED_ETA),
                make_parcel("p3", "out_for_delivery", eta=TIMED_ETA, outgoing=True),
                make_parcel("p4", "available_for_pickup", eta=TIMED_ETA),
            ]
        ),
    )
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("sensor.boxblip_next_delivery")
    assert state.state == "2026-11-18"
    assert state.attributes["parcels"] == ["Name p2"]

    last_read = hass.states.get("sensor.boxblip_last_read")
    assert last_read.state == "2026-11-18T10:00:00+00:00"
    entry = er.async_get(hass).async_get("sensor.boxblip_last_read")
    assert entry.entity_category is EntityCategory.DIAGNOSTIC

    mock_parcels(aioclient_mock, status=304, etag='"v1"')
    await _poll(hass, freezer)
    assert hass.states.get("sensor.boxblip_last_read").state == (
        "2026-11-18T10:05:00+00:00"
    )

    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]), etag='"v2"')
    await _poll(hass, freezer)
    assert hass.states.get("sensor.boxblip_next_delivery").state == "unknown"
    assert hass.states.get("sensor.boxblip_next_delivery").attributes["parcels"] == []


@pytest.mark.freeze_time("2026-11-18T10:00:00+00:00")
async def test_calendar(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Delivery windows: timed when the carrier gives times, all-day otherwise."""
    await hass.config.async_update(time_zone="Europe/Rome")
    mock_parcels(
        aioclient_mock,
        make_body(
            [
                make_parcel("p1", eta=DAY_ETA),
                make_parcel("p2", eta=TIMED_ETA),
                make_parcel("p3", "delivered", eta=DAY_ETA),
                make_parcel("p4", outgoing=True, eta=DAY_ETA),
                make_parcel("p5"),
            ]
        ),
    )
    await setup_entry(hass, mock_config_entry)

    state = hass.states.get("calendar.boxblip_deliveries")
    assert state.attributes["friendly_name"] == "BoxBlip Deliveries"
    # 10:00 UTC is 11:00 in Rome: inside p2's 10:15-12:15 window.
    assert state.state == "on"
    assert state.attributes["message"] == "Name p2"
    assert state.attributes["all_day"] is False

    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.boxblip_deliveries",
            "start_date_time": "2026-11-17T00:00:00+01:00",
            "end_date_time": "2026-11-25T00:00:00+01:00",
        },
        blocking=True,
        return_response=True,
    )
    events = result["calendar.boxblip_deliveries"]["events"]
    assert [(e["summary"], e["start"], e["end"]) for e in events] == [
        ("Name p2", "2026-11-18T10:15:00+01:00", "2026-11-18T12:15:00+01:00"),
        ("Name p1", "2026-11-19", "2026-11-21"),
    ]
    assert events[1]["description"].splitlines() == [
        "In Transit",
        "BRT",
        "Arrived at depot",
        "https://carrier.example/track/p1",
    ]

    # Outside every window: nothing.
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.boxblip_deliveries",
            "start_date_time": "2026-11-22T00:00:00+01:00",
            "end_date_time": "2026-11-23T00:00:00+01:00",
        },
        blocking=True,
        return_response=True,
    )
    assert result["calendar.boxblip_deliveries"]["events"] == []
