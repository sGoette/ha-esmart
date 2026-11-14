"""eSmart sensors: per-node power, apartment meters, extra temperatures."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ESmartConfigEntry
from .client import ESmartClient, iter_nodes
from .entity import ESmartEntity, ESmartNodeEntity, apartment_device


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    client = entry.runtime_data
    entities: list[SensorEntity] = []
    for _floor, room, node in iter_nodes(client.structure):
        entities.append(ESmartNodePower(client, room, node))
    for meter in client.meters:
        for field in ("power", "flow_rate", "temperature"):
            if field in meter:
                entities.append(ESmartMeter(client, int(meter["id"]), field, meter.get("name")))
    async_add_entities(entities)


class ESmartNodePower(ESmartNodeEntity, SensorEntity):
    _attr_translation_key = "node_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client, room, node) -> None:
        super().__init__(client, room, node, key="power")

    @property
    def native_value(self) -> float | None:
        return self.data.get("power")


class ESmartMeter(ESmartEntity, SensorEntity):
    """A value from the `meters` list of the data stream (keyed by id + field)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, client: ESmartClient, meter_id: int, field: str, name: str | None) -> None:
        super().__init__(client)
        self.meter_id = meter_id
        self.field = field
        self._attr_unique_id = f"{client.apartment_id}-meter-{meter_id}-{field}"
        self._attr_device_info: DeviceInfo = apartment_device(client)
        label = name or f"Meter {meter_id}"
        if field == "power":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_name = label
        elif field == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_name = name or f"Temperature {meter_id}"
        else:  # flow_rate, unit not documented by eSmart
            self._attr_name = f"{label} flow"
            self._attr_icon = "mdi:water"

    def _value(self) -> Any:
        for meter in self.client.meters:
            if int(meter.get("id", -1)) == self.meter_id and self.field in meter:
                return meter[self.field]
        return None

    @property
    def native_value(self) -> Any:
        return self._value()

    @property
    def available(self) -> bool:
        return self.client.available and self._value() is not None

    def _relevant(self, changed: set[int] | None) -> bool:
        return changed is None
