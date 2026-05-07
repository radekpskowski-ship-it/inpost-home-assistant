"""DataUpdateCoordinator dla integracji InPost."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import InpostClient, RateLimited, ReAuthRequired
from .const import (
    CONF_AUTH_TOKEN,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    PICKUP_STATUSES,
    UPDATE_INTERVAL_MIN,
)

_LOGGER = logging.getLogger(__name__)


class InpostCoordinator(DataUpdateCoordinator[list[dict]]):
    """Pobiera liste paczek; expose: pickup_parcels (status=READY_TO_PICKUP itp.)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data[CONF_PHONE]}",
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MIN),
        )
        self.entry = entry
        self._session = async_get_clientsession(hass)

        @callback
        def _on_tokens(auth: str, refresh: str) -> None:
            new_data = {**entry.data, CONF_AUTH_TOKEN: auth, CONF_REFRESH_TOKEN: refresh}
            hass.config_entries.async_update_entry(entry, data=new_data)

        self.client = InpostClient(
            phone_number=entry.data[CONF_PHONE],
            auth_token=entry.data[CONF_AUTH_TOKEN],
            refresh_token=entry.data[CONF_REFRESH_TOKEN],
            session=self._session,
            on_tokens_updated=_on_tokens,
        )

    async def _async_update_data(self) -> list[dict]:
        try:
            return await self.client.get_parcels()
        except ReAuthRequired as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RateLimited as err:
            raise UpdateFailed(f"InPost API rate-limited: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"InPost transport error: {err}") from err
        except RuntimeError as err:
            raise UpdateFailed(f"InPost API error: {err}") from err

    @property
    def all_parcels(self) -> list[dict]:
        """Wszystkie aktywne paczki widoczne w API (kazdy status, nie tylko 'do odbioru')."""
        return list(self.data or [])

    @property
    def pickup_parcels(self) -> list[dict]:
        return [p for p in (self.data or []) if p.get("status") in PICKUP_STATUSES]
