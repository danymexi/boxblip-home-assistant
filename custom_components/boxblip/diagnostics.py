"""Diagnostics for BoxBlip, with personal data redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .const import CONF_WEBHOOK_ID, DEFAULT_URL
from .coordinator import BoxBlipConfigEntry

TO_REDACT_ENTRY = {CONF_TOKEN, CONF_WEBHOOK_ID}
# Personal or identifying fields of a parcel.
PARCEL_REDACT = {"name", "display_name", "tracking_number", "tracking_url"}
EVENT_REDACT = {"text", "location"}


def _redact_parcel(raw: dict[str, Any]) -> dict[str, Any]:
    parcel = {
        key: (REDACTED if key in PARCEL_REDACT and value is not None else value)
        for key, value in raw.items()
    }
    if isinstance(last_event := raw.get("last_event"), dict):
        parcel["last_event"] = {
            key: (REDACTED if key in EVENT_REDACT and value is not None else value)
            for key, value in last_event.items()
        }
    return parcel


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BoxBlipConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    push = coordinator.push
    entry_data = async_redact_data(dict(entry.data), TO_REDACT_ENTRY)
    # The default server is public; a custom one could identify the user.
    if entry_data.get(CONF_URL) not in (None, DEFAULT_URL):
        entry_data[CONF_URL] = REDACTED
    return {
        "entry": {
            "data": entry_data,
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "language": coordinator.language,
        },
        "push": {
            "enabled": push is not None,
            "active": push.active if push else False,
            "via_cloud": push.via_cloud if push else False,
            "refused_reason": push.refused_reason if push else None,
        },
        "data": {
            "summary": asdict(data.response.summary) if data else None,
            "delivered_days": data.response.delivered_days if data else None,
            "parcels": [_redact_parcel(p.raw) for p in data.parcels.values()]
            if data
            else [],
        },
    }
