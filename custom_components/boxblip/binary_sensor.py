"""Binary sensor for BoxBlip: is a delivery expected today?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import BoxBlipConfigEntry, BoxBlipCoordinator
from .entity import BoxBlipEntity

PARALLEL_UPDATES = 0

# Statuses in which a parcel is still travelling towards the recipient.
INCOMING_STATUSES = frozenset(
    {"pending", "info_received", "in_transit", "out_for_delivery"}
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoxBlipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor."""
    async_add_entities([BoxBlipDeliveryTodaySensor(entry.runtime_data)])


class BoxBlipDeliveryTodaySensor(BoxBlipEntity, BinarySensorEntity):
    """On when an incoming parcel is out for delivery or its ETA window ends today."""

    _attr_translation_key = "delivery_today"

    def __init__(self, coordinator: BoxBlipCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "delivery_today")

    @property
    def is_on(self) -> bool:
        """Return True when something should arrive today."""
        today = dt_util.now().date()
        for parcel in self.coordinator.data.visible_parcels.values():
            if parcel.outgoing or parcel.status not in INCOMING_STATUSES:
                continue
            if parcel.status == "out_for_delivery":
                return True
            if parcel.eta_end is not None and (
                dt_util.as_local(parcel.eta_end).date() == today
            ):
                return True
        return False
