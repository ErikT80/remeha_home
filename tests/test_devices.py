"""Test the devices that the integration registers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.remeha_home.const import DOMAIN
from custom_components.remeha_home.coordinator import get_producer_id

from .conftest import (
    APPLIANCE_ID,
    CLIMATE_ZONE_ID,
    HOT_WATER_ZONE_ID,
    entity_id,
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


async def test_hybrid_producers_with_same_instance_keep_separate_devices_and_energy(
    hass: HomeAssistant, config_entry, mock_api, appliance
) -> None:
    """Elga Ace can report instance 1 for both its heat pump and gas boiler."""
    consumption = mock_api.async_get_consumption_data_for_today.return_value["data"][0]
    heat_pump, boiler = consumption["producerPerformanceStatistics"]["producers"]
    boiler["instanceWithinDevice"] = heat_pump["instanceWithinDevice"]

    await setup_integration(hass, config_entry, mock_api)

    heat_pump_id = get_producer_id(APPLIANCE_ID, heat_pump, [heat_pump, boiler])
    boiler_id = get_producer_id(APPLIANCE_ID, boiler, [heat_pump, boiler])
    assert heat_pump_id != boiler_id

    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    assert coordinator.get_by_id(heat_pump_id)["producerType"] == "HeatPumpAirSource"
    assert coordinator.get_by_id(boiler_id)["producerType"] == "GasBoiler"

    registry = dr.async_get(hass)
    pump_device = registry.async_get_device_by_identifier(
        (DOMAIN, heat_pump_id), config_entry.entry_id
    )
    boiler_device = registry.async_get_device_by_identifier(
        (DOMAIN, boiler_id), config_entry.entry_id
    )
    assert pump_device is not None and boiler_device is not None
    assert pump_device.id != boiler_device.id

    pump_entity = entity_id(hass, "sensor", heat_pump_id, "energyConsumptionCH")
    boiler_entity = entity_id(hass, "sensor", boiler_id, "energyConsumptionCH")
    assert pump_entity is not None and boiler_entity is not None
    assert float(hass.states.get(pump_entity).state) == 4.0
    assert float(hass.states.get(boiler_entity).state) == round(
        1.0 / appliance["gasCalorificValue"], 2
    )
    assert hass.states.get(pump_entity).attributes["unit_of_measurement"] == "kWh"
    assert hass.states.get(boiler_entity).attributes["unit_of_measurement"] == "m³"


def test_producer_ids_remain_unchanged_without_collisions() -> None:
    """Stand-alone, conventional hybrid and multi-producer IDs remain stable."""
    heat_pump = {
        "producerType": "HeatPumpAirSource",
        "energyType": "Electric",
        "instanceWithinDevice": 1,
    }
    boiler = {
        "producerType": "GasBoiler",
        "energyType": "NaturalGas",
        "instanceWithinDevice": 2,
    }
    assert get_producer_id(APPLIANCE_ID, heat_pump, [heat_pump]) == f"{APPLIANCE_ID}_1"
    assert get_producer_id(APPLIANCE_ID, boiler, [boiler]) == f"{APPLIANCE_ID}_2"
    assert get_producer_id(APPLIANCE_ID, heat_pump, [heat_pump, boiler]) == f"{APPLIANCE_ID}_1"
    assert get_producer_id(APPLIANCE_ID, boiler, [heat_pump, boiler]) == f"{APPLIANCE_ID}_2"


def test_colliding_ids_do_not_depend_on_api_producer_order() -> None:
    """An Elga hybrid with duplicated instance indexes is deterministic."""
    heat_pump = {
        "producerType": "HeatPumpAirSource",
        "energyType": "Electric",
        "instanceWithinDevice": 1,
    }
    boiler = {
        "producerType": "GasBoiler",
        "energyType": "NaturalGas",
        "instanceWithinDevice": 1,
    }
    for producers in ([heat_pump, boiler], [boiler, heat_pump]):
        assert get_producer_id(APPLIANCE_ID, heat_pump, producers) == f"{APPLIANCE_ID}_1"
        assert get_producer_id(APPLIANCE_ID, boiler, producers) == f"{APPLIANCE_ID}_1_GasBoiler"
