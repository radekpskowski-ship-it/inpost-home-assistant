"""Sensory: jeden per paczka (kazdy status) + sensor zbiorczy + sensor 'najblizsza paczka [m]'."""
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
)
from .coordinator import InpostCoordinator

_LOGGER = logging.getLogger(__name__)

# Sanitizacja shipmentNumber pod entity_id (HA wymaga [a-z0-9_], wiec dziala wyrocznia: lowercase + non-alnum -> "_").
# UWAGA: dla unique_id uzywamy SUROWEGO shipmentNumber - chroni przed kolizjami slug(R12-AB) == slug(R12_AB).
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

    # dynamiczne: synchronizujemy entity_registry ze WSZYSTKIMI paczkami z API (kazdy status).
    # Encja zyje od pojawienia sie paczki w API az do AUTOREMOVE_GRACE_TICKS pustych odczytow po znikniieciu.
    parcel_uid_prefix = f"{entry.entry_id}_parcel_"
    miss_count: dict[str, int] = {}  # sn -> ile kolejnych odczytow API NIE zwrocilo paczki

    @callback
    def _refresh() -> None:
        ent_reg = er.async_get(hass)

        active: set[str] = set()
        for p in coord.all_parcels:
            sn = p.get("shipmentNumber") or p.get("id")
            if sn:
                active.add(str(sn))

        # encje paczkowe znane registry'emu
        existing: set[str] = set()
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = ent.unique_id or ""
            if uid.startswith(parcel_uid_prefix):
                existing.add(uid[len(parcel_uid_prefix):])

        # ADD: nowe paczki ktore nie maja jeszcze encji
        new_entities: list[SensorEntity] = []
        for sn in active - existing:
            new_entities.append(InpostParcelSensor(coord, entry, sn))
        if new_entities:
            async_add_entities(new_entities)

        # zresetuj licznik missow dla paczek, ktore wrocily
        for sn in active:
            miss_count.pop(sn, None)

        # GRACE: nie usuwaj od razu, dopiero po N kolejnych pustych odczytach
        for sn in existing - active:
            miss_count[sn] = miss_count.get(sn, 0) + 1
            if miss_count[sn] < AUTOREMOVE_GRACE_TICKS:
                _LOGGER.debug(
                    "InPost: paczka %s nieobecna (%d/%d) - czekam przed usunieciem",
                    sn, miss_count[sn], AUTOREMOVE_GRACE_TICKS,
                )
                continue
            unique_id = parcel_uid_prefix + sn
            entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                _LOGGER.info("InPost: usuwam encje %s (paczka zniknela z API)", entity_id)
                ent_reg.async_remove(entity_id)
            miss_count.pop(sn, None)

    _refresh()
    entry.async_on_unload(coord.async_add_listener(_refresh))


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    phone = entry.data.get(CONF_PHONE, "")
    # Krotka nazwa zeby entity_id nie zawieralo numeru telefonu (PII).
    # Numer telefonu trafia do `model` - widoczny w UI urzadzenia, nie w entity_id.
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
    _attr_translation_key = "count"
    _attr_name = "Paczki do odbioru"
    _attr_icon = "mdi:package-variant-closed-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coord, entry):
        super().__init__(coord, entry)
        self._attr_unique_id = f"{entry.entry_id}_count"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.pickup_parcels)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "parcels": [p.get("shipmentNumber") for p in self.coordinator.pickup_parcels],
            "all_tracked": len(self.coordinator.all_parcels),
        }


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
        nearest_sn = None
        nearest_d = None
        for p in self.coordinator.pickup_parcels:
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
                    nearest_sn = p.get("shipmentNumber")
        return {
            "nearest_pickup_point": nearest_name,
            "nearest_shipment_number": nearest_sn,
            "trackers_used": len(positions),
        }


class InpostParcelSensor(InpostBase):
    """Pojedyncza paczka - state odzwierciedla pelny cykl zycia (utworzona, w doreczeniu, doreczona, ...)."""

    def __init__(self, coord: InpostCoordinator, entry: ConfigEntry, shipment_number: str):
        super().__init__(coord, entry)
        self._sn = str(shipment_number)
        # unique_id: SUROWY shipmentNumber (chroni przed kolizjami slug)
        self._attr_unique_id = f"{entry.entry_id}_parcel_{self._sn}"
        # entity_id sufix: alfanumeryczny (HA tak czy siak by to slugifikowal)
        self._attr_suggested_object_id = f"paczka_{_slug_for_entity_id(self._sn)}"
        self._attr_name = f"Paczka {self._sn}"

    def _data(self) -> dict | None:
        for p in self.coordinator.all_parcels:
            if str(p.get("shipmentNumber") or p.get("id") or "") == self._sn:
                return p
        return None

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
    def entity_picture(self) -> str | None:
        d = self._data()
        if not d:
            return None
        return (d.get("pickUpPoint") or {}).get("imageUrl")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._data()
        if not d:
            return {}
        pp = d.get("pickUpPoint") or {}
        loc = pp.get("location") or {}
        addr = pp.get("addressDetails") or {}
        return {
            "shipment_number": d.get("shipmentNumber"),
            "status_raw": d.get("status"),
            "sender": (d.get("sender") or {}).get("name"),
            "pickup_point": pp.get("name"),
            "address": " ".join(x for x in [
                addr.get("street"),
                addr.get("buildingNumber"),
                f'({addr.get("postCode", "")} {addr.get("city", "")})'.strip(),
            ] if x).strip(),
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
