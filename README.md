# Kirk Hill Wind Farm — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A HACS-compatible custom integration that connects Home Assistant to the [Kirk Hill Community Co-op](https://dashboard.kirkhillcoop.org) wind farm dashboard API. Designed for co-owners who want to monitor the farm's performance and track income from their share of generation.

---

## Features

- **Site performance** — today/7-day/30-day generation, instantaneous power, capacity factor, active turbine count, wind speed
- **Owner share** — your generation and power figures are returned directly by the API (scoped to your account); no share percentage needs to be entered
- **Per-turbine monitoring** — generation, status, capacity factor, and availability per turbine, added automatically from the API
- **Income tracking** — optional; estimated income from your generation using a configurable £/kWh rate
- **Rate history** — multiple income rates each with an effective date; the correct rate is applied automatically to each measurement period
- **Organised by device** — sensors are split across three device types so each device page shows a focused set of sensors

---

## Devices and sensors

Sensors are grouped into separate HA devices so each device page shows a focused set of related entities.

### Kirk Hill Wind Farm *(site-wide metrics)*

| Sensor | Unit | Description |
|---|---|---|
| Generation Today | kWh | Total farm generation since midnight |
| Generation (7 days) | kWh | Total farm generation over the last 7 days |
| Generation (30 days) | kWh | Total farm generation over the last 30 days |
| Power | kW | Instantaneous site-wide output, derived from the most recent 10-minute interval |
| Capacity Factor | % | Ratio of actual site output to rated capacity |
| Active Turbines | — | Number of turbines currently generating |
| Wind Speed | m/s | Most recent 1-minute wind speed reading |
| Average Wind Speed (today) | m/s | Mean of all 1-minute wind speed readings since midnight |

### Your Share *(owner-scoped metrics)*

| Sensor | Unit | Description |
|---|---|---|
| Generation Today | kWh | Your share of generation since midnight |
| Generation (7 days) | kWh | Your share of generation over the last 7 days |
| Generation (30 days) | kWh | Your share of generation over the last 30 days |
| Power | kW | Your instantaneous output, derived from the most recent 10-minute interval |
| Ownership Share | % | Your proportional ownership of the farm, derived from 7-day generation ratio |
| Revenue Today | £ | Estimated income from today's generation *(only shown when rates are configured)* |
| Revenue (7 days) | £ | Estimated income from your 7-day generation *(only shown when rates are configured)* |
| Active Income Rate | £/kWh | Rate currently in use; full rate history in the `rate_history` attribute *(only shown when rates are configured)* |

### Turbine *N* *(one device per turbine, created dynamically on first data fetch)*

| Sensor | Unit | Description |
|---|---|---|
| My Generation Today | kWh | Your ownership share of this turbine's generation since midnight |
| Total Generation Today | kWh | Whole-farm generation from this turbine since midnight |
| Status | — | `active`, `inactive`, or `unknown` as reported by the API; `state_text` and `status_since` available as extra attributes |
| Capacity Factor | % | Actual output as a percentage of rated peak capacity |
| Rotor Speed | rpm | Most recent rotor speed reading |

---

## Installation via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DougManton&repository=kirkhill-windfarm&category=Integration)

