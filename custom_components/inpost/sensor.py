"""Sensory: jeden per PACZKOMAT z aktywnymi paczkami + sensor zbiorczy + 'najblizsza paczka [m]'.

Encja per paczkomat dziala w ten sposob: gdy 1+ paczka uzytkownika trafi do
konkretnego paczkomatu, powstaje `sensor.inpost_paczkomat_<kod>`. State = ile
paczek tam czeka, atrybuty zawieraja adres / lat-lon / godziny + liste paczek.
Encja znika po 2 pustych odczytach (grace) gdy wszystkie paczki w niej zostana
odebrane.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import haversine_m
from .const import (
    AUTOREMOVE_GRACE_TICKS,
    CONF_DEVICE_TRACKER,
    CONF_DEVICE_TRACKERS,
    CONF_PHONE,
    DOMAIN,
    canonicalize_sender,
    is_sender_ignored,
)
from .coordinator import InpostCoordinator

_LOGGER = logging.getLogger(__name__)

# Sanitizacja kodu paczkomatu pod entity_id (HA wymaga [a-z0-9_]).
# UWAGA: dla unique_id uzywamy SUROWEJ nazwy - chroni przed kolizjami slug.
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def _slug_for_entity_id(s: str) -> str:
    return _NON_ALNUM_RE.sub("_", str(s)).strip("_").lower()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: InpostCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # static: zbiorczy + 'najblizszy paczkomat'
    async_add_entities([
        InpostCountSensor(coord, entry),
        InpostNearestSensor(coord, entry),
    ])

    # MIGRACJA z <0.9.0: starsze wersje tworzyly encje per paczka z prefixem
    # `_parcel_`. Nowy kod ich nie tworzy, ale registry je trzyma jako sieroty.
    # Usuwamy je raz przy starcie - czyste przejscie na model per-paczkomat.
    parcel_uid_prefix = f"{entry.entry_id}_parcel_"
    ent_reg_init = er.async_get(hass)
    for ent in list(er.async_entries_for_config_entry(ent_reg_init, entry.entry_id)):
        if (ent.unique_id or "").startswith(parcel_uid_prefix):
            _LOGGER.info("InPost: migracja - usuwam stara encje %s (model per-paczka)", ent.entity_id)
            ent_reg_init.async_remove(ent.entity_id)

    # dynamiczne: synchronizujemy entity_registry z aktywnymi paczkomatami.
    # Klucz: surowa nazwa pickup_point (np. "BIA45M").
    pp_uid_prefix = f"{entry.entry_id}_pickup_point_"
    miss_count: dict[str, int] = {}

    @callback
    def _refresh() -> None:
        ent_reg = er.async_get(hass)

        # zbierz unikalne paczkomaty z aktywnymi paczkami (tylko PICKUP_STATUSES,
        # pomijajac ignorowanych nadawcow)
        active: set[str] = set()
        for p in coord.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            pp = p.get("pickUpPoint") or {}
            pp_name = pp.get("name")
            if pp_name:
                active.add(str(pp_name))

        # encje paczkomatow znane registry'emu
        existing: set[str] = set()
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = ent.unique_id or ""
            if uid.startswith(pp_uid_prefix):
                existing.add(uid[len(pp_uid_prefix):])

        # ADD: nowe paczkomaty bez encji
        new_entities: list[SensorEntity] = []
        for pp_name in active - existing:
            new_entities.append(InpostPickupPointSensor(coord, entry, pp_name))
        if new_entities:
            async_add_entities(new_entities)

        # zresetuj licznik missow dla paczkomatow ktore wrocily
        for pp_name in active:
            miss_count.pop(pp_name, None)

        # GRACE: usuwamy puste paczkomaty po N pustych odczytach
        for pp_name in existing - active:
            miss_count[pp_name] = miss_count.get(pp_name, 0) + 1
            if miss_count[pp_name] < AUTOREMOVE_GRACE_TICKS:
                _LOGGER.debug(
                    "InPost: paczkomat %s pusty (%d/%d) - czekam przed usunieciem",
                    pp_name, miss_count[pp_name], AUTOREMOVE_GRACE_TICKS,
                )
                continue
            unique_id = pp_uid_prefix + pp_name
            entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                _LOGGER.info("InPost: usuwam encje %s (wszystkie paczki odebrane)", entity_id)
                ent_reg.async_remove(entity_id)
            miss_count.pop(pp_name, None)

    _refresh()
    entry.async_on_unload(coord.async_add_listener(_refresh))


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    phone = entry.data.get(CONF_PHONE, "")
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="InPost",
        manufacturer="InPost",
        model=f"+48 {phone}" if phone else "Mobile API",
        configuration_url="https://inpost.pl/",
    )


# ---------------------------------------------------------------------------


class InpostBase(CoordinatorEntity[InpostCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord: InpostCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coord)
        self._entry = entry
        self._attr_device_info = _device_info(entry)


class InpostCountSensor(InpostBase):
    """Liczba paczek aktualnie czekajacych w paczkomacie do odbioru.

    State = liczba paczek w PICKUP_STATUSES (z pominieciem ignorowanych nadawcow).
    """

    _attr_translation_key = "count"
    _attr_name = "Paczki do odbioru"
    _attr_icon = "mdi:package-variant-closed-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coord, entry):
        super().__init__(coord, entry)
        self._attr_unique_id = f"{entry.entry_id}_count"

    @property
    def native_value(self) -> int:
        return sum(
            1 for p in self.coordinator.pickup_parcels
            if not is_sender_ignored((p.get("sender") or {}).get("name"))
        )


class InpostNearestSensor(InpostBase):
    """Dystans (m) z wybranego device_tracker do najblizszego paczkomatu z paczka do odbioru."""

    _attr_translation_key = "nearest_distance"
    _attr_name = "Najblizsza paczka (dystans)"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = UnitOfLength.METERS

    def __init__(self, coord, entry):
        super().__init__(coord, entry)
        self._attr_unique_id = f"{entry.entry_id}_nearest_distance"

    @property
    def _trackers(self) -> list[str]:
        opts = self._entry.options
        v = opts.get(CONF_DEVICE_TRACKERS)
        if isinstance(v, list) and v:
            return [t for t in v if t]
        legacy = (opts.get(CONF_DEVICE_TRACKER) or "").strip()
        return [legacy] if legacy else []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        trackers = self._trackers
        if trackers:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, trackers, self._tracker_changed
                )
            )
        self.async_write_ha_state()

    @callback
    def _tracker_changed(self, _event) -> None:
        self.async_write_ha_state()

    def _user_positions(self) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for t in self._trackers:
            st = self.hass.states.get(t)
            if not st:
                continue
            la = st.attributes.get("latitude")
            lo = st.attributes.get("longitude")
            if la is None or lo is None:
                continue
            try:
                out.append((float(la), float(lo)))
            except (TypeError, ValueError):
                continue
        return out

    @property
    def available(self) -> bool:
        return bool(self._trackers) and super().available

    @property
    def native_value(self):
        positions = self._user_positions()
        if not positions:
            return None
        best = None
        for p in self.coordinator.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            pp = p.get("pickUpPoint") or {}
            loc = pp.get("location") or {}
            la = loc.get("latitude")
            lo = loc.get("longitude")
            if la is None or lo is None:
                continue
            try:
                lap, lop = float(la), float(lo)
            except (TypeError, ValueError):
                continue
            for ula, ulo in positions:
                d = haversine_m(ula, ulo, lap, lop)
                if best is None or d < best[0]:
                    best = (d, p)
        return round(best[0]) if best else None

    @property
    def extra_state_attributes(self):
        positions = self._user_positions()
        if not positions:
            return {}
        nearest_name = None
        nearest_d = None
        for p in self.coordinator.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            pp = p.get("pickUpPoint") or {}
            loc = pp.get("location") or {}
            la = loc.get("latitude")
            lo = loc.get("longitude")
            if la is None or lo is None:
                continue
            try:
                lap, lop = float(la), float(lo)
            except (TypeError, ValueError):
                continue
            for ula, ulo in positions:
                d = haversine_m(ula, ulo, lap, lop)
                if nearest_d is None or d < nearest_d:
                    nearest_d = d
                    nearest_name = pp.get("name")
        return {
            "nearest_pickup_point": nearest_name,
            "trackers_used": len(positions),
        }


class InpostPickupPointSensor(InpostBase):
    """Paczkomat z paczkami czekajacymi na odbior.

    State = liczba paczek w tym paczkomacie. Atrybuty: adres, wspolrzedne,
    godziny otwarcia + lista paczek (sender, open_code, qr_code, expiry).
    Encja istnieje tylko gdy 1+ paczka czeka - po pustym refreshu znika
    przez grace period (AUTOREMOVE_GRACE_TICKS).
    """

    _attr_icon = "mdi:package-variant-closed-check"

    def __init__(self, coord: InpostCoordinator, entry: ConfigEntry, pickup_point_name: str):
        super().__init__(coord, entry)
        self._pp = str(pickup_point_name)
        # unique_id: SUROWA nazwa paczkomatu (chroni przed kolizjami slug)
        self._attr_unique_id = f"{entry.entry_id}_pickup_point_{self._pp}"
        # entity_id sufix: alfanumeryczny
        self._attr_suggested_object_id = f"paczkomat_{_slug_for_entity_id(self._pp)}"
        # name = kod paczkomatu (HA i tak doda prefix urzadzenia "InPost ")
        self._attr_name = self._pp

    def _parcels(self) -> list[dict]:
        out = []
        for p in self.coordinator.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            pp = p.get("pickUpPoint") or {}
            if str(pp.get("name") or "") == self._pp:
                out.append(p)
        return out

    @property
    def available(self) -> bool:
        return len(self._parcels()) > 0

    @property
    def native_value(self) -> int:
        return len(self._parcels())

    @property
    def entity_picture(self) -> str | None:
        parcels = self._parcels()
        if not parcels:
            return None
        return (parcels[0].get("pickUpPoint") or {}).get("imageUrl")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parcels = self._parcels()
        if not parcels:
            return {}
        # info o paczkomacie - bierzemy z pierwszej paczki (te same dla wszystkich w tym pp)
        pp = parcels[0].get("pickUpPoint") or {}
        loc = pp.get("location") or {}
        addr = pp.get("addressDetails") or {}
        address_str = " ".join(x for x in [
            addr.get("street"),
            addr.get("buildingNumber"),
            f'({addr.get("postCode", "")} {addr.get("city", "")})'.strip(),
        ] if x).strip()
        parcel_list = []
        for p in parcels:
            raw_sender = (p.get("sender") or {}).get("name")
            parcel_list.append({
                "shipment_number": p.get("shipmentNumber"),
                "sender": canonicalize_sender(raw_sender),
                "sender_raw": raw_sender,
                "open_code": p.get("openCode"),
                "qr_code": p.get("qrCode"),
                "expiry_date": p.get("expiryDate"),
                "stored_date": p.get("storedDate"),
                "status_raw": p.get("status"),
            })
        return {
            "pickup_point": self._pp,
            "address": address_str,
            "city": addr.get("city"),
            "street": addr.get("street"),
            "post_code": addr.get("postCode"),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "opening_hours": pp.get("openingHours"),
            "location_description": pp.get("locationDescription"),
            "is_24_7": pp.get("location247"),
            "easy_access_zone": pp.get("easyAccessZone"),
            "count": len(parcels),
            "parcels": parcel_list,
        }
