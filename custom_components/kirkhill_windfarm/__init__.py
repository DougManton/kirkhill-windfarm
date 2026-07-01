from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_API_TOKEN, DOMAIN, PLATFORMS
from .coordinator import KirkhillCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate older config entry versions."""
    _LOGGER.info(
        "Migrating Kirk Hill Wind Farm entry from version %s", config_entry.version
    )

    if config_entry.version < 2:
        # v1 → v2: per-turbine sensors changed from 30-day rolling to today.
        # Old unique_id suffix: turbine_{id}_generation
        # New unique_id suffix: turbine_{id}_generation_today
        # Remove the old entities so they don't persist as orphans.
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id):
            uid = entity.unique_id or ""
            if "_turbine_" in uid and uid.endswith("_generation"):
                _LOGGER.debug("Removing stale turbine entity: %s", entity.entity_id)
                registry.async_remove(entity.entity_id)

        hass.config_entries.async_update_entry(config_entry, version=2)

    if config_entry.version < 3:
        # v2 → v3: sensors split across per-device DeviceInfo (site / owner / turbines).
        # The old single device used a hard-coded identifier; remove it so the
        # now-empty device doesn't linger in the device registry.
        dev_reg = dr.async_get(hass)
        stale = dev_reg.async_get_device(identifiers={(DOMAIN, "kirkhill_wind_farm")})
        if stale is not None:
            dev_reg.async_remove_device(stale.id)

        hass.config_entries.async_update_entry(config_entry, version=3)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = KirkhillCoordinator(hass, entry.data[CONF_API_TOKEN])
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)
