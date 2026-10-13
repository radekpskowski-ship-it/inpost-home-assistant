"""Klient InPost Mobile API (OAuth2 + PKCE) z auto-refresh tokena.

Uwaga: korzysta z NIEOFICJALNEGO mobilnego API InPost
(`api-inmobile-pl.easypack24.net`) - tego ktorego uzywa apka mobilna.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp
import async_timeout

from .const import (
    API_BASE,
    APP_ID,
    ENDPOINT_ME,
    ENDPOINT_NOTIFICATIONS,
    ENDPOINT_TOKEN,
    ENDPOINT_TRACKED,
    ENDPOINT_TRACKED_LEGACY,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    USER_AGENT,
    normalize_phone,
)

_LOGGER = logging.getLogger(__name__)

_REQ_TIMEOUT = 15  # sekund
# Odswiezamy z zapasem, zeby token nie wygasl w polowie requestu.
_EXPIRY_MARGIN = 60


def _headers(device_uid: str) -> dict[str, str]:
    return {
        "x-app-id":        APP_ID,
        "device-uid":      device_uid,
        "User-Agent":      USER_AGENT,
        "Accept":          "application/json",
        "Accept-Language": "pl-PL",
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _bearer(token: str) -> str:
    # stare wpisy trzymaly token razem z prefiksem "Bearer "
    token = (token or "").strip()
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


class ReAuthRequired(Exception):
    """Sesja nie da sie odswiezyc - trzeba zalogowac sie od nowa w przegladarce."""


class RateLimited(Exception):
    """API zwrocilo 429 / Retry-After."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__(f"rate limited, retry_after={retry_after}")
        self.retry_after = retry_after


