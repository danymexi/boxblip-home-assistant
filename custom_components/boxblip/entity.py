"""Base entities and devices for BoxBlip.

Devices: one service device per account ("BoxBlip") with the counts, the calendar and
the "delivery today" sensor, and one device per parcel, linked to the account device,
with that parcel's entities. Parcel devices come and go with the server's list.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .api import Parcel
from .const import DOMAIN
from .coordinator import BoxBlipConfigEntry, BoxBlipCoordinator

ACCOUNT_MANUFACTURER = "BoxBlip"
ACCOUNT_MODEL = "Parcel tracker"
PARCEL_KEY_PREFIX = "parcel_"
# Separates the parcel id from the entity key in a parcel entity's unique id. The
# status sensor has no key: its unique id predates parcel devices and stays as is.
PARCEL_KEY_SEPARATOR = "-"

# Device "model" shown under a parcel device's name. Device models are not
# translatable, so the integration picks one in Home Assistant's language.
_PARCEL_MODEL: dict[str, tuple[str, str]] = {
    "en": ("Parcel", "Outgoing parcel"),
    "it": ("Pacco", "Pacco in uscita"),
    "fr": ("Colis", "Colis sortant"),
    "de": ("Paket", "Ausgehendes Paket"),
    "es": ("Paquete", "Paquete saliente"),
}


def account_identifier(entry_id: str) -> tuple[str, str]:
    """Identifier of the account (service) device."""
    return (DOMAIN, entry_id)


def parcel_identifier(entry_id: str, parcel_id: str) -> tuple[str, str]:
    """Identifier of a parcel's device."""
    return (DOMAIN, f"{entry_id}_{PARCEL_KEY_PREFIX}{parcel_id}")


def parcel_id_of_device(entry_id: str, device: dr.DeviceEntry) -> str | None:
    """The parcel a device stands for, None for the account device."""
    prefix = f"{entry_id}_{PARCEL_KEY_PREFIX}"
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier.startswith(prefix):
            return identifier.removeprefix(prefix)
    return None


def parcel_unique_id(entry_id: str, parcel_id: str, key: str | None) -> str:
    """Unique id of a parcel entity; key None is the status sensor."""
    base = f"{entry_id}_{PARCEL_KEY_PREFIX}{parcel_id}"
    return base if key is None else f"{base}{PARCEL_KEY_SEPARATOR}{key}"


def parcel_id_of_unique_id(entry_id: str, unique_id: str) -> str | None:
    """The parcel a unique id belongs to, None for account entities."""
    prefix = f"{entry_id}_{PARCEL_KEY_PREFIX}"
    if not unique_id.startswith(prefix):
        return None
    return unique_id.removeprefix(prefix).partition(PARCEL_KEY_SEPARATOR)[0]


def parcel_model(language: str | None, outgoing: bool) -> str:
    """The parcel device's model in Home Assistant's language."""
    primary = (language or "en").split("-")[0].split("_")[0].lower()
    incoming, sent = _PARCEL_MODEL.get(primary, _PARCEL_MODEL["en"])
    return sent if outgoing else incoming


def account_device_info(entry_id: str) -> DeviceInfo:
    """Device info of the account device."""
    return DeviceInfo(
        identifiers={account_identifier(entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer=ACCOUNT_MANUFACTURER,
        model=ACCOUNT_MODEL,
        name="BoxBlip",
    )


@callback
def async_register_account_device(
    hass: HomeAssistant, entry: BoxBlipConfigEntry
) -> dr.DeviceEntry:
    """Create (or find) the account device, so parcel devices can point at it."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={account_identifier(entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer=ACCOUNT_MANUFACTURER,
        model=ACCOUNT_MODEL,
        name="BoxBlip",
    )


def parcel_device_fields(hass: HomeAssistant, parcel: Parcel) -> dict[str, str | None]:
    """Name, manufacturer, model and link of a parcel's device."""
    return {
        "name": parcel.display_name,
        "manufacturer": parcel.carrier_name or ACCOUNT_MANUFACTURER,
        "model": parcel_model(hass.config.language, parcel.outgoing),
        "configuration_url": parcel.tracking_url,
    }


def parcel_device_info(
    coordinator: BoxBlipCoordinator, parcel_id: str, parcel: Parcel | None
) -> DeviceInfo:
    """Device info of a parcel's device, linked to the account device."""
    entry_id = coordinator.config_entry.entry_id
    identifiers = {parcel_identifier(entry_id, parcel_id)}
    if parcel is None:
        return DeviceInfo(identifiers=identifiers)
    fields = parcel_device_fields(coordinator.hass, parcel)
    kwargs: dict[str, Any] = {
        "identifiers": identifiers,
        "name": fields["name"],
        "manufacturer": fields["manufacturer"],
        "model": fields["model"],
        **_via(coordinator),
    }
    if url := fields["configuration_url"]:
        kwargs["configuration_url"] = url
    # Optional fields go in only when known. DeviceInfo is a TypedDict before 2026.11
    # and a dataclass from 2026.11; both take keyword arguments.
    info: DeviceInfo = DeviceInfo(**kwargs)  # type: ignore[typeddict-item]
    return info


def _via(coordinator: BoxBlipCoordinator) -> dict[str, Any]:
    """Link to the account device: via_device_id on 2026.8+, via_device before."""
    entry_id = coordinator.config_entry.entry_id
    by_identifier = getattr(dr, "async_get_device_id_by_identifier", None)
    if by_identifier is None:  # pragma: no cover - Home Assistant before 2026.8
        return {"via_device": account_identifier(entry_id)}
    try:
        via_id = by_identifier(
            coordinator.hass, account_identifier(entry_id), config_entry_id=entry_id
        )
    except ValueError:
        return {}
    return {"via_device_id": via_id}


class BoxBlipEntity(CoordinatorEntity[BoxBlipCoordinator]):
    """An entity of the account device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BoxBlipCoordinator, key: str) -> None:
        """Initialise."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = account_device_info(entry_id)


class BoxBlipParcelEntity(CoordinatorEntity[BoxBlipCoordinator]):
    """An entity of one parcel's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BoxBlipCoordinator,
        parcel_id: str,
        key: str | None,
        platform: str,
    ) -> None:
        """Initialise; key None is the parcel's status sensor."""
        super().__init__(coordinator)
        self.parcel_id = parcel_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = parcel_unique_id(entry_id, parcel_id, key)
        parcel = self._parcel
        self._attr_device_info = parcel_device_info(coordinator, parcel_id, parcel)
        # New entities get an id that starts with boxblip_ and does not depend on
        # the language (sensor.boxblip_shoes, sensor.boxblip_shoes_last_event), as
        # the parcel sensors had before parcel devices. Existing entities keep theirs.
        slug = slugify(parcel.display_name if parcel else parcel_id) or parcel_id
        suffix = f"_{key}" if key else ""
        self.entity_id = f"{platform}.{DOMAIN}_{slug}{suffix}"

    @property
    def _parcel(self) -> Parcel | None:
        return self.coordinator.data.visible_parcels.get(self.parcel_id)

    @property
    def available(self) -> bool:
        """Unavailable while the server is unreachable or the parcel left the list."""
        return super().available and self._parcel is not None
