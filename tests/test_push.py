"""Optional push via a Home Assistant webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.boxblip.push import signing_key, verify_signature
from homeassistant.core import HomeAssistant

from .conftest import (
    PARCELS_URL,
    TOKEN,
    WEBHOOK_ID,
    WEBHOOK_URL,
    make_body,
    make_parcel,
    setup_entry,
)

EXTERNAL = "https://home.example.org"


def _sign(body: bytes, ts: int, token: str = TOKEN) -> str:
    key = hashlib.sha256(b"boxblip-ha-webhook:v1:" + token.encode()).digest()
    return (
        "sha256=" + hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    )


def test_verify_signature() -> None:
    """Valid, tampered, stale and malformed signatures."""
    key = signing_key(TOKEN)
    body = b'{"event":"parcel_updated"}'
    now = 1_800_000_000
    good = _sign(body, now)
    assert verify_signature(key, str(now), good, body, now=now)
    assert verify_signature(key, str(now), good, body, now=now + 299)
    assert not verify_signature(key, str(now), good, body, now=now + 301)
    assert not verify_signature(key, str(now), good, body, now=now - 301)
    assert not verify_signature(key, str(now), good, body + b" ", now=now)
    assert not verify_signature(key, str(now + 1), good, body, now=now)
    assert not verify_signature(
        signing_key("bbha_other"), str(now), good, body, now=now
    )
    assert not verify_signature(key, None, good, body, now=now)
    assert not verify_signature(key, "abc", good, body, now=now)
    assert not verify_signature(key, str(now), None, body, now=now)
    assert not verify_signature(key, str(now), good.removeprefix("sha256="), body)


@pytest.fixture
def push_entry(mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """The entry with push enabled."""
    return MockConfigEntry(
        domain=mock_config_entry.domain,
        title=mock_config_entry.title,
        data=dict(mock_config_entry.data),
        options={"push": True, "delivered_days": 3},
        entry_id=mock_config_entry.entry_id,
    )


def _mock_api(aioclient_mock: AiohttpClientMocker, put_status: int = 200) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        PARCELS_URL,
        json=make_body([make_parcel("p1")]),
        headers={"ETag": '"v1"'},
    )
    if put_status == 200:
        aioclient_mock.put(
            WEBHOOK_URL, json={"webhook": {"host": "home.example.org", "active": True}}
        )
    else:
        aioclient_mock.put(
            WEBHOOK_URL,
            status=put_status,
            json={"detail": {"error": "webhook_refused", "reason": "private_address"}},
        )
    aioclient_mock.delete(WEBHOOK_URL, status=204)


def _calls(aioclient_mock: AiohttpClientMocker, method: str) -> list:
    return [c for c in aioclient_mock.mock_calls if c[0].lower() == method]


async def test_push_registers_and_verifies(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    push_entry: MockConfigEntry,
    hass_client_no_auth,
) -> None:
    """With an external URL the webhook is registered, verified and removed."""
    await hass.config.async_update(external_url=EXTERNAL, language="it")
    _mock_api(aioclient_mock)
    await setup_entry(hass, push_entry)

    puts = _calls(aioclient_mock, "put")
    assert len(puts) == 1
    assert puts[0][2] == {"url": f"{EXTERNAL}/api/webhook/{WEBHOOK_ID}", "lang": "it"}
    push = push_entry.runtime_data.push
    assert push.active
    assert push.server_host == "home.example.org"

    client = await hass_client_no_auth()
    path = f"/api/webhook/{WEBHOOK_ID}"
    body = json.dumps({"event": "parcel_updated", "parcel": {"id": "p1"}}).encode()
    gets_before = len(_calls(aioclient_mock, "get"))

    # Bad signature: refused, no refresh.
    resp = await client.post(
        path,
        data=body,
        headers={
            "X-Boxblip-Timestamp": str(int(time.time())),
            "X-Boxblip-Signature": "sha256=00",
        },
    )
    assert resp.status == 401
    # Stale timestamp: refused.
    old = int(time.time()) - 3600
    resp = await client.post(
        path,
        data=body,
        headers={
            "X-Boxblip-Timestamp": str(old),
            "X-Boxblip-Signature": _sign(body, old),
        },
    )
    assert resp.status == 401
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_calls(aioclient_mock, "get")) == gets_before

    # Valid: accepted and the coordinator refreshes from the API.
    now = int(time.time())
    resp = await client.post(
        path,
        data=body,
        headers={
            "X-Boxblip-Timestamp": str(now),
            "X-Boxblip-Signature": _sign(body, now),
        },
    )
    assert resp.status == 200
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_calls(aioclient_mock, "get")) == gets_before + 1

    assert await hass.config_entries.async_unload(push_entry.entry_id)
    assert len(_calls(aioclient_mock, "delete")) == 1


async def test_push_skipped_without_external_url(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    push_entry: MockConfigEntry,
) -> None:
    """No public https URL: polling only, nothing sent to the server."""
    _mock_api(aioclient_mock)
    await setup_entry(hass, push_entry)
    assert not _calls(aioclient_mock, "put")
    assert not push_entry.runtime_data.push.active
    assert await hass.config_entries.async_unload(push_entry.entry_id)
    assert not _calls(aioclient_mock, "delete")


async def test_push_skipped_for_http_external_url(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    push_entry: MockConfigEntry,
) -> None:
    """The server only accepts https, so an http URL is not offered."""
    await hass.config.async_update(external_url="http://home.example.org")
    _mock_api(aioclient_mock)
    await setup_entry(hass, push_entry)
    assert not _calls(aioclient_mock, "put")


async def test_push_refused_by_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    push_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 422 keeps polling and logs the reason."""
    await hass.config.async_update(external_url=EXTERNAL)
    _mock_api(aioclient_mock, put_status=422)
    await setup_entry(hass, push_entry)
    push = push_entry.runtime_data.push
    assert not push.active
    assert push.refused_reason == "private_address"
    assert "private_address" in caplog.text
    assert push_entry.runtime_data.last_update_success


async def test_push_server_error_keeps_polling(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    push_entry: MockConfigEntry,
) -> None:
    """Any other failure keeps polling."""
    await hass.config.async_update(external_url=EXTERNAL)
    _mock_api(aioclient_mock, put_status=500)
    await setup_entry(hass, push_entry)
    assert not push_entry.runtime_data.push.active


async def test_push_disabled_by_option(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """push: false never touches the webhook endpoint."""
    await hass.config.async_update(external_url=EXTERNAL)
    _mock_api(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.runtime_data.push is None
    assert not _calls(aioclient_mock, "put")
