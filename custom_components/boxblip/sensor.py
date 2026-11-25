"""Sensors for BoxBlip.

Account device: the counts (on the way also per store account), the next expected
delivery and when BoxBlip was last read.
Each parcel's device: its status plus, once the server has them, its expected
delivery, last update, last event, last place, carrier and tracking number.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .api import Parcel, Summary
from .const import DOMAIN, STATUSES
from .coordinator import BoxBlipConfigEntry, BoxBlipCoordinator, BoxBlipData
from .entity import BoxBlipEntity, BoxBlipParcelEntity

# Read-only, coordinator-driven: no limit on parallel updates.
PARALLEL_UPDATES = 0

# Home Assistant states are at most 255 characters.
MAX_STATE_LENGTH = 255

# Statuses in which a parcel is still travelling towards the recipient.
INCOMING_STATUSES = frozenset(
    {"pending", "info_received", "in_transit", "out_for_delivery"}
)


def parcels_by_account(data: BoxBlipData) -> dict[str, int]:
    """Parcels on the way per store account, for parcels that have one."""
    counts: dict[str, int] = {}
    for parcel in data.visible_parcels.values():
        if parcel.account is not None and parcel.active:
            counts[parcel.account] = counts.get(parcel.account, 0) + 1
    return dict(sorted(counts.items()))


@dataclass(frozen=True, kw_only=True)
class BoxBlipSummarySensorDescription(SensorEntityDescription):
    """Describes one count from the summary."""

    value_fn: Callable[[Summary], int]
    attrs_fn: Callable[[BoxBlipData], dict[str, Any]] | None = None


SUMMARY_SENSORS: tuple[BoxBlipSummarySensorDescription, ...] = (
    BoxBlipSummarySensorDescription(
        key="on_the_way",
        translation_key="on_the_way",
        value_fn=lambda s: s.on_the_way,
        attrs_fn=lambda d: {"by_account": parcels_by_account(d)},
    ),
    BoxBlipSummarySensorDescription(
        key="out_for_delivery",
        translation_key="out_for_delivery",
        value_fn=lambda s: s.out_for_delivery,
    ),
    BoxBlipSummarySensorDescription(
        key="to_pick_up",
        translation_key="to_pick_up",
        value_fn=lambda s: s.to_pick_up,
    ),
    BoxBlipSummarySensorDescription(
        key="delivered_today",
        translation_key="delivered_today",
        value_fn=lambda s: s.delivered_today,
    ),
    BoxBlipSummarySensorDescription(
        key="problems",
        translation_key="problems",
        value_fn=lambda s: s.problems,
    ),
)


def _truncate(text: str | None) -> str | None:
    if text is None or len(text) <= MAX_STATE_LENGTH:
        return text
    return text[: MAX_STATE_LENGTH - 1] + "…"


def _eta_date(parcel: Parcel) -> date | None:
    """The last day of the parcel's delivery window, in Home Assistant's time zone."""
    if parcel.eta_end is None:
        return None
    return dt_util.as_local(parcel.eta_end).date()


def expected_parcels(data: BoxBlipData) -> list[Parcel]:
    """Incoming parcels with an expected delivery window, soonest first."""
    parcels = [
        p
        for p in data.visible_parcels.values()
        if not p.outgoing
        and p.status in INCOMING_STATUSES
        and p.eta_start is not None
        and p.eta_end is not None
    ]
    return sorted(parcels, key=lambda p: (p.eta_end or p.eta_start, p.display_name))


@dataclass(frozen=True, kw_only=True)
class BoxBlipParcelSensorDescription(SensorEntityDescription):
    """Describes one detail of a parcel, with its own entity."""

    value_fn: Callable[[Parcel], StateType | date | datetime]
    # The entity is created the first time this is true, so parcels never get
    # entities for details the server has no data for.
    exists_fn: Callable[[Parcel], bool]
    attrs_fn: Callable[[Parcel], dict[str, Any]] | None = None


PARCEL_SENSORS: tuple[BoxBlipParcelSensorDescription, ...] = (
    BoxBlipParcelSensorDescription(
        key="expected_delivery",
        translation_key="expected_delivery",
        device_class=SensorDeviceClass.DATE,
        value_fn=_eta_date,
        exists_fn=lambda p: p.eta_end is not None,
        attrs_fn=lambda p: {
            "window_start": p.eta_start,
            "window_end": p.eta_end,
            "has_time": p.eta_has_time,
        },
    ),
    BoxBlipParcelSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: p.last_event_time,
        exists_fn=lambda p: p.last_event_time is not None,
    ),
    BoxBlipParcelSensorDescription(
        key="last_event",
        translation_key="last_event",
        value_fn=lambda p: _truncate(p.last_event_text),
        exists_fn=lambda p: p.last_event_text is not None,
    ),
    BoxBlipParcelSensorDescription(
        key="location",
        translation_key="location",
        value_fn=lambda p: _truncate(p.last_event_location),
        exists_fn=lambda p: p.last_event_location is not None,
    ),
    BoxBlipParcelSensorDescription(
        key="carrier",
        translation_key="carrier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.carrier_name,
        exists_fn=lambda p: p.carrier_name is not None,
    ),
    BoxBlipParcelSensorDescription(
        key="tracking_number",
        translation_key="tracking_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: _truncate(p.tracking_number),
        exists_fn=lambda p: p.tracking_number is not None,
        attrs_fn=lambda p: {"tracking_url": p.tracking_url},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoxBlipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors and keep parcel sensors in sync with the server list.

    Removing what left the list is done once for all platforms in __init__.py.
    """
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                BoxBlipSummarySensor(coordinator, description)
                for description in SUMMARY_SENSORS
            ),
            BoxBlipNextDeliverySensor(coordinator),
            BoxBlipLastReadSensor(coordinator),
        ]
    )

    known: set[tuple[str, str | None]] = set()

    @callback
    def _async_add_parcel_sensors() -> None:
        parcels = coordinator.data.visible_parcels
        known.difference_update([k for k in known if k[0] not in parcels])
        new: list[SensorEntity] = []
        for parcel_id in sorted(parcels):
            parcel = parcels[parcel_id]
            if (parcel_id, None) not in known:
                known.add((parcel_id, None))
                new.append(BoxBlipParcelStatusSensor(coordinator, parcel_id))
            for description in PARCEL_SENSORS:
                key = (parcel_id, description.key)
                if key not in known and description.exists_fn(parcel):
                    known.add(key)
                    new.append(
                        BoxBlipParcelDetailSensor(coordinator, parcel_id, description)
                    )
        if new:
            async_add_entities(new)

    _async_add_parcel_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_parcel_sensors))


