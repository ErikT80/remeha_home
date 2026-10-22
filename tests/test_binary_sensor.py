"""Test the binary sensors."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    APPLIANCE_ID,
    CLIMATE_ZONE_ID,
    HOT_WATER_ZONE_ID,
    entity_id,
    refresh,
    setup_integration,
)


def state(hass: HomeAssistant, item_id: str, key: str) -> str:
    """Return the state of a binary sensor."""
    return hass.states.get(entity_id(hass, "binary_sensor", item_id, key)).state


@pytest.mark.parametrize(("online", "expected"), [(True, STATE_ON), (False, STATE_OFF)])
async def test_online(
    hass: HomeAssistant, config_entry, mock_api, appliance, online, expected
) -> None:
    """The Online sensor shows the connection of the appliance."""
    appliance["applianceOnline"] = online
    await setup_integration(hass, config_entry, mock_api)

    assert state(hass, APPLIANCE_ID, "applianceOnline") == expected
    entry = er.async_get(hass).async_get(
        entity_id(hass, "binary_sensor", APPLIANCE_ID, "applianceOnline")
    )
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.original_device_class == "connectivity"


@pytest.mark.parametrize(("ok", "expected"), [(True, STATE_OFF), (False, STATE_ON)])
async def test_water_pressure(
    hass: HomeAssistant, config_entry, mock_api, appliance, ok, expected
) -> None:
    """The Water Pressure problem sensor is on when the pressure is not OK."""
    appliance["waterPressureOK"] = ok
    await setup_integration(hass, config_entry, mock_api)

    assert state(hass, APPLIANCE_ID, "waterPressureOK") == expected


async def test_pre_heat(
    hass: HomeAssistant, config_entry, mock_api, climate_zone
) -> None:
    """The Pre-heat sensor follows the pre-heat of the climate zone."""
    await setup_integration(hass, config_entry, mock_api)
    assert state(hass, CLIMATE_ZONE_ID, "preHeat.active") == STATE_OFF

    climate_zone["preHeat"] = {"enabled": True, "active": True}
    await refresh(hass, config_entry)
    assert state(hass, CLIMATE_ZONE_ID, "preHeat.active") == STATE_ON


async def test_not_reported(
    hass: HomeAssistant, config_entry, mock_api, appliance, climate_zone
) -> None:
    """No binary sensors for values the appliance doesn't report."""
    del appliance["applianceOnline"]
    del climate_zone["preHeat"]
    await setup_integration(hass, config_entry, mock_api)

    assert entity_id(hass, "binary_sensor", APPLIANCE_ID, "applianceOnline") is None
    assert entity_id(hass, "binary_sensor", CLIMATE_ZONE_ID, "preHeat.active") is None
    assert entity_id(hass, "binary_sensor", APPLIANCE_ID, "waterPressureOK")


async def test_value_disappears(
    hass: HomeAssistant, config_entry, mock_api, appliance
) -> None:
    """A value that disappears or becomes null is unknown, not an error."""
    await setup_integration(hass, config_entry, mock_api)

    del appliance["applianceOnline"]
    appliance["waterPressureOK"] = None
    await refresh(hass, config_entry)

    assert state(hass, APPLIANCE_ID, "applianceOnline") == STATE_UNKNOWN
    assert state(hass, APPLIANCE_ID, "waterPressureOK") == STATE_UNKNOWN


async def test_status_sensors(hass: HomeAssistant, config_entry, mock_api) -> None:
    """The status binary sensors of the zones are still created."""
    await setup_integration(hass, config_entry, mock_api)

    for item_id, key in (
        (CLIMATE_ZONE_ID, "activeComfortDemand"),
        (HOT_WATER_ZONE_ID, "dhwStatus"),
    ):
        assert entity_id(hass, "binary_sensor", item_id, key), key
