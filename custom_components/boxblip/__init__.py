"""The BoxBlip parcel tracker integration."""

from __future__ import annotations

from homeassistant.components import webhook
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BoxBlipClient
from .const import CONF_PUSH, CONF_WEBHOOK_ID, DEFAULT_PUSH, DEFAULT_URL, DOMAIN
from .coordinator import BoxBlipConfigEntry, BoxBlipCoordinator
from .entity import (
    async_register_account_device,
    parcel_device_fields,
    parcel_id_of_device,
    parcel_id_of_unique_id,
)
from .push import BoxBlipPush, async_remove_cloudhook
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BoxBlipConfigEntry) -> bool:
    """Set up BoxBlip from a config entry."""
    if CONF_WEBHOOK_ID not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEBHOOK_ID: webhook.async_generate_id()}
        )
    client = BoxBlipClient(
        async_get_clientsession(hass),
        entry.data.get(CONF_URL, DEFAULT_URL),
        entry.data[CONF_TOKEN],
    )
    coordinator = BoxBlipCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Parcel devices point at the account device, so it has to exist first.
    async_register_account_device(hass, entry)
    _async_sync_parcel_devices(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(
            callback(lambda: _async_sync_parcel_devices(hass, entry))
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry.options.get(CONF_PUSH, DEFAULT_PUSH):
        push = BoxBlipPush(hass, entry, coordinator)
        await push.async_start()
        coordinator.push = push
    # Returning a bool keeps compatibility with Home Assistant before 2026.11.
    return True


@callback
def _async_sync_parcel_devices(hass: HomeAssistant, entry: BoxBlipConfigEntry) -> None:
    """Follow the server's list: drop parcels that left it, keep names up to date.

    A parcel leaves the list when it is archived, deleted, or delivered longer ago
    than the configured window. Its device goes, and with it its entities. Parcel
    entities without a device (from before parcel devices) are removed on their own.
    """
    parcels = entry.runtime_data.data.visible_parcels
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        parcel_id = parcel_id_of_device(entry.entry_id, device)
        if parcel_id is None:
            continue
        if (parcel := parcels.get(parcel_id)) is None:
            device_registry.async_remove_device(device.id)
            continue
        fields = parcel_device_fields(hass, parcel)
        changes = {
            key: value for key, value in fields.items() if getattr(device, key) != value
        }
        if changes:
            device_registry.async_update_device(device.id, **changes)  # type: ignore[arg-type]

    entity_registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        parcel_id = parcel_id_of_unique_id(entry.entry_id, reg_entry.unique_id)
        if parcel_id is not None and parcel_id not in parcels:
            entity_registry.async_remove(reg_entry.entity_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: BoxBlipConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let the user delete a parcel's device once the parcel left the list.

    A parcel still on the list would come straight back, and the account device
    goes only with the integration.
    """
    parcel_id = parcel_id_of_device(entry.entry_id, device)
    if parcel_id is None:
        return False
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return True
    return parcel_id not in coordinator.data.visible_parcels


async def async_unload_entry(hass: HomeAssistant, entry: BoxBlipConfigEntry) -> bool:
    """Unload a config entry."""
    if (push := entry.runtime_data.push) is not None:
        await push.async_stop()
        entry.runtime_data.push = None
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BoxBlipConfigEntry) -> None:
    """Clean up a cloudhook created for this entry."""
    if webhook_id := entry.data.get(CONF_WEBHOOK_ID):
        await async_remove_cloudhook(hass, webhook_id)
