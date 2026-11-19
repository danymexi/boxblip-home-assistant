"""Actions for the BoxBlip integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import ATTR_CONFIG_ENTRY_ID, DOMAIN, SERVICE_REFRESH
from .coordinator import BoxBlipConfigEntry

REFRESH_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register boxblip.refresh."""

    async def async_refresh(call: ServiceCall) -> None:
        entries: list[BoxBlipConfigEntry]
        if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="entry_not_found",
                    translation_placeholders={"entry_id": entry_id},
                )
            if entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="entry_not_loaded",
                    translation_placeholders={"title": entry.title},
                )
            entries = [entry]
        else:
            entries = hass.config_entries.async_loaded_entries(DOMAIN)
        for entry in entries:
            await entry.runtime_data.async_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, async_refresh, schema=REFRESH_SCHEMA
    )
