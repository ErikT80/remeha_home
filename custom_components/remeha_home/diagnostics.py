"""Diagnostics support for Remeha Home."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Redacted wherever they appear. The ids (applianceId, climateZoneId, ...) are kept:
# they are random, and needed to match the data to the entities.
TO_REDACT = {
    # The config entry is titled with the email address used to log in
    "title",
    "token",
    # Names typed in the Remeha Home app: home, zones and activities
    "houseName",
    "name",
    "controlledZoneName",
    # Identify the hardware: serial number of a gateway, device index of a producer
    "serialNumber",
    "deviceIndex",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The dashboard is the one the integration holds, with what it adds: per
    appliance the energy consumption of today as the sensors use it (heating and
    hot water of a gas boiler in m³, null producer values 0), per climate zone the
    heating curve and per hot water zone the current activity.
    """
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "dashboard": coordinator.data,
            "technical_info": coordinator.technical_info,
            "activities": coordinator.activities_data,
        },
        TO_REDACT,
    )
