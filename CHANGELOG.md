# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.13.2] - 2026-05-07

### Changed
- **`image.inpost_qr_paczka_<sn>` state = openCode** (PIN, 4-6 digits) instead of HA's default `image_last_updated.isoformat()` timestamp. The `picture-entity` card now shows `Upalna 64, Białystok • 1234` instead of `Upalna 64, Białystok • 2026-05-07T20:00:35+00:00`. Image fetching uses `image_last_updated` independently — no impact on cache invalidation.

## [0.13.1] - 2026-05-07

### Fixed
- **QR images now actually display.** v0.13.0 returned `/local/inpost/<sn>.png` from `image_url` property — HA's image component treats `image_url` as a URL to **HTTP-fetch** and crashed with `Request URL is missing an 'http://' or 'https://' protocol`. Replaced with `async_image()` returning PNG bytes directly from disk. `picture-entity` and `image` cards now render the QR codes correctly.

## [0.13.0] - 2026-05-07

### ⚠ BREAKING — QR moved to `image` domain
- **Removed**: `sensor.inpost_qr_paczka_<sn>` (QR-tile sensor from 0.12.1).
- **Added**: `image.inpost_qr_paczka_<sn>` — Home Assistant native `image` entity (HA core 2023.7+). Same data, but proper domain for what it actually is. Matches the pattern UniFi Protect uses for camera previews — frontend has built-in **large render and tap-to-fullscreen** support without any Lovelace card hacks.
- Lovelace usage:
  ```yaml
  type: picture-entity
  entity: image.inpost_qr_paczka_520099999900000000000001
  ```
  → renders the QR at full card width; click → fullscreen modal.
- The integration now exposes 2 platforms: `sensor` + `image` (was: just `sensor`).

### Implementation notes
- `image_url` returns `/local/inpost/<sn>.png` — HA frontend serves the static file directly (no extra event-loop traffic).
- `image_last_updated` is bumped on every QR regeneration → frontend automatically refetches when openCode changes.
- Startup cleanup also wipes orphaned `_pickup_qr_*` sensor entries from 0.12.x deployments.

### Migration
Lovelace cards using `sensor.inpost_qr_paczka_<sn>` need to be re-pointed to `image.inpost_qr_paczka_<sn>`. Open codes are still readable via `state_attr('image.inpost_qr_paczka_<sn>', 'open_code')`.

## [0.12.1] - 2026-05-07

### Changed (vs 0.12.0)
- **QR moved to a dedicated entity.** Reverted: 0.12.0 set `entity_picture` to the QR PNG **on the parcel entity itself**, which masked the status icon (`mdi:truck-delivery`, etc.). Now there is a separate entity `sensor.inpost_qr_paczka_<shipmentNumber>`, dedicated to the dashboard preview of the QR + address; the parcel entity (`sensor.inpost_paczka_<sn>`) keeps its status icon untouched.

### Added
- **`InpostPickupQRSensor`** — one entity per parcel that is currently in a paczkomat with an `openCode`. Created dynamically when status enters `PICKUP_STATUSES`, removed via grace period after pickup.
  - **Friendly name** = paczkomat address `ulica numer, miasto`
  - **State** = `openCode` (4-digit PIN — useful in templates / displays)
  - **`entity_picture`** = locally-generated QR PNG (`/local/inpost/<sn>.png`)
  - **Attributes**: `shipment_number`, `sender`, `open_code`, `qr_image_url`, `pickup_point` (raw code), `address` (with postcode), `city`, `street`, `post_code`, `expiry_date`, `stored_date`
- Lovelace tip — drop one card per parcel onto the dashboard:
  ```yaml
  type: entity
  entity: sensor.inpost_qr_paczka_520099999900000000000001
  # name auto: "Upalna 64, Białystok"
  # state auto: "1234" (open_code)
  # picture auto: QR
  ```

## [0.12.0] - 2026-05-07

