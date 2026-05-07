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

# Statusy "terminalne" - paczka skonczona, encja od razu pomijana (bez 12h okna).
# UWAGA: RETURNED_TO_SENDER / RETURNED_TO_SOURCE_BRANCH zostaja widoczne - sa wciaz interesujace.
TERMINAL_STATUSES = {
    "CANCELED",
    "PICKUP_TIME_EXPIRED",
}

# Statusy "odebrana z paczkomatu" - encja widoczna jeszcze przez
# POST_PICKUP_VISIBILITY_HOURS godzin po odbiorze (z napisem "Odebrana"),
# potem znika przez normalny grace period.
RECENT_PICKUP_STATUSES = {
    "DELIVERED",
    "PICKED_UP",
}
POST_PICKUP_VISIBILITY_HOURS = 12

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
    "DELIVERED": "Odebrana",
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

# Mapping surowych nazw nadawcow z API InPost na czytelne, krotkie etykiety.
# Lista kolejnosci - dopasowanie po pierwszym `pattern in raw_sender.lower()`,
# wiec bardziej specyficzne wzorce stawiaj WYZEJ. Pattern jest lowercase'owany.
# Niedopasowane nazwy przelatuja bez zmian (po opcjonalnym truncate w sensor.py).
SENDER_ALIASES: list[tuple[str, str]] = [
    # Marketplaces / e-commerce
    ("cainiao", "AliExpress"),                  # "Seller using Cainiao logistics services"
    ("aliexpress", "AliExpress"),
    ("temu", "Temu"),
    ("shein", "SHEIN"),
    ("amazon", "Amazon"),
    ("allegro", "Allegro"),                     # Allegro / Allegro Smart / Allegro Lokalnie
    ("vinted", "Vinted"),
    ("empik", "Empik"),
    ("zalando", "Zalando"),
    ("modivo", "Modivo"),
    ("answear", "Answear"),
    ("ebay", "eBay"),
    ("etsy", "Etsy"),
    # Elektronika / AGD
    ("x-kom", "x-kom"),
    ("morele", "Morele"),
    ("media markt", "MediaMarkt"),
    ("mediamarkt", "MediaMarkt"),
    ("euro agd", "RTV Euro AGD"),
    ("rtveuroagd", "RTV Euro AGD"),
    ("rtv euro agd", "RTV Euro AGD"),
    ("klinikaagd", "Klinika AGD"),
    ("oleole", "OleOle"),
    # Moda / obuwie
    ("eobuwie", "eobuwie"),
    ("ccc.eu", "CCC"),
    ("4f.pl", "4F"),
    ("sinsay", "Sinsay"),
    ("reserved", "Reserved"),
    ("h&m", "H&M"),
    ("zara", "Zara"),
    ("c&a", "C&A"),
    # Drogeria / kosmetyki
    ("notino", "Notino"),
    ("hebe", "Hebe"),
    ("rossmann", "Rossmann"),
    ("douglas", "Douglas"),
    ("sephora", "Sephora"),
    # Dom / hobby
    ("ikea", "IKEA"),
    ("leroy", "Leroy Merlin"),
    ("castorama", "Castorama"),
    ("decathlon", "Decathlon"),
    ("apart", "Apart"),
    ("smyk", "Smyk"),
    # Spozywka / inne
    ("pyszne", "Pyszne.pl"),
    ("biedronka", "Biedronka"),
    ("auchan", "Auchan"),
    # Curierzy / sami nadawcy
    ("inpost", "InPost"),
]


# Nadawcy ignorowani na sztywno - zwykle paczki testowe/spam ktorych nie chcemy
# w UI ani w powiadomieniach. Match case-insensitive substring.
# Aby dorzucic kolejny - po prostu dodaj wzorzec ponizej.
IGNORED_SENDER_PATTERNS: set[str] = {
    "eryk sssss",
}


def is_sender_ignored(raw_sender: str | None) -> bool:
    """Czy nadawca pasuje do ktoregos wzorca z IGNORED_SENDER_PATTERNS."""
    if not raw_sender:
        return False
    s = raw_sender.strip().lower()
    if not s:
        return False
    return any(p in s for p in IGNORED_SENDER_PATTERNS)


def canonicalize_sender(raw_sender: str | None) -> str:
    """Spaczowanie surowych nazw nadawcow z API na ladne, krotkie etykiety.

    Zwraca canonical name jesli pattern z SENDER_ALIASES wystepuje w raw_sender
    (case insensitive). W przeciwnym razie zwraca raw_sender (po strip).
    """
    if not raw_sender:
        return ""
    s = raw_sender.strip()
    if not s:
        return ""
    s_low = s.lower()
    for pattern, canonical in SENDER_ALIASES:
        if pattern in s_low:
            return canonical
    return s

API_BASE = "https://api-inmobile-pl.easypack24.net"
USER_AGENT = "InPost-Mobile/3.27.2 (Android 14; SDK 34) okhttp/4.11.0"