class InpostLogin:
    """Jedna proba logowania: buduje link i wymienia wklejony adres zwrotny na tokeny."""

    def __init__(self) -> None:
        self._verifier = _b64url(secrets.token_bytes(48))
        self._state = _b64url(secrets.token_bytes(24))

    @property
    def url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": "openid",
            "code_challenge": _b64url(hashlib.sha256(self._verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "state": self._state,
            "nonce": _b64url(secrets.token_bytes(24)),
            "response_mode": "query",
            "lang": "pl",
            "supported_markets": "PL",
        }
        return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    def extract_code(self, pasted: str) -> str:
        """Wyciagnij `code` z wklejonego adresu.

        ValueError("sms") - wklejono kod SMS, ValueError("state") - adres z innej
        proby logowania, ValueError("no_code") - wszystko inne.
        """
        value = (pasted or "").strip()
        if "code=" not in value:
            raise ValueError("sms" if value.replace(" ", "").isdigit() else "no_code")
        query = parse_qs(urlsplit(value).query or value)
        code = (query.get("code") or [""])[0]
        if not code:
            raise ValueError("no_code")
        state = (query.get("state") or [None])[0]
        if state is not None and state != self._state:
            raise ValueError("state")
        return code

    async def async_exchange(
        self, session: aiohttp.ClientSession, device_uid: str, code: str
    ) -> dict[str, Any]:
        return await _token_request(
            session,
            device_uid,
            {
                "client_id": OAUTH_CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": self._verifier,
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
        )


async def _token_request(
    session: aiohttp.ClientSession, device_uid: str, form: dict[str, str]
) -> dict[str, Any]:
    """POST na endpoint tokenow. ReAuthRequired = sesja martwa, RuntimeError = przejsciowe."""
    try:
        async with async_timeout.timeout(_REQ_TIMEOUT):
            async with session.post(
                f"{API_BASE}{ENDPOINT_TOKEN}", data=form, headers=_headers(device_uid)
            ) as r:
                if r.status == 429:
                    raise RateLimited(_parse_retry_after(r))
                if r.status in (400, 401, 403):
                    # invalid_grant: kod albo refresh token zuzyty/uniewazniony.
                    # NIE logujemy body - moze zawierac echo tokena
                    _LOGGER.error("InPost token request failed: HTTP %s", r.status)
                    raise ReAuthRequired(f"token request failed: HTTP {r.status}")
                if r.status != 200:
                    raise RuntimeError(f"token request -> HTTP {r.status}")
                d = await r.json(content_type=None)
    except asyncio.TimeoutError as err:
        # padnieta siec to awaria lacza, nie martwa sesja
        raise RuntimeError("token request timeout") from err
    except aiohttp.ClientError as err:
        raise RuntimeError(f"token request transport: {err}") from err

    if not isinstance(d, dict) or not d.get("access_token") or not d.get("refresh_token"):
        _LOGGER.error("InPost token request: brak tokenow w odpowiedzi")
        raise ReAuthRequired("malformed token response")
    return {
        "access_token": d["access_token"],
        "refresh_token": d["refresh_token"],
        "expires_at": time.time() + float(d.get("expires_in") or 0),
    }


def phone_from_token(access_token: str) -> str:
    """Numer konta (9 cyfr) z claimu `phone` tokena JWT; '' gdy sie nie da.

    Tokeny OAuth maja `phone` (9 cyfr) + `phone_prefix`, a `sub` to UUID osoby;
    w starych tokenach (logowanie SMS-em) numer byl w `sub`.
    """
    try:
        payload = (access_token or "").split(" ")[-1].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return ""
    for claim in ("phone", "sub"):
        value = str(claims.get(claim) or "")
        if value.isdigit() and len(value) >= 9:
            return normalize_phone(value)
    return ""


def phone_from_profile(profile: dict[str, Any]) -> str:
    """Numer konta (9 cyfr) z profilu /people/me, niezaleznie od zagniezdzenia."""
    phone = profile.get("phoneNumber") or profile.get("phone")
    if isinstance(phone, dict):
        phone = phone.get("value") or phone.get("number")
    return normalize_phone(phone)


class InpostClient:
    """Async klient do mobilnej apki InPost.

    Trzyma tokeny w pamieci. InPost rotuje refresh tokeny (kazde odswiezenie
    uniewaznia stary), wiec po refreshu zglasza on_tokens_updated callback,
    zeby wpis ConfigEntry zaktualizowac w storage.
    """

    def __init__(
        self,
        phone_number: str,
        auth_token: str,
        refresh_token: str,
        session: aiohttp.ClientSession,
        device_uid: str | None = None,
        expires_at: float | None = None,
        on_tokens_updated=None,
    ) -> None:
        self._phone = phone_number
        self._auth = auth_token
        self._refresh = refresh_token
        self._session = session
        self._device_uid = device_uid
        self._expires_at = float(expires_at or 0)
        self._on_tokens_updated = on_tokens_updated
        self._refresh_lock = asyncio.Lock()

    @property
    def auth_token(self) -> str:
        return self._auth

    @property
    def refresh_token(self) -> str:
        return self._refresh

    async def _refresh_now(self, seen_auth: str | None = None) -> None:
        # asyncio.Lock chroni przed dwoma rownoczesnymi refresh-ami (ktore unieważnia by się nawzajem)
        async with self._refresh_lock:
            if seen_auth is not None and seen_auth != self._auth:
                _LOGGER.debug("Refresh: token already updated by another coroutine, skipping")
                return
            if not self._device_uid:
                # wpis sprzed OAuth (logowanie SMS-em) - tych tokenow nie da sie juz odswiezyc
                raise ReAuthRequired("stara sesja SMS - zaloguj sie ponownie")
            tokens = await _token_request(
                self._session,
                self._device_uid,
                {
                    "client_id": OAUTH_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh,
                },
            )
            self._auth = tokens["access_token"]
            self._refresh = tokens["refresh_token"]
            self._expires_at = tokens["expires_at"]
            _LOGGER.info("InPost token refreshed")
            if self._on_tokens_updated:
                self._on_tokens_updated(self._auth, self._refresh, self._expires_at)

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
        if self._expires_at and time.time() > self._expires_at - _EXPIRY_MARGIN:
            await self._refresh_now()
        seen = self._auth
        h = {**_headers(self._device_uid or ""), "Authorization": _bearer(seen)}
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
        await self._refresh_now(seen_auth=seen)
        h = {**_headers(self._device_uid or ""), "Authorization": _bearer(self._auth)}
        try:
            async with async_timeout.timeout(_REQ_TIMEOUT):
                async with self._session.get(f"{API_BASE}{path}", headers=h) as r:
                    if r.status == 429:
                        raise RateLimited(_parse_retry_after(r))
                    if r.status in (401, 403):
                        raise ReAuthRequired(f"GET {path} (post-refresh) -> HTTP {r.status}")
                    if r.status != 200:
                        raise RuntimeError(f"GET {path} (post-refresh) -> HTTP {r.status}")
                    return await r.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise RuntimeError(f"GET {path} timeout (post-refresh)") from err
        except aiohttp.ClientError as err:
            raise RuntimeError(f"GET {path} transport (post-refresh): {err}") from err

    async def get_parcels(self) -> list[dict]:
        try:
            data = await self._get(ENDPOINT_TRACKED)
        except RuntimeError as err:
            if "HTTP 404" not in str(err):
                raise
            data = await self._get(ENDPOINT_TRACKED_LEGACY)
        parcels = [p for p in (data.get("parcels") or []) if isinstance(p, dict)]
        for p in parcels:
            # reszta integracji porownuje statusy WIELKIMI literami
            if isinstance(p.get("status"), str):
                p["status"] = p["status"].upper()
        return parcels

    async def get_notifications(self) -> list[dict]:
        """Powiadomienia, ktore apka pokazuje w swojej skrzynce (i wysyla jako push)."""
        data = await self._get(ENDPOINT_NOTIFICATIONS)
        items = data.get("notifications") if isinstance(data, dict) else None
        return [n for n in (items or []) if isinstance(n, dict)]

    async def get_me(self) -> dict[str, Any]:
        """Profil konta - do rozpoznania, na ktory numer sie zalogowano."""
        data = await self._get(ENDPOINT_ME)
        return data if isinstance(data, dict) else {}


def _parse_retry_after(r: aiohttp.ClientResponse) -> float | None:
    val = r.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None
