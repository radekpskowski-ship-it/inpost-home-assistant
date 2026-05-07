"""Stale dla integracji InPost."""
DOMAIN = "inpost"
CONF_PHONE = "phone_number"
CONF_AUTH_TOKEN = "auth_token"
CONF_REFRESH_TOKEN = "refresh_token"

# --- options flow ---
# Stare klucze - zachowane dla backwards compat (czytane jako fallback w setup_options).
CONF_NOTIFY_COOLDOWN_MIN = "notify_cooldown_min"

# Klucze konfiguracyjne (v0.5.0+): jeden serwis notify, jeden cooldown w godzinach, wiele telefonow.
CONF_DEVICE_TRACKER = "device_tracker"            # legacy single tracker (fallback)
CONF_DEVICE_TRACKERS = "device_trackers"          # lista device_tracker entity_id (mobile_app)
CONF_NOTIFY_SERVICE = "notify_service"            # pojedynczy serwis 'notify.<x>'
CONF_NOTIFY_DISTANCE_M = "notify_distance_m"
CONF_NOTIFY_COOLDOWN_HOURS = "notify_cooldown_hours"

DEFAULT_DEVICE_TRACKERS: list[str] = []
DEFAULT_NOTIFY_SERVICE = ""
DEFAULT_NOTIFY_DISTANCE_M = 500
DEFAULT_NOTIFY_COOLDOWN_HOURS = 1
DEFAULT_NOTIFY_COOLDOWN_MIN = 60                  # tylko fallback dla starych wpisow

# autoremove: ile kolejnych pustych odczytow zanim usuniemy encje paczki (chroni przed znikaniem przy chwilowym blip API)
AUTOREMOVE_GRACE_TICKS = 2

# Storage key dla persistowanych danych powiadomien (klucz cooldownow per shipmentNumber)
STORAGE_VERSION = 1
STORAGE_KEY_NOTIFY = "inpost_notify"

UPDATE_INTERVAL_MIN = 15  # co ile minut odpytujemy API

# Statusy w ktorych paczka jest gotowa do odbioru (tylko te wyzwalaja powiadomienia o dystansie).
PICKUP_STATUSES = {
    "READY_TO_PICKUP",
    "READY_TO_PICKUP_FROM_POK",
    "READY_TO_PICKUP_FROM_BRANCH",
    "PICKUP_REMINDER_SENT",
}

# Statusy "terminalne" - paczka skonczona. Uzytkownik nie chce ich w UI HA, wiec encje
# sa pomijane przy tworzeniu i usuwane gdy istniejaca paczka tu trafi (przez normalny grace period).
# UWAGA: RETURNED_TO_SENDER / RETURNED_TO_SOURCE_BRANCH zostaja widoczne - sa wciaz interesujace
# (zwrot do nadawcy / w drodze powrotnej do oddzialu).
TERMINAL_STATUSES = {
    "DELIVERED",
    "PICKED_UP",
    "CANCELED",
    "PICKUP_TIME_EXPIRED",
}

