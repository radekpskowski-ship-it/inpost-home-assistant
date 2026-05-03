"""Custom integration: InPost (paczki gotowe do odbioru)."""
from __future__ import annotations

import logging
from datetime import timedelta
from math import asin, cos, radians, sin, sqrt

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_TRACKER,
    CONF_NOTIFY_COOLDOWN_MIN,
    CONF_NOTIFY_DISTANCE_M,
    CONF_NOTIFY_SERVICE,
    DEFAULT_DEVICE_TRACKER,
    DEFAULT_NOTIFY_COOLDOWN_MIN,
    DEFAULT_NOTIFY_DISTANCE_M,
    DOMAIN,
    STORAGE_KEY_NOTIFY,
    STORAGE_VERSION,
)
from .coordinator import InpostCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * asin(sqrt(a))


class InpostNotifier:
    """Wystrzela notify.<service> gdy paczka znajdzie sie blizej progu (per shipmentNumber, z cooldownem).

    Cooldowny persistowane przez Store - przezywaja restart HA.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coord: InpostCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coord = coord
        # ISO-format timestamps (utc) per shipmentNumber, persistowane
        self._last_notified: dict[str, str] = {}
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_NOTIFY}.{entry.entry_id}"
        )
        self._loaded = False

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._last_notified = {
                str(k): str(v) for k, v in data.items() if v is not None
            }
        self._loaded = True

    async def _async_save(self) -> None:
        await self._store.async_save(self._last_notified)

    @callback
    def maybe_notify(self) -> None:
        if not self._loaded:
            return
        opts = self.entry.options
        try:
            threshold = float(opts.get(CONF_NOTIFY_DISTANCE_M, DEFAULT_NOTIFY_DISTANCE_M) or 0)
        except (TypeError, ValueError):
            return
        service_full = (opts.get(CONF_NOTIFY_SERVICE) or "").strip()
        try:
            cooldown_min = int(opts.get(CONF_NOTIFY_COOLDOWN_MIN, DEFAULT_NOTIFY_COOLDOWN_MIN) or 0)
        except (TypeError, ValueError):
            cooldown_min = DEFAULT_NOTIFY_COOLDOWN_MIN

        if threshold <= 0 or not service_full or "." not in service_full:
            return
        domain, service = service_full.split(".", 1)

        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning(
                "InPost: serwis %s.%s nie jest zarejestrowany - powiadomienia pominiete",
                domain, service,
            )
            return

        tracker_id = (opts.get(CONF_DEVICE_TRACKER) or DEFAULT_DEVICE_TRACKER).strip()
        if not tracker_id:
            return
        st = self.hass.states.get(tracker_id)
        if not st:
            return
        try:
            user_lat = float(st.attributes.get("latitude"))
            user_lon = float(st.attributes.get("longitude"))
        except (TypeError, ValueError):
            return

        now = dt_util.utcnow()
        cooldown = timedelta(minutes=cooldown_min)
        active_sns: set[str] = set()
        dirty = False

        for p in self.coord.pickup_parcels:
            sn = p.get("shipmentNumber") or p.get("id")
            if not sn:
                continue
            sn = str(sn)
            active_sns.add(sn)

            pp = p.get("pickUpPoint") or {}
            loc = pp.get("location") or {}
            try:
                la = float(loc.get("latitude"))
                lo = float(loc.get("longitude"))
            except (TypeError, ValueError):
                continue
            d = haversine_m(user_lat, user_lon, la, lo)
            if d > threshold:
                continue

            last_iso = self._last_notified.get(sn)
            if last_iso:
                last_dt = dt_util.parse_datetime(last_iso)
                if last_dt and (now - last_dt) < cooldown:
                    continue

            addr = pp.get("addressDetails") or {}
            address_str = " ".join(x for x in [
                addr.get("street"),
                addr.get("buildingNumber"),
                f'({addr.get("postCode", "")} {addr.get("city", "")})'.strip(),
            ] if x).strip()
            message = f"Paczka {sn} w {pp.get('name', '')} ({address_str}), {round(d)} m"
            if p.get("openCode"):
                message += f", kod {p.get('openCode')}"

            _LOGGER.debug("InPost: notify candidate sn=%s d=%dm", sn, round(d))
            self.hass.async_create_task(
                self._fire_notify(domain, service, message, sn),
                name=f"inpost_notify_{sn}",
            )

        # GC: usun cooldowny dla paczek ktore znikly z aktywnych (odebrane/zwrocone)
        gone = set(self._last_notified.keys()) - active_sns
        if gone:
            for sn in gone:
                del self._last_notified[sn]
            dirty = True

        if dirty:
            self.hass.async_create_task(self._async_save(), name="inpost_notify_save")

    async def _fire_notify(self, domain: str, service: str, message: str, sn: str) -> None:
        try:
            await self.hass.services.async_call(
                domain, service,
                {"title": "InPost - paczkomat blisko", "message": message},
                blocking=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "InPost: notify %s.%s zawiodl - cooldown NIE ustawiony", domain, service,
            )
            return
        self._last_notified[sn] = dt_util.utcnow().isoformat()
        await self._async_save()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = InpostCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    notifier = InpostNotifier(hass, entry, coordinator)
    await notifier.async_load()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "notifier": notifier,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_add_listener(notifier.maybe_notify))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry gdy uzytkownik zmieni opcje (UI -> Konfiguruj)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
