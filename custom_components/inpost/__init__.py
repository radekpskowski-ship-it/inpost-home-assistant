"""Custom integration: InPost (paczki, status pelnego cyklu zycia)."""
from __future__ import annotations

import logging
from datetime import timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_TRACKER,
    CONF_DEVICE_TRACKERS,
    CONF_NOTIFY_COOLDOWN_HOURS,
    CONF_NOTIFY_COOLDOWN_MIN,
    CONF_NOTIFY_DISTANCE_M,
    CONF_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_COOLDOWN_HOURS,
    DEFAULT_NOTIFY_COOLDOWN_MIN,
    DEFAULT_NOTIFY_DISTANCE_M,
    DOMAIN,
    STORAGE_KEY_NOTIFY,
    STORAGE_VERSION,
    is_sender_ignored,
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


def _resolve_trackers(opts: dict[str, Any]) -> list[str]:
    """Listy z opcji + fallback na stary single device_tracker."""
    trackers = opts.get(CONF_DEVICE_TRACKERS)
    if isinstance(trackers, list) and trackers:
        return [t for t in trackers if t]
    legacy = (opts.get(CONF_DEVICE_TRACKER) or "").strip()
    return [legacy] if legacy else []


def _resolve_notify_service(opts: dict[str, Any]) -> str:
    """Pojedynczy serwis 'notify.X'. Wspiera te tez stare wpisy ktore mialy liste/CSV."""
    val = opts.get(CONF_NOTIFY_SERVICE)
    if isinstance(val, list):
        for s in val:
            if isinstance(s, str) and "." in s:
                return s.strip()
        return ""
    if isinstance(val, str) and val.strip():
        # legacy CSV: bierzemy pierwszy wpis
        first = val.split(",", 1)[0].strip()
        return first if "." in first else ""
    return ""


def _resolve_cooldown(opts: dict[str, Any]) -> timedelta:
    """Cooldown - preferuj nowe godziny, fallback na stare minuty."""
    if CONF_NOTIFY_COOLDOWN_HOURS in opts:
        try:
            return timedelta(hours=int(opts[CONF_NOTIFY_COOLDOWN_HOURS] or DEFAULT_NOTIFY_COOLDOWN_HOURS))
        except (TypeError, ValueError):
            pass
    if CONF_NOTIFY_COOLDOWN_MIN in opts:
        try:
            return timedelta(minutes=int(opts[CONF_NOTIFY_COOLDOWN_MIN] or DEFAULT_NOTIFY_COOLDOWN_MIN))
        except (TypeError, ValueError):
            pass
    return timedelta(hours=DEFAULT_NOTIFY_COOLDOWN_HOURS)


class InpostNotifier:
    """Wystrzela powiadomienie push gdy paczka 'gotowa do odbioru' znajdzie sie blizej progu.

    Cooldowny per shipmentNumber persistowane przez Store (przezywaja restart HA).
    Tylko jeden serwis notify - prosto i przewidywalnie.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coord: InpostCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coord = coord
        self._last_notified: dict[str, str] = {}  # ISO utc per shipmentNumber
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
        if threshold <= 0:
            return

        notify_service = _resolve_notify_service(opts)
        if not notify_service:
            return

        trackers = _resolve_trackers(opts)
        if not trackers:
            return

        # zbierz pozycje wszystkich aktywnych telefonow
        positions: list[tuple[float, float]] = []
        for t in trackers:
            st = self.hass.states.get(t)
            if not st:
                continue
            la = st.attributes.get("latitude")
            lo = st.attributes.get("longitude")
            if la is None or lo is None:
                continue
            try:
                positions.append((float(la), float(lo)))
            except (TypeError, ValueError):
                continue
        if not positions:
            return

        cooldown = _resolve_cooldown(opts)
        now = dt_util.utcnow()
        active_sns: set[str] = set()
        dirty = False

        for p in self.coord.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
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

            # min dystans miedzy ktoryms telefonem a paczkomatem
            min_d = min(haversine_m(ula, ulo, la, lo) for (ula, ulo) in positions)
            if min_d > threshold:
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
            message = f"Paczka {sn} w {pp.get('name', '')} ({address_str}), {round(min_d)} m"
            if p.get("openCode"):
                message += f", kod {p.get('openCode')}"

            _LOGGER.debug("InPost: notify candidate sn=%s d=%dm", sn, round(min_d))
            self.hass.async_create_task(
                self._fire(notify_service, message, sn),
                name=f"inpost_notify_{sn}",
            )

        # GC: usun cooldowny dla paczek ktore znikly (odebrane/zwrocone)
        gone = set(self._last_notified.keys()) - active_sns
        if gone:
            for sn in gone:
                del self._last_notified[sn]
            dirty = True

        if dirty:
            self.hass.async_create_task(self._async_save(), name="inpost_notify_save")

    async def _fire(self, notify_service: str, message: str, sn: str) -> None:
        if "." not in notify_service:
            return
        domain, service = notify_service.split(".", 1)
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("InPost: serwis %s nie jest zarejestrowany - pomijam", notify_service)
            return
        try:
            await self.hass.services.async_call(
                domain, service,
                {"title": "InPost - paczkomat blisko", "message": message},
                blocking=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("InPost: notify %s zawiodl - cooldown NIE ustawiony dla %s",
                              notify_service, sn)
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
