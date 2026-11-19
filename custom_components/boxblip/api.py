"""Small async client for the BoxBlip Home Assistant API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any

import aiohttp

from homeassistant.util import dt as dt_util

from .const import REQUEST_TIMEOUT, STATUSES


class BoxBlipError(Exception):
    """Base error for the BoxBlip API."""


class AuthError(BoxBlipError):
    """The token is missing, unknown or revoked (HTTP 401)."""


class RateLimited(BoxBlipError):
    """Too many requests (HTTP 429)."""

    def __init__(self, retry_after: float | None) -> None:
        """Store the server's Retry-After, in seconds."""
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class ServerDisabled(BoxBlipError):
    """Home Assistant access is switched off on the server, or it is down (HTTP 503)."""


class CannotConnect(BoxBlipError):
    """Network error, timeout, unexpected answer or other 5xx."""


class WebhookRefused(BoxBlipError):
    """The server refused the push URL (HTTP 422)."""

    def __init__(self, reason: str) -> None:
        """Store the server's reason."""
        super().__init__(f"webhook refused: {reason}")
        self.reason = reason


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return dt_util.parse_datetime(value)


@dataclass(frozen=True, slots=True)
class Summary:
    """Counts across the account's parcels."""

    on_the_way: int = 0
    out_for_delivery: int = 0
    to_pick_up: int = 0
    delivered_today: int = 0
    problems: int = 0

    @classmethod
    def from_dict(cls, data: Any) -> Summary:
        """Build from the API's summary object."""
        if not isinstance(data, dict):
            return cls()

        def num(key: str) -> int:
            value = data.get(key)
            return value if isinstance(value, int) and value >= 0 else 0

        return cls(
            on_the_way=num("on_the_way"),
            out_for_delivery=num("out_for_delivery"),
            to_pick_up=num("to_pick_up"),
            delivered_today=num("delivered_today"),
            problems=num("problems"),
        )


@dataclass(frozen=True, slots=True)
class Parcel:
    """One parcel as returned by the API."""

    id: str
    display_name: str
    status: str | None
    raw: dict[str, Any] = field(repr=False, compare=False)
    name: str | None = None
    tracking_number: str | None = None
    carrier_code: str | None = None
    carrier_name: str | None = None
    status_text: str | None = None
    active: bool = False
    last_event_time: datetime | None = None
    last_event_text: str | None = None
    last_event_location: str | None = None
    eta_start: datetime | None = None
    eta_end: datetime | None = None
    eta_has_time: bool = False
    added_at: datetime | None = None
    updated_at: datetime | None = None
    delivered_at: datetime | None = None
    tracking_url: str | None = None
    outgoing: bool = False
    archived: bool = False
    shared: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Parcel | None:
        """Build from one item of the API's parcels list; None if unusable."""
        parcel_id = _str(data.get("id"))
        if parcel_id is None:
            return None
        status = data.get("status")
        carrier = _dict(data.get("carrier"))
        last_event = _dict(data.get("last_event"))
        eta = _dict(data.get("eta"))
        tracking_number = _str(data.get("tracking_number"))
        return cls(
            id=parcel_id,
            raw=data,
            display_name=_str(data.get("display_name"))
            or _str(data.get("name"))
            or tracking_number
            or parcel_id,
            status=status if status in STATUSES else None,
            name=_str(data.get("name")),
            tracking_number=tracking_number,
            carrier_code=_str(carrier.get("code")),
            carrier_name=_str(carrier.get("name")),
            status_text=_str(data.get("status_text")),
            active=data.get("active") is True,
            last_event_time=_dt(last_event.get("time")),
            last_event_text=_str(last_event.get("text")),
            last_event_location=_str(last_event.get("location")),
            eta_start=_dt(eta.get("start")),
            eta_end=_dt(eta.get("end")),
            eta_has_time=eta.get("has_time") is True,
            added_at=_dt(data.get("added_at")),
            updated_at=_dt(data.get("updated_at")),
            delivered_at=_dt(data.get("delivered_at")),
            tracking_url=_str(data.get("tracking_url")),
            outgoing=data.get("outgoing") is True,
            archived=data.get("archived") is True,
            shared=data.get("shared") is True,
        )


