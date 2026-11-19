"""Config flow for BoxBlip."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AuthError, BoxBlipClient, BoxBlipError, ServerDisabled
from .const import (
    CONF_DELIVERED_DAYS,
    CONF_PUSH,
    CONF_WEBHOOK_ID,
    DEFAULT_DELIVERED_DAYS,
    DEFAULT_PUSH,
    DEFAULT_URL,
    DOMAIN,
    LOGGER,
    MAX_DELIVERED_DAYS,
    TOKEN_PREFIX,
)

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def normalize_url(url: str) -> str | None:
    """Return the base URL without a trailing slash, or None if not acceptable.

    https is required; plain http is only accepted for a server on this machine.
    """
    url = url.strip().rstrip("/")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError:
        return None
    if not host or parts.query or parts.fragment:
        return None
    if parts.scheme == "https" or (parts.scheme == "http" and host in LOCAL_HOSTS):
        return url
    return None


class BoxBlipConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up BoxBlip with a token from the app."""

    VERSION = 1

    async def _async_validate(self, url: str, token: str) -> dict[str, str]:
        """Call the API with the token; return form errors (empty on success)."""
        if not token.startswith(TOKEN_PREFIX):
            return {CONF_TOKEN: "invalid_token_format"}
        client = BoxBlipClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.async_get_summary()
        except AuthError:
            return {"base": "invalid_auth"}
        except ServerDisabled:
            return {"base": "server_disabled"}
        except BoxBlipError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error while validating the BoxBlip token")
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the token (and, rarely, another server)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            url = normalize_url(user_input.get(CONF_URL) or DEFAULT_URL)
            # The server exposes no account id and tokens rotate, so the only
            # stable thing to deduplicate on is the token itself.
            self._async_abort_entries_match({CONF_TOKEN: token})
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                errors = await self._async_validate(url, token)
            if not errors:
                return self.async_create_entry(
                    title="BoxBlip",
                    data={
                        CONF_TOKEN: token,
                        CONF_URL: url,
                        CONF_WEBHOOK_ID: webhook.async_generate_id(),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
                        vol.Optional(CONF_URL): URL_SELECTOR,
                    }
                ),
                {CONF_URL: DEFAULT_URL, **(user_input or {})},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The token was revoked or replaced."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            self._async_abort_other_entries_with_token(entry, token)
            errors = await self._async_validate(
                entry.data.get(CONF_URL, DEFAULT_URL), token
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the token or the server URL."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            url = normalize_url(user_input.get(CONF_URL) or DEFAULT_URL)
            self._async_abort_other_entries_with_token(entry, token)
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                errors = await self._async_validate(url, token)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token, CONF_URL: url}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
                        vol.Optional(CONF_URL): URL_SELECTOR,
                    }
                ),
                {CONF_URL: entry.data.get(CONF_URL, DEFAULT_URL), **(user_input or {})},
            ),
            errors=errors,
        )

    def _async_abort_other_entries_with_token(
        self, entry: ConfigEntry, token: str
    ) -> None:
        for other in self._async_current_entries(include_ignore=False):
            if other.entry_id != entry.entry_id and other.data.get(CONF_TOKEN) == token:
                raise AbortFlow("already_configured")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BoxBlipOptionsFlow:
        """Return the options flow."""
        return BoxBlipOptionsFlow()


class BoxBlipOptionsFlow(OptionsFlowWithReload):
    """Push on/off and how long delivered parcels stay."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_PUSH: user_input[CONF_PUSH],
                    CONF_DELIVERED_DAYS: int(user_input[CONF_DELIVERED_DAYS]),
                }
            )
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PUSH, default=options.get(CONF_PUSH, DEFAULT_PUSH)
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_DELIVERED_DAYS,
                        default=options.get(
                            CONF_DELIVERED_DAYS, DEFAULT_DELIVERED_DAYS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=MAX_DELIVERED_DAYS,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
