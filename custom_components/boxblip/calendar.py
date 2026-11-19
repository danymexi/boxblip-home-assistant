"""Calendar for BoxBlip: the expected delivery window of every incoming parcel."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Parcel
from .const import DOMAIN
from .coordinator import BoxBlipConfigEntry, BoxBlipCoordinator
from .entity import BoxBlipEntity
from .sensor import expected_parcels

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoxBlipConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar."""
    async_add_entities([BoxBlipDeliveriesCalendar(entry.runtime_data)])


def parcel_event(parcel: Parcel) -> CalendarEvent | None:
    """The parcel's delivery window as an event.

    A window with times from the carrier ("10:15-12:15") is a timed event. Otherwise
    the window is a range of days and becomes an all-day event over those days.
    """
    if parcel.eta_start is None or parcel.eta_end is None:
        return None
    description = "\n".join(
        part
        for part in (
            parcel.status_text,
            parcel.carrier_name,
            parcel.last_event_text,
            parcel.tracking_url,
        )
        if part
    )
    if parcel.eta_has_time and parcel.eta_end > parcel.eta_start:
        return CalendarEvent(
            start=dt_util.as_local(parcel.eta_start),
            end=dt_util.as_local(parcel.eta_end),
            summary=parcel.display_name,
            description=description or None,
            uid=parcel.id,
        )
    first = dt_util.as_local(parcel.eta_start).date()
    last = max(dt_util.as_local(parcel.eta_end).date(), first)
    return CalendarEvent(
        start=first,
        end=last + timedelta(days=1),
        summary=parcel.display_name,
        description=description or None,
        uid=parcel.id,
    )


def _bounds(event: CalendarEvent) -> tuple[datetime, datetime]:
    """Start and end as aware datetimes (all-day events span local midnights)."""
    return event.start_datetime_local, event.end_datetime_local


class BoxBlipDeliveriesCalendar(BoxBlipEntity, CalendarEntity):
    """Expected deliveries."""

    _attr_translation_key = "deliveries"

    def __init__(self, coordinator: BoxBlipCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "deliveries")
        # The same id in every language, so dashboards can name it.
        self.entity_id = f"{Platform.CALENDAR}.{DOMAIN}_deliveries"

    def _events(self) -> list[CalendarEvent]:
        events = [
            event
            for parcel in expected_parcels(self.coordinator.data)
            if (event := parcel_event(parcel)) is not None
        ]
        return sorted(events, key=lambda e: (_bounds(e), e.summary))

    @property
    def event(self) -> CalendarEvent | None:
        """The delivery window in progress, or the next one."""
        now = dt_util.now()
        upcoming = [e for e in self._events() if _bounds(e)[1] > now]
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Delivery windows that overlap the requested range."""
        return [
            event
            for event in self._events()
            if _bounds(event)[0] < end_date and _bounds(event)[1] > start_date
        ]
