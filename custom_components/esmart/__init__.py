"""eSmart integration (unofficial)."""

from __future__ import annotations

import contextlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.util.ssl import get_default_context
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .client import ESmartAuthError, ESmartClient, ESmartError
from .const import CONF_DOORS, CONF_JID, CONF_PASSWORD, CONF_SWITCHABLE, PLATFORMS

_LOGGER = logging.getLogger(__name__)

type ESmartConfigEntry = ConfigEntry[ESmartClient]


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry) -> bool:
    client = ESmartClient(
        entry.data[CONF_JID],
        entry.data[CONF_PASSWORD],
        allowed_nodes=entry.options.get(CONF_SWITCHABLE, []),
        allow_doors=entry.options.get(CONF_DOORS, False),
        ssl_context=get_default_context(),
    )
    await client.start()
    try:
        await client.wait_ready(45)
    except ESmartAuthError as err:
        await client.stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except ESmartError as err:
        await client.stop()
        raise ConfigEntryNotReady(str(err)) from err

    with contextlib.suppress(ESmartError):
        await client.wait_doors(10)  # door locks; a late list is picked up by the lock platform

    entry.runtime_data = client
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ESmartConfigEntry) -> None:
    client = entry.runtime_data
    if bool(entry.options.get(CONF_DOORS, False)) != client.allow_doors:
        # door locks appear/disappear: reload
        await hass.config_entries.async_reload(entry.entry_id)
        return
    client.allowed_nodes = {int(n) for n in entry.options.get(CONF_SWITCHABLE, [])}
    _LOGGER.info("eSmart switchable nodes: %s", sorted(client.allowed_nodes) or "none (read-only)")


async def async_unload_entry(hass: HomeAssistant, entry: ESmartConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded
