"""Config flow for Enmax: sign in with your MyAccount email + password."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnmaxAuthError, EnmaxClient, EnmaxError
from .const import CONF_ACCOUNT_NO, CONF_BP, DOMAIN

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


class EnmaxConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, str] = {}
        self._accounts: list[dict[str, Any]] = []

    async def _login(self, username: str, password: str) -> list[dict[str, Any]]:
        client = EnmaxClient(async_get_clientsession(self.hass), username, password)
        await client.login()
        return await client.associated_accounts()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._accounts = await self._login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except EnmaxAuthError:
                errors["base"] = "invalid_auth"
            except (EnmaxError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if not self._accounts:
                    errors["base"] = "no_accounts"
                else:
                    self._creds = user_input
                    if len(self._accounts) == 1:
                        return await self._create(self._accounts[0])
                    return await self.async_step_account()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = {
            a["account_no"]: f"{a.get('nickname') or a.get('description') or ''} ({a['account_no']})"
            for a in self._accounts
        }
        if user_input is not None:
            acct = next(a for a in self._accounts if a["account_no"] == user_input[CONF_ACCOUNT_NO])
            return await self._create(acct)
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema({vol.Required(CONF_ACCOUNT_NO): vol.In(options)}),
        )

    async def _create(self, acct: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(acct["account_no"])
        self._abort_if_unique_id_configured()
        title = f"Enmax {acct.get('nickname') or acct['account_no'][-4:]}"
        return self.async_create_entry(
            title=title,
            data={**self._creds, CONF_ACCOUNT_NO: acct["account_no"], CONF_BP: acct.get("bp_id")},
        )

    # ------------------------------------------------------------- reauth
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await self._login(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            except EnmaxAuthError:
                errors["base"] = "invalid_auth"
            except (EnmaxError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
