"""Read-only historical energy action and hybrid-producer normalization."""

from __future__ import annotations

import copy
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.remeha_home.api import RemehaHomeAPI
from custom_components.remeha_home.const import DOMAIN
from custom_components.remeha_home.energy_history import (
    combine_history,
    history_batches,
    normalize_history_day,
)
from custom_components.remeha_home.energy_history_service import (
    SERVICE_GET_ENERGY_HISTORY,
)

from .conftest import APPLIANCE_ID, load_fixture, setup_integration


def _january_9() -> dict:
    """Make a historical Elga Ace hybrid response with app screenshot values."""
    item = copy.deepcopy(load_fixture("consumption.json")["data"][0])
    item["timeStamp"] = "2026-01-09T00:00:00+00:00"
    item["heatingEnergyConsumed"] = 20.0  # includes gas; NOT the HP input
    item["heatingEnergyDelivered"] = 68.0
    producers = item["producerPerformanceStatistics"]["producers"]
    producers[0]["energyConsumptionCH"] = 18.0
    producers[0]["energyProductionCH"] = 68.0
    producers[1]["energyConsumptionCH"] = 2.0
    producers[1]["energyProductionCH"] = None
    # A broken, unnamed hybrid producer like one observed in Remeha diagnostics.
    producers.append(
        {"instanceWithinDevice": None, "producerType": None, "energyType": None}
    )
    return item


def test_normalize_hybrid_and_cop() -> None:
    """Calculate day COP from the heat-pump producer, never mixed totals."""
    row = normalize_history_day(_january_9())
    assert row is not None
    assert row["date"] == "2026-01-09"
    assert row["heat_pump_consumed_kwh"] == 18.0
    assert row["heat_pump_delivered_kwh"] == 68.0
    assert row["heat_pump_cop"] == pytest.approx(68 / 18)
    assert row["gas_heating_consumed_kwh"] == 2.0
    assert len(row["producers"]) == 2
    assert all("instance_within_device" not in row for row in row["producers"])


def test_incomplete_identified_heat_pump_makes_total_unknown() -> None:
    """Never report a partial sum if a recognizable producer has no instance."""
    item = _january_9()
    item["producerPerformanceStatistics"]["producers"].append(
        {
            "instanceWithinDevice": None,
            "producerType": "HeatPumpAirSource",
            "energyType": "Electric",
            "energyConsumptionCH": 3.0,
            "energyProductionCH": 12.0,
        }
    )
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] is None
    assert row["heat_pump_delivered_kwh"] is None
    assert row["heat_pump_cop"] is None
    assert row["gas_heating_consumed_kwh"] == 2.0
    assert len(row["producers"]) == 2


def test_duplicate_heat_pump_records_are_not_double_counted() -> None:
    """Duplicated API producers must not double the historical energy totals."""
    item = _january_9()
    producers = item["producerPerformanceStatistics"]["producers"]
    producers.append(copy.deepcopy(producers[0]))
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] is None
    assert row["heat_pump_delivered_kwh"] is None
    assert row["heat_pump_cop"] is None
    assert row["gas_heating_consumed_kwh"] == 2.0
    assert len(row["producers"]) == 1


def test_duplicate_gas_producer_only_invalidates_gas() -> None:
    """Ambiguous boiler measurements must not hide valid heat-pump energy."""
    item = _january_9()
    producers = item["producerPerformanceStatistics"]["producers"]
    producers.append(copy.deepcopy(producers[1]))
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] == 18.0
    assert row["heat_pump_delivered_kwh"] == 68.0
    assert row["gas_heating_consumed_kwh"] is None


def test_shared_instance_and_distinct_pumps_remain_valid() -> None:
    """A hybrid collision is valid, as are multiple distinct heat pumps."""
    item = _january_9()
    producers = item["producerPerformanceStatistics"]["producers"]
    producers[1]["instanceWithinDevice"] = 1
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] == 18.0
    assert row["gas_heating_consumed_kwh"] == 2.0

    extra_pump = copy.deepcopy(producers[0])
    extra_pump["instanceWithinDevice"] = "3"
    extra_pump["energyConsumptionCH"] = 1.0
    extra_pump["energyProductionCH"] = 4.0
    producers.append(extra_pump)
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] == 19.0
    assert row["heat_pump_delivered_kwh"] == 72.0
    assert row["heat_pump_cop"] == pytest.approx(72 / 19)


def test_malformed_performance_statistics_remain_unknown() -> None:
    """Malformed API structures must not break the action."""
    item = _january_9()
    item["producerPerformanceStatistics"] = []
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] is None
    assert row["gas_heating_consumed_kwh"] is None


def test_completed_daily_history_across_dst_matches_observed_action_values() -> None:
    """Regression for five days observed through HA's action, not a raw fixture."""
    expected = [
        ("2026-10-23", 5.2, 24.8),
        ("2026-10-24", 6.3, 29.8),
        ("2026-10-25", 5.5, 24.8),
        ("2026-10-26", 7.0, 30.8),
        ("2026-10-27", 1.7, 9.3),
    ]
    raw_days = []
    for day, consumed, delivered in expected:
        item = _january_9()
        item["timeStamp"] = f"{day}T00:00:00+00:00"
        producers = item["producerPerformanceStatistics"]["producers"]
        producers[0]["energyConsumptionCH"] = consumed
        producers[0]["energyProductionCH"] = delivered
        producers[1]["energyConsumptionCH"] = 0.0
        raw_days.append(item)

    result = combine_history(
        date(2026, 10, 23), date(2026, 10, 27), [{"data": raw_days}]
    )
    assert result["missing_dates"] == []
    for row, (day, consumed, delivered) in zip(
        result["days"], expected, strict=True
    ):
        assert row["date"] == day
        assert row["heat_pump_consumed_kwh"] == consumed
        assert row["heat_pump_delivered_kwh"] == delivered
        assert row["heat_pump_cop"] == pytest.approx(delivered / consumed)
        assert row["gas_heating_consumed_kwh"] == 0.0


