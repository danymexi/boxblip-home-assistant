"""Constants for the BoxBlip integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "boxblip"
LOGGER = logging.getLogger(__package__)

DEFAULT_URL: Final = "https://api-boxblip.daniele-messi.com"
TOKEN_PREFIX: Final = "bbha_"

CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_PUSH: Final = "push"
CONF_DELIVERED_DAYS: Final = "delivered_days"

DEFAULT_PUSH: Final = True
DEFAULT_DELIVERED_DAYS: Final = 3
MAX_DELIVERED_DAYS: Final = 30

UPDATE_INTERVAL: Final = timedelta(minutes=5)
REQUEST_TIMEOUT: Final = 20

EVENT_PARCEL_UPDATED: Final = "boxblip_parcel_updated"
SERVICE_REFRESH: Final = "refresh"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

# Languages the server can localise status texts into.
SUPPORTED_LANGUAGES: Final = ("it", "en", "fr", "de", "es")

# Every status the server can return, in lifecycle order.
STATUSES: Final = (
    "pending",
    "info_received",
    "in_transit",
    "out_for_delivery",
    "available_for_pickup",
    "delivered",
    "exception",
    "returned",
    "expired",
)
ACTIVE_STATUSES: Final = frozenset(
    {
        "pending",
        "info_received",
        "in_transit",
        "out_for_delivery",
        "available_for_pickup",
        "exception",
    }
)

# Webhook signature
WEBHOOK_KEY_PREFIX: Final = b"boxblip-ha-webhook:v1:"
WEBHOOK_MAX_SKEW: Final = 300  # seconds
HEADER_TIMESTAMP: Final = "X-Boxblip-Timestamp"
HEADER_SIGNATURE: Final = "X-Boxblip-Signature"
