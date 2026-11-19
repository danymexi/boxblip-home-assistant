"""Optional push: the server calls a Home Assistant webhook on every status change.

The webhook body is never trusted beyond "something changed": a valid, fresh
signature only makes the coordinator refresh from the API.
"""

from __future__ import annotations

from contextlib import suppress
import hashlib
import hmac
from http import HTTPStatus
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError

from .api import BoxBlipError, WebhookRefused
from .const import (
    CONF_WEBHOOK_ID,
    DOMAIN,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    LOGGER,
    WEBHOOK_KEY_PREFIX,
    WEBHOOK_MAX_SKEW,
)

if TYPE_CHECKING:
    from .coordinator import BoxBlipConfigEntry, BoxBlipCoordinator


def signing_key(token: str) -> bytes:
    """Derive the HMAC key the server signs webhook calls with."""
    return hashlib.sha256(WEBHOOK_KEY_PREFIX + token.encode()).digest()


def verify_signature(
    key: bytes,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> bool:
    """Check X-Boxblip-Timestamp / X-Boxblip-Signature for a raw body."""
    if not timestamp or not signature or not signature.startswith("sha256="):
        return False
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - sent) > WEBHOOK_MAX_SKEW:
        return False
    expected = hmac.new(key, f"{sent}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


class BoxBlipPush:
    """Registers a webhook in Home Assistant and on the BoxBlip server."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BoxBlipConfigEntry,
        coordinator: BoxBlipCoordinator,
    ) -> None:
        """Initialise."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self._key = signing_key(entry.data[CONF_TOKEN])
        self.active = False
        self.via_cloud = False
        self.server_host: str | None = None
        self.refused_reason: str | None = None

    async def _async_public_url(self) -> str | None:
        """Return a public https URL for the webhook, or None to stay on polling."""
        if (cloudhook := await self._async_cloudhook_url()) is not None:
            self.via_cloud = True
            return cloudhook
        try:
            url = webhook.async_generate_url(
                self.hass,
                self.webhook_id,
                allow_internal=False,
                allow_ip=False,
                prefer_external=True,
            )
        except NoURLAvailableError:
            return None
        if urlsplit(url).scheme != "https":
            return None
        return url

    async def _async_cloudhook_url(self) -> str | None:
        """Return a Home Assistant Cloud webhook URL when a subscription is active."""
        if "cloud" not in self.hass.config.components:
            return None
        # Imported lazily: the cloud component is heavy and usually absent.
        from homeassistant.components import cloud  # noqa: PLC0415

        if not cloud.async_active_subscription(self.hass):
            return None
        try:
            return await cloud.async_get_or_create_cloudhook(self.hass, self.webhook_id)
        except cloud.CloudNotAvailable:
            return None

    async def async_start(self) -> None:
        """Register the webhook; silently fall back to polling when impossible."""
        url = await self._async_public_url()
        if url is None:
            LOGGER.debug("No public https URL for Home Assistant: polling only")
            return
        webhook.async_register(
            self.hass,
            DOMAIN,
            "BoxBlip",
            self.webhook_id,
            self._async_handle,
            allowed_methods=["POST"],
        )
        try:
            self.server_host = await self.coordinator.client.async_set_webhook(
                url, self.coordinator.language
            )
        except WebhookRefused as err:
            self.refused_reason = err.reason
            LOGGER.warning(
                "The BoxBlip server refused the push URL (%s): polling only",
                err.reason,
            )
        except BoxBlipError as err:
            LOGGER.warning("Could not enable BoxBlip push (%s): polling only", err)
        else:
            self.active = True
            return
        webhook.async_unregister(self.hass, self.webhook_id)

    async def async_stop(self) -> None:
        """Unregister locally and ask the server to stop calling."""
        if not self.active:
            return
        webhook.async_unregister(self.hass, self.webhook_id)
        self.active = False
        try:
            await self.coordinator.client.async_delete_webhook()
        except BoxBlipError as err:
            LOGGER.debug("Could not remove the BoxBlip push URL: %s", err)

    async def _async_handle(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response:
        """Verify the signature, then nudge the coordinator."""
        body = await request.content.read()
        if not verify_signature(
            self._key,
            request.headers.get(HEADER_TIMESTAMP),
            request.headers.get(HEADER_SIGNATURE),
            body,
        ):
            LOGGER.debug("Ignoring a BoxBlip webhook call with a bad signature")
            return Response(status=HTTPStatus.UNAUTHORIZED)
        self.entry.async_create_background_task(
            hass,
            self.coordinator.async_request_refresh(),
            "boxblip_webhook_refresh",
        )
        return Response(status=HTTPStatus.OK)


async def async_remove_cloudhook(hass: HomeAssistant, webhook_id: str) -> None:
    """Delete a cloudhook created for this entry, if any."""
    if "cloud" not in hass.config.components:
        return
    from homeassistant.components import cloud  # noqa: PLC0415

    if cloud.async_is_logged_in(hass):
        with suppress(cloud.CloudNotAvailable, ValueError):
            await cloud.async_delete_cloudhook(hass, webhook_id)
