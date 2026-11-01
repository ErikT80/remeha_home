"""Normalize historical Remeha energy data without mixing hybrid heat sources.

The Remeha API reports indicative energy estimates, not independently metered
heat output. Missing producer data must never be replaced with appliance-wide
totals: those may include both the heat pump and the gas boiler.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import math

from .producer_ids import is_valid_producer

MAX_HISTORY_DAYS = 366
HISTORY_BATCH_DAYS = 31


def history_batches(start: date, end: date):
    """Yield inclusive date ranges, with bounded requests to Remeha's API."""
    if end < start:
        raise ValueError("End date must be on or after start date")
    if (end - start).days >= MAX_HISTORY_DAYS:
        raise ValueError(f"A request may cover at most {MAX_HISTORY_DAYS} days")
    cursor = start
    while cursor <= end:
        batch_end = min(end, cursor + timedelta(days=HISTORY_BATCH_DAYS - 1))
        yield cursor, batch_end
        cursor = batch_end + timedelta(days=1)


def _energy(value) -> float | None:
    """Preserve missing and invalid values; never silently interpret as zero."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _sum_complete(values: list[float | None]) -> float | None:
    """Unknown is not zero, even with several producers of the same type."""
    if not values or any(v is None for v in values):
        return None
    return sum(values)


def _producer_category(kind: object, source: object) -> str | None:
    """Identify the affected energy total when a producer is incomplete."""
    if not isinstance(kind, str) or not isinstance(source, str):
        return None
    if kind.startswith("HeatPump") and source == "Electric":
        return "heat_pump"
    if kind == "GasBoiler" and source == "NaturalGas":
        return "gas"
    return None


def normalize_history_day(item: dict) -> dict | None:
    """Return a JSON-safe day with per-producer and heat-pump-only metrics."""
    if not isinstance(item, dict) or item.get("isDataReported") is False:
        return None
    timestamp = item.get("timeStamp")
    if not isinstance(timestamp, str):
        return None
    try:
        day = date.fromisoformat(timestamp[:10])
    except ValueError:
        return None

    statistics = item.get("producerPerformanceStatistics")
    producer_data = statistics.get("producers") if isinstance(statistics, dict) else None
    raw_producers = producer_data if isinstance(producer_data, list) else []
    valid_producers = [
        producer for producer in raw_producers if is_valid_producer(producer)
    ]
    # The API can temporarily repeat or omit producers. Like the live
    # resolver, reject indistinguishable records rather than double count them.
    identities = Counter(
        (
            int(producer["instanceWithinDevice"]),
            producer["producerType"],
            producer["energyType"],
        )
        for producer in valid_producers
    )
    incomplete_categories: set[str] = set()
    producers = []
    for producer in raw_producers:
        if not isinstance(producer, dict):
            continue
        kind, source = producer.get("producerType"), producer.get("energyType")
        category = _producer_category(kind, source)
        if not is_valid_producer(producer):
            # An unknown placeholder cannot be assigned to a heat source. If
            # the source is recognizable, its total must remain unknown.
            if category is not None:
                incomplete_categories.add(category)
            continue
        identity = (int(producer["instanceWithinDevice"]), kind, source)
        if identities[identity] > 1:
            if category is not None:
                incomplete_categories.add(category)
            continue
        producers.append(
            {
                "producer_type": kind,
                "energy_type": source,
                "heating_consumed_kwh": _energy(
                    producer.get("energyConsumptionCH")
                ),
                "heating_delivered_kwh": _energy(
                    producer.get("energyProductionCH")
                ),
            }
        )

    heat_pumps = [
        producer
        for producer in producers
        if producer["producer_type"].startswith("HeatPump")
        and producer["energy_type"] == "Electric"
    ]
    boilers = [
        producer
        for producer in producers
        if producer["producer_type"] == "GasBoiler"
        and producer["energy_type"] == "NaturalGas"
    ]
    # Incomplete or duplicated producers make their category's total unknown,
    # even if other producers of that type still have valid measurements.
    consumed = (
        _sum_complete([p["heating_consumed_kwh"] for p in heat_pumps])
        if "heat_pump" not in incomplete_categories
        else None
    )
    delivered = (
        _sum_complete([p["heating_delivered_kwh"] for p in heat_pumps])
        if "heat_pump" not in incomplete_categories
        else None
    )
    gas = (
        _sum_complete([p["heating_consumed_kwh"] for p in boilers])
        if "gas" not in incomplete_categories
        else None
    )
    cop = (
        delivered / consumed
        if consumed is not None and consumed > 0 and delivered is not None
        else None
    )

    return {
        "date": day.isoformat(),
        "timestamp": timestamp,
        "heat_pump_consumed_kwh": consumed,
        "heat_pump_delivered_kwh": delivered,
        "heat_pump_cop": cop,
        "gas_heating_consumed_kwh": gas,
        "producers": producers,
    }


def combine_history(start: date, end: date, responses: list[dict]) -> dict:
    """Assemble chronological API responses and report absent days as unknown."""
    by_date = {}
    for response in responses:
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise TypeError("Remeha energy history response has no data list")
        for raw_day in rows:
            row = normalize_history_day(raw_day)
            if row is None or not start <= date.fromisoformat(row["date"]) <= end:
                continue
            if row["date"] in by_date and row != by_date[row["date"]]:
                raise ValueError(
                    "Remeha returned conflicting entries for " + row["date"]
                )
            by_date[row["date"]] = row

    days = [by_date[key] for key in sorted(by_date)]
    missing = [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).isoformat() not in by_date
    ]
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": days,
        "missing_dates": missing,
        "note": "Remeha's indicative estimates; no independently verified heat meter",
    }
