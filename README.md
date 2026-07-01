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
| Generation Today | kWh | Generation for this turbine since midnight |
| Status | — | `running` or `stopped` |
| Capacity Factor | % | Actual output as a percentage of rated peak capacity |
| Availability | % | Percentage of time the turbine was operational (excludes planned/unplanned downtime) |

---

## Installation via HACS

1. In Home Assistant open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/DougManton/kirkhill-windfarm` as an **Integration**.
3. Search for **Kirk Hill Wind Farm** and install.
4. Restart Home Assistant.

## Manual installation

Copy `custom_components/kirkhill_windfarm/` into your HA `config/custom_components/` directory and restart.

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

Current power output is not a direct API field. It is derived from the most recent 10-minute generation interval: `generation_kwh × 6 = instantaneous kW`.

### Retry on transient errors

Connection failures, timeouts, and 5xx server errors are retried up to **2 times** with exponential backoff (initial delays of 2 s and 5 s, each with ±50 % random jitter). Non-retryable errors (401, 403, 404) are raised immediately.

### Rate limiting

The API uses a rolling request quota reported via `X-Ratelimit-Limit` and `X-Ratelimit-Remaining` headers.

**Reactive (429 response):**
- If the API returns HTTP 429 and the `Retry-After` header indicates a wait of 30 seconds or less, the integration sleeps inline and retries within the current update cycle.
- If `Retry-After` exceeds 30 seconds (or is absent), requests are suppressed for the full duration (defaulting to 5 minutes if no header is present). Sensors will show **Unavailable** until the ban lifts.

**Proactive (low-water guard):**
- After each update cycle the integration checks the minimum `X-Ratelimit-Remaining` value seen across all four endpoints. If the remaining count drops below **8**, fetches are paused for **60 seconds**.
- During a proactive pause the previous data is returned unchanged, so sensors remain valid and no error is shown in the HA UI.
- The pause is cleared automatically once the quota recovers.

### Cache awareness

The API caches responses for **60 seconds** (`X-Wind-Farm-Api-Cache-Ttl`). Polling faster than this would return identical data and waste quota unnecessarily. The 5-minute poll interval is well above this threshold; a warning is logged if the two values ever diverge. Cache hit/miss status (`X-Wind-Farm-Api-Cache`) is logged at DEBUG level for each request.

### Fleet deployments

When many HA instances restart simultaneously (power cut, co-ordinated update) they would otherwise all poll the API in lockstep indefinitely. To prevent this, each instance picks a random offset of ±30 seconds at startup, which permanently desynchronises its polling from every other instance.

---

## API response structure

All responses follow the envelope `{"data": {"window": {…}, "summary": {…}, "series": […]}}`. The integration strips the outer `data` wrapper before using the payload.

```json
// GET /api/v1/generation?range=7d  (owner-scoped by default)
{
  "data": {
    "window": {
      "range": "7d",
      "from": "2026-06-24T23:00:00Z",
      "to": "2026-07-01T10:44:00Z",
      "bucket": "10m",
      "scope": "owner",
      "timezone": "Europe/London"
    },
    "summary": {
      "total_generation_kwh": 149.1,
      "capacity_factor_percent": 34.68,
      "active_turbines": 8,
      "site_capacity_watts": 18800000,
      "latest_generation_interval_end": "2026-07-01T10:20:00Z",
      "latest_import_status": "running"
    },
    "series": [
      {"timestamp": "2026-06-24T23:00:00Z", "generation_kwh": 0.0},
      {"timestamp": "2026-06-24T23:10:00Z", "generation_kwh": 0.461}
    ]
  }
}
```

Adding `&scope=site` to the generation endpoint returns the same structure with whole-farm totals instead of owner-scoped figures.