### Added
- **QR code as `entity_picture`.** When a parcel reaches `READY_TO_PICKUP*` and the API exposes its `openCode`, the integration generates a PNG QR locally to `/config/www/inpost/<shipmentNumber>.png` and sets the parcel entity's `entity_picture` to `/local/inpost/<shipmentNumber>.png`. The Lovelace entity card / picture-glance card shows the QR inline alongside the address attribute. After pickup the `openCode` disappears from the API → PNG is deleted, `entity_picture` returns `None`, the status icon (`mdi:package-check`) becomes visible again.
- **Privacy**: QR generation is fully local. The `openCode` (your locker PIN) is never sent to any third-party service. Requires a one-time install of `qrcode[pil]>=7.4.2` (HA pulls it automatically via `manifest.requirements`).

## [0.11.0] - 2026-05-07

### Added
- **12 h post-pickup visibility window.** When a parcel transitions to `DELIVERED` / `PICKED_UP`, the entity stays visible for 12 hours showing state `Odebrana` (icon `mdi:package-check`) before being removed via the normal grace period. Window driven by the API's `pickUpDate` field (no extra storage). Useful so you can still see what was just picked up on the dashboard for the rest of the day.
- New constant `POST_PICKUP_VISIBILITY_HOURS = 12` in `const.py` — change to extend / shorten the window.

### Changed
- `STATUS_LABELS_PL["DELIVERED"]` is now `Odebrana` (was `Doreczona`) for consistency with `PICKED_UP`.
- `TERMINAL_STATUSES` no longer includes `DELIVERED` / `PICKED_UP` — those moved to `RECENT_PICKUP_STATUSES`. Terminal set now contains only the "no-window" finals: `CANCELED`, `PICKUP_TIME_EXPIRED`.

### Note
- Remote opening of paczkomat compartments via API is **not possible** — the InPost mobile app uses Bluetooth Low Energy (BLE) proximity for compartment unlock, not pure HTTP. The integration exposes `open_code` (PIN) and `qr_code` attributes which are sufficient to unlock at the locker keypad/scanner manually. `tracking_url` deep-links to the InPost app on a phone.

## [0.10.2] - 2026-05-07

### Changed
- **Startup cleanup of dynamic entities.** Every time the integration is set up (HA boot or reload), all dynamic entities (`_parcel_*` and `_pickup_point_*`) are wiped from `entity_registry` first; then the first refresh recreates them based on the current API state. This eliminates zombie entities left over from previous sessions, mock testing, or model migrations. Static entities (count, nearest_distance) are preserved.

## [0.10.1] - 2026-05-07

### Fixed
- Icons now visible on parcel and paczkomat entities. `entity_picture` (which was returning `pickUpPoint.imageUrl`) was suppressing the status-based MDI icons in the HA frontend. Removed `entity_picture` from both `InpostParcelSensor` and `InpostPickupPointSensor` — `STATUS_ICONS` (mdi:truck-delivery, mdi:package-variant-closed-check, mdi:bell-ring, etc.) now shows correctly.

## [0.10.0] - 2026-05-07

### Hybrid entity model: per-parcel + per-paczkomat
- **`sensor.inpost_paczka_<shipmentNumber>`** restored — one entity per active parcel (every non-terminal status). State = Polish status label, friendly name = sender (via aliases). All attributes from 0.8.0 (status_raw, status_group, sender, sender_raw, references, courier_phone_number, tracking_url, shipment_type, pickup_date, can_collect, address, lat/lon, open_code, qr_code, expiry, stored_date, opening_hours, is_24_7, easy_access_zone).
- **`sensor.inpost_paczkomat_<code>`** added — one entity per paczkomat that physically holds 1+ parcels right now. State = number of parcels at that locker. **Friendly name = address** in the format `ulica numer, miasto` (e.g. `Sienkiewicza 12, Białystok`). Attributes: `pickup_point` (raw code), `address` (with postcode), `city`, `street`, `post_code`, `latitude`, `longitude`, `opening_hours`, `location_description`, `is_24_7`, `easy_access_zone`, `count`, `parcels` (list with shipment_number, sender, open_code, qr_code, expiry, status_raw).
- Both entity types are dynamic: created when relevant data appears in the API, removed via 2-tick grace period (~30 min) when the parcel reaches a terminal status / the paczkomat empties.
- Filtering rules unchanged: `eryk sssss` and other ignored senders are skipped from both entity sets and from notifications.

