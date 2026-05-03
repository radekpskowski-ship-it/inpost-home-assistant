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
    CONF_NOTIFY_COOLDOWN_MIN,
    CONF_NOTIFY_DISTANCE_M,
    CONF_NOTIFY_SERVICE,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    DEFAULT_DEVICE_TRACKER,
    DEFAULT_NOTIFY_COOLDOWN_MIN,
    DEFAULT_NOTIFY_DISTANCE_M,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PHONE_SCHEMA = vol.Schema({vol.Required(CONF_PHONE): str})
CODE_SCHEMA = vol.Schema({vol.Required("sms_code"): str})


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Wspolny schemat dla setup-options (config flow) i options flow.

    Wszystkie pola optional - user moze pominac i ustawic pozniej w Configure.
    """
    return vol.Schema(
        {
            vol.Optional(
                CONF_DEVICE_TRACKER,
                default=current.get(CONF_DEVICE_TRACKER, DEFAULT_DEVICE_TRACKER),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="device_tracker",
                    integration="mobile_app",
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
                default=current.get(CONF_NOTIFY_SERVICE, ""),
            ): str,
            vol.Optional(
                CONF_NOTIFY_COOLDOWN_MIN,
                default=current.get(CONF_NOTIFY_COOLDOWN_MIN, DEFAULT_NOTIFY_COOLDOWN_MIN),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=1440, step=1,
                    unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _normalize_options(d: dict[str, Any]) -> dict[str, Any]:
    """NumberSelector zwraca float; trzymamy intery dla zwartego storage."""
    if CONF_NOTIFY_COOLDOWN_MIN in d and d[CONF_NOTIFY_COOLDOWN_MIN] is not None:
        try:
            d[CONF_NOTIFY_COOLDOWN_MIN] = int(d[CONF_NOTIFY_COOLDOWN_MIN])
        except (TypeError, ValueError):
            pass
    if CONF_NOTIFY_DISTANCE_M in d and d[CONF_NOTIFY_DISTANCE_M] is not None:
        try:
            d[CONF_NOTIFY_DISTANCE_M] = int(d[CONF_NOTIFY_DISTANCE_M])
        except (TypeError, ValueError):
            pass
    # usun puste stringi (zostawiamy default behavior)
    for k in (CONF_DEVICE_TRACKER, CONF_NOTIFY_SERVICE):
        if k in d and (d[k] is None or (isinstance(d[k], str) and not d[k].strip())):
            d.pop(k, None)
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
    """Options flow: telefon do liczenia dystansu, prog powiadomien, serwis notify, cooldown."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize_options(dict(user_input)))
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(self.config_entry.options)
        )
