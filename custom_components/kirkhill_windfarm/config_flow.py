from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    CONF_API_TOKEN,
    CONF_EFFECTIVE_FROM,
    CONF_INCOME_RATES,
    CONF_RATE_PER_KWH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _validate_api_token(hass: HomeAssistant, token: str) -> bool | None:
    """Check whether the token is accepted by the API.

    Returns True (2xx), False (401/403), or None (unreachable / unexpected error).
    Any exception is logged at DEBUG so the HA log reveals the root cause.
    """
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        async with asyncio.timeout(15):
            async with session.get(
                f"{API_BASE_URL}/api/v1/generation?range=7d",
                headers=headers,
            ) as resp:
                if resp.status in (401, 403):
                    return False
                resp.raise_for_status()
                return True
    except Exception:  # pylint: disable=broad-except
        _LOGGER.warning("Kirk Hill API token validation error", exc_info=True)
        return None


def _validate_date(value: str) -> str:
    if not _DATE_RE.match(value):
        raise vol.Invalid("Date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as err:
        raise vol.Invalid(str(err)) from err
    return value


def _rate_label(rate: dict) -> str:
    return f"{rate[CONF_EFFECTIVE_FROM]}: £{rate[CONF_RATE_PER_KWH]:.4f}/kWh"


class KirkhillConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Kirk Hill Wind Farm."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_token: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: validate API token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            result = await _validate_api_token(self.hass, user_input[CONF_API_TOKEN])
            if result is None:
                errors["base"] = "cannot_connect"
            elif result is False:
                errors["base"] = "invalid_auth"
            else:
                self._api_token = user_input[CONF_API_TOKEN]
                return await self.async_step_income_rate()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )

    async def async_step_income_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: set the opening income rate."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _validate_date(user_input[CONF_EFFECTIVE_FROM])
            except vol.Invalid:
                errors[CONF_EFFECTIVE_FROM] = "invalid_date"
            else:
                return self.async_create_entry(
                    title="Kirk Hill Wind Farm",
                    data={CONF_API_TOKEN: self._api_token},
                    options={
                        CONF_INCOME_RATES: [
                            {
                                CONF_EFFECTIVE_FROM: user_input[CONF_EFFECTIVE_FROM],
                                CONF_RATE_PER_KWH: user_input[CONF_RATE_PER_KWH],
                            }
                        ]
                    },
                )

        return self.async_show_form(
            step_id="income_rate",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RATE_PER_KWH): vol.All(
                        vol.Coerce(float), vol.Range(min=0.0)
                    ),
                    vol.Required(
                        CONF_EFFECTIVE_FROM, default=date.today().isoformat()
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> KirkhillOptionsFlow:
        return KirkhillOptionsFlow(config_entry)


class KirkhillOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Kirk Hill Wind Farm: manage income rate history."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._income_rates: list[dict] = list(
            config_entry.options.get(CONF_INCOME_RATES, [])
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_rate", "remove_rate"],
        )

    async def async_step_add_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add or replace an income rate entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                _validate_date(user_input[CONF_EFFECTIVE_FROM])
            except vol.Invalid:
                errors[CONF_EFFECTIVE_FROM] = "invalid_date"
            else:
                new_entry = {
                    CONF_EFFECTIVE_FROM: user_input[CONF_EFFECTIVE_FROM],
                    CONF_RATE_PER_KWH: user_input[CONF_RATE_PER_KWH],
                }
                self._income_rates = [
                    r
                    for r in self._income_rates
                    if r[CONF_EFFECTIVE_FROM] != new_entry[CONF_EFFECTIVE_FROM]
                ]
                self._income_rates.append(new_entry)
                self._income_rates.sort(key=lambda r: r[CONF_EFFECTIVE_FROM])
                return self._save()

        return self.async_show_form(
            step_id="add_rate",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EFFECTIVE_FROM, default=date.today().isoformat()
                    ): str,
                    vol.Required(CONF_RATE_PER_KWH): vol.All(
                        vol.Coerce(float), vol.Range(min=0.0)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_remove_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove an income rate entry."""
        if not self._income_rates:
            return self._save()

        if user_input is not None:
            key = user_input.get("rate_entry")
            self._income_rates = [
                r for r in self._income_rates if _rate_label(r) != key
            ]
            return self._save()

        options = {_rate_label(r): _rate_label(r) for r in self._income_rates}
        return self.async_show_form(
            step_id="remove_rate",
            data_schema=vol.Schema({vol.Required("rate_entry"): vol.In(options)}),
        )

    def _save(self) -> FlowResult:
        return self.async_create_entry(
            title="",
            data={CONF_INCOME_RATES: self._income_rates},
        )
