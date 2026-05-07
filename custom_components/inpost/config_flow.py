"""Config flow dla integracji InPost (telefon + SMS + opcje)."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import confirm_sms_code, send_sms_code
from .const import (
    CONF_AUTH_TOKEN,
    CONF_DEVICE_TRACKER,
    CONF_DEVICE_TRACKERS,
    CONF_NOTIFY_COOLDOWN_HOURS,
    CONF_NOTIFY_DISTANCE_M,
    CONF_NOTIFY_SERVICE,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    DEFAULT_DEVICE_TRACKERS,
    DEFAULT_NOTIFY_COOLDOWN_HOURS,
    DEFAULT_NOTIFY_DISTANCE_M,
    DEFAULT_NOTIFY_SERVICE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PHONE_SCHEMA = vol.Schema({vol.Required(CONF_PHONE): str})
CODE_SCHEMA = vol.Schema({vol.Required("sms_code"): str})


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
        }
    )


def _normalize_options(d: dict[str, Any]) -> dict[str, Any]:
    """NumberSelector zwraca float; trzymamy intery dla zwartego storage. Plus parsing typu."""
    if CONF_NOTIFY_COOLDOWN_HOURS in d and d[CONF_NOTIFY_COOLDOWN_HOURS] is not None:
        try:
            d[CONF_NOTIFY_COOLDOWN_HOURS] = int(d[CONF_NOTIFY_COOLDOWN_HOURS])
        except (TypeError, ValueError):
            pass
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
        self._tokens: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "InpostOptionsFlow":
        return InpostOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = user_input[CONF_PHONE].strip().replace("+48", "").replace(" ", "")
            await self.async_set_unique_id(phone)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            try:
                ok = await send_sms_code(session, phone)
            except Exception:
                _LOGGER.exception("send_sms_code error")
                ok = False
            if not ok:
                errors["base"] = "cannot_send_sms"
            else:
                self._phone = phone
                return await self.async_step_sms()

        return self.async_show_form(
            step_id="user", data_schema=PHONE_SCHEMA, errors=errors,
            description_placeholders={"info": "Numer telefonu z konta InPost (9 cyfr, bez +48)"}
        )

    async def async_step_sms(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            code = user_input["sms_code"].strip()
            session = async_get_clientsession(self.hass)
            try:
                tokens = await confirm_sms_code(session, self._phone, code)
            except Exception:
                _LOGGER.exception("confirm_sms_code error")
                tokens = None
            if not tokens:
                errors["base"] = "wrong_code"
            else:
                self._tokens = tokens
                return await self.async_step_setup_options()

        return self.async_show_form(
            step_id="sms", data_schema=CODE_SCHEMA, errors=errors,
            description_placeholders={"phone": f"+48 {self._phone}"}
        )

    async def async_step_setup_options(self, user_input: dict[str, Any] | None = None):
        """Trzeci krok dodawania: telefon do dystansu + powiadomienia (wszystko opcjonalne)."""
        if user_input is not None:
            options = _normalize_options(dict(user_input))
            return self.async_create_entry(
                title=f"InPost +48 {self._phone}",
                data={
                    CONF_PHONE: self._phone,
                    CONF_AUTH_TOKEN: self._tokens["authToken"],
                    CONF_REFRESH_TOKEN: self._tokens["refreshToken"],
                },
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

        if user_input is None:
            session = async_get_clientsession(self.hass)
            await send_sms_code(session, self._phone)
            return self.async_show_form(step_id="reauth_confirm", data_schema=CODE_SCHEMA)

        session = async_get_clientsession(self.hass)
        tokens = await confirm_sms_code(
            session, self._phone, user_input["sms_code"].strip()
        )
        if not tokens:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=CODE_SCHEMA,
                errors={"base": "wrong_code"},
            )

        if entry is None:
            return self.async_abort(reason="reauth_successful")

        return self.async_update_reload_and_abort(
            entry,
            data={
                **entry.data,
                CONF_AUTH_TOKEN: tokens["authToken"],
                CONF_REFRESH_TOKEN: tokens["refreshToken"],
            },
            reason="reauth_successful",
        )


class InpostOptionsFlow(config_entries.OptionsFlow):
    """Options flow: telefony do liczenia dystansu, prog powiadomien, serwis notify, cooldown."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize_options(dict(user_input)))
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(self.config_entry.options)
        )
