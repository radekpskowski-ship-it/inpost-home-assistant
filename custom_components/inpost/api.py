"""Klient InPost Mobile API z auto-refresh tokena.

Uwaga: korzysta z NIEOFICJALNEGO mobilnego API InPost
(`api-inmobile-pl.easypack24.net`) - tego ktorego uzywa apka Android.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import aiohttp
import async_timeout

from .const import API_BASE, USER_AGENT

_LOGGER = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent":    USER_AGENT,
    "Accept":        "application/json",
    "Content-Type":  "application/json",
    "X-Api-Version": "1",
}

_REQ_TIMEOUT = 15  # sekund


class ReAuthRequired(Exception):
    """Sesja nie da sie odswiezyc - trzeba przejsc przez SMS od poczatku."""


class RateLimited(Exception):
    """API zwrocilo 429 / Retry-After."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__(f"rate limited, retry_after={retry_after}")
        self.retry_after = retry_after


class InpostClient:
    """Async klient do mobilnej apki InPost.

    Trzyma tokeny w pamieci. Po refresh tokena zglasza on_tokens_updated
    callback, zeby wpis ConfigEntry zaktualizowac w storage.
    """

    def __init__(
        self,
        phone_number: str,
        auth_token: str,
        refresh_token: str,
        session: aiohttp.ClientSession,
        on_tokens_updated=None,
    ) -> None:
        self._phone = phone_number
        self._auth = auth_token
        self._refresh = refresh_token
        self._session = session
        self._on_tokens_updated = on_tokens_updated
        self._refresh_lock = asyncio.Lock()

    @property
    def auth_token(self) -> str:
        return self._auth

    @property
    def refresh_token(self) -> str:
        return self._refresh

    async def _refresh_now(self) -> None:
        # asyncio.Lock chroni przed dwoma rownoczesnymi refresh-ami (ktore unieważnia by się nawzajem)
        async with self._refresh_lock:
            captured_auth = self._auth
            try:
                async with async_timeout.timeout(_REQ_TIMEOUT):
                    async with self._session.post(
                        f"{API_BASE}/v1/authenticate",
                        json={"refreshToken": self._refresh, "phoneOS": "Android"},
                        headers=_HEADERS,
                    ) as r:
                        if r.status == 429:
                            raise RateLimited(_parse_retry_after(r))
                        if r.status != 200:
                            # NIE logujemy body - moze zawierac odpowiedz z echem refreshToken
                            _LOGGER.error("InPost refresh failed: HTTP %s", r.status)
                            raise ReAuthRequired(f"refresh failed: HTTP {r.status}")
                        d = await r.json(content_type=None)
            except asyncio.TimeoutError as err:
                raise ReAuthRequired("refresh timeout") from err
            except aiohttp.ClientError as err:
                raise ReAuthRequired(f"refresh transport error: {err}") from err

            if not isinstance(d, dict) or not d.get("authToken"):
                _LOGGER.error("InPost refresh: brak authToken w odpowiedzi")
                raise ReAuthRequired("malformed refresh response")

            # jesli inny korutyn juz odswiezyl token w tym czasie, zachowaj nowszy
            if captured_auth != self._auth:
                _LOGGER.debug("Refresh: token already updated by another coroutine, skipping")
                return

            self._auth = d["authToken"]
            if d.get("refreshToken"):
                self._refresh = d["refreshToken"]
            _LOGGER.info("InPost token refreshed")
            if self._on_tokens_updated:
                self._on_tokens_updated(self._auth, self._refresh)

    async def _get(self, path: str) -> dict[str, Any]:
        for attempt in range(3):
            try:
                return await self._do_get(path)
            except RateLimited as err:
                wait = (err.retry_after or 5.0) + random.uniform(0, 1.5)
                if attempt == 2:
                    raise
                _LOGGER.warning("InPost rate-limited, sleeping %.1fs", wait)
                await asyncio.sleep(wait)
        # unreachable
        raise RuntimeError("retry loop exhausted")

    async def _do_get(self, path: str) -> dict[str, Any]:
        h = {**_HEADERS, "Authorization": self._auth}
        try:
            async with async_timeout.timeout(_REQ_TIMEOUT):
                async with self._session.get(f"{API_BASE}{path}", headers=h) as r:
                    if r.status == 429:
                        raise RateLimited(_parse_retry_after(r))
                    if r.status != 401:
                        if r.status != 200:
                            raise RuntimeError(f"GET {path} -> HTTP {r.status}")
                        return await r.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise RuntimeError(f"GET {path} timeout") from err
        except aiohttp.ClientError as err:
            raise RuntimeError(f"GET {path} transport: {err}") from err

        # 401 - refresh and retry once
        await self._refresh_now()
        h = {**_HEADERS, "Authorization": self._auth}
        try:
            async with async_timeout.timeout(_REQ_TIMEOUT):
                async with self._session.get(f"{API_BASE}{path}", headers=h) as r:
                    if r.status == 429:
                        raise RateLimited(_parse_retry_after(r))
                    if r.status != 200:
                        raise RuntimeError(f"GET {path} (post-refresh) -> HTTP {r.status}")
                    return await r.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise RuntimeError(f"GET {path} timeout (post-refresh)") from err
        except aiohttp.ClientError as err:
            raise RuntimeError(f"GET {path} transport (post-refresh): {err}") from err

    async def get_parcels(self) -> list[dict]:
        data = await self._get("/v3/parcels/tracked")
        return data.get("parcels", []) or []


def _parse_retry_after(r: aiohttp.ClientResponse) -> float | None:
    val = r.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


# --- statyczne pomocniki dla flow logowania (bez tokenow) ---


async def send_sms_code(session: aiohttp.ClientSession, phone: str) -> bool:
    try:
        async with async_timeout.timeout(_REQ_TIMEOUT):
            async with session.post(
                f"{API_BASE}/v1/sendSMSCode",
                json={"phoneNumber": phone},
                headers=_HEADERS,
            ) as r:
                return r.status == 200
    except (asyncio.TimeoutError, aiohttp.ClientError):
        _LOGGER.exception("send_sms_code transport error")
        return False


async def confirm_sms_code(
    session: aiohttp.ClientSession, phone: str, code: str
) -> dict | None:
    try:
        async with async_timeout.timeout(_REQ_TIMEOUT):
            async with session.post(
                f"{API_BASE}/v1/confirmSMSCode",
                json={"phoneNumber": phone, "smsCode": code, "phoneOS": "Android"},
                headers=_HEADERS,
            ) as r:
                if r.status != 200:
                    _LOGGER.warning("confirm_sms_code HTTP %s", r.status)
                    return None
                return await r.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        _LOGGER.exception("confirm_sms_code transport error")
        return None
