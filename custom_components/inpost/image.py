"""Image entities: QR code per paczka w paczkomacie.

Native HA Image domain (HA core 2023.7+) - daje pelnoekranowy podglad
przy klikaniu (jak camera w UniFi Protect). Encja:
  image.inpost_qr_paczka_<shipmentNumber>

- name = adres paczkomatu (ulica numer, miasto)
- content_type = image/png
- async_image() = bytes z lokalnie wygenerowanego PNG
- image_last_updated = timestamp ostatniej regeneracji (cache invalidation)

Encja istnieje TYLKO gdy paczka w PICKUP_STATUSES + ma openCode + nadawca
nie jest na liscie ignorowanych. Tworzona/usuwana dynamicznie przez listener
coordinator w sensor.py:_refresh (wspolny dla wszystkich dynamicznych encji).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AUTOREMOVE_GRACE_TICKS,
    CONF_PHONE,
    DOMAIN,
    canonicalize_sender,
    is_sender_ignored,
)
from .coordinator import InpostCoordinator
from .sensor import (
    _QR_SUBDIR,
    _delete_qr_png,
    _format_pp_address,
    _generate_qr_png,
    _qr_dir,
    _qr_path_for,
    _qr_url_for,
    _slug_for_entity_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: InpostCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    qr_uid_prefix = f"{entry.entry_id}_qr_image_"
    qr_miss: dict[str, int] = {}

    @callback
    def _refresh() -> None:
        ent_reg = er.async_get(hass)

        # aktywne paczki = w paczkomacie + openCode + nie-ignorowany nadawca
        active: set[str] = set()
        for p in coord.pickup_parcels:
            if is_sender_ignored((p.get("sender") or {}).get("name")):
                continue
            if not p.get("openCode"):
                continue
            sn = p.get("shipmentNumber") or p.get("id")
            if sn:
                active.add(str(sn))

        existing: set[str] = set()
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = ent.unique_id or ""
            if uid.startswith(qr_uid_prefix):
                existing.add(uid[len(qr_uid_prefix):])

        new_entities: list[ImageEntity] = []
        for sn in active - existing:
            new_entities.append(InpostQRImage(coord, entry, sn))
        if new_entities:
            async_add_entities(new_entities)

        for sn in active:
            qr_miss.pop(sn, None)
        for sn in existing - active:
            qr_miss[sn] = qr_miss.get(sn, 0) + 1
            if qr_miss[sn] < AUTOREMOVE_GRACE_TICKS:
                _LOGGER.debug(
                    "InPost: qr image %s nieobecny (%d/%d) - czekam",
                    sn, qr_miss[sn], AUTOREMOVE_GRACE_TICKS,
                )
                continue
            entity_id = ent_reg.async_get_entity_id(
                "image", DOMAIN, qr_uid_prefix + sn
            )
            if entity_id:
                _LOGGER.info("InPost: usuwam image %s (paczka odebrana)", entity_id)
                ent_reg.async_remove(entity_id)
            qr_miss.pop(sn, None)

    # STARTUP CLEANUP: usuwamy stare image-encje z poprzedniej sesji
    ent_reg_init = er.async_get(hass)
    for ent in list(er.async_entries_for_config_entry(ent_reg_init, entry.entry_id)):
        uid = ent.unique_id or ""
        if uid.startswith(qr_uid_prefix):
            _LOGGER.info("InPost startup cleanup: usuwam %s", ent.entity_id)
            ent_reg_init.async_remove(ent.entity_id)

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


class InpostQRImage(CoordinatorEntity[InpostCoordinator], ImageEntity):
    """Image entity z QR kodem do otwarcia skrytki w paczkomacie.

    State (timestamp last_updated) zmienia sie gdy regenerujemy PNG -
    HA frontend automatycznie odswiezy podglad.
    """

    _attr_has_entity_name = True
    _attr_content_type = "image/png"
    _attr_icon = "mdi:qrcode"

    def __init__(self, coord: InpostCoordinator, entry: ConfigEntry, shipment_number: str):
        # ImageEntity wymaga hass parametru w __init__
        ImageEntity.__init__(self, coord.hass)
        CoordinatorEntity.__init__(self, coord)
        self._entry = entry
        self._sn = str(shipment_number)
        self._attr_unique_id = f"{entry.entry_id}_qr_image_{self._sn}"
        self._attr_suggested_object_id = f"qr_paczka_{_slug_for_entity_id(self._sn)}"
        self._attr_device_info = _device_info(entry)
        # sledzenie openCode dla cache PNG-a
        self._qr_for_open_code: str | None = None

    def _data(self) -> dict | None:
        for p in self.coordinator.pickup_parcels:
            if str(p.get("shipmentNumber") or p.get("id") or "") == self._sn:
                if is_sender_ignored((p.get("sender") or {}).get("name")):
                    return None
                if not p.get("openCode"):
                    return None
                return p
        return None

    @property
    def available(self) -> bool:
        return self._data() is not None

    @property
    def name(self) -> str:
        d = self._data()
        if not d:
            return f"QR Paczka {self._sn}"
        addr = (d.get("pickUpPoint") or {}).get("addressDetails") or {}
        formatted = _format_pp_address(addr, with_postcode=False)
        return formatted or f"QR Paczka {self._sn}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._data()
        if not d:
            return {}
        pp = d.get("pickUpPoint") or {}
        addr = pp.get("addressDetails") or {}
        raw_sender = (d.get("sender") or {}).get("name")
        return {
            "shipment_number": d.get("shipmentNumber"),
            "sender": canonicalize_sender(raw_sender),
            "open_code": d.get("openCode"),
            "pickup_point": pp.get("name"),
            "address": _format_pp_address(addr, with_postcode=True),
            "city": addr.get("city"),
            "street": addr.get("street"),
            "post_code": addr.get("postCode"),
            "expiry_date": d.get("expiryDate"),
            "stored_date": d.get("storedDate"),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_sync_qr()

    async def async_will_remove_from_hass(self) -> None:
        # uprzatamy plik PNG przy usuwaniu
        await self.hass.async_add_executor_job(
            _delete_qr_png, _qr_path_for(self.hass, self._sn)
        )
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.hass.async_create_task(self._async_sync_qr())
        super()._handle_coordinator_update()

    async def _async_sync_qr(self) -> None:
        d = self._data()
        path = _qr_path_for(self.hass, self._sn)
        open_code = (d or {}).get("openCode") if d else None
        if open_code:
            if self._qr_for_open_code == open_code and os.path.exists(path):
                return
            try:
                await self.hass.async_add_executor_job(
                    _generate_qr_png, open_code, path
                )
                self._qr_for_open_code = open_code
                # bump cache - HA frontend pobierze nowy obrazek
                self._attr_image_last_updated = datetime.now(timezone.utc)
                _LOGGER.debug("InPost: QR image - wygenerowany dla %s", self._sn)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("InPost: blad generacji QR image dla %s", self._sn)
        else:
            if self._qr_for_open_code is not None or os.path.exists(path):
                await self.hass.async_add_executor_job(_delete_qr_png, path)
                self._qr_for_open_code = None
                self._attr_image_last_updated = None

    async def async_image(self) -> bytes | None:
        """Zwraca bytes PNG z dysku.

        UWAGA: NIE uzywamy `image_url` - HA implementuje to przez HTTP fetch
        z absolutnego URL, a nasze pliki sa lokalne. async_image zwracajaca
        bytes omija fetch w ogole.
        """
        if not self._qr_for_open_code:
            return None
        path = _qr_path_for(self.hass, self._sn)

        def _read() -> bytes | None:
            try:
                with open(path, "rb") as f:
                    return f.read()
            except FileNotFoundError:
                return None
            except OSError:
                _LOGGER.exception("InPost: blad odczytu QR PNG %s", path)
                return None

        return await self.hass.async_add_executor_job(_read)