# Pelny slownik statusow API mobilnego InPost -> czytelna etykieta PL.
# Encja paczki dziedziczy state z tej mapy (fallback: surowy status).
STATUS_LABELS_PL: dict[str, str] = {
    "CREATED": "Utworzona",
    "OFFERS_PREPARED": "Oferty przygotowane",
    "OFFER_SELECTED": "Oferta wybrana",
    "CONFIRMED": "Potwierdzona",
    "DISPATCHED_BY_SENDER": "Nadana przez nadawce",
    "DISPATCHED_BY_SENDER_TO_POK": "Nadana w PaczkoPunkcie",
    "COLLECTED_FROM_SENDER": "Odebrana od nadawcy",
    "TAKEN_BY_COURIER": "Odebrana przez kuriera",
    "ADOPTED_AT_SOURCE_BRANCH": "W oddziale nadawczym",
    "SENT_FROM_SOURCE_BRANCH": "Wyslana z oddzialu nadawczego",
    "ADOPTED_AT_SORTING_CENTER": "W sortowni",
    "SENT_FROM_SORTING_CENTER": "Wyslana z sortowni",
    "ADOPTED_AT_TARGET_BRANCH": "W oddziale docelowym",
    "OUT_FOR_DELIVERY": "W doreczeniu",
    "READY_TO_PICKUP": "Gotowa do odbioru",
    "READY_TO_PICKUP_FROM_POK": "Gotowa do odbioru w PaczkoPunkcie",
    "READY_TO_PICKUP_FROM_BRANCH": "Gotowa do odbioru w oddziale",
    "PICKUP_REMINDER_SENT": "Przypomnienie o odbiorze",
    "PICKUP_TIME_EXPIRED": "Czas odbioru minal",
    "AVIZO": "Awizo",
    "DELIVERED": "Doreczona",
    "PICKED_UP": "Odebrana",
    "RETURNED_TO_SENDER": "Zwrocona do nadawcy",
    "RETURNED_TO_SOURCE_BRANCH": "Zwrocona do oddzialu",
    "CANCELED": "Anulowana",
    "CLAIMED": "Reklamowana",
    "STACK_IN_CUSTOMER_SERVICE_POINT": "W obsludze klienta",
    "STACK_IN_BOX_MACHINE": "Czeka w paczkomacie",
    "UNKNOWN": "Nieznany",
}

# Mapa statusow na ikonki MDI - przyjazne dla UI Home Assistant.
STATUS_ICONS: dict[str, str] = {
    "CREATED": "mdi:package-variant",
    "OFFERS_PREPARED": "mdi:package-variant",
    "OFFER_SELECTED": "mdi:package-variant",
    "CONFIRMED": "mdi:package-variant",
    "DISPATCHED_BY_SENDER": "mdi:package-up",
    "DISPATCHED_BY_SENDER_TO_POK": "mdi:package-up",
    "COLLECTED_FROM_SENDER": "mdi:truck-fast",
    "TAKEN_BY_COURIER": "mdi:truck-fast",
    "ADOPTED_AT_SOURCE_BRANCH": "mdi:warehouse",
    "SENT_FROM_SOURCE_BRANCH": "mdi:truck-cargo-container",
    "ADOPTED_AT_SORTING_CENTER": "mdi:warehouse",
    "SENT_FROM_SORTING_CENTER": "mdi:truck-cargo-container",
    "ADOPTED_AT_TARGET_BRANCH": "mdi:warehouse",
    "OUT_FOR_DELIVERY": "mdi:truck-delivery",
    "READY_TO_PICKUP": "mdi:package-variant-closed-check",
    "READY_TO_PICKUP_FROM_POK": "mdi:package-variant-closed-check",
    "READY_TO_PICKUP_FROM_BRANCH": "mdi:package-variant-closed-check",
    "PICKUP_REMINDER_SENT": "mdi:bell-ring",
    "PICKUP_TIME_EXPIRED": "mdi:clock-alert",
    "AVIZO": "mdi:email-alert",
    "DELIVERED": "mdi:package-check",
    "PICKED_UP": "mdi:package-check",
    "RETURNED_TO_SENDER": "mdi:keyboard-return",
    "RETURNED_TO_SOURCE_BRANCH": "mdi:keyboard-return",
    "CANCELED": "mdi:cancel",
    "CLAIMED": "mdi:alert",
    "STACK_IN_CUSTOMER_SERVICE_POINT": "mdi:account-question",
    "STACK_IN_BOX_MACHINE": "mdi:package-variant-closed",
    "UNKNOWN": "mdi:help-circle",
}

DEFAULT_PARCEL_ICON = "mdi:package-variant-closed"

API_BASE = "https://api-inmobile-pl.easypack24.net"
USER_AGENT = "InPost-Mobile/3.27.2 (Android 14; SDK 34) okhttp/4.11.0"
