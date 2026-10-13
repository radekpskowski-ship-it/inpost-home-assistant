"""DataUpdateCoordinator dla integracji InPost."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import InpostClient, RateLimited, ReAuthRequired
from .const import (
    BOOST_INTERVAL_MIN,
    BOOST_MAX_MIN,
    CONF_AUTH_TOKEN,
    CONF_DEVICE_UID,
    CONF_EXPOSE_CODES,
    CONF_IGNORED_SENDERS,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_EXPOSE_CODES,
    DOMAIN,
    EVENT_BUS_NOTIFICATION,
    EVENT_BUS_PARCEL,
    EVENT_DELIVERED,
    EVENT_NEW_PARCEL,
    EVENT_NOTIFICATION,
    EVENT_READY_TO_PICKUP,
    EVENT_STATUS_CHANGED,
    MAX_SEEN_NOTIFICATIONS,
    PICKUP_STATUSES,
    RECENT_PICKUP_STATUSES,
    RETURN_STATUSES,
    STATUS_LABELS_PL,
    STORAGE_KEY_EVENTS,
    STORAGE_VERSION,
    TERMINAL_STATUSES,
    UPDATE_INTERVAL_MIN,
    canonicalize_sender,
    is_sender_ignored,
    normalize_phone,
)

_LOGGER = logging.getLogger(__name__)


def parcel_sn(parcel: dict) -> str:
    return str(parcel.get("shipmentNumber") or parcel.get("id") or "")


def format_pp_address(addr_details: dict, *, with_postcode: bool = False) -> str:
    """Buduje krotki adres paczkomatu: 'ulica numer, miasto' (lub z kodem gdy with_postcode).

    addr_details: pickUpPoint.addressDetails z API (street/buildingNumber/city/postCode).
    """
    street = (addr_details.get("street") or "").strip()
    building = (addr_details.get("buildingNumber") or "").strip()
    city = (addr_details.get("city") or "").strip()
    post = (addr_details.get("postCode") or "").strip()
    line1 = " ".join(x for x in [street, building] if x).strip()
    if with_postcode and post:
        right = f"{post} {city}".strip() if city else post
    else:
        right = city
    parts = [x for x in [line1, right] if x]
    return ", ".join(parts)


def _account_phones(hass: HomeAssistant) -> set[str]:
    """Numery wszystkich dodanych kont InPost (znormalizowane do 9 cyfr)."""
    phones: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue  # konto niezalogowane / wylaczone nie "zabiera" paczek
        p = normalize_phone(entry.data.get(CONF_PHONE))
        if p:
            phones.add(p)
    return phones


def parcel_belongs_to_entry(hass: HomeAssistant, entry: ConfigEntry, parcel: dict) -> bool:
    """Czy paczka ma byc materializowana jako encja na TYM koncie (dedup `sharedTo`).

    Paczke wspoldzielona widac w API obu kont -> bez dedup powstaja duplikaty
    encji (m.in. dwie karty QR) na kazdym dodanym numerze. Regula: encje tworzy
    konto bedace ODBIORCA paczki (`receiver.phoneNumber` == numer konta).

    Fallback bezpieczny - zeby nic nie zniknelo z dashboardu:
      * brak `receiver.phoneNumber` w API -> pokaz (nie ma jak rozsadzic),
      * odbiorca to osoba trzecia (zaden z dodanych numerow) -> pokaz wszedzie.
    Ukrywamy WYLACZNIE gdy odbiorca to INNE konto dodane do tej integracji.
    """
    recv = normalize_phone((parcel.get("receiver") or {}).get("phoneNumber"))
    if not recv:
        return True
    if recv == normalize_phone(entry.data.get(CONF_PHONE)):
        return True
    return recv not in _account_phones(hass)


class InpostCoordinator(DataUpdateCoordinator[list[dict]]):
    """Pobiera liste paczek; expose: pickup_parcels (status=READY_TO_PICKUP itp.)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data[CONF_PHONE]}",
            update_interval=timedelta(minutes=self._interval_min(entry)),
        )
        self.entry = entry
        self._session = async_get_clientsession(hass)
        self._last_success: datetime | None = None
        # sn -> czas ostatniego podgladu QR; obecnosc = paczka w trybie boost (2-min polling)
        self._qr_viewed: dict[str, datetime] = {}
        self._normal_interval = timedelta(minutes=self._interval_min(entry))
        self._grace = self._normal_interval * 3
        self._extra_ignored = [
            x.strip().lower()
            for x in str(entry.options.get(CONF_IGNORED_SENDERS) or "").split(",")
            if x.strip()
        ]
        # Zdarzenia: co juz oglosilismy (przezywa restart - restart nie powtarza starych zdarzen).
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_EVENTS}.{entry.entry_id}")
        self._state_loaded = False
        self._known_status: dict[str, str] | None = None
        self._seen_notifications: list[str] | None = None
        self._event_listeners: dict[str, list[Callable[[str, dict[str, Any]], None]]] = {}
        self.notifications: list[dict[str, Any]] = []
        self._boost_interval = timedelta(minutes=BOOST_INTERVAL_MIN)

        @callback
        def _on_tokens(auth: str, refresh: str, expires_at: float) -> None:
            new_data = {
                **entry.data,
                CONF_AUTH_TOKEN: auth,
                CONF_REFRESH_TOKEN: refresh,
                CONF_TOKEN_EXPIRES_AT: expires_at,
            }
            hass.config_entries.async_update_entry(entry, data=new_data)

        self.client = InpostClient(
            phone_number=entry.data[CONF_PHONE],
            auth_token=entry.data[CONF_AUTH_TOKEN],
            refresh_token=entry.data[CONF_REFRESH_TOKEN],
            session=self._session,
            device_uid=entry.data.get(CONF_DEVICE_UID),
            expires_at=entry.data.get(CONF_TOKEN_EXPIRES_AT),
            on_tokens_updated=_on_tokens,
        )

    @staticmethod
    def _interval_min(entry: ConfigEntry) -> int:
        try:
            return max(1, int(entry.options.get(CONF_UPDATE_INTERVAL) or UPDATE_INTERVAL_MIN))
        except (TypeError, ValueError):
            return UPDATE_INTERVAL_MIN

    @property
    def expose_codes(self) -> bool:
        return bool(self.entry.options.get(CONF_EXPOSE_CODES, DEFAULT_EXPOSE_CODES))

    def _ignored(self, parcel: dict) -> bool:
        name = (parcel.get("sender") or {}).get("name")
        if is_sender_ignored(name):
            return True
        low = (name or "").strip().lower()
        return bool(low) and any(x in low for x in self._extra_ignored)

    async def _async_update_data(self) -> list[dict]:
        try:
            if not self._state_loaded:
                stored = await self._store.async_load()
                if isinstance(stored, dict):
                    if isinstance(stored.get("parcels"), dict):
                        self._known_status = {str(k): str(v) for k, v in stored["parcels"].items()}
                    if isinstance(stored.get("notifications"), list):
                        self._seen_notifications = [str(x) for x in stored["notifications"]]
                self._state_loaded = True
            # ignorowani nadawcy odpadaja u zrodla - reszta integracji ich nie widzi
            data = [p for p in await self.client.get_parcels() if not self._ignored(p)]
            self._last_success = dt_util.utcnow()
            self._reconcile_boost(data)
            dirty = self._detect_parcel_changes(data)
            dirty |= await self._async_poll_notifications()
            if dirty:
                self._store.async_delay_save(self._snapshot, 5)
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
        return (dt_util.utcnow() - self._last_success) < self._grace

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

    def is_own(self, parcel: dict) -> bool:
        return parcel_belongs_to_entry(self.hass, self.entry, parcel)

    @property
    def own_parcels(self) -> list[dict]:
        """Paczki tego konta - wspoldzielone (`sharedTo`) liczy tylko konto-odbiorca."""
        return [p for p in (self.data or []) if self.is_own(p)]

    @property
    def own_pickup_parcels(self) -> list[dict]:
        return [p for p in self.own_parcels if p.get("status") in PICKUP_STATUSES]

    @property
    def own_in_transit_parcels(self) -> list[dict]:
        done = PICKUP_STATUSES | RECENT_PICKUP_STATUSES | TERMINAL_STATUSES | RETURN_STATUSES
        return [p for p in self.own_parcels if p.get("status") and p.get("status") not in done]

    # ------------------------------------------------------------------ zdarzenia

    @callback
    def async_add_event_listener(
        self, kind: str, listener: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        """kind: 'parcel' | 'notification'. Listener dostaje (event_type, payload)."""
        self._event_listeners.setdefault(kind, []).append(listener)

        def _remove() -> None:
            self._event_listeners.get(kind, []).remove(listener)

        return _remove

    def _emit(self, kind: str, event_type: str, payload: dict[str, Any], bus_event: str) -> None:
        for listener in list(self._event_listeners.get(kind, [])):
            listener(event_type, payload)
        self.hass.bus.async_fire(
            bus_event,
            {"type": event_type, "account": self.entry.data.get(CONF_PHONE), **payload},
        )

    def _snapshot(self) -> dict[str, Any]:
        return {
            "parcels": self._known_status or {},
            "notifications": (self._seen_notifications or [])[-MAX_SEEN_NOTIFICATIONS:],
        }

    def parcel_payload(self, parcel: dict, previous_status: str | None = None) -> dict[str, Any]:
        """Plaski opis paczki do zdarzen (kod odbioru tylko przy wlaczonej opcji)."""
        pp = parcel.get("pickUpPoint") or {}
        status = parcel.get("status")
        raw_sender = (parcel.get("sender") or {}).get("name")
        out: dict[str, Any] = {
            "shipment_number": parcel_sn(parcel),
            "sender": canonicalize_sender(raw_sender) or raw_sender,
            "status_raw": status,
            "status": STATUS_LABELS_PL.get(status, status),
            "previous_status_raw": previous_status,
            "pickup_point": pp.get("name"),
            "address": format_pp_address(pp.get("addressDetails") or {}, with_postcode=True),
            "expiry_date": parcel.get("expiryDate"),
        }
        if self.expose_codes:
            out["open_code"] = parcel.get("openCode")
        return out

    def _detect_parcel_changes(self, data: list[dict]) -> bool:
        """Porownaj statusy z zapamietanymi i wyemituj zdarzenia. True gdy cos sie zmienilo."""
        current: dict[str, str] = {}
        by_sn: dict[str, dict] = {}
        for p in data:
            sn = parcel_sn(p)
            if not sn or not p.get("status") or not parcel_belongs_to_entry(self.hass, self.entry, p):
                continue
            current[sn] = p["status"]
            by_sn[sn] = p
        if self._known_status is None:
            # pierwsze uruchomienie: stan zastany to punkt wyjscia, nic nie oglaszamy
            self._known_status = current
            return True
        if current == self._known_status:
            return False
        finished = RECENT_PICKUP_STATUSES | TERMINAL_STATUSES
        for sn, status in current.items():
            prev = self._known_status.get(sn)
            if prev == status:
                continue
            if prev is None:
                if status in finished:
                    continue  # stara, zakonczona paczka dopiero co widoczna - to nie nowosc
                etype = EVENT_READY_TO_PICKUP if status in PICKUP_STATUSES else EVENT_NEW_PARCEL
            elif status in PICKUP_STATUSES and prev not in PICKUP_STATUSES:
                etype = EVENT_READY_TO_PICKUP
            elif status in RECENT_PICKUP_STATUSES:
                etype = EVENT_DELIVERED
            else:
                etype = EVENT_STATUS_CHANGED
            _LOGGER.debug("InPost: zdarzenie %s dla paczki %s (%s -> %s)", etype, sn, prev, status)
            self._emit("parcel", etype, self.parcel_payload(by_sn[sn], prev), EVENT_BUS_PARCEL)
        self._known_status = current
        return True

    async def _async_poll_notifications(self) -> bool:
        """Powiadomienia z apki InPost. Blad tutaj NIE psuje pollu paczek."""
        try:
            raw = await self.client.get_notifications()
        except ReAuthRequired:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("InPost: nie udalo sie pobrac powiadomien: %s", err)
            return False
        items = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            items.append({
                "id": str(item["id"]),
                "title": item.get("title"),
                "message": item.get("content"),
                "date": item.get("date"),
                "shipment_number": item.get("shipmentNumber"),
                "sender": item.get("sender"),
            })
        items.sort(key=lambda n: str(n.get("date") or ""))
        self.notifications = items[-20:]
        ids = [n["id"] for n in items]
        if self._seen_notifications is None:
            self._seen_notifications = ids
            return True
        seen = set(self._seen_notifications)
        fresh = [n for n in items if n["id"] not in seen]
        if not fresh:
            return False
        for n in fresh:
            self._emit(
                "notification", EVENT_NOTIFICATION,
                {k: v for k, v in n.items() if k != "id"}, EVENT_BUS_NOTIFICATION,
            )
        self._seen_notifications = (self._seen_notifications + [n["id"] for n in fresh])[-MAX_SEEN_NOTIFICATIONS:]
        return True
