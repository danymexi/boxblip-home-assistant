"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.config_flow import normalize_url
from custom_components.boxblip.const import CONF_WEBHOOK_ID, DEFAULT_URL, DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import OTHER_TOKEN, SUMMARY_URL, TOKEN, make_body, mock_parcels

SUMMARY_BODY = {"summary": make_body([])["summary"]}


@pytest.fixture(autouse=True)
def no_setup():
    """Do not set the entry up after the flow."""
    with patch("custom_components.boxblip.async_setup_entry", return_value=True):
        yield


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_ok(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A valid token creates the entry."""
    aioclient_mock.get(SUMMARY_URL, json=SUMMARY_BODY)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: f"  {TOKEN} ", CONF_URL: DEFAULT_URL + "/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BoxBlip"
    assert result["data"][CONF_TOKEN] == TOKEN
    assert result["data"][CONF_URL] == DEFAULT_URL
    assert len(result["data"][CONF_WEBHOOK_ID]) == 64
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, "invalid_auth"), (500, "cannot_connect"), (503, "server_disabled")],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    error: str,
) -> None:
    """Errors are shown and the form recovers."""
    aioclient_mock.get(SUMMARY_URL, status=status)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, json=SUMMARY_BODY)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_network_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timeout is cannot_connect."""
    aioclient_mock.get(SUMMARY_URL, exc=TimeoutError())
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: TOKEN}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error(hass: HomeAssistant) -> None:
    """An unexpected exception is unknown."""
    flow_id = await _start(hass)
    with patch(
        "custom_components.boxblip.config_flow.BoxBlipClient.async_get_summary",
        side_effect=RuntimeError,
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_TOKEN: TOKEN}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_bad_token_and_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Bad token format and plain-http URLs are refused without a request."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: "hello"}
    )
    assert result["errors"] == {CONF_TOKEN: "invalid_token_format"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: TOKEN, CONF_URL: "http://example.com"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert aioclient_mock.call_count == 0


def test_normalize_url() -> None:
    """Only https, or http on this machine."""
    assert normalize_url("https://a.example/") == "https://a.example"
    assert normalize_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert normalize_url("http://localhost:8000/") == "http://localhost:8000"
    assert normalize_url("http://192.168.1.2") is None
    assert normalize_url("ftp://a.example") is None
    assert normalize_url("https://a.example/?x=1") is None
    assert normalize_url("https://a.example:99999") is None
    assert normalize_url("nonsense") is None


async def test_duplicate_token_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The same token cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A new token replaces the revoked one."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.get(SUMMARY_URL, status=401)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: OTHER_TOKEN}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SUMMARY_URL, json=SUMMARY_BODY)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: OTHER_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKEN] == OTHER_TOKEN
    assert mock_config_entry.data[CONF_WEBHOOK_ID]


async def test_reauth_token_of_other_entry_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth with a token another entry already uses aborts."""
    mock_config_entry.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, data={CONF_TOKEN: OTHER_TOKEN}).add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: OTHER_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfigure changes the server URL and token."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN, CONF_URL: "http://example.com"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}

    aioclient_mock.get("http://127.0.0.1:8000/v1/ha/summary", json=SUMMARY_BODY)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN, CONF_URL: "http://127.0.0.1:8000"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_URL] == "http://127.0.0.1:8000"


async def test_options_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Options store push and delivered_days."""
    mock_parcels(aioclient_mock, make_body([]))
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"push": True, "delivered_days": 7.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {"push": True, "delivered_days": 7}
