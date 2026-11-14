"""Base entities for eSmart."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .client import ESmartClient, ESmartError, ESmartNotAllowed, iter_nodes
from .const import DOMAIN, MANUFACTURER


def apartment_device(client: ESmartClient) -> DeviceInfo:
    apt = (client.structure or {}).get("apartment", {})
    caps_name = next(iter(f.get("name") for f in apt.get("floors", [])), None)
    return DeviceInfo(
        identifiers={(DOMAIN, client.apartment_id or "apartment")},
        name=f"eSmart {caps_name or client.apartment_id}",
        manufacturer=MANUFACTURER,
        model="Apartment controller",
    )


def nodes_of_type(client: ESmartClient, *type_ids: int):
    for _floor, room, node in iter_nodes(client.structure):
        if node.get("type_id") in type_ids:
            yield room, node


def _device_name(room: dict[str, Any], node: dict[str, Any]) -> str:
    """Node name; the room comes from the area. Heating zones are named after their room."""
    name = (node.get("name") or "").strip()
    room_name = (room.get("name") or "").strip()
    if not name or name.casefold() == room_name.casefold():
        return node.get("type_name") or f"Node {node.get('id')}"
    return name


class ESmartEntity(Entity):
    """Push-updated entity bound to the client."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, client: ESmartClient) -> None:
        self.client = client

    @property
    def available(self) -> bool:
        return self.client.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.client.add_listener(self._on_update))

    def _relevant(self, changed: set[int] | None) -> bool:
        return True

    def _on_update(self, changed: set[int] | None) -> None:
        if changed is None or self._relevant(changed):
            self.async_write_ha_state()


class ESmartNodeEntity(ESmartEntity):
    """Entity for one eSmart node (light, blind, heating ...)."""

    def __init__(self, client: ESmartClient, room: dict[str, Any], node: dict[str, Any], key: str | None = None) -> None:
        super().__init__(client)
        self.node = node
        self.node_id = int(node["id"])
        base = f"{client.apartment_id}-{self.node_id}"
        self._attr_unique_id = f"{base}-{key}" if key else base
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base)},
            name=_device_name(room, node),
            manufacturer=MANUFACTURER,
            model=node.get("type_name"),
            suggested_area=room.get("name", "").strip() or None,
        )

    @property
    def data(self) -> dict[str, Any]:
        return self.client.nodes.get(self.node_id, {})

    @property
    def available(self) -> bool:
        return self.client.available and self.node_id in self.client.nodes

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "esmart_node_id": self.node_id,
            "switchable": self.node_id in self.client.allowed_nodes,
        }

    def _relevant(self, changed: set[int] | None) -> bool:
        return changed is None or self.node_id in changed

    async def _command(self, **fields: Any) -> None:
        try:
            await self.client.command(self.node_id, **fields)
        except ESmartNotAllowed as err:
            raise HomeAssistantError(
                f"{self.device_info and self.device_info.get('name')}: not declared as switchable. "
                "Add it under Settings → Devices & services → eSmart → Configure."
            ) from err
        except ESmartError as err:
            raise HomeAssistantError(str(err)) from err
