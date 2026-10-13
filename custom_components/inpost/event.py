"""Encje zdarzen: zmiana statusu paczki + powiadomienie z apki InPost.

- event.inpost_paczka: typy new_parcel / ready_to_pickup / delivered / status_changed,
  atrybuty = plaski opis paczki (numer, nadawca, status, paczkomat, adres, termin).
- event.inpost_powiadomienie: kazde nowe powiadomienie ze skrzynki apki (title, message).

To samo leci na szyne zdarzen HA (inpost_parcel_event / inpost_notification).
Co juz ogloszono, pamieta koordynator (Store) - restart nie powtarza starych zdarzen.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_NOTIFICATION, PARCEL_EVENT_TYPES
from .coordinator import InpostCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: InpostCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        InpostEvent(coord, entry, "parcel", "Paczka", "mdi:package-variant", PARCEL_EVENT_TYPES),
        InpostEvent(coord, entry, "notification", "Powiadomienie", "mdi:bell-ring-outline", [EVENT_NOTIFICATION]),
    ])


class InpostEvent(EventEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, coord: InpostCoordinator, entry: ConfigEntry,
        kind: str, name: str, icon: str, event_types: list[str],
    ) -> None:
        self._coord = coord
        self._kind = kind
        self._attr_name = name
        self._attr_icon = icon
        self._attr_event_types = event_types
        self._attr_unique_id = f"{entry.entry_id}_event_{kind}"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._coord.async_add_event_listener(self._kind, self._handle))

    @callback
    def _handle(self, event_type: str, payload: dict[str, Any]) -> None:
        self._trigger_event(event_type, payload)
        self.async_write_ha_state()