### Removed
- Migration cleanup from 0.9.1 — no longer needed since per-parcel entities are back.

## [0.9.1] - 2026-05-07

### Fixed
- Auto-migration on setup: orphaned `sensor.inpost_paczka_*` entities left over from 0.5.0–0.8.0 are now removed from `entity_registry` on first refresh. Without this they sat as unavailable entities cluttering the registry after upgrading to 0.9.0.

## [0.9.0] - 2026-05-07

### ⚠ BREAKING — Per-paczkomat entity model
- **Per-parcel sensors removed.** `sensor.inpost_paczka_<shipmentNumber>` no longer exists.
- **New per-paczkomat sensors.** `sensor.inpost_paczkomat_<code>` is created dynamically when at least one parcel is waiting at that locker. Removed via grace period (~30 min) when the locker becomes empty.
  - **State** = number of parcels currently waiting at this paczkomat (ignored senders excluded)
  - **Attributes**: `pickup_point`, `address`, `city`, `street`, `post_code`, `latitude`, `longitude`, `opening_hours`, `location_description`, `is_24_7`, `easy_access_zone`, `count`, **`parcels`** (list of `{shipment_number, sender, sender_raw, open_code, qr_code, expiry_date, stored_date, status_raw}`).
- **Why**: focuses entirely on the "actionable" state — when something is at the paczkomat. Parcels in transit (CONFIRMED, OUT_FOR_DELIVERY, etc.) no longer surface as entities; track them in the InPost mobile app directly.

### Migration notes
Automations referencing `sensor.inpost_paczka_*` need to be updated to use `sensor.inpost_paczkomat_*` instead. Example:
```yaml
# old (0.5.0–0.8.0)
trigger:
  - platform: state
    entity_id: sensor.inpost_paczka_520000013815668103180697

# new (0.9.0+)
trigger:
  - platform: state
    entity_id: sensor.inpost_paczkomat_bia45m
    # state increments when a parcel arrives, decrements on pickup
```

## [0.8.0] - 2026-05-07

### Added
Per-parcel sensor exposes more API fields as attributes:
- **`courier_phone_number`** — phone to reach the courier (only set when `status=OUT_FOR_DELIVERY`)
- **`tracking_url`** — `https://inpost.pl/redirect/?token=…` link to open the parcel in InPost web/app
- **`status_group`** — high-level bucket (`TO_SEND` / `IN_DELIVERY` / `READY_FOR_PICKUP` / `DELIVERED`); much easier to use in automations than the 25+ raw statuses
- **`shipment_type`** — `courier` / `parcel_locker`
- **`pickup_date`** — when the parcel was picked up (set on `DELIVERED`)
- **`can_collect`** — `True/False` from `operations.collect` (whether collection is currently possible)

### Changed
- `InpostCountSensor` ("Paczki do odbioru") simplified — now exposes only the count as state, no extra attributes (per-parcel detail belongs on the parcel entities, not on the aggregate count).

## [0.7.1] - 2026-05-07

### Added
- **Ignored senders blocklist** (`IGNORED_SENDER_PATTERNS` in `const.py`). Parcels whose sender name matches any pattern (case-insensitive substring) are skipped — no entity created and no notifications fired. Default blocklist contains `eryk sssss` (test/spam parcel observed in the wild). Extend the set in `const.py` to add more.
- Existing entities for newly-blocked senders go `unavailable` and are GC'd via the normal grace period.

## [0.7.0] - 2026-05-07