class BoxBlipSummarySensor(BoxBlipEntity, SensorEntity):
    """A count of parcels in one state across the account."""

    entity_description: BoxBlipSummarySensorDescription
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: BoxBlipCoordinator,
        description: BoxBlipSummarySensorDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        """Return the count."""
        return self.entity_description.value_fn(self.coordinator.data.response.summary)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra details, for the counts that have some."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class BoxBlipNextDeliverySensor(BoxBlipEntity, SensorEntity):
    """The day the next incoming parcel is expected by."""

    _attr_device_class = SensorDeviceClass.DATE
    _attr_translation_key = "next_delivery"

    def __init__(self, coordinator: BoxBlipCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "next_delivery")
        # The same id in every language, so dashboards can name it.
        self.entity_id = f"{Platform.SENSOR}.{DOMAIN}_next_delivery"

    def _next(self) -> list[Parcel]:
        parcels = expected_parcels(self.coordinator.data)
        if not parcels:
            return []
        first = _eta_date(parcels[0])
        return [p for p in parcels if _eta_date(p) == first]

    @property
    def native_value(self) -> date | None:
        """Return the date."""
        parcels = self._next()
        return _eta_date(parcels[0]) if parcels else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which parcels are expected that day."""
        return {"parcels": [p.display_name for p in self._next()]}


class BoxBlipLastReadSensor(BoxBlipEntity, SensorEntity):
    """When BoxBlip last answered (also when nothing had changed)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_read"

    def __init__(self, coordinator: BoxBlipCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "last_read")
        self.entity_id = f"{Platform.SENSOR}.{DOMAIN}_last_read"

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last successful read."""
        return self.coordinator.last_read


class BoxBlipParcelStatusSensor(BoxBlipParcelEntity, SensorEntity):
    """The status of one parcel: the main entity of its device.

    Its unique id is the one parcel sensors had before parcel devices, so existing
    entities, their ids and automations carry over. The attributes stay as well.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "parcel"
    # The device's own name: "Shoes", not "Shoes Parcel".
    _attr_name = None

    def __init__(self, coordinator: BoxBlipCoordinator, parcel_id: str) -> None:
        """Initialise."""
        super().__init__(coordinator, parcel_id, None, Platform.SENSOR)
        self._attr_options = list(STATUSES)

    @property
    def native_value(self) -> str | None:
        """Return the status slug."""
        parcel = self._parcel
        return parcel.status if parcel else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return parcel details."""
        if (parcel := self._parcel) is None:
            return None
        return {
            "name": parcel.name,
            "display_name": parcel.display_name,
            "status_text": parcel.status_text,
            "carrier": parcel.carrier_name,
            "carrier_code": parcel.carrier_code,
            "tracking_number": parcel.tracking_number,
            "last_event": parcel.last_event_text,
            "last_event_time": parcel.last_event_time,
            "last_event_location": parcel.last_event_location,
            "eta_start": parcel.eta_start,
            "eta_end": parcel.eta_end,
            "tracking_url": parcel.tracking_url,
            "outgoing": parcel.outgoing,
            "shared": parcel.shared,
            "account": parcel.account,
            "added_at": parcel.added_at,
            "delivered_at": parcel.delivered_at,
        }


class BoxBlipParcelDetailSensor(BoxBlipParcelEntity, SensorEntity):
    """One detail of a parcel."""

    entity_description: BoxBlipParcelSensorDescription

    def __init__(
        self,
        coordinator: BoxBlipCoordinator,
        parcel_id: str,
        description: BoxBlipParcelSensorDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, parcel_id, description.key, Platform.SENSOR)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | date | datetime:
        """Return the detail, None when the server no longer has it."""
        if (parcel := self._parcel) is None:
            return None
        return self.entity_description.value_fn(parcel)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra details, for the descriptions that have some."""
        if (parcel := self._parcel) is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(parcel)
