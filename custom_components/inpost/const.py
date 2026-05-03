"""Stale dla integracji InPost."""
DOMAIN = "inpost"
CONF_PHONE = "phone_number"
CONF_AUTH_TOKEN = "auth_token"
CONF_REFRESH_TOKEN = "refresh_token"

# --- options flow ---
# Stare klucze - zachowane dla backwards compat (czytane jako fallback w setup_options).
CONF_DEVICE_TRACKER = "device_tracker"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFY_COOLDOWN_MIN = "notify_cooldown_min"

# Nowe klucze (v0.4.0+): wiele telefonow, wiele serwisow notify, cooldown w godzinach, TTS.
CONF_DEVICE_TRACKERS = "device_trackers"          # lista device_tracker entity_id (mobile_app)
CONF_NOTIFY_SERVICES = "notify_services"          # lista 'notify.<service>' (string oddzielony przecinkami w UI)
CONF_NOTIFY_DISTANCE_M = "notify_distance_m"
CONF_NOTIFY_COOLDOWN_HOURS = "notify_cooldown_hours"
CONF_TTS_MESSAGE = "tts_message"                  # tekst do wymowienia przez TTS (puste = TTS off)
CONF_TTS_SERVICE = "tts_service"                  # np. tts.google_translate_say / tts.cloud_say
CONF_TTS_TARGETS = "tts_targets"                  # lista media_player entity_id

DEFAULT_DEVICE_TRACKER = ""
DEFAULT_DEVICE_TRACKERS: list[str] = []
DEFAULT_NOTIFY_SERVICES: list[str] = []
DEFAULT_NOTIFY_DISTANCE_M = 500
DEFAULT_NOTIFY_COOLDOWN_HOURS = 1
DEFAULT_NOTIFY_COOLDOWN_MIN = 60                  # tylko fallback dla starych wpisow
DEFAULT_TTS_SERVICE = "tts.google_translate_say"
DEFAULT_TTS_TARGETS: list[str] = []

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
