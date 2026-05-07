"""Sensory: zbiorczy + 'najblizsza paczka [m]' + per-paczka + per-paczkomat.

Model hybrydowy:
- `sensor.inpost_paczka_<shipmentNumber>` per paczka, state = polski label statusu
  (np. 'Potwierdzona', 'W doreczeniu'). Friendly name = nazwa nadawcy
  (z aliasow: Cainiao -> AliExpress itd.). Tworzona dla kazdej paczki w API
  poza statusami terminalnymi (DELIVERED/PICKED_UP/CANCELED/PICKUP_TIME_EXPIRED)
  i ignorowanymi nadawcami (`eryk sssss`).
- `sensor.inpost_paczkomat_<kod>` per paczkomat - tworzona TYLKO gdy 1+ paczka
  fizycznie czeka w nim (statusy PICKUP_STATUSES). State = liczba paczek.
  Friendly name = adres ('ulica numer, miasto'). W atrybutach pelne info
  o paczkomacie + lista paczek.

Encje znikaja przez grace period (AUTOREMOVE_GRACE_TICKS) gdy paczka osiagnie
status terminalny / paczkomat sie oprozni.
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
    DEFAULT_PARCEL_ICON,
    DOMAIN,
    STATUS_ICONS,
    STATUS_LABELS_PL,
    TERMINAL_STATUSES,
    canonicalize_sender,
    is_sender_ignored,
)
from .coordinator import InpostCoordinator

_LOGGER = logging.getLogger(__name__)

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def _slug_for_entity_id(s: str) -> str:
    return _NON_ALNUM_RE.sub("_", str(s)).strip("_").lower()


def _format_pp_address(addr_details: dict, *, with_postcode: bool = False) -> str:
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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: InpostCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # static
    async_add_entities([
        InpostCountSensor(coord, entry),
        InpostNearestSensor(coord, entry),
    ])

    parcel_uid_prefix = f"{entry.entry_id}_parcel_"
    pp_uid_prefix = f"{entry.entry_id}_pickup_point_"
    parcel_miss: dict[str, int] = {}  # sn -> ile pustych odczytow
    pp_miss: dict[str, int] = {}      # pp_name -> ile pustych odczytow

    @callback
    def _refresh() -> None:
        ent_reg = er.async_get(hass)
        new_entities: list[SensorEntity] = []

        # ============ PER-PACZKA ============
        # Aktywne paczki = wszystkie w API poza terminalami i ignorowanymi nadawcami.
        active_parcels: set[str] = set()
        for p in coord.all_parcels:
            if p.get("status") in TERMINAL_STATUSES:
                continue
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            sn = p.get("shipmentNumber") or p.get("id")
            if sn:
                active_parcels.add(str(sn))

        existing_parcels: set[str] = set()
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = ent.unique_id or ""
            if uid.startswith(parcel_uid_prefix):
                existing_parcels.add(uid[len(parcel_uid_prefix):])

        for sn in active_parcels - existing_parcels:
            new_entities.append(InpostParcelSensor(coord, entry, sn))
        for sn in active_parcels:
            parcel_miss.pop(sn, None)
        for sn in existing_parcels - active_parcels:
            parcel_miss[sn] = parcel_miss.get(sn, 0) + 1
            if parcel_miss[sn] < AUTOREMOVE_GRACE_TICKS:
                _LOGGER.debug(
                    "InPost: paczka %s nieobecna (%d/%d) - czekam przed usunieciem",
                    sn, parcel_miss[sn], AUTOREMOVE_GRACE_TICKS,
                )
                continue
            entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, parcel_uid_prefix + sn)
            if entity_id:
                _LOGGER.info("InPost: usuwam encje paczki %s (terminal/ignore)", entity_id)
                ent_reg.async_remove(entity_id)
            parcel_miss.pop(sn, None)

        # ============ PER-PACZKOMAT ============
        # Paczkomat tworzony tylko gdy paczka FIZYCZNIE w nim (PICKUP_STATUSES).
        active_pps: set[str] = set()
        for p in coord.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            pp_name = (p.get("pickUpPoint") or {}).get("name")
            if pp_name:
                active_pps.add(str(pp_name))

        existing_pps: set[str] = set()
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = ent.unique_id or ""
            if uid.startswith(pp_uid_prefix):
                existing_pps.add(uid[len(pp_uid_prefix):])

        for pp_name in active_pps - existing_pps:
            new_entities.append(InpostPickupPointSensor(coord, entry, pp_name))
        for pp_name in active_pps:
            pp_miss.pop(pp_name, None)
        for pp_name in existing_pps - active_pps:
            pp_miss[pp_name] = pp_miss.get(pp_name, 0) + 1
            if pp_miss[pp_name] < AUTOREMOVE_GRACE_TICKS:
                _LOGGER.debug(
                    "InPost: paczkomat %s pusty (%d/%d) - czekam przed usunieciem",
                    pp_name, pp_miss[pp_name], AUTOREMOVE_GRACE_TICKS,
                )
                continue
            entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, pp_uid_prefix + pp_name)
            if entity_id:
                _LOGGER.info("InPost: usuwam encje paczkomatu %s (pusty)", entity_id)
                ent_reg.async_remove(entity_id)
            pp_miss.pop(pp_name, None)

        if new_entities:
            async_add_entities(new_entities)

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
    """Liczba paczek aktualnie czekajacych w paczkomacie do odbioru."""

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
    """Dystans (m) z device_tracker do najblizszego paczkomatu z paczka do odbioru."""

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
                async_track_state_change_event(self.hass, trackers, self._tracker_changed)
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


class InpostParcelSensor(InpostBase):
    """Pojedyncza paczka - state = polski label statusu, friendly name = nadawca."""

    def __init__(self, coord: InpostCoordinator, entry: ConfigEntry, shipment_number: str):
        super().__init__(coord, entry)
        self._sn = str(shipment_number)
        # unique_id: SUROWY shipmentNumber - chroni przed kolizjami slug
        self._attr_unique_id = f"{entry.entry_id}_parcel_{self._sn}"
        self._attr_suggested_object_id = f"paczka_{_slug_for_entity_id(self._sn)}"
        # name jest dynamiczny przez property `name` (sender z aliasow)

    def _data(self) -> dict | None:
        for p in self.coordinator.all_parcels:
            if str(p.get("shipmentNumber") or p.get("id") or "") == self._sn:
                if p.get("status") in TERMINAL_STATUSES:
                    return None
                if is_sender_ignored((p.get("sender") or {}).get("name")):
                    return None
                return p
        return None

    @property
    def name(self) -> str:
        d = self._data()
        raw = (d.get("sender") or {}).get("name") if d else None
        sender = canonicalize_sender(raw)
        if sender:
            if len(sender) > 40:
                sender = sender[:40].rstrip() + "…"
            return sender
        return f"Paczka {self._sn}"

    @property
    def available(self) -> bool:
        return self._data() is not None

    @property
    def icon(self) -> str:
        d = self._data()
        if not d:
            return DEFAULT_PARCEL_ICON
        return STATUS_ICONS.get(d.get("status") or "", DEFAULT_PARCEL_ICON)

    @property
    def native_value(self) -> str | None:
        d = self._data()
        if not d:
            return None
        status = d.get("status")
        if not status:
            return None
        return STATUS_LABELS_PL.get(status, status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._data()
        if not d:
            return {}
        pp = d.get("pickUpPoint") or {}
        loc = pp.get("location") or {}
        addr = pp.get("addressDetails") or {}
        refs = d.get("references")
        if isinstance(refs, dict):
            refs = refs.get("value")
        raw_sender = (d.get("sender") or {}).get("name")
        ops = d.get("operations") or {}
        return {
            "shipment_number": d.get("shipmentNumber"),
            "status_raw": d.get("status"),
            "status_group": d.get("statusGroup"),
            "sender": canonicalize_sender(raw_sender),
            "sender_raw": raw_sender,
            "references": refs,
            "estimated_delivery_date": d.get("estimatedDeliveryDate"),
            "courier_phone_number": d.get("courierPhoneNumber"),
            "tracking_url": ops.get("redirectionUrl"),
            "shipment_type": d.get("shipmentType"),
            "pickup_date": d.get("pickUpDate"),
            "can_collect": ops.get("collect"),
            "pickup_point": pp.get("name"),
            "address": _format_pp_address(addr, with_postcode=True),
            "city": addr.get("city"),
            "street": addr.get("street"),
            "post_code": addr.get("postCode"),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "open_code": d.get("openCode"),
            "qr_code": d.get("qrCode"),
            "expiry_date": d.get("expiryDate"),
            "stored_date": d.get("storedDate"),
            "opening_hours": pp.get("openingHours"),
            "location_description": pp.get("locationDescription"),
            "is_24_7": pp.get("location247"),
            "easy_access_zone": pp.get("easyAccessZone"),
        }


class InpostPickupPointSensor(InpostBase):
    """Paczkomat z paczkami czekajacymi na odbior.

    State = liczba paczek tam czekajacych. Friendly name = adres
    ('ulica numer, miasto'). Atrybuty: pelne info paczkomatu + lista paczek.
    """

    _attr_icon = "mdi:package-variant-closed-check"

    def __init__(self, coord: InpostCoordinator, entry: ConfigEntry, pickup_point_name: str):
        super().__init__(coord, entry)
        self._pp = str(pickup_point_name)
        self._attr_unique_id = f"{entry.entry_id}_pickup_point_{self._pp}"
        self._attr_suggested_object_id = f"paczkomat_{_slug_for_entity_id(self._pp)}"
        # name dynamiczny - z adresu paczkomatu

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
    def name(self) -> str:
        parcels = self._parcels()
        if not parcels:
            return self._pp  # fallback gdy chwilowo brak paczek
        addr = (parcels[0].get("pickUpPoint") or {}).get("addressDetails") or {}
        formatted = _format_pp_address(addr, with_postcode=False)
        return formatted or self._pp

    @property
    def available(self) -> bool:
        return len(self._parcels()) > 0

    @property
    def native_value(self) -> int:
        return len(self._parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parcels = self._parcels()
        if not parcels:
            return {}
        pp = parcels[0].get("pickUpPoint") or {}
        loc = pp.get("location") or {}
        addr = pp.get("addressDetails") or {}
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
            "address": _format_pp_address(addr, with_postcode=True),
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
