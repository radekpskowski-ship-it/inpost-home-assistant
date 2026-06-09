"""DataUpdateCoordinator dla integracji InPost."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.util.dt as dt_util
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import InpostClient, RateLimited, ReAuthRequired
from .const import (
    AVAILABILITY_GRACE_MIN,
    BOOST_INTERVAL_MIN,
    BOOST_MAX_MIN,
    CONF_AUTH_TOKEN,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    PICKUP_STATUSES,
    RECENT_PICKUP_STATUSES,
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
        self._last_success: datetime | None = None
        # sn -> czas ostatniego podgladu QR; obecnosc = paczka w trybie boost (2-min polling)
        self._qr_viewed: dict[str, datetime] = {}
        self._normal_interval = timedelta(minutes=UPDATE_INTERVAL_MIN)
        self._boost_interval = timedelta(minutes=BOOST_INTERVAL_MIN)

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
            data = await self.client.get_parcels()
            self._last_success = dt_util.utcnow()
            self._reconcile_boost(data)
            return data
        except ReAuthRequired as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RateLimited as err:
            raise UpdateFailed(f"InPost API rate-limited: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"InPost transport error: {err}") from err
        except RuntimeError as err:
            raise UpdateFailed(f"InPost API error: {err}") from err

    @property
    def is_recently_alive(self) -> bool:
        """True dopoki API odpowiadalo w oknie AVAILABILITY_GRACE_MIN.

        Encje statyczne (np. licznik 'Paczki do odbioru') uzywaja tego zamiast
        surowego last_update_success - pojedynczy nieudany poll NIE wrzuca ich
        od razu w 'unavailable' (dane sa zachowane przez DataUpdateCoordinator).
        """
        if self.last_update_success:
            return True
        if self._last_success is None:
            return False
        return (dt_util.utcnow() - self._last_success) < timedelta(minutes=AVAILABILITY_GRACE_MIN)

    @callback
    def note_qr_viewed(self, shipment_number: str) -> None:
        """Ktos wyswietlil QR odbioru -> wlacz boost (2-min polling) i odpytaj od razu."""
        sn = str(shipment_number)
        first = sn not in self._qr_viewed
        self._qr_viewed[sn] = dt_util.utcnow()
        if first:
            _LOGGER.info("InPost: QR paczki %s wyswietlony - boost %d-min do odbioru", sn, BOOST_INTERVAL_MIN)
        self._apply_interval()
        if first:
            # natychmiastowe sprawdzenie zamiast czekania do nastepnego cyklu
            self.hass.async_create_task(self.async_request_refresh())

    def _reconcile_boost(self, data: list[dict]) -> None:
        """Po kazdym udanym pollu: zdejmij z boostu paczki odebrane / znikniete / przeterminowane."""
        if not self._qr_viewed:
            return
        now = dt_util.utcnow()
        by_sn = {str(p.get("shipmentNumber") or p.get("id") or ""): p for p in data}
        cap = timedelta(minutes=BOOST_MAX_MIN)
        for sn in list(self._qr_viewed):
            p = by_sn.get(sn)
            status = (p or {}).get("status")
            picked_up = status in RECENT_PICKUP_STATUSES
            gone = p is None
            expired = (now - self._qr_viewed[sn]) >= cap
            if picked_up or gone or expired:
                why = "odebrana" if picked_up else ("zniknela z API" if gone else "limit czasu")
                _LOGGER.info("InPost: koniec boostu dla paczki %s (%s)", sn, why)
                self._qr_viewed.pop(sn, None)
        # wewnatrz cyklu update - koordynator przeplanuje poll sam po zwroceniu danych
        self._apply_interval(reschedule=False)

    @callback
    def _apply_interval(self, *, reschedule: bool = True) -> None:
        """Ustaw interwal pollingu: boost gdy cokolwiek wyswietlone, inaczej normalny."""
        target = self._boost_interval if self._qr_viewed else self._normal_interval
        if self.update_interval != target:
            self.update_interval = target
            # przeplanuj nastepny poll wg nowego interwalu (gdy wolane spoza cyklu update)
            if reschedule:
                self._schedule_refresh()

    @property
    def all_parcels(self) -> list[dict]:
        """Wszystkie aktywne paczki widoczne w API (kazdy status, nie tylko 'do odbioru')."""
        return list(self.data or [])

    @property
    def pickup_parcels(self) -> list[dict]:
        return [p for p in (self.data or []) if p.get("status") in PICKUP_STATUSES]