@dataclass(frozen=True, slots=True)
class ParcelsResponse:
    """The /v1/ha/parcels payload plus its ETag."""

    parcels: dict[str, Parcel]
    summary: Summary
    delivered_days: int | None
    etag: str | None

    @classmethod
    def from_dict(cls, data: Any, etag: str | None) -> ParcelsResponse:
        """Parse the JSON body."""
        if not isinstance(data, dict) or not isinstance(data.get("parcels"), list):
            raise CannotConnect("unexpected response body")
        parcels: dict[str, Parcel] = {}
        for item in data["parcels"]:
            if isinstance(item, dict) and (parcel := Parcel.from_dict(item)):
                parcels[parcel.id] = parcel
        days = data.get("delivered_days")
        return cls(
            parcels=parcels,
            summary=Summary.from_dict(data.get("summary")),
            delivered_days=days if isinstance(days, int) else None,
            etag=etag,
        )


class BoxBlipClient:
    """Talks to the BoxBlip server with a read-only Home Assistant token."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    @property
    def base_url(self) -> str:
        """Return the server's base URL."""
        return self._base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        all_headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            **(headers or {}),
        }
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json,
                headers=all_headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                body: Any = None
                if status not in (HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED):
                    try:
                        body = await resp.json(content_type=None)
                    except ValueError:
                        body = None
                resp_headers = {
                    key: value
                    for key in ("ETag", "Retry-After")
                    if (value := resp.headers.get(key)) is not None
                }
        except TimeoutError as err:
            raise CannotConnect("timeout") from err
        except aiohttp.ClientError as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

        if status == HTTPStatus.UNAUTHORIZED:
            raise AuthError("token rejected")
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimited(_retry_after(resp_headers))
        if status == HTTPStatus.SERVICE_UNAVAILABLE:
            raise ServerDisabled("service unavailable")
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise CannotConnect(f"server error {status}")
        return status, body, resp_headers

    async def async_get_parcels(
        self,
        *,
        lang: str,
        tz: str,
        delivered_days: int,
        etag: str | None = None,
    ) -> ParcelsResponse | None:
        """Fetch parcels; return None when the server answers 304 Not Modified."""
        headers = {"Accept-Language": lang}
        if etag:
            headers["If-None-Match"] = etag
        status, body, resp_headers = await self._request(
            "GET",
            "/v1/ha/parcels",
            params={"lang": lang, "delivered_days": str(delivered_days), "tz": tz},
            headers=headers,
        )
        if status == HTTPStatus.NOT_MODIFIED:
            return None
        if status != HTTPStatus.OK:
            raise CannotConnect(f"unexpected status {status}")
        return ParcelsResponse.from_dict(body, resp_headers.get("ETag"))

    async def async_get_summary(self) -> Summary:
        """Fetch only the counts; cheap, used to validate a token."""
        status, body, _ = await self._request("GET", "/v1/ha/summary")
        if status != HTTPStatus.OK or not isinstance(body, dict):
            raise CannotConnect(f"unexpected status {status}")
        return Summary.from_dict(body.get("summary"))

    async def async_set_webhook(self, url: str, lang: str) -> str | None:
        """Register the push URL; return the host the server will call."""
        status, body, _ = await self._request(
            "PUT", "/v1/ha/webhook", json={"url": url, "lang": lang}
        )
        if status == HTTPStatus.UNPROCESSABLE_ENTITY:
            reason = "unknown"
            if isinstance(body, dict) and isinstance(body.get("detail"), dict):
                reason = str(body["detail"].get("reason") or "unknown")
            raise WebhookRefused(reason)
        if status != HTTPStatus.OK:
            raise CannotConnect(f"unexpected status {status}")
        if isinstance(body, dict) and isinstance(body.get("webhook"), dict):
            return _str(body["webhook"].get("host"))
        return None

    async def async_delete_webhook(self) -> None:
        """Stop push notifications."""
        status, _, _ = await self._request("DELETE", "/v1/ha/webhook")
        if status not in (HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.NOT_FOUND):
            raise CannotConnect(f"unexpected status {status}")


def _retry_after(headers: dict[str, str]) -> float | None:
    """Parse Retry-After (seconds or HTTP date) into seconds."""
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = (when - dt_util.utcnow()).total_seconds()
    return max(seconds, 0.0)
