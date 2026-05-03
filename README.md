# InPost (paczki) — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant custom integration for **InPost parcel lockers (paczkomaty)** — pickup status, distance to the nearest locker, and notifications when a phone gets close to a locker with a parcel waiting.

## Features

- Logs in via SMS (the same flow as the InPost mobile app — phone number → SMS code).
- Auto-refreshes the session token; reauth via SMS when refresh fails.
- Sensors:
  - **`sensor.paczki_do_odbioru`** — number of parcels ready for pickup. Attribute: `parcels` (list of shipment numbers).
  - **`sensor.najblizsza_paczka_dystans`** — distance in metres from a chosen `device_tracker` to the nearest locker holding a parcel. Attributes: nearest pickup point name + shipment number.
  - **`sensor.paczka_<shipmentNumber>`** — one entity per pickup-ready parcel. Attributes include `open_code`, `qr_code`, address, opening hours, expiry date, 24/7 flag, easy-access zone.
- Per-parcel sensors are added/removed dynamically as parcels arrive and are picked up (with a 2-tick grace period to absorb transient API glitches).
- Optional **push notifications** when a parcel comes within a configurable distance of your phone — per-parcel cooldown, persistent across HA restarts.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories** → paste this repo URL → category **Integration** → *Add*.
2. Find **InPost (paczki)** in HACS → *Download*.
3. Restart Home Assistant.
4. *Settings → Devices & services → Add integration → InPost*.

### Manual

1. Copy `custom_components/inpost/` into your HA config directory:

   ```
   <config>/custom_components/inpost/
   ```

2. Restart Home Assistant.
3. *Settings → Devices & services → Add integration → InPost*.

## Configuration

1. Click **Add Integration → InPost**.
2. Enter the phone number from your InPost account (9 digits, no `+48`).
3. Wait for the SMS — same SMS as on the InPost mobile app.
4. Enter the 6-digit code.

Done — sensors are created automatically.

### Options (per integration entry)

Click **Configure** on the integration card:

| Option | Default | Notes |
|---|---|---|
| **Phone (device_tracker)** | (empty) | List filtered to `mobile_app` integration — only phones running Home Assistant Companion show up. |
| **Notification threshold (m)** | `500` | Notify when a parcel is within this distance. `0` disables notifications. |
| **Notify service** | (empty) | e.g. `notify.mobile_app_<your_phone>`. Must exist in Home Assistant. |
| **Per-parcel cooldown (min)** | `60` | Same parcel won't notify again for this many minutes after a successful notify. |

Cooldowns are persisted with `helpers.storage.Store`, so a HA restart will not re-trigger notifications for parcels you have already been pinged about.

## Multiple accounts

Add the integration multiple times — once per phone number. Each entry gets its own `device_tracker` selection and its own notify service, so e.g. you and your partner can have notifications routed to your respective phones.

## Limitations / known caveats

- The integration polls `/v3/parcels/tracked` every **15 minutes**. Push from InPost is not supported (no public webhook).
- Only parcels with status `READY_TO_PICKUP*` and `PICKUP_REMINDER_SENT` are surfaced; parcels in transit are ignored.
- Lockers without `pickUpPoint.location` (some indoor / branch pickups) are counted but excluded from distance calculations.
- The notification message contains the parcel `open_code` — if you route notifications to a service that logs to disk or a third party, treat that as sensitive.

## Disclaimer

> Ta integracja korzysta z **NIEOFICJALNEGO** mobilnego API InPost (`api-inmobile-pl.easypack24.net`) — tego samego, którego używa aplikacja **InPost Mobile** na Androidzie. **Nie jest to publiczne API** i InPost nie autoryzował jego użycia przez aplikacje trzecie.
>
> Używasz na **własne ryzyko**:
> - InPost może w każdej chwili zerwać sesję, zmienić API lub zablokować konto.
> - Spoofowany User-Agent + endpointy `/v1/sendSMSCode`, `/v1/confirmSMSCode`, `/v3/parcels/tracked` mogą naruszać regulamin usługi.
> - Autorzy nie ponoszą odpowiedzialności za utratę dostępu do konta InPost ani za skutki używania tej integracji.
> - Tokeny `authToken` / `refreshToken` są zapisane w `.storage/core.config_entries` w plaintext — **nie commituj snapshotów konfiguracji HA do publicznych repo**.
>
> ---
>
> This integration uses the **UNOFFICIAL** InPost mobile API (`api-inmobile-pl.easypack24.net`) — the one used by the InPost Mobile Android app. **It is not a public API** and InPost has not authorized third-party use.
>
> Use at your own risk: InPost may rescind access at any time, change the API, or lock your account. Tokens are stored in plaintext in `.storage/core.config_entries` — never commit HA configuration snapshots to public repos.

## Development

```bash
# 1. Clone into a HA config dir or symlink
git clone <this-repo> /config/custom_components/inpost-dev
ln -s /config/custom_components/inpost-dev/custom_components/inpost /config/custom_components/inpost

# 2. Enable debug logs
# configuration.yaml
logger:
  default: warning
  logs:
    custom_components.inpost: debug
```

## License

[MIT](LICENSE).

## Acknowledgements

Inspired by `IFOSSA/inpost-python` (different design — this integration speaks to the mobile API directly to handle Cloudflare and refresh-token edge cases).
