from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_EFFECTIVE_FROM,
    CONF_INCOME_RATES,
    CONF_RATE_PER_KWH,
    DOMAIN,
)
from .coordinator import KirkhillCoordinator

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.const import CURRENCY_POUND  # type: ignore[attr-defined]

    _GBP = CURRENCY_POUND
except ImportError:
    _GBP = "GBP"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_applicable_rate(
    income_rates: list[dict], target_date: date
) -> float | None:
    """Return the income rate (£/kWh) active on target_date, or None."""
    applicable: dict | None = None
    for rate in sorted(income_rates, key=lambda r: r[CONF_EFFECTIVE_FROM]):
        try:
            if date.fromisoformat(rate[CONF_EFFECTIVE_FROM]) <= target_date:
                applicable = rate
        except (KeyError, ValueError):
            continue
    return applicable[CONF_RATE_PER_KWH] if applicable is not None else None


def _site_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_site")},
        name="Kirk Hill Wind Farm",
        manufacturer="Kirk Hill Community Co-op",
        model="Wind Farm Dashboard",
        configuration_url="https://dashboard.kirkhillcoop.org",
    )


def _owner_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_owner")},
        name="Your Share",
        manufacturer="Kirk Hill Community Co-op",
        model="Wind Farm Dashboard",
        via_device=(DOMAIN, f"{entry.entry_id}_site"),
        configuration_url="https://dashboard.kirkhillcoop.org",
    )


def _turbine_device_info(
    entry: ConfigEntry, turbine_id: str, turbine_name: str
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_turbine_{turbine_id}")},
        name=turbine_name,
        manufacturer="Kirk Hill Community Co-op",
        model="Wind Turbine",
        via_device=(DOMAIN, f"{entry.entry_id}_site"),
        configuration_url="https://dashboard.kirkhillcoop.org",
    )


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: KirkhillCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        # Site device — whole-farm metrics
        SiteGenerationTodaySensor(coordinator, entry),
        SiteGenerationSensor(coordinator, entry),
        SiteGeneration30dSensor(coordinator, entry),
        CurrentPowerSensor(coordinator, entry),
        CapacityFactorSensor(coordinator, entry),
        ActiveTurbinesSensor(coordinator, entry),
        WindSpeedCurrentSensor(coordinator, entry),
        WindSpeedAverageSensor(coordinator, entry),
        # Owner device — your share
        OwnerGenerationTodaySensor(coordinator, entry),
        OwnerGenerationSensor(coordinator, entry),
        OwnerGeneration30dSensor(coordinator, entry),
        OwnerPowerSensor(coordinator, entry),
        OwnerShareSensor(coordinator, entry),
    ]

    if entry.options.get(CONF_INCOME_RATES):
        entities.extend([
            OwnerRevenueTodaySensor(coordinator, entry),
            OwnerRevenueSensor(coordinator, entry),
            ActiveIncomeSensor(coordinator, entry),
        ])

    if coordinator.data:
        for turbine in coordinator.data.get("turbines", {}).get("turbines", []):
            entities.extend([
                TurbineGenerationTodaySensor(coordinator, entry, turbine),
                TurbineStatusSensor(coordinator, entry, turbine),
                TurbineCapacityFactorSensor(coordinator, entry, turbine),
                TurbineAvailabilitySensor(coordinator, entry, turbine),
            ])

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------


class KirkhillSensorBase(CoordinatorEntity[KirkhillCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = device_info
        self._entry = entry

    @property
    def _income_rates(self) -> list[dict]:
        return self._entry.options.get(CONF_INCOME_RATES, [])

    def _owner_summary(self) -> dict:
        return (self.coordinator.data or {}).get("owner", {}).get("summary", {})

    def _site_summary(self) -> dict:
        return (self.coordinator.data or {}).get("site", {}).get("summary", {})


class _SiteBase(KirkhillSensorBase):
    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator, entry, unique_suffix, _site_device_info(entry))


class _OwnerBase(KirkhillSensorBase):
    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator, entry, unique_suffix, _owner_device_info(entry))


# ---------------------------------------------------------------------------
# Site sensors  (device: "Kirk Hill Wind Farm")
# ---------------------------------------------------------------------------


class SiteGenerationTodaySensor(_SiteBase):
    _attr_name = "Generation Today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:wind-turbine"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "site_generation_today")

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get("site_today", {}).get("summary", {}).get("total_generation_kwh")
        return round(float(val), 1) if val is not None else None


class SiteGenerationSensor(_SiteBase):
    _attr_name = "Generation (7 days)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:wind-turbine"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "site_generation_7d")

    @property
    def native_value(self) -> float | None:
        val = self._site_summary().get("total_generation_kwh")
        return round(float(val), 1) if val is not None else None


