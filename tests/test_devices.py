"""Test the devices that the integration registers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.remeha_home.const import DOMAIN

from .conftest import (
    APPLIANCE_ID,
    CLIMATE_ZONE_ID,
    HOT_WATER_ZONE_ID,
    setup_integration,
)


async def test_devices_linked_to_appliance(
    hass: HomeAssistant, config_entry, mock_api, caplog
) -> None:
    """Link the zones and the producers to the appliance by its device id."""
    await setup_integration(hass, config_entry, mock_api)

    registry = dr.async_get(hass)
    appliance = registry.async_get_device_by_identifier(
        (DOMAIN, APPLIANCE_ID), config_entry.entry_id
    )
    assert appliance is not None
    assert appliance.via_device_id is None

    children = [
        device
        for device in dr.async_entries_for_config_entry(registry, config_entry.entry_id)
        if device.id != appliance.id
    ]
    child_ids = {item_id for device in children for _, item_id in device.identifiers}
    assert {CLIMATE_ZONE_ID, HOT_WATER_ZONE_ID} <= child_ids
    # The climate zone, the hot water zone, the heat pump and the gas boiler
    assert len(children) == 4
    assert all(device.via_device_id == appliance.id for device in children)

    # Home Assistant 2027.8 no longer accepts the deprecated via_device
    assert "deprecated `via_device`" not in caplog.text