1. In Home Assistant open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/DougManton/kirkhill-windfarm` as an **Integration**.
3. Search for **Kirk Hill Wind Farm** and install.
4. Restart Home Assistant.

## Manual installation

Copy `custom_components/kirkhill_windfarm/` into your HA `config/custom_components/` directory and restart.

### Docker installations

The Home Assistant container runs as uid `1000`. If you copy the files as `root` (common with Docker volume mounts), HA cannot read them and the integration will not appear — the tell-tale sign is that a `__pycache__` folder is never created in the `kirkhill_windfarm` directory.

Fix the ownership and permissions after copying, then restart:

```bash
chown -R 1000:1000 /your/config/path/custom_components/kirkhill_windfarm
chmod -R 755 /your/config/path/custom_components/kirkhill_windfarm
```

If you use the `linuxserver/homeassistant` image the uid may differ — check with `docker exec homeassistant id`.

---

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for **Kirk Hill Wind Farm**.

### Step 1 — API token

Generate a token from your dashboard at `dashboard.kirkhillcoop.org`. The dashboard API returns data already scoped to your ownership share, so no share percentage is required here.

### Step 2 — Income rate (optional)

Optionally enter the rate (£ per kWh) you receive on generation income and the date from which it applies. Leave the rate field blank to skip — income tracking can be configured at any time via the integration's **Configure** button. The **Your Revenue** and **Active Income Rate** sensors only appear once at least one rate is configured.

### Managing income rates

Rates can change over time (e.g. contract renewals). To add or remove entries:

1. Go to **Settings → Devices & Services → Kirk Hill Wind Farm → Configure**.
2. Choose **Add income rate** and enter the effective date and new rate.
3. The **Your Revenue (7 days)** sensor automatically uses the rate that was active at the start of the 7-day window.

The **Active Income Rate** sensor's `rate_history` attribute lists the full chronological rate table, which can be used in HA templates or the Energy dashboard.

---

## Polling and API behaviour

### Poll interval

The integration polls all endpoints every **5 minutes** (±30 seconds of randomised jitter — see [Fleet deployments](#fleet-deployments) below).

Current power output is read directly from the `total_power_kw` field returned by `/api/v1/current`.

### Retry on transient errors

Connection failures, timeouts, and 5xx server errors are retried up to **2 times** with exponential backoff (initial delays of 2 s and 5 s, each with ±50 % random jitter). Non-retryable errors (401, 403, 404) are raised immediately.

### Rate limiting

The API uses a rolling request quota reported via `X-Ratelimit-Limit` and `X-Ratelimit-Remaining` headers.

**Reactive (429 response):**
- If the API returns HTTP 429 and the `Retry-After` header indicates a wait of 30 seconds or less, the integration sleeps inline and retries within the current update cycle.
- If `Retry-After` exceeds 30 seconds (or is absent), requests are suppressed for the full duration (defaulting to 5 minutes if no header is present). Sensors will show **Unavailable** until the ban lifts.

**Proactive (low-water guard):**
- After each update cycle the integration checks the minimum `X-Ratelimit-Remaining` value seen across all endpoints. If the remaining count drops below **8**, fetches are paused for **60 seconds**.
- During a proactive pause the previous data is returned unchanged, so sensors remain valid and no error is shown in the HA UI.
- The pause is cleared automatically once the quota recovers.

### Cache awareness

The API caches responses for **60 seconds** (`X-Wind-Farm-Api-Cache-Ttl`). Polling faster than this would return identical data and waste quota unnecessarily. The 5-minute poll interval is well above this threshold; a warning is logged if the two values ever diverge. Cache hit/miss status (`X-Wind-Farm-Api-Cache`) is logged at DEBUG level for each request.

### Fleet deployments

When many HA instances restart simultaneously (power cut, co-ordinated update) they would otherwise all poll the API in lockstep indefinitely. To prevent this, each instance picks a random offset of ±30 seconds at startup, which permanently desynchronises its polling from every other instance.

---

## API response structure

All responses follow the envelope `{"data": {…}}`. The integration strips the outer `data` wrapper before using the payload.

### Current (`/api/v1/current`)

Real-time snapshot — used for instantaneous power, wind speed, today's generation total, and per-turbine status.

```json
// GET /api/v1/current  (owner-scoped by default)
{
  "data": {
    "summary": {
      "total_power_kw": 1.655,
      "wind_speed_mps": 9.14,
      "capacity_factor_percent": 59.96,
      "active_turbines": 8,
      "inactive_turbines": 0,
      "unknown_turbines": 0,
      "total_turbines": 8,
      "total_generation_kwh_today": 38.272,
      "latest_power_at": "2026-09-20T15:20:00Z"
    },
    "turbines": [
      {
        "id": "T1",
        "status": "active",
        "power_kw": 0.253,
        "wind_speed_mps": 9.9,
        "capacity_factor_percent": 73.32,
        "status_started_at": "2026-09-13T07:44:14Z",
        "state_text": "Turbine in operation"
      }
    ]
  }
}
```

Adding `&scope=site` returns whole-farm totals.

### Generation (`/api/v1/generation`)

```json
// GET /api/v1/generation?range=today  (owner-scoped by default)
{
  "data": {
    "window": {
      "range": "today",
      "from": "2026-09-19T23:00:00Z",
      "to": "2026-09-20T15:29:00Z",
      "bucket": "1m",
      "scope": "owner"
    },
    "summary": {
      "total_generation_kwh": 38.13,
      "capacity_factor_percent": 85.23,
      "active_turbines": 8,
      "capacity_watts": 2760.387,
      "latest_generation_interval_end": "2026-09-20T15:12:00Z",
      "latest_import_status": "success"
    },
    "series": [
      {"timestamp": "2026-09-19T23:00:00Z", "generation_kwh": 0.046},
      {"timestamp": "2026-09-19T23:01:00Z", "generation_kwh": 0.045}
    ]
  }
}
```

Adding `&scope=site` returns the same structure with whole-farm totals. Supported `range` values: `today`, `7d`, `30d`.

### Wind speed (`/api/v1/wind-speed`)

```json
// GET /api/v1/wind-speed?range=today
{
  "data": {
    "series": [
      {"timestamp": "2026-09-19T23:00:00Z", "wind_speed_mps": 12.51},
      {"timestamp": "2026-09-19T23:01:00Z", "wind_speed_mps": 12.23}
    ]
  }
}
```

### Turbines (`/api/v1/turbines`)

```json
// GET /api/v1/turbines?range=today
{
  "data": {
    "turbines": [
      {
        "id": "T1",
        "generation_kwh": 5.052,
        "generation_share_percent": 13.26,
        "capacity_factor_percent": 90.47,
        "capacity_watts": 345.048375,
        "latest_generation_interval_end": "2026-09-20T15:11:00Z",
        "latest_rotor_speed_rpm": 14.72,
        "latest_rotor_speed_at": "2026-09-20T15:11:00Z"
      }
    ]
  }
}
```

Turbines are identified by `id` (e.g. `T1`–`T8`). Adding `&scope=site` returns whole-farm generation figures per turbine.
