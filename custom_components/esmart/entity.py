"""Base entities for eSmart."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .client import ESmartClient, ESmartError, ESmartNotAllowed, iter_nodes
from .const import DOMAIN, MANUFACTURER
from .convert import field_matches

PENDING_SECONDS = 5  # default: show the requested value until eSmart confirms it, at most this long


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

    _pending: dict[str, Any] | None = None
    _pending_until = 0.0
    _pending_seconds = PENDING_SECONDS

    @property
    def data(self) -> dict[str, Any]:
        """Live node data, overlaid with values we just sent until eSmart confirms them.

        Right after a CMD the INFO stream still carries the old values for about a second;
        without the overlay, HomeKit and the UI flip back to the old state and then forward again.
        """
        live = self.client.nodes.get(self.node_id, {})
        if self._pending and time.monotonic() < self._pending_until:
            return {**live, **self._pending}
        return live

    def _optimistic(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Fields to show immediately after sending them. Covers exclude the moving position."""
        return fields

    def _on_update(self, changed: set[int] | None) -> None:
        if self._pending:
            # only the regular data stream counts as confirmation: INFO/operation may confirm
            # first and the following data frames still carry the old value (seen on heating)
            live = self.client.stream_nodes.get(self.node_id, {})
            if time.monotonic() >= self._pending_until or all(
                field_matches(k, v, live.get(k)) for k, v in self._pending.items()
            ):
                self._pending = None
        super()._on_update(changed)

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
        if pending := self._optimistic(fields):
            self._pending = {**(self._pending or {}), **pending}
            self._pending_until = time.monotonic() + self._pending_seconds
        self.async_write_ha_state()
