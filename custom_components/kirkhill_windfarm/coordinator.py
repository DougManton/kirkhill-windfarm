from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_BASE_URL, API_TIMEOUT, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "KirkhillHAIntegration/1.0 (+https://github.com/DougManton/kirkhill-windfarm)"
)

# Fleet-load mitigation: randomise the polling interval within a ±30 s window
# so simultaneous HA restarts don't produce a thundering-herd effect.
_SCAN_JITTER_SECS = 30

# Retry backoff delays (seconds) for transient failures (5xx, timeouts,
# connection errors). ±50 % jitter is applied at runtime so concurrent clients
# don't synchronise their retry attempts after a shared failure.
_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)

# 429 Retry-After values at or below this threshold are honoured inline (the
# coordinator sleeps and retries within the current update cycle). Longer
# values are deferred to the next scheduled update.
_MAX_INLINE_RETRY_AFTER_SECS = 30.0

# Proactive rate-limit protection: if the remaining-requests counter falls
# below this threshold the coordinator pauses for _RATELIMIT_PAUSE_SECS before
# its next cycle. This avoids hitting 429 under normal operation.
_RATELIMIT_LOW_WATER = 8
_RATELIMIT_PAUSE_SECS = 60.0


# ---------------------------------------------------------------------------
# Response-metadata helpers (module-level so they can be tested independently)
# ---------------------------------------------------------------------------


