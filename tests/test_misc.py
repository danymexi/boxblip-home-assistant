"""Client edge cases, cloudhooks and entry lifecycle."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.api import (
    BoxBlipClient,
    CannotConnect,
    RateLimited,
    _retry_after,
)
from custom_components.boxblip.const import CONF_WEBHOOK_ID, DEFAULT_URL
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import (
    PARCELS_URL,
    SUMMARY_URL,
    TOKEN,
    WEBHOOK_URL,
    make_body,
    make_parcel,
    mock_parcels,
    setup_entry,
)


def test_retry_after_forms() -> None:
    """Seconds, HTTP dates and junk."""
    assert _retry_after({}) is None
    assert _retry_after({"Retry-After": "120"}) == 120
    assert _retry_after({"Retry-After": "-5"}) == 0
    assert _retry_after({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}) == 0
    assert _retry_after({"Retry-After": "soon"}) is None


async def test_client_edge_cases(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Unexpected answers raise CannotConnect; 429 carries Retry-After."""
    client = BoxBlipClient(async_get_clientsession(hass), DEFAULT_URL + "/", TOKEN)
    assert client.base_url == DEFAULT_URL

    aioclient_mock.get(SUMMARY_URL, status=200, text="not json")
    with pytest.raises(CannotConnect):
        await client.async_get_summary()

    aioclient_mock.clear_requests()
    aioclient_mock.get(PARCELS_URL, status=404)
    with pytest.raises(CannotConnect):
        await client.async_get_parcels(lang="en", tz="UTC", delivered_days=3)

    aioclient_mock.clear_requests()
    aioclient_mock.get(PARCELS_URL, status=429, headers={"Retry-After": "60"})
    with pytest.raises(RateLimited) as err:
        await client.async_get_parcels(lang="en", tz="UTC", delivered_days=3)
    assert err.value.retry_after == 60

    aioclient_mock.clear_requests()
    aioclient_mock.put(WEBHOOK_URL, status=200, json={})
    assert await client.async_set_webhook("https://h.example/x", "en") is None
    aioclient_mock.clear_requests()
    aioclient_mock.put(WEBHOOK_URL, status=403)
    with pytest.raises(CannotConnect):
        await client.async_set_webhook("https://h.example/x", "en")
    aioclient_mock.clear_requests()
    aioclient_mock.delete(WEBHOOK_URL, status=400)
    with pytest.raises(CannotConnect):
        await client.async_delete_webhook()


async def test_entry_without_webhook_id_gets_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Older entries get a webhook id at setup."""
    entry = MockConfigEntry(
        domain="boxblip",
        data={CONF_TOKEN: TOKEN, CONF_URL: DEFAULT_URL},
        options={"push": False},
    )
    mock_parcels(aioclient_mock, make_body([]))
    await setup_entry(hass, entry)
    assert len(entry.data[CONF_WEBHOOK_ID]) == 64


def _fake_cloud(active: bool, logged_in: bool = True) -> ModuleType:
    module = ModuleType("homeassistant.components.cloud")

    class CloudNotAvailable(Exception):
        pass

    module.CloudNotAvailable = CloudNotAvailable  # type: ignore[attr-defined]
    module.async_active_subscription = MagicMock(return_value=active)  # type: ignore[attr-defined]
    module.async_is_logged_in = MagicMock(return_value=logged_in)  # type: ignore[attr-defined]
    module.async_get_or_create_cloudhook = AsyncMock(  # type: ignore[attr-defined]
        return_value="https://hooks.example.com/abc"
    )
    module.async_delete_cloudhook = AsyncMock()  # type: ignore[attr-defined]
    return module


@pytest.mark.parametrize("active", [True, False])
async def test_push_via_cloudhook_and_remove(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    active: bool,
) -> None:
    """With a cloud subscription the cloudhook URL is used, and deleted on removal."""
    entry = MockConfigEntry(
        domain="boxblip",
        data=dict(mock_config_entry.data),
        options={"push": True},
    )
    cloud = _fake_cloud(active)
    hass.config.components.add("cloud")
    mock_parcels(aioclient_mock, make_body([make_parcel("p1")]))
    aioclient_mock.put(WEBHOOK_URL, json={"webhook": {"host": "hooks.example.com"}})
    aioclient_mock.delete(WEBHOOK_URL, status=204)
    with patch.dict("sys.modules", {"homeassistant.components.cloud": cloud}):
        await setup_entry(hass, entry)
        push = entry.runtime_data.push
        assert push.via_cloud is active
        assert push.active is active
        puts = [c for c in aioclient_mock.mock_calls if c[0].lower() == "put"]
        if active:
            assert puts[0][2]["url"] == "https://hooks.example.com/abc"
        else:
            assert puts == []
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    cloud.async_delete_cloudhook.assert_awaited_once()


async def test_cloudhook_unavailable_falls_back(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A cloud error falls back to the external URL check (none here: polling)."""
    entry = MockConfigEntry(
        domain="boxblip", data=dict(mock_config_entry.data), options={"push": True}
    )
    cloud = _fake_cloud(True)
    cloud.async_get_or_create_cloudhook.side_effect = cloud.CloudNotAvailable
    hass.config.components.add("cloud")
    mock_parcels(aioclient_mock, make_body([]))
    with patch.dict("sys.modules", {"homeassistant.components.cloud": cloud}):
        await setup_entry(hass, entry)
    assert not entry.runtime_data.push.active


async def test_delete_webhook_failure_is_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Unload succeeds even if the server cannot be told."""
    entry = MockConfigEntry(
        domain="boxblip", data=dict(mock_config_entry.data), options={"push": True}
    )
    await hass.config.async_update(external_url="https://home.example.org")
    mock_parcels(aioclient_mock, make_body([]))
    aioclient_mock.put(WEBHOOK_URL, json={"webhook": {"host": "home.example.org"}})
    aioclient_mock.delete(WEBHOOK_URL, status=500)
    await setup_entry(hass, entry)
    assert entry.runtime_data.push.active
    assert await hass.config_entries.async_unload(entry.entry_id)
