"""A manually invoked, read-only SignalR diagnostics action."""

from __future__ import annotations

import asyncio

from aiohttp import ClientError, ClientResponseError
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .const import DOMAIN
from .energy_history_service import _appliance_for_device
from .signalr_probe import SignalRProbeError, async_probe_signalr

SERVICE_PROBE_SIGNALR = "probe_signalr"
SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Optional("listen_seconds", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=30)
        ),
    }
)


def async_register_signalr_probe_service(hass: HomeAssistant) -> None:
    """Register the opt-in diagnostic without starting any background tasks."""
    probe_lock = asyncio.Lock()

    async def async_probe(call: ServiceCall) -> ServiceResponse:
        if probe_lock.locked():
            raise HomeAssistantError("A Remeha SignalR probe is already running")

        # The selected Remeha device selects the account's existing OAuth session.
        # SignalR is a home-level connection, not a command to this device.
        _, api = _appliance_for_device(hass, call.data["device_id"])
        async with probe_lock:
            try:
                return await async_probe_signalr(
                    api,
                    async_get_clientsession(hass),
                    call.data["listen_seconds"],
                )
            except ClientResponseError as exc:
                # Do not leak response text or URLs (WebSocket IDs are sensitive).
                raise HomeAssistantError(
                    f"Remeha SignalR returned HTTP {exc.status}"
                ) from None
            except (SignalRProbeError, ClientError, TimeoutError) as exc:
                if isinstance(exc, SignalRProbeError):
                    raise HomeAssistantError(str(exc)) from None
                raise HomeAssistantError(
                    "Remeha SignalR connection failed or timed out"
                ) from None

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROBE_SIGNALR,
        async_probe,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
