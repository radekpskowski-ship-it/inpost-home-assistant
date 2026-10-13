"""Config flow dla integracji InPost (logowanie w przegladarce + opcje)."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InpostClient, InpostLogin, ReAuthRequired, phone_from_profile, phone_from_token
from .const import (
    CONF_AUTH_TOKEN,
    CONF_DEVICE_TRACKER,
    CONF_DEVICE_TRACKERS,
    CONF_DEVICE_UID,
    CONF_EXPOSE_CODES,
    CONF_IGNORED_SENDERS,
    CONF_NOTIFY_COOLDOWN_HOURS,
    CONF_NOTIFY_DISTANCE_M,
    CONF_NOTIFY_SERVICE,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DEVICE_TRACKERS,
    DEFAULT_EXPOSE_CODES,
    DEFAULT_NOTIFY_COOLDOWN_HOURS,
    DEFAULT_NOTIFY_DISTANCE_M,
    DEFAULT_NOTIFY_SERVICE,
    DOMAIN,
    UPDATE_INTERVAL_MIN,
    normalize_phone,
)

_LOGGER = logging.getLogger(__name__)

CONF_CALLBACK = "callback_url"
CALLBACK_SCHEMA = vol.Schema({vol.Required(CONF_CALLBACK): selector.TextSelector()})


def _resolve_default_trackers(current: dict[str, Any]) -> list[str]:
    """Backwards compat: jesli stary single device_tracker istnieje, uzyj jako default listy."""
    val = current.get(CONF_DEVICE_TRACKERS)
    if val:
        return list(val)
    legacy = (current.get(CONF_DEVICE_TRACKER) or "").strip()
    return [legacy] if legacy else list(DEFAULT_DEVICE_TRACKERS)


def _resolve_default_notify_service(current: dict[str, Any]) -> str:
    """Backwards compat: stara wartosc moze byc lista (multi-notify) lub stringiem CSV."""
    val = current.get(CONF_NOTIFY_SERVICE)
    if isinstance(val, list):
        for s in val:
            if isinstance(s, str) and "." in s:
                return s.strip()
        return DEFAULT_NOTIFY_SERVICE
    if isinstance(val, str):
        first = val.split(",", 1)[0].strip()
        if "." in first:
            return first
    return DEFAULT_NOTIFY_SERVICE


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Wspolny schemat dla setup-options (config flow) i options flow.

    Wszystkie pola optional - user moze pominac i ustawic pozniej w Configure.
    """
    return vol.Schema(
        {
            vol.Optional(
                CONF_DEVICE_TRACKERS,
                default=_resolve_default_trackers(current),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="device_tracker",
                    integration="mobile_app",
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_NOTIFY_DISTANCE_M,
                default=current.get(CONF_NOTIFY_DISTANCE_M, DEFAULT_NOTIFY_DISTANCE_M),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=10000, step=50,
                    unit_of_measurement="m", mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_NOTIFY_SERVICE,
                default=_resolve_default_notify_service(current),
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=False)
            ),
            vol.Optional(
                CONF_NOTIFY_COOLDOWN_HOURS,
                default=current.get(CONF_NOTIFY_COOLDOWN_HOURS, DEFAULT_NOTIFY_COOLDOWN_HOURS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=168, step=1,
                    unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=current.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_MIN),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=2, max=60, step=1,
                    unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_EXPOSE_CODES,
                default=current.get(CONF_EXPOSE_CODES, DEFAULT_EXPOSE_CODES),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_IGNORED_SENDERS,
                description={"suggested_value": current.get(CONF_IGNORED_SENDERS, "")},
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=False)
            ),
        }
    )


def _normalize_options(d: dict[str, Any]) -> dict[str, Any]:
    """NumberSelector zwraca float; trzymamy intery dla zwartego storage. Plus parsing typu."""
    if CONF_NOTIFY_COOLDOWN_HOURS in d and d[CONF_NOTIFY_COOLDOWN_HOURS] is not None:
        try:
            d[CONF_NOTIFY_COOLDOWN_HOURS] = int(d[CONF_NOTIFY_COOLDOWN_HOURS])
        except (TypeError, ValueError):
            pass
    if d.get(CONF_UPDATE_INTERVAL) is not None:
        try:
            d[CONF_UPDATE_INTERVAL] = int(d[CONF_UPDATE_INTERVAL])
        except (TypeError, ValueError):
            d.pop(CONF_UPDATE_INTERVAL, None)
    if CONF_IGNORED_SENDERS in d:
        d[CONF_IGNORED_SENDERS] = ", ".join(
            x.strip() for x in str(d[CONF_IGNORED_SENDERS] or "").split(",") if x.strip()
        )
    if CONF_NOTIFY_DISTANCE_M in d and d[CONF_NOTIFY_DISTANCE_M] is not None:
        try:
            d[CONF_NOTIFY_DISTANCE_M] = int(d[CONF_NOTIFY_DISTANCE_M])
        except (TypeError, ValueError):
            pass
    # NOTIFY_SERVICE: walidacja - musi zawierac "."
    if CONF_NOTIFY_SERVICE in d:
        v = d[CONF_NOTIFY_SERVICE]
        if isinstance(v, str):
            v = v.strip()
            if not v or "." not in v:
                d.pop(CONF_NOTIFY_SERVICE, None)
            else:
                d[CONF_NOTIFY_SERVICE] = v
        elif not v:
            d.pop(CONF_NOTIFY_SERVICE, None)
    # DEVICE_TRACKERS: EntitySelector(multiple=True) zwraca liste - usun jesli pusta
    if CONF_DEVICE_TRACKERS in d and not d[CONF_DEVICE_TRACKERS]:
        d.pop(CONF_DEVICE_TRACKERS, None)
    return d


class InpostConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._data: dict[str, Any] | None = None
        # Wszystkie linki pokazane w tym oknie zostaja wazne: logowanie w przegladarce
        # trwa (SMS, captcha), a w miedzyczasie formularz mogl dostac nowy link.
        self._logins = [InpostLogin()]
        # Jeden device-uid na wpis, staly: API wiaze z nim sesje.
        self._device_uid = str(uuid.uuid4())

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "InpostOptionsFlow":
        return InpostOptionsFlow()

    def _match(self, pasted: str) -> tuple[InpostLogin, str]:
        """Znajdz probe logowania, z ktorej pochodzi wklejony adres."""
        for login in reversed(self._logins):
            try:
                return login, login.extract_code(pasted)
            except ValueError as err:
                if str(err) != "state":
                    raise
        raise ValueError("state")

    async def _async_login(self, pasted: str) -> tuple[dict[str, Any] | None, str | None]:
        """Wymien wklejony adres na tokeny. Zwraca (data wpisu, None) albo (None, klucz bledu)."""
        try:
            login, code = self._match(pasted)
        except ValueError as err:
            return None, "sms_code" if str(err) == "sms" else "invalid_callback"

        session = async_get_clientsession(self.hass)
        try:
            tokens = await login.async_exchange(session, self._device_uid, code)
        except ReAuthRequired:
            error = "invalid_auth"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("InPost: wymiana kodu na tokeny nie powiodla sie")
            error = "cannot_connect"
        else:
            phone = phone_from_token(tokens["access_token"])
            if not phone:
                client = InpostClient(
                    "", tokens["access_token"], tokens["refresh_token"], session,
                    device_uid=self._device_uid, expires_at=tokens["expires_at"],
                )
                try:
                    phone = phone_from_profile(await client.get_me())
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("InPost: nie udalo sie pobrac profilu konta", exc_info=True)
            return {
                CONF_PHONE: phone,
                CONF_AUTH_TOKEN: tokens["access_token"],
                CONF_REFRESH_TOKEN: tokens["refresh_token"],
                CONF_TOKEN_EXPIRES_AT: tokens["expires_at"],
                CONF_DEVICE_UID: self._device_uid,
            }, None
        # Kod jednorazowy jest spalony - nastepna proba potrzebuje nowego linku.
        self._logins.append(InpostLogin())
        return None, error

    def _login_form(self, step_id: str, errors: dict[str, str], phone: str = ""):
        return self.async_show_form(
            step_id=step_id,
            data_schema=CALLBACK_SCHEMA,
            errors=errors,
            description_placeholders={"login_url": self._logins[-1].url, "phone": phone},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            data, error = await self._async_login(user_input[CONF_CALLBACK])
            if error:
                errors["base"] = error
            elif not data[CONF_PHONE]:
                errors["base"] = "cannot_identify"
                self._logins.append(InpostLogin())
            else:
                await self.async_set_unique_id(data[CONF_PHONE])
                self._abort_if_unique_id_configured()
                self._phone = data[CONF_PHONE]
                self._data = data
                return await self.async_step_setup_options()

        return self._login_form("user", errors)

    async def async_step_setup_options(self, user_input: dict[str, Any] | None = None):
        """Drugi krok dodawania: telefon do dystansu + powiadomienia (wszystko opcjonalne)."""
        if user_input is not None:
            options = _normalize_options(dict(user_input))
            return self.async_create_entry(
                title=f"InPost +48 {self._phone}",
                data=self._data,
                options=options,
            )

        return self.async_show_form(
            step_id="setup_options",
            data_schema=_options_schema({}),
            description_placeholders={
                "info": (
                    "Wybierz telefon (z aplikacji HA Companion) do liczenia dystansu i ustaw powiadomienia. "
                    "Mozesz wszystko pominac i skonfigurowac pozniej w 'Configure'."
                )
            },
        )

    async def async_step_reauth(self, entry_data):
        self._phone = entry_data[CONF_PHONE]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        # konkretny entry odzyskujemy przez context["entry_id"], nie po telefonie
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        phone = f"+48 {self._phone}"

        errors: dict[str, str] = {}
        if user_input is not None:
            data, error = await self._async_login(user_input[CONF_CALLBACK])
            if error:
                errors["base"] = error
            elif data[CONF_PHONE] and normalize_phone(data[CONF_PHONE]) != normalize_phone(self._phone):
                # zalogowano sie na inne konto (np. drugi numer w domu) - nie podmieniamy tokenow
                errors["base"] = "wrong_account"
                self._logins.append(InpostLogin())
            elif entry is None:
                return self.async_abort(reason="reauth_successful")
            else:
                if not data[CONF_PHONE]:
                    _LOGGER.warning("InPost: nie udalo sie potwierdzic numeru konta po logowaniu")
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, **data, CONF_PHONE: entry.data[CONF_PHONE]},
                    reason="reauth_successful",
                )

        return self._login_form("reauth_confirm", errors, phone)


class InpostOptionsFlow(config_entries.OptionsFlow):
    """Options flow: telefony do dystansu, powiadomienia, interwal, kody odbioru, ignorowani nadawcy.

    `self.config_entry` podstawia Home Assistant (>= 2024.11) - wlasny __init__ z przypisaniem
    wywala AttributeError na nowych wersjach.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize_options(dict(user_input)))
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(self.config_entry.options)
        )
