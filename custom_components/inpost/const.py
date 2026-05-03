"""Stale dla integracji InPost."""
DOMAIN = "inpost"
CONF_PHONE = "phone_number"
CONF_AUTH_TOKEN = "auth_token"
CONF_REFRESH_TOKEN = "refresh_token"

# --- options flow ---
CONF_DEVICE_TRACKER = "device_tracker"
CONF_NOTIFY_DISTANCE_M = "notify_distance_m"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFY_COOLDOWN_MIN = "notify_cooldown_min"

DEFAULT_DEVICE_TRACKER = ""  # user wybiera w options flow; pusty = nearest_distance/notify wylaczone
DEFAULT_NOTIFY_DISTANCE_M = 500
DEFAULT_NOTIFY_COOLDOWN_MIN = 60

# autoremove: ile kolejnych pustych odczytow zanim usuniemy encje paczki (chroni przed znikaniem przy chwilowym blip API)
AUTOREMOVE_GRACE_TICKS = 2

# Storage key dla persistowanych danych powiadomien (klucz cooldownow per shipmentNumber)
STORAGE_VERSION = 1
STORAGE_KEY_NOTIFY = "inpost_notify"

UPDATE_INTERVAL_MIN = 15  # co ile minut odpytujemy API

PICKUP_STATUSES = {
    "READY_TO_PICKUP",
    "READY_TO_PICKUP_FROM_POK",
    "READY_TO_PICKUP_FROM_BRANCH",
    "PICKUP_REMINDER_SENT",
}

API_BASE = "https://api-inmobile-pl.easypack24.net"
USER_AGENT = "InPost-Mobile/3.27.2 (Android 14; SDK 34) okhttp/4.11.0"
