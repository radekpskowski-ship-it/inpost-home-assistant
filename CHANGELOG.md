# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