### Added
- **Sender alias dictionary** (`SENDER_ALIASES` in `const.py`). Normalizes the awkward names InPost returns into clean, recognizable shop labels:
  - `Seller using Cainiao logistics services` → `AliExpress` (Cainiao = Alibaba's logistics arm, mostly AliExpress)
  - `Amazon Polska` → `Amazon`
  - `Allegro Smart` / `Allegro Lokalnie` → `Allegro`
  - `KLINIKAAGD.PL` → `Klinika AGD`
  - …plus ~40 common Polish e-commerce / drogeria / fashion brands (Vinted, Zalando, Empik, x-kom, Morele, RTV Euro AGD, Notino, Hebe, Rossmann, IKEA, Decathlon, Sinsay, Reserved, H&M, Temu, SHEIN, Apart, Smyk, etc.). See `const.py:SENDER_ALIASES` to extend.
- New attribute `sender_raw` exposes the original (un-normalized) sender name from the API for templates that need it.

### Changed
- `sensor.sender` attribute now contains the canonical name (matching the friendly name). Use `sensor.sender_raw` for the original API value.

## [0.6.2] - 2026-05-07

### Changed
- Parcel friendly name now uses **sender name** (e.g. `VINTED`, `Amazon Polska`) instead of `Paczka <shipmentNumber>`. Long sender strings (>40 chars, e.g. `Seller using Cainiao logistics services`) are truncated. Duplicates are fine — HA disambiguates via entity_id (still shipment-based). Fallback to `Paczka <shipmentNumber>` if API returns no sender.
- `entity_id` and `unique_id` unchanged — automations referencing `sensor.inpost_paczka_<sn>` keep working.

## [0.6.1] - 2026-05-07

### Changed
- `RETURNED_TO_SENDER` and `RETURNED_TO_SOURCE_BRANCH` no longer treated as terminal — entities for returned parcels stay visible (return state is still interesting). Terminal set is now only `DELIVERED`, `PICKED_UP`, `CANCELED`, `PICKUP_TIME_EXPIRED`.

### Removed
- Attributes `parcel_size` and `weight` removed from parcel entity (rarely useful, added in 0.6.0).

## [0.6.0] - 2026-05-07

### Added
- **Extra parcel attributes**: `references` (sender's order ID, e.g. Allegro/Amazon), `parcel_size`, `weight`, `estimated_delivery_date`. Useful for matching parcels to specific orders or driving dashboards by delivery ETA.

### Changed
- **Hide finished parcels.** Parcel sensors are no longer created for terminal statuses (`DELIVERED`, `PICKED_UP`, `RETURNED_TO_SENDER`, `RETURNED_TO_SOURCE_BRANCH`, `CANCELED`, `PICKUP_TIME_EXPIRED`). Existing entities transition to `unavailable` once a parcel reaches a terminal status and are removed by the normal grace period (2 ticks = 30 min) — keeps the UI clean of completed deliveries.

## [0.5.0] - 2026-05-07

### Added
- **Per-parcel sensor for the entire lifecycle.** Each parcel from the API now gets its own `sensor.paczka_<shipmentNumber>` regardless of status. State reflects the current stage in Polish: `Utworzona`, `W doreczeniu`, `Gotowa do odbioru`, `Doreczona`, `Zwrocona do nadawcy`, etc.
- **Status-aware icons.** Icon switches automatically with the parcel state (`mdi:truck-delivery` while in transit, `mdi:package-variant-closed-check` when ready, `mdi:package-check` when delivered, etc.).
- **`status_raw`** attribute exposes the original API status for templates/automations.
- **`all_tracked`** attribute on the count sensor reports total tracked parcels (any status).

### Removed
- **Multiple notify services.** `notify_services` (CSV / list) replaced by single `notify_service` field. Existing list/CSV values from older versions still work as fallback (first valid entry is used).
- **TTS support.** `tts_message`, `tts_service`, `tts_targets` removed entirely. Use a HA automation hooked to the parcel sensor state if you still want TTS.

### Changed
- Parcel sensor lifecycle now driven by `coordinator.all_parcels` instead of `pickup_parcels`. Distance/notify logic still operates only on pickup-ready parcels (`PICKUP_STATUSES`).
- `manifest.json` keys re-ordered to satisfy hassfest (domain, name, then alphabetical).
- CI: `actions/checkout@v4` → `actions/checkout@v5` (Node 20 deprecated by GitHub from 2026-06-02).

### Fixed
- HACS brand assets (`brand/icon.png`, `brand/icon@2x.png`, `brand/logo*.png`) added so the HACS `brands` validation no longer fails.
- hassfest manifest key-order failure resolved.

## [0.4.0] - 2026-05-03

### Added
- **Multi-phone support** - `device_trackers` option accepts a list of phones from HA Companion. Distance is computed against ALL of them; the smallest distance (any phone, any locker) wins. Useful for couples / families with one InPost account.
- **Multiple notify services** - `notify_services` accepts a comma-separated list (`notify.mobile_app_pixel_8, notify.mobile_app_iphone`). Each parcel triggers all of them in parallel.
- **TTS support** - new fields `tts_message`, `tts_service` (default `tts.google_translate_say`), `tts_targets` (multi-select media_players). When threshold is crossed, the message is spoken on every selected media_player. Empty `tts_message` keeps TTS off.
- **Cooldown in hours** - `notify_cooldown_hours` (1-168) replaces the legacy minute-based field for clearer semantics.

### Changed
- Old single-value options (`device_tracker`, `notify_service`, `notify_cooldown_min`) remain as fallback for backwards compatibility - existing entries continue to work without re-configuration.
- Cooldown is set only when at least one notify or TTS target succeeds (lost-notification fix preserved).

## [0.3.0] - 2026-05-03

### Added
- Initial setup form now includes the options step (`setup_options`): pick the device_tracker phone, distance threshold, notify service, and per-parcel cooldown right when adding the integration. All optional - can be skipped and configured later via `Configure`.

### Changed
- Clearer field labels: "Powiadom gdy paczka blizej niz (m)" instead of generic "Próg powiadomień".
- Both `pl` and `en` translations updated for the new step.

## [0.2.0] - 2026-05-03

### Added
- Options flow: configurable device_tracker (filtered to `mobile_app` integration), notification threshold (meters), notify service, per-parcel cooldown.
- Persistent notification cooldowns via `homeassistant.helpers.storage.Store` (survive HA restart).
- Dynamic add/remove of per-parcel sensors with grace period (2 ticks) before deletion.
- Rate-limit handling (429) with exponential backoff.
- Async `asyncio.Lock` around token refresh to prevent racing refresh storms.
- Request timeouts on all HTTP calls.
- English translation (`translations/en.json`).
- `DeviceInfo` for all entities (groups them under one InPost device per account).
- `EntityCategory.DIAGNOSTIC` on the count sensor.

### Changed
- `unique_id` for parcel sensors now uses raw `shipmentNumber` (no slug collision risk).
- Reauth flow uses `context["entry_id"]` + `async_update_reload_and_abort` (idiomatic).
- Notification logging downgraded to DEBUG; pickup `openCode` no longer goes to INFO log.
- `datetime.now()` replaced with `homeassistant.util.dt.utcnow()` (TZ-safe).
- Notify cooldown set ONLY after successful service call (lost-notification fix).

### Fixed
- Token refresh race condition (two parallel 401s could mutually invalidate refreshTokens).
- Removed cudzy URL placeholder from `manifest.json` (`IFOSSA/inpost-python`).
- `DEFAULT_DEVICE_TRACKER` no longer hardcoded to a personal device.

## [0.1.0] - 2026-04-30

### Added
- Initial release: SMS-based authentication, pickup parcel sensors, distance to nearest paczkomat.