def test_missing_heat_pump_is_unknown_not_zero() -> None:
    """Never substitute appliance totals if the heat-pump fields are absent."""
    item = _january_9()
    item["producerPerformanceStatistics"] = {"producers": []}
    row = normalize_history_day(item)
    assert row["heat_pump_consumed_kwh"] is None
    assert row["heat_pump_delivered_kwh"] is None
    assert row["heat_pump_cop"] is None
    assert row["gas_heating_consumed_kwh"] is None


def test_invalid_and_zero_energy_no_cop() -> None:
    """Missing output, invalid input, and zero input are not divided through."""
    for consumed, delivered in ((0, 5), (None, 5), ("NaN", 5), (2, None)):
        item = _january_9()
        producer = item["producerPerformanceStatistics"]["producers"][0]
        producer["energyConsumptionCH"] = consumed
        producer["energyProductionCH"] = delivered
        assert normalize_history_day(item)["heat_pump_cop"] is None


def test_empty_history_and_duplicate_guard() -> None:
    """Unreported days remain missing, and mismatched duplicate days fail."""
    start, end = date(2026, 1, 9), date(2026, 1, 10)
    result = combine_history(start, end, [{"data": [_january_9()]}])
    assert result["missing_dates"] == ["2026-01-10"]
    assert len(result["days"]) == 1

    altered = _january_9()
    altered["producerPerformanceStatistics"]["producers"][0]["energyConsumptionCH"] = (
        17.0
    )
    with pytest.raises(ValueError, match="conflicting entries"):
        combine_history(start, end, [{"data": [_january_9(), altered]}])


def test_history_batches() -> None:
    """Split in 31-day windows and do not allow unbounded API reads."""
    assert list(history_batches(date(2026, 1, 1), date(2026, 2, 5))) == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 5)),
    ]
    with pytest.raises(ValueError, match="End date"):
        list(history_batches(date(2026, 2, 1), date(2026, 1, 1)))
    with pytest.raises(ValueError, match="366"):
        list(history_batches(date(2026, 1, 1), date(2027, 1, 2)))


async def test_api_date_range_uses_oauth() -> None:
    """Use the existing authenticated session; never create new credentials."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value={"data": []})
    oauth = MagicMock()
    oauth.async_request = AsyncMock(return_value=response)
    api = RemehaHomeAPI(oauth)

    assert await api.async_get_consumption_data_for_range(
        APPLIANCE_ID, date(2026, 1, 9), date(2026, 1, 10)
    ) == {"data": []}
    kwargs = oauth.async_request.call_args
    assert kwargs.args[:2] == (
        "GET",
        f"https://api.bdrthermea.net/Mobile/api/appliances/{APPLIANCE_ID}"
        "/energyconsumption/daily",
    )
    assert kwargs.kwargs["params"] == {
        "startDate": "2026-01-09T00:00:00.000Z",
        "endDate": "2026-01-10T23:59:59.999Z",
    }
    assert "Ocp-Apim-Subscription-Key" in kwargs.kwargs["headers"]


async def test_action_returns_history(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """Retrieve one day without writing entities or invoking heat controls."""
    await setup_integration(hass, config_entry, mock_api)
    appliance = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, APPLIANCE_ID), config_entry.entry_id
    )
    assert appliance is not None
    mock_api.async_get_consumption_data_for_range.return_value = {
        "data": [_january_9()]
    }

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_ENERGY_HISTORY,
        {
            "device_id": appliance.id,
            "start_date": "2026-01-09",
            "end_date": "2026-01-09",
        },
        blocking=True,
        return_response=True,
    )
    assert result["appliance_id"] == APPLIANCE_ID
    assert result["days"][0]["heat_pump_cop"] == pytest.approx(68 / 18)
    assert not result["missing_dates"]
    mock_api.async_get_consumption_data_for_range.assert_awaited_once_with(
        APPLIANCE_ID, date(2026, 1, 9), date(2026, 1, 9)
    )
    mock_api.async_set_manual.assert_not_awaited()


async def test_action_parent_resolution_and_monthly_batches(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """A producer device targets its appliance; dates are split into batches."""
    await setup_integration(hass, config_entry, mock_api)
    hp = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{APPLIANCE_ID}_1"), config_entry.entry_id
    )
    assert hp is not None
    mock_api.async_get_consumption_data_for_range.return_value = {"data": []}

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_ENERGY_HISTORY,
        {
            "device_id": hp.id,
            "start_date": "2026-01-01",
            "end_date": "2026-02-05",
        },
        blocking=True,
        return_response=True,
    )
    assert len(result["missing_dates"]) == 36
    assert mock_api.async_get_consumption_data_for_range.await_count == 2


async def test_action_rejects_wrong_device_and_dates(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """Reject mistaken targets or excessive ranges before sending API calls."""
    await setup_integration(hass, config_entry, mock_api)
    service = SERVICE_GET_ENERGY_HISTORY
    with pytest.raises(HomeAssistantError, match="Select an appliance"):
        await hass.services.async_call(
            DOMAIN,
            service,
            {
                "device_id": "not-a-remeha-device",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            blocking=True,
            return_response=True,
        )

    with pytest.raises(HomeAssistantError, match="at most 366"):
        await hass.services.async_call(
            DOMAIN,
            service,
            {
                "device_id": "not-a-remeha-device",
                "start_date": "2026-01-01",
                "end_date": "2027-01-02",
            },
            blocking=True,
            return_response=True,
        )
    mock_api.async_get_consumption_data_for_range.assert_not_awaited()