def _parse_retry_after(headers: Any) -> float | None:
    """Return Retry-After in seconds, or None if absent/unparseable.

    Handles both the integer-seconds form and the HTTP-date form (RFC 7231).
    """
    if headers is None:
        return None
    value: str | None = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        retry_dt = parsedate_to_datetime(value)
        return max((retry_dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except Exception:
        return None


def _parse_response_meta(headers: Any) -> dict[str, Any]:
    """Extract rate-limit and cache metadata from a successful response."""
    if headers is None:
        return {}
    meta: dict[str, Any] = {}

    # Standard rate-limit headers (X-Ratelimit-Limit / X-Ratelimit-Remaining)
    for key, field in (
        ("X-Ratelimit-Limit", "ratelimit_limit"),
        ("X-Ratelimit-Remaining", "ratelimit_remaining"),
    ):
        raw = headers.get(key)
        if raw is not None:
            try:
                meta[field] = int(raw)
            except (ValueError, TypeError):
                pass

    # API-specific cache headers
    cache_status = headers.get("X-Wind-Farm-Api-Cache")
    if cache_status:
        meta["cache_status"] = cache_status.upper()

    raw_ttl = headers.get("X-Wind-Farm-Api-Cache-Ttl")
    if raw_ttl is not None:
        try:
            meta["cache_ttl"] = int(raw_ttl)
        except (ValueError, TypeError):
            pass

    return meta


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class KirkhillCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Kirk Hill Wind Farm API data."""

    def __init__(self, hass: HomeAssistant, api_token: str) -> None:
        jitter = random.uniform(-_SCAN_JITTER_SECS, _SCAN_JITTER_SECS)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL + timedelta(seconds=jitter),
        )
        self._api_token = api_token

        # Rate-limit state ─────────────────────────────────────────────────
        # Set to a future datetime to suppress fetches until that time.
        self._rate_limited_until: datetime | None = None
        # True when the pause was self-imposed (low-water); False for 429.
        self._rate_limit_is_proactive: bool = False
        # Minimum X-Ratelimit-Remaining seen across all endpoints this cycle.
        self._ratelimit_remaining: int | None = None
        self._ratelimit_limit: int | None = None

        # Cache-TTL validation fires once on the first successful response.
        self._cache_ttl_validated: bool = False

    @property
    def ratelimit_remaining(self) -> int | None:
        """Most recent rate-limit remaining count (across all endpoints)."""
        return self._ratelimit_remaining

    @property
    def ratelimit_limit(self) -> int | None:
        """Rate-limit ceiling reported by the API."""
        return self._ratelimit_limit

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }

    # ------------------------------------------------------------------
    # Main update loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from all API endpoints concurrently."""
        now = datetime.now(timezone.utc)
        if self._rate_limited_until and now < self._rate_limited_until:
            remaining_secs = (self._rate_limited_until - now).total_seconds()
            if self._rate_limit_is_proactive and self.data is not None:
                # Proactive low-water pause: return previous data so sensors
                # stay valid rather than showing "Unavailable".
                _LOGGER.debug(
                    "Skipping fetch: proactive rate-limit pause (%.0f s remaining)",
                    remaining_secs,
                )
                return self.data
            # Reactive 429 pause: data may be incomplete, signal unavailability.
            raise UpdateFailed(
                f"Rate limited by API until {self._rate_limited_until.isoformat()} "
                f"({remaining_secs:.0f} s remaining)"
            )

        # Reset per-cycle minimum tracker before concurrent fetches.
        self._ratelimit_remaining = None

        session = async_get_clientsession(self.hass)
        (
            gen_owner,
            gen_site,
            gen_owner_today,
            gen_site_today,
            gen_owner_30d,
            gen_site_30d,
            wind_speed,
            turbines,
            turbines_site,
        ) = await asyncio.gather(
            self._fetch_with_retry(session, "/api/v1/generation?range=7d"),
            self._fetch_with_retry(session, "/api/v1/generation?range=7d&scope=site"),
            self._fetch_with_retry(session, "/api/v1/generation?range=today"),
            self._fetch_with_retry(session, "/api/v1/generation?range=today&scope=site"),
            self._fetch_with_retry(session, "/api/v1/generation?range=30d"),
            self._fetch_with_retry(session, "/api/v1/generation?range=30d&scope=site"),
            self._fetch_with_retry(session, "/api/v1/wind-speed?range=today"),
            self._fetch_with_retry(session, "/api/v1/turbines?range=today"),
            self._fetch_with_retry(session, "/api/v1/turbines?range=today&scope=site"),
            return_exceptions=True,
        )

        # If a 429 inside _fetch_with_retry set _rate_limited_until, abort the
        # whole cycle so sensors hold their previous values until the ban lifts.
        now = datetime.now(timezone.utc)
        if (
            self._rate_limited_until
            and now < self._rate_limited_until
            and not self._rate_limit_is_proactive
        ):
            raise UpdateFailed(
                f"Rate limited by API until {self._rate_limited_until.isoformat()}"
            )

        # ── Proactive rate-limit protection ────────────────────────────────
        # All four fetches completed; check whether the quota is running low.
        if (
            self._ratelimit_remaining is not None
            and self._ratelimit_remaining < _RATELIMIT_LOW_WATER
        ):
            self._rate_limited_until = datetime.now(timezone.utc) + timedelta(
                seconds=_RATELIMIT_PAUSE_SECS
            )
            self._rate_limit_is_proactive = True
            _LOGGER.warning(
                "Rate limit low (%d/%s remaining); pausing fetches for %.0f s",
                self._ratelimit_remaining,
                self._ratelimit_limit if self._ratelimit_limit else "?",
                _RATELIMIT_PAUSE_SECS,
            )
        else:
            # Clear any previous proactive pause once quota has recovered.
            if self._rate_limit_is_proactive:
                self._rate_limited_until = None
                self._rate_limit_is_proactive = False

        # ── Unwrap API envelope ────────────────────────────────────────────
        def _unwrap(result: Any, label: str) -> dict[str, Any]:
            if isinstance(result, Exception):
                _LOGGER.warning("Failed to fetch %s: %s", label, result)
                return {}
            return result.get("data", result) if isinstance(result, dict) else {}

        owner = _unwrap(gen_owner, "generation (owner)")
        site = _unwrap(gen_site, "generation (site)")
        owner_today = _unwrap(gen_owner_today, "generation today (owner)")
        site_today = _unwrap(gen_site_today, "generation today (site)")
        owner_30d = _unwrap(gen_owner_30d, "generation 30d (owner)")
        site_30d = _unwrap(gen_site_30d, "generation 30d (site)")
        ws = _unwrap(wind_speed, "wind-speed")
        tb = _unwrap(turbines, "turbines")
        tb_site = _unwrap(turbines_site, "turbines (site)")

        # Derive instantaneous power: kWh over 10-min interval × 6 → kW.
        def _last_interval_kw(series: list[dict]) -> float | None:
            if not series:
                return None
            last_kwh = series[-1].get("generation_kwh")
            return round(float(last_kwh) * 6, 2) if last_kwh is not None else None

        return {
            "owner": owner,
            "site": site,
            "owner_today": owner_today,
            "site_today": site_today,
            "owner_30d": owner_30d,
            "site_30d": site_30d,
            "wind_speed": ws,
            "turbines": tb,
            "turbines_site": tb_site,
            "current_power_kw": _last_interval_kw(site.get("series", [])),
            "current_owner_power_kw": _last_interval_kw(owner.get("series", [])),
        }

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    async def _fetch_with_retry(
        self, session: aiohttp.ClientSession, path: str
    ) -> dict[str, Any]:
        """Fetch path with exponential-backoff retry for transient failures.

        Retry policy:
          - Connection errors, timeouts, 5xx: retry up to len(_RETRY_DELAYS)
            times with jittered exponential backoff.
          - 429 with Retry-After ≤ _MAX_INLINE_RETRY_AFTER_SECS: sleep inline
            and retry (does not consume a normal retry slot).
          - 429 with longer (or absent) Retry-After: set _rate_limited_until
            and raise immediately.
          - Other 4xx: raise immediately, no retry.
        """
        last_exc: Exception | None = None
        delay_iter = iter(_RETRY_DELAYS)

        while True:
            try:
                body, meta = await self._fetch(session, path)
                self._process_response_meta(meta, path)
                return body

            except aiohttp.ClientResponseError as err:
                if err.status == 429:
                    retry_after = _parse_retry_after(err.headers)
                    if (
                        retry_after is not None
                        and retry_after <= _MAX_INLINE_RETRY_AFTER_SECS
                    ):
                        _LOGGER.warning(
                            "Rate limited on %s; sleeping %.0f s (Retry-After)",
                            path,
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        # Does not consume a normal retry slot; loop directly.
                        continue

                    backoff = retry_after if retry_after is not None else 300.0
                    self._rate_limited_until = datetime.now(timezone.utc) + timedelta(
                        seconds=backoff
                    )
                    self._rate_limit_is_proactive = False
                    _LOGGER.warning(
                        "Rate limited on %s; suppressing requests for %.0f s "
                        "(until %s)",
                        path,
                        backoff,
                        self._rate_limited_until.isoformat(),
                    )
                    raise UpdateFailed(
                        f"Rate limited by API (Retry-After {backoff:.0f} s)"
                    ) from err

                if err.status not in (500, 502, 503, 504):
                    raise  # Non-retryable client error

                last_exc = err
                _LOGGER.debug(
                    "Transient server error %d on %s; will retry", err.status, path
                )

            except (aiohttp.ClientError, TimeoutError) as err:
                last_exc = err
                _LOGGER.debug(
                    "Network/timeout error on %s (%s); will retry",
                    path,
                    type(err).__name__,
                )

            try:
                base_delay = next(delay_iter)
            except StopIteration:
                raise UpdateFailed(
                    f"Request to {path} failed after "
                    f"{len(_RETRY_DELAYS) + 1} attempts"
                ) from last_exc

            jittered = base_delay * random.uniform(0.5, 1.5)
            _LOGGER.debug("Retrying %s in %.1f s", path, jittered)
            await asyncio.sleep(jittered)

    # ------------------------------------------------------------------
    # Response-metadata side-effects
    # ------------------------------------------------------------------

    def _process_response_meta(self, meta: dict[str, Any], path: str) -> None:
        """Update coordinator state from a successful response's metadata."""
        # Rate-limit counters ─────────────────────────────────────────────
        # Track the minimum remaining across all endpoints in this cycle so
        # we can make one accurate low-water decision after gather() returns.
        if (remaining := meta.get("ratelimit_remaining")) is not None:
            self._ratelimit_limit = meta.get("ratelimit_limit") or self._ratelimit_limit
            self._ratelimit_remaining = (
                remaining
                if self._ratelimit_remaining is None
                else min(self._ratelimit_remaining, remaining)
            )

        # Cache-TTL validation (fires once on the first successful response) ─
        if not self._cache_ttl_validated:
            if (cache_ttl := meta.get("cache_ttl")) is not None:
                self._cache_ttl_validated = True
                scan_secs = self.update_interval.total_seconds()
                if scan_secs < cache_ttl:
                    _LOGGER.warning(
                        "Scan interval (%.0f s) is shorter than the server "
                        "cache TTL (%d s); responses will be served from cache "
                        "and contain no new data. Consider increasing "
                        "SCAN_INTERVAL in const.py.",
                        scan_secs,
                        cache_ttl,
                    )
                else:
                    _LOGGER.debug(
                        "Server cache TTL: %d s — scan interval %.0f s is fine",
                        cache_ttl,
                        scan_secs,
                    )

        # Cache status (HIT/MISS) at debug level ─────────────────────────
        if cache_status := meta.get("cache_status"):
            _LOGGER.debug("API cache %s for %s", cache_status, path)

    # ------------------------------------------------------------------
    # Low-level single-attempt fetch
    # ------------------------------------------------------------------

    async def _fetch(
        self, session: aiohttp.ClientSession, path: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Make one HTTP GET, returning (response_body, parsed_header_meta)."""
        url = f"{API_BASE_URL}{path}"
        async with asyncio.timeout(API_TIMEOUT):
            async with session.get(url, headers=self._headers) as response:
                response.raise_for_status()
                body = await response.json()
                meta = _parse_response_meta(response.headers)
                return body, meta

    # ------------------------------------------------------------------
    # Token validation (config-flow setup only — no retry needed)
    # ------------------------------------------------------------------

    async def async_validate_api_token(self) -> bool:
        """Return True if the token is accepted; False for 401/403."""
        session = async_get_clientsession(self.hass)
        try:
            await self._fetch(session, "/api/v1/generation?range=7d")
            return True
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                return False
            raise
