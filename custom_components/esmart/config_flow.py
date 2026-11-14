"""Config flow for eSmart: register + pair a new remote device, or use existing credentials."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.util.ssl import get_default_context
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import (
    ESmartAuthError,
    ESmartClient,
    ESmartError,
    ESmartTimeout,
    iter_nodes,
    register_account,
)
from .const import CONF_JID, CONF_PASSWORD, CONF_SWITCHABLE, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _title(client: ESmartClient) -> str:
    floors = (client.structure or {}).get("apartment", {}).get("floors", [])
    name = floors[0].get("name") if floors else None
    return f"eSmart {name or client.apartment_id}"


class ESmartConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._client: ESmartClient | None = None
        self._jid: str | None = None
        self._password: str | None = None

    @callback
    def async_remove(self) -> None:
        if self._client:
            self.hass.async_create_task(self._client.stop())
            self._client = None

    async def _finish(self) -> ConfigFlowResult:
        assert self._client
        client = self._client
        await self.async_set_unique_id(client.apartment_id)
        self._abort_if_unique_id_configured()
        title = _title(client)
        await client.stop()
        self._client = None
        return self.async_create_entry(
            title=title,
            data={CONF_JID: self._jid, CONF_PASSWORD: self._password},
            options={CONF_SWITCHABLE: []},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["pair", "credentials"])

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._client is None:
            try:
                self._jid, self._password = await register_account(ssl_context=get_default_context())
                self._client = ESmartClient(self._jid, self._password, device_name="Home Assistant", ssl_context=get_default_context())
                await self._client.start()
                await self._client.wait_online(20)
            except ESmartError as err:
                _LOGGER.warning("eSmart registration failed: %s", err)
                return self.async_abort(reason="cannot_connect")
            except Exception:  # noqa: BLE001
                _LOGGER.exception("eSmart registration failed")
                return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            try:
                await self._client.send_pair_code(user_input["code"], timeout=30)
                await self._client.wait_ready(30)
            except ValueError:
                errors["code"] = "invalid_code"
            except ESmartTimeout:
                errors["base"] = "pair_failed"
            except ESmartError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish()

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
            description_placeholders={"jid": self._jid or ""},
        )

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._jid = user_input[CONF_JID].strip()
            self._password = user_input[CONF_PASSWORD]
            self._client = ESmartClient(self._jid, self._password, ssl_context=get_default_context())
            try:
                await self._client.start()
                await self._client.wait_ready(30)
            except ESmartAuthError:
                errors["base"] = "invalid_auth"
            except ESmartTimeout:
                errors["base"] = "not_paired"
            except ESmartError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish()
            await self._client.stop()
            self._client = None

        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema({vol.Required(CONF_JID): str, vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return ESmartOptionsFlow()


class ESmartOptionsFlow(OptionsFlow):
    """Choose which eSmart nodes Home Assistant may switch. Default: none (read-only)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SWITCHABLE: sorted(int(n) for n in user_input.get(CONF_SWITCHABLE, []))}
            )

        client: ESmartClient | None = getattr(self.config_entry, "runtime_data", None)
        options: list[SelectOptionDict] = []
        if client is not None:
            for _floor, room, node in iter_nodes(client.structure):
                label = f"{room.get('name', '').strip()} – {node.get('name')} ({node.get('type_name')}, #{node['id']})"
                options.append(SelectOptionDict(value=str(node["id"]), label=label))
        current = [str(n) for n in self.config_entry.options.get(CONF_SWITCHABLE, [])]
        if client is None:
            options = [SelectOptionDict(value=v, label=f"#{v}") for v in current]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SWITCHABLE, default=current): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )
