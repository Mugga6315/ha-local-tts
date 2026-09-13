from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import client
from .const import CONF_API_KEY, CONF_BASE_URL, DOMAIN, OPT_OVERRIDES

DEFAULT_BASE_URL = "http://homeassistant.local:8100"


def _selector(spec: dict[str, Any]):
    """Map a backend param spec (from /api/prod) to the matching HA selector, so
    choices become dropdowns and numbers get a stepped box."""
    ptype = spec.get("type")
    if ptype == "bool":
        return selector.BooleanSelector()
    if ptype == "choice":
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[str(c) for c in spec.get("choices", [])],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    if ptype in ("int", "float"):
        cfg: dict[str, Any] = {"mode": selector.NumberSelectorMode.BOX}
        for key in ("min", "max", "step"):
            if spec.get(key) is not None:
                cfg[key] = spec[key]
        if "step" not in cfg and ptype == "float":
            cfg["step"] = 0.01
        return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))
    return selector.TextSelector()


def _coerce(ptype: str | None, value: Any) -> Any:
    """NumberSelector returns floats; put ints back so an unchanged int param
    compares equal to its spec value and isn't saved as a bogus override."""
    if isinstance(value, bool):
        return value
    if ptype == "int" and isinstance(value, (int, float)):
        return int(value)
    if ptype == "float" and isinstance(value, (int, float)):
        return float(value)
    return value


class LocalTTSConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            api_key = user_input.get(CONF_API_KEY, "").strip()
            session = async_get_clientsession(self.hass)
            data = await client.fetch_prod(session, base_url, api_key)
            if not data:
                return self.async_show_form(
                    step_id="user", data_schema=self._schema(user_input),
                    errors={"base": "cannot_connect"},
                    description_placeholders={"url": base_url})
            return self.async_create_entry(
                title="Local TTS",
                data={CONF_BASE_URL: base_url, CONF_API_KEY: api_key})
        return self.async_show_form(step_id="user", data_schema=self._schema())

    def _schema(self, current: dict | None = None) -> vol.Schema:
        current = current or {}
        return vol.Schema({
            vol.Required(CONF_BASE_URL, default=current.get(CONF_BASE_URL, DEFAULT_BASE_URL)): str,
            vol.Optional(CONF_API_KEY, default=current.get(CONF_API_KEY, "")): str,
        })

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LocalTTSOptionsFlow()


class LocalTTSOptionsFlow(OptionsFlow):
    """View a prod entry's params and set persistent HA-side overrides.

    Step 1 picks an entry; step 2 shows that entry's params (defaulted to the
    current override, else the entry's own value) and saves the edits. Saved
    values are merged under any per-call options at synth time (see tts._body).
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._backends: dict[str, Any] = {}
        self._selected: dict[str, Any] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        session = async_get_clientsession(self.hass)
        data = await client.fetch_prod(
            session,
            self.config_entry.data[CONF_BASE_URL],
            self.config_entry.data.get(CONF_API_KEY, ""),
        )
        self._entries = data.get("entries", [])
        self._backends = data.get("backends", {})
        if not self._entries:
            return self.async_abort(reason="no_entries")

        if user_input is not None:
            chosen = user_input["entry"]
            self._selected = next(e for e in self._entries if e["id"] == chosen)
            return await self.async_step_entry()

        labels = {e["id"]: e.get("label", e["id"]) for e in self._entries}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required("entry"): vol.In(labels)}),
        )

    async def async_step_entry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._selected
        assert entry is not None
        entry_id = entry["id"]
        saved = self.config_entry.options.get(OPT_OVERRIDES, {}).get(entry_id, {})
        specs: list[dict[str, Any]] = self._backends.get(entry.get("backend") or "", {}).get("params", [])
        entry_params: dict[str, Any] = entry.get("params", {})
        types = {s["name"]: s.get("type") for s in specs}
        # The entry's own value per param (its saved value, else the spec default).
        baseline = {
            s["name"]: _coerce(types[s["name"]], entry_params.get(s["name"], s.get("default")))
            for s in specs
        }

        if user_input is not None:
            overrides = dict(self.config_entry.options.get(OPT_OVERRIDES, {}))
            # Keep only values that differ from the entry's own — a field left at
            # the shown value is "no override" and must not pin against later
            # server-side param changes.
            submitted = {k: _coerce(types.get(k), v) for k, v in user_input.items()}
            diff = {k: v for k, v in submitted.items() if v != baseline.get(k)}
            if diff:
                overrides[entry_id] = diff
            else:
                overrides.pop(entry_id, None)
            return self.async_create_entry(data={OPT_OVERRIDES: overrides})

        # Build the field per backend spec (dropdown for choices, stepped number,
        # bool), prefilled with the effective value so the flow doubles as a view.
        schema = vol.Schema({
            vol.Optional(
                s["name"],
                description={"suggested_value": saved.get(s["name"], baseline[s["name"]])},
            ): _selector(s)
            for s in specs
        })
        # Per-field labels + help come from strings.json data/data_description
        # (HA renders them under each field); only the step title/subtitle is
        # filled dynamically here.
        return self.async_show_form(
            step_id="entry",
            data_schema=schema,
            description_placeholders={
                "entry": entry.get("label", entry_id),
                "backend": f"{entry.get('backend', '')} / {entry.get('model', '')}",
            },
        )
