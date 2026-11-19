"""eSmart doors as locks, for Apple Home (via HomeKit Bridge): Face ID to unlock, Siri, Wallet.

The door has no lock state; it opens by itself on a pulse and closes again after a while.
"Unlocked" here means "door opener just released" and lasts UNLOCKED_SECONDS, then the lock
shows "locked" (ready) again so it can be unlocked again. Locking sends nothing.
Only with the door opt-in. Locks belong to the apartment device and are named after the door.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ESmartConfigEntry
from .client import ESmartClient, ESmartError, ESmartNotAllowed
from .const import DOMAIN
from .entity import ESmartEntity, apartment_device

UNLOCKED_SECONDS = 10


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    client = entry.runtime_data
    door_prefix = f"{client.apartment_id}-door-"

    # clean up: door buttons and per-door devices from an earlier version; locks when opt-in is off
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.unique_id.startswith(door_prefix) and (ent.domain == "button" or not client.allow_doors):
            registry.async_remove(ent.entity_id)
    devices = dr.async_get(hass)
    for dev in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if any(d == DOMAIN and i.startswith(door_prefix) for d, i in dev.identifiers):
            devices.async_remove_device(dev.id)

    if not client.allow_doors:
        return

    added: set[int] = set()

    @callback
    def _add_new(_changed=None) -> None:
        new = [ESmartDoorLock(client, door_id) for door_id in client.doors if door_id not in added]
        added.update(e.door_id for e in new)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(client.add_listener(_add_new))


class ESmartDoorLock(ESmartEntity, LockEntity):

    def __init__(self, client: ESmartClient, door_id: int) -> None:
        super().__init__(client)
        self.door_id = door_id
        self._attr_unique_id = f"{client.apartment_id}-door-{door_id}-lock"
        self._attr_device_info = apartment_device(client)
        self._attr_name = client.doors.get(door_id, f"Door {door_id}")
        self._unlocking = False
        self._unlocked_until = 0.0
        self._cancel_relock = None

    @property
    def available(self) -> bool:
        return self.client.available and self.door_id in self.client.doors

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        return {"esmart_door_id": self.door_id}

    def _relevant(self, changed) -> bool:
        return changed is None

    @property
    def is_unlocking(self) -> bool:
        return self._unlocking

    @property
    def is_locked(self) -> bool:
        return not self._unlocking and time.monotonic() >= self._unlocked_until

    async def async_unlock(self, **kwargs: Any) -> None:
        self._unlocking = True
        self.async_write_ha_state()
        try:
            await self.client.open_door(self.door_id)
        except ESmartNotAllowed as err:
            raise HomeAssistantError(
                "Door opening is not enabled. Turn it on under Settings → Devices & services → eSmart → Configure."
            ) from err
        except ESmartError as err:
            raise HomeAssistantError(str(err)) from err
        else:
            self._set_unlocked(UNLOCKED_SECONDS)
        finally:
            self._unlocking = False
            self.async_write_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Nothing to send: the door closes by itself. Just show 'ready' again."""
        self._set_unlocked(0)
        self.async_write_ha_state()

    @callback
    def _set_unlocked(self, seconds: float) -> None:
        if self._cancel_relock:
            self._cancel_relock()
            self._cancel_relock = None
        self._unlocked_until = time.monotonic() + seconds
        if seconds > 0:
            self._cancel_relock = async_call_later(self.hass, seconds, self._relock)

    @callback
    def _relock(self, _now) -> None:
        self._cancel_relock = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_relock:
            self._cancel_relock()
            self._cancel_relock = None