class SiteGeneration30dSensor(_SiteBase):
    _attr_name = "Generation (30 days)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:wind-turbine"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "site_generation_30d")

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get("site_30d", {}).get("summary", {}).get("total_generation_kwh")
        return round(float(val), 1) if val is not None else None


class CurrentPowerSensor(_SiteBase):
    _attr_name = "Power"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_power")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("current_power_kw")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        site = self._site_summary()
        attrs: dict[str, Any] = {}
        if "latest_generation_interval_end" in site:
            attrs["latest_interval_end"] = site["latest_generation_interval_end"]
        if "latest_import_status" in site:
            attrs["import_status"] = site["latest_import_status"]
        return attrs


class CapacityFactorSensor(_SiteBase):
    _attr_name = "Capacity Factor"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:percent"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "capacity_factor")

    @property
    def native_value(self) -> float | None:
        val = self._site_summary().get("capacity_factor_percent")
        return round(float(val), 2) if val is not None else None


class ActiveTurbinesSensor(_SiteBase):
    _attr_name = "Active Turbines"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wind-turbine-check"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "active_turbines")

    @property
    def native_value(self) -> int | None:
        val = self._site_summary().get("active_turbines")
        return int(val) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        site = self._site_summary()
        attrs: dict[str, Any] = {}
        if "site_capacity_watts" in site:
            attrs["site_capacity_kw"] = round(float(site["site_capacity_watts"]) / 1000, 0)
        return attrs


class WindSpeedCurrentSensor(_SiteBase):
    _attr_name = "Wind Speed"
    _attr_native_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND
    _attr_device_class = SensorDeviceClass.WIND_SPEED
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "wind_speed_current")

    @property
    def native_value(self) -> float | None:
        series = (self.coordinator.data or {}).get("wind_speed", {}).get("series", [])
        if not series:
            return None
        val = series[-1].get("wind_speed_mps")
        return round(float(val), 2) if val is not None else None


class WindSpeedAverageSensor(_SiteBase):
    _attr_name = "Average Wind Speed (today)"
    _attr_native_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND
    _attr_device_class = SensorDeviceClass.WIND_SPEED
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "wind_speed_average")

    @property
    def native_value(self) -> float | None:
        series = (self.coordinator.data or {}).get("wind_speed", {}).get("series", [])
        values = [e["wind_speed_mps"] for e in series if "wind_speed_mps" in e]
        if not values:
            return None
        return round(sum(values) / len(values), 2)


# ---------------------------------------------------------------------------
# Owner sensors  (device: "Your Share")
# ---------------------------------------------------------------------------


class OwnerGenerationTodaySensor(_OwnerBase):
    _attr_name = "Generation Today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:account-circle"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_generation_today")

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get("owner_today", {}).get("summary", {}).get("total_generation_kwh")
        return round(float(val), 3) if val is not None else None


class OwnerGenerationSensor(_OwnerBase):
    _attr_name = "Generation (7 days)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:account-circle"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_generation_7d")

    @property
    def native_value(self) -> float | None:
        val = self._owner_summary().get("total_generation_kwh")
        return round(float(val), 3) if val is not None else None


class OwnerGeneration30dSensor(_OwnerBase):
    _attr_name = "Generation (30 days)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:account-circle"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_generation_30d")

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get("owner_30d", {}).get("summary", {}).get("total_generation_kwh")
        return round(float(val), 3) if val is not None else None


class OwnerPowerSensor(_OwnerBase):
    _attr_name = "Power"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-arrow-right"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_power")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("current_owner_power_kw")


class OwnerShareSensor(_OwnerBase):
    _attr_name = "Ownership Share"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:percent-circle"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_share")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        owner_kwh = data.get("owner", {}).get("summary", {}).get("total_generation_kwh")
        site_kwh = data.get("site", {}).get("summary", {}).get("total_generation_kwh")
        if not owner_kwh or not site_kwh:
            return None
        return round(float(owner_kwh) / float(site_kwh) * 100, 4)


