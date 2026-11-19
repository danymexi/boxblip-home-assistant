"""Data update coordinator for BoxBlip."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AuthError,
    BoxBlipClient,
    CannotConnect,
    Parcel,
    ParcelsResponse,
    RateLimited,
    ServerDisabled,
)
from .const import (
    CONF_DELIVERED_DAYS,
    DEFAULT_DELIVERED_DAYS,
    DOMAIN,
    EVENT_PARCEL_UPDATED,
    LOGGER,
    SUPPORTED_LANGUAGES,
    UPDATE_INTERVAL,
)

if TYPE_CHECKING:
    from .push import BoxBlipPush

type BoxBlipConfigEntry = ConfigEntry[BoxBlipCoordinator]


def api_language(language: str | None) -> str:
    """Map Home Assistant's language (e.g. 'it', 'en-GB') to one the server knows."""
    primary = (language or "en").split("-")[0].split("_")[0].lower()
    return primary if primary in SUPPORTED_LANGUAGES else "en"


@dataclass(frozen=True, slots=True)
class BoxBlipData:
    """What the entities read."""

    response: ParcelsResponse

    @property
    def parcels(self) -> dict[str, Parcel]:
        """All parcels the server returned, archived included."""
        return self.response.parcels

    @property
    def visible_parcels(self) -> dict[str, Parcel]:
        """Parcels that get a sensor: everything except archived ones."""
        return {pid: p for pid, p in self.response.parcels.items() if not p.archived}


class BoxBlipCoordinator(DataUpdateCoordinator[BoxBlipData]):
    """Polls /v1/ha/parcels every five minutes (sooner when pushed)."""

    config_entry: BoxBlipConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BoxBlipConfigEntry,
        client: BoxBlipClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.push: BoxBlipPush | None = None
        self._etag: str | None = None
        # When the server last answered, also with 304 Not Modified.
        self.last_read: datetime | None = None

    @property
    def language(self) -> str:
        """Language for status texts."""
        return api_language(self.hass.config.language)

    async def _async_update_data(self) -> BoxBlipData:
        """Fetch parcels, keeping the previous data on 304."""
        delivered_days = int(
            self.config_entry.options.get(CONF_DELIVERED_DAYS, DEFAULT_DELIVERED_DAYS)
        )
        previous = self.data
        try:
            response = await self.client.async_get_parcels(
                lang=self.language,
                tz=str(self.hass.config.time_zone),
                delivered_days=delivered_days,
                etag=self._etag if previous is not None else None,
            )
        except AuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except RateLimited as err:
            interval = UPDATE_INTERVAL.total_seconds()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
                retry_after=max(err.retry_after or interval, interval),
            ) from err
        except ServerDisabled as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="server_disabled"
            ) from err
        except CannotConnect as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        self.last_read = dt_util.utcnow()
        if response is None:
            # 304 Not Modified: nothing changed since the last ETag.
            return previous

        self._etag = response.etag
        new = BoxBlipData(response)
        if previous is not None:
            self._fire_status_events(previous, new)
        return new

    def _fire_status_events(self, old: BoxBlipData, new: BoxBlipData) -> None:
        """Fire boxblip_parcel_updated for each status change since the last poll.

        A parcel that was not in the previous list fires with old_status None.
        Archived parcels and parcels that disappear never fire.
        """
        for parcel_id, parcel in new.visible_parcels.items():
            before = old.parcels.get(parcel_id)
            old_status = before.status if before is not None else None
            if before is not None and old_status == parcel.status:
                continue
            self.hass.bus.async_fire(
                EVENT_PARCEL_UPDATED, self._event_data(parcel, old_status)
            )

    def _event_data(self, parcel: Parcel, old_status: str | None) -> dict[str, Any]:
        raw = parcel.raw
        return {
            "config_entry_id": self.config_entry.entry_id,
            "parcel_id": parcel.id,
            "name": parcel.name,
            "display_name": parcel.display_name,
            "tracking_number": parcel.tracking_number,
            "carrier": parcel.carrier_name,
            "carrier_code": parcel.carrier_code,
            "old_status": old_status,
            "new_status": parcel.status,
            "status_text": parcel.status_text,
            "last_event": raw.get("last_event"),
            "eta": raw.get("eta"),
            "tracking_url": parcel.tracking_url,
            "outgoing": parcel.outgoing,
        }
