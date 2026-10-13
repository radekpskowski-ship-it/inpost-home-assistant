"""Image entities: QR code per paczka w paczkomacie.

Native HA Image domain (HA core 2023.7+) - daje pelnoekranowy podglad
przy klikaniu (jak camera w UniFi Protect). Encja:
  image.inpost_qr_paczka_<shipmentNumber>

- name = adres paczkomatu (ulica numer, miasto)
- content_type = image/png
- async_image() = bytes PNG wygenerowanego lokalnie i trzymanego TYLKO w pamieci
  (do 0.15 plik lezal w /config/www/inpost/, czyli pod /local/ bez logowania;
  teraz obrazek idzie wylacznie przez uwierzytelnione /api/image_proxy)
- image_last_updated = timestamp ostatniej regeneracji (cache invalidation)

Encja istnieje TYLKO gdy paczka w PICKUP_STATUSES + ma openCode + nadawca
nie jest na liscie ignorowanych. Tworzona/usuwana dynamicznie przez listener
coordinator w sensor.py:_refresh (wspolny dla wszystkich dynamicznych encji).
"""
from __future__ import annotations

import io
import logging
import os
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
)
from .coordinator import InpostCoordinator, format_pp_address, parcel_sn
from .sensor import ensure_stable_entity_ids

_LOGGER = logging.getLogger(__name__)


def _generate_qr_png(payload: str) -> bytes:
    """Synchroniczna generacja QR PNG z gotowego payloadu. Wywoluj przez async_add_executor_job.

    `payload` to dokladna zawartosc QR - pole `qrCode` z API InPost
    (format `P|+48<numer-odbiorcy>|<openCode>`). QR generowany lokalnie -
    payload nigdy nie jest wysylany do zadnej zewnetrznej uslugi.
    """
    import qrcode  # imported lazily so we don't pay if no parcels yet

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _purge_legacy_png(directory: str) -> None:
    """Usun PNG-i z QR zostawione przez wersje <= 0.15 w /config/www/inpost/ (publiczne /local/).

    Katalog nalezal wylacznie do integracji; czyscimy wszystkie konta naraz, takze te,
    ktore akurat sie nie zaladowaly (np. czekaja na ponowne logowanie).
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if name.endswith(".png"):
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                _LOGGER.debug("Nie udalo sie usunac %s", name, exc_info=True)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: InpostCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    qr_uid_prefix = f"{entry.entry_id}_qr_image_"
    qr_miss: dict[str, int] = {}
    created: set[str] = set()  # encje utworzone w tej sesji (rejestr pamieta je miedzy restartami)

    stable_ids = {"_qr_image_": "image.inpost_qr_"}
    ensure_stable_entity_ids(hass, entry, stable_ids)
    await hass.async_add_executor_job(
        _purge_legacy_png, hass.config.path("www", "inpost")
    )

    @callback
    def _refresh() -> None:
        ent_reg = er.async_get(hass)
        ensure_stable_entity_ids(hass, entry, stable_ids)

        # aktywne paczki = wlasne, w paczkomacie, z openCode
        active: set[str] = {
            parcel_sn(p) for p in coord.own_pickup_parcels if p.get("openCode") and parcel_sn(p)
        }

        existing: set[str] = set()
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = ent.unique_id or ""
            if uid.startswith(qr_uid_prefix):
                existing.add(uid[len(qr_uid_prefix):])

        new_entities: list[ImageEntity] = []
        for sn in active - created:
            new_entities.append(InpostQRImage(coord, entry, sn))
            created.add(sn)
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
            created.discard(sn)

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
        # do rejestracji nazwa "QR <numer>" (-> stale image.inpost_qr_<numer>), potem adres
        self._registered = False
        self._attr_device_info = _device_info(entry)
        # PNG trzymany w pamieci; payload sledzony, zeby regenerowac tylko gdy sie zmieni
        self._qr_payload: str | None = None
        self._png: bytes | None = None

    def _data(self) -> dict | None:
        for p in self.coordinator.own_pickup_parcels:
            if parcel_sn(p) == self._sn:
                if not p.get("openCode"):
                    return None
                return p
        return None

    @property
    def available(self) -> bool:
        return self._data() is not None

    # UWAGA: NIE nadpisujemy `state` - HA frontend dla image domain parsuje state
    # jako timestamp i wyswietla "1 stycznia 5678 01:00" gdy zwrocimy "5678".
    # Zamiast tego openCode wstawiamy do `name` - frontend renderuje to jako
    # tekst, nie jako date. State pozostaje timestamp (mozna ukryc w karcie
    # picture-entity przez `show_state: false`).

    @property
    def name(self) -> str:
        """Friendly name = adres (+ ' • openCode' tylko przy wlaczonej opcji publikowania kodow)."""
        if not self._registered:
            return f"QR {self._sn}"
        d = self._data()
        if not d:
            return f"QR Paczka {self._sn}"
        addr = (d.get("pickUpPoint") or {}).get("addressDetails") or {}
        formatted = format_pp_address(addr, with_postcode=False)
        open_code = (d.get("openCode") or "").strip() if self.coordinator.expose_codes else ""
        if formatted and open_code:
            return f"{formatted} • {open_code}"
        if formatted:
            return formatted
        if open_code:
            return f"Kod {open_code}"
        return f"QR Paczka {self._sn}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._data()
        if not d:
            return {}
        pp = d.get("pickUpPoint") or {}
        addr = pp.get("addressDetails") or {}
        raw_sender = (d.get("sender") or {}).get("name")
        attrs = {
            "inpost_type": "qr",
            "account": self._entry.data.get(CONF_PHONE),
            "shipment_number": d.get("shipmentNumber"),
            "sender": canonicalize_sender(raw_sender),
            "parcel_size": d.get("parcelSize"),
            "pickup_point": pp.get("name"),
            "address": format_pp_address(addr, with_postcode=True),
            "city": addr.get("city"),
            "street": addr.get("street"),
            "post_code": addr.get("postCode"),
            "location_description": pp.get("locationDescription"),
            "is_24_7": pp.get("location247"),
            "expiry_date": d.get("expiryDate"),
            "stored_date": d.get("storedDate"),
        }
        if self.coordinator.expose_codes:
            attrs["open_code"] = d.get("openCode")
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._registered = True
        await self._async_sync_qr()

    def _resolve_qr_payload(self, d: dict | None) -> str | None:
        """Payload QR = pole `qrCode` z API (verbatim).

        Fallback gdy API nie poda `qrCode`: skladamy `P|+48<receiver>|<openCode>`
        z numeru ODBIORCY paczki (nie numeru logowania konta!), z prefiksem +48.
        """
        if not d:
            return None
        api_qr = (d.get("qrCode") or "").strip()
        if api_qr:
            return api_qr
        open_code = (d.get("openCode") or "").strip()
        if not open_code:
            return None
        phone = str((d.get("receiver") or {}).get("phoneNumber") or "").strip()
        if not phone:
            # ostateczny fallback - numer konta (entry)
            phone = str(self._entry.data.get(CONF_PHONE) or "").strip()
        if not phone:
            return None
        if not phone.startswith("+"):
            phone = f"+48{phone}"
        return f"P|{phone}|{open_code}"

    @callback
    def _handle_coordinator_update(self) -> None:
        self.hass.async_create_task(self._async_sync_qr())
        super()._handle_coordinator_update()

    async def _async_sync_qr(self) -> None:
        payload = self._resolve_qr_payload(self._data())
        if payload:
            if self._qr_payload == payload and self._png:
                return
            try:
                self._png = await self.hass.async_add_executor_job(_generate_qr_png, payload)
                self._qr_payload = payload
                # bump cache - HA frontend pobierze nowy obrazek
                self._attr_image_last_updated = datetime.now(timezone.utc)
                self.async_write_ha_state()
                _LOGGER.debug("InPost: QR image - wygenerowany dla %s", self._sn)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("InPost: blad generacji QR image dla %s", self._sn)
        elif self._png is not None:
            self._png = None
            self._qr_payload = None
            self._attr_image_last_updated = None

    async def async_image(self) -> bytes | None:
        """Zwraca bytes PNG z pamieci (przez uwierzytelnione /api/image_proxy)."""
        if not self._png:
            return None
        # Frontend pobral bajty QR = ktos wlasnie podejrzal kod odbioru.
        # Wlaczamy szybszy polling, zeby blyskawicznie wykryc odbior paczki.
        # UWAGA: domena 'image' cache'uje - hook odpala sie zwykle raz, przy
        # pierwszym pobraniu po (re)generacji QR. Do startu boostu to wystarcza.
        self.coordinator.note_qr_viewed(self._sn)
        return self._png