class OwnerRevenueTodaySensor(_OwnerBase):
    _attr_name = "Revenue Today"
    _attr_native_unit_of_measurement = _GBP
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_revenue_today")

    @property
    def native_value(self) -> float | None:
        owner_kwh = (self.coordinator.data or {}).get("owner_today", {}).get("summary", {}).get("total_generation_kwh")
        if owner_kwh is None:
            return None
        rate = get_applicable_rate(self._income_rates, date.today())
        if rate is None:
            return None
        return round(float(owner_kwh) * rate, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rate = get_applicable_rate(self._income_rates, date.today())
        return {"income_rate_per_kwh": rate, "period": date.today().isoformat()}


class OwnerRevenueSensor(_OwnerBase):
    _attr_name = "Revenue (7 days)"
    _attr_native_unit_of_measurement = _GBP
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "owner_revenue_7d")

    @property
    def native_value(self) -> float | None:
        owner_kwh = self._owner_summary().get("total_generation_kwh")
        if owner_kwh is None:
            return None
        period_start = date.today() - timedelta(days=7)
        rate = get_applicable_rate(self._income_rates, period_start)
        if rate is None:
            return None
        return round(float(owner_kwh) * rate, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        period_start = date.today() - timedelta(days=7)
        rate = get_applicable_rate(self._income_rates, period_start)
        return {
            "income_rate_per_kwh": rate,
            "period_start": period_start.isoformat(),
        }


class ActiveIncomeSensor(_OwnerBase):
    _attr_name = "Active Income Rate"
    _attr_native_unit_of_measurement = f"{_GBP}/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:currency-gbp"

    def __init__(self, coordinator: KirkhillCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "active_income_rate")

    @property
    def native_value(self) -> float | None:
        return get_applicable_rate(self._income_rates, date.today())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rates = sorted(self._income_rates, key=lambda r: r[CONF_EFFECTIVE_FROM])
        return {
            "rate_history": [
                {
                    "effective_from": r[CONF_EFFECTIVE_FROM],
                    "rate_per_kwh": r[CONF_RATE_PER_KWH],
                }
                for r in rates
            ]
        }


# ---------------------------------------------------------------------------
# Per-turbine sensors  (one device per turbine)
# ---------------------------------------------------------------------------


class _TurbineBase(KirkhillSensorBase):
    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        turbine: dict[str, Any],
        unique_suffix: str,
    ) -> None:
        turbine_id = str(turbine.get("id", "unknown"))
        turbine_name = turbine.get("name", f"Turbine {turbine_id}")
        super().__init__(
            coordinator,
            entry,
            unique_suffix,
            _turbine_device_info(entry, turbine_id, turbine_name),
        )
        self._turbine_id = turbine_id
        self._turbine_label = turbine_name

    def _turbine_data(self) -> dict[str, Any]:
        for t in (self.coordinator.data or {}).get("turbines", {}).get("turbines", []):
            if str(t.get("id")) == self._turbine_id:
                return t
        return {}


class TurbineGenerationTodaySensor(_TurbineBase):
    _attr_name = "Generation Today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:wind-turbine"

    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        turbine: dict[str, Any],
    ) -> None:
        turbine_id = str(turbine.get("id", "unknown"))
        super().__init__(coordinator, entry, turbine, f"turbine_{turbine_id}_generation_today")

    @property
    def native_value(self) -> float | None:
        val = self._turbine_data().get("generation_kwh")
        return round(float(val), 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._turbine_data()
        return {k: t[k] for k in ("share_percent", "latest_interval_end") if k in t}


class TurbineStatusSensor(_TurbineBase):
    _attr_name = "Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["running", "stopped"]
    _attr_icon = "mdi:wind-turbine"

    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        turbine: dict[str, Any],
    ) -> None:
        turbine_id = str(turbine.get("id", "unknown"))
        super().__init__(coordinator, entry, turbine, f"turbine_{turbine_id}_status")

    @property
    def native_value(self) -> str | None:
        status = self._turbine_data().get("status")
        if status is None:
            return None
        return str(status).lower()


class TurbineCapacityFactorSensor(_TurbineBase):
    _attr_name = "Capacity Factor"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gauge"

    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        turbine: dict[str, Any],
    ) -> None:
        turbine_id = str(turbine.get("id", "unknown"))
        super().__init__(coordinator, entry, turbine, f"turbine_{turbine_id}_capacity_factor")

    @property
    def native_value(self) -> float | None:
        val = self._turbine_data().get("capacity_factor_percent")
        return round(float(val), 2) if val is not None else None


class TurbineAvailabilitySensor(_TurbineBase):
    _attr_name = "Availability"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-check"

    def __init__(
        self,
        coordinator: KirkhillCoordinator,
        entry: ConfigEntry,
        turbine: dict[str, Any],
    ) -> None:
        turbine_id = str(turbine.get("id", "unknown"))
        super().__init__(coordinator, entry, turbine, f"turbine_{turbine_id}_availability")

    @property
    def native_value(self) -> float | None:
        val = self._turbine_data().get("availability_pct")
        return round(float(val), 2) if val is not None else None
