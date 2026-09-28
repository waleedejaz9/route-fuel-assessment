"""Cheapest refueling plan along a fixed route (the "gas station problem").

Greedy strategy, optimal when fuel cost is linear in gallons:
  * At a station, if a cheaper station (or the destination) is within range,
    buy just enough fuel to reach the first such one.
  * Otherwise fill the tank and drive to the cheapest station within range.

Stations are visited in mile-marker order, so this runs in O(n * k) where k is
the number of stations within one tank's range.

Optionally, purchases smaller than `min_purchase_gallons` are then folded into
the previous stop (when the tank has room), trading a few cents for not
stopping to buy a gallon at a marginally cheaper station.
"""
from bisect import bisect_right
from dataclasses import dataclass

EPSILON = 1e-9


class FuelPlanError(Exception):
    """The trip cannot be completed with the given range and stations."""


@dataclass(frozen=True)
class Candidate:
    """A station on the route. `ref` is an opaque handle returned in the plan."""
    mile: float
    price: float
    ref: object = None


@dataclass(frozen=True)
class Purchase:
    candidate: Candidate
    gallons: float

    @property
    def cost(self):
        return self.gallons * self.candidate.price


def plan_fuel_stops(candidates, total_miles, range_miles, mpg, start_fuel_gallons=0.0, min_purchase_gallons=0.0):
    """Return the list of Purchases minimising total fuel cost.

    If the tank starts empty (start_fuel_gallons == 0), the vehicle starts with
    just enough fuel to reach the first station on the route, and that fuel is
    charged at the first station's price, so total gallons always equals
    total_miles / mpg.
    """
    if total_miles <= 0:
        return []
    capacity = range_miles / mpg
    if not 0 <= start_fuel_gallons <= capacity:
        raise FuelPlanError(f'Starting fuel must be between 0 and {capacity:g} gallons.')

    stations = _cheapest_per_location(c for c in candidates if 0 <= c.mile < total_miles)
    # The destination acts as a station cheaper than any other: we always want to arrive empty.
    destination = len(stations)
    stations.append(Candidate(mile=total_miles, price=float('-inf')))
    miles = [s.mile for s in stations]
    purchases = {}  # station index -> gallons; merges repeated buys at the same station

    def buy(i, gallons):
        if gallons > EPSILON:
            purchases[i] = purchases.get(i, 0.0) + gallons

    fuel = start_fuel_gallons
    if fuel == 0:
        if stations[0].mile > range_miles or destination == 0:
            raise FuelPlanError(f'No fuel station within {range_miles:g} miles of the start.')
        buy(0, stations[0].mile / mpg)
        current = 0
    else:
        current = None  # at the origin, where fuel cannot be bought

    while current != destination:
        here_mile = 0.0 if current is None else stations[current].mile
        here_price = float('inf') if current is None else stations[current].price
        reach = here_mile + (fuel * mpg if current is None else range_miles)
        first = 0 if current is None else current + 1
        reachable = range(first, bisect_right(miles, reach + EPSILON))
        if not reachable:
            raise FuelPlanError(
                f'No fuel station within {range_miles:g} miles after mile {here_mile:.0f}; '
                'the trip cannot be completed with this vehicle range.'
            )

        cheaper = next((j for j in reachable if stations[j].price < here_price), None)
        if cheaper is not None:
            # Buy just enough to reach the next cheaper station (or the destination).
            target = cheaper
            needed = (stations[target].mile - here_mile) / mpg
            if current is not None:
                buy(current, needed - fuel)
                fuel = max(fuel, needed)
        else:
            # Nothing cheaper in range: fill up here, then go to the cheapest reachable station.
            target = min(reachable, key=lambda j: (stations[j].price, -stations[j].mile))
            buy(current, capacity - fuel)
            fuel = capacity
        fuel -= (stations[target].mile - here_mile) / mpg
        current = target

    plan = [Purchase(stations[i], gallons) for i, gallons in sorted(purchases.items())]
    if min_purchase_gallons > 0:
        plan = _merge_small_purchases(plan, min_purchase_gallons, capacity, mpg, start_fuel_gallons)
    return plan


def _cheapest_per_location(candidates):
    """Sort by mile; of stations sharing a mile marker keep only the cheapest (the rest are dominated)."""
    kept = []
    for c in sorted(candidates, key=lambda c: (c.mile, c.price)):
        if not kept or c.mile > kept[-1].mile:
            kept.append(c)
    return kept


def _merge_small_purchases(plan, min_gallons, capacity, mpg, start_fuel):
    """Fold purchases below `min_gallons` into the previous stop when the tank has room.

    Buying g gallons earlier raises the tank level only between the two stops,
    so the move is feasible iff the level after the earlier purchase stays <= capacity.
    """
    plan = list(plan)
    k = 1
    while k < len(plan):
        if plan[k].gallons < min_gallons:
            prev = plan[k - 1]
            if _level_after_purchase(plan, k - 1, mpg, start_fuel) + plan[k].gallons <= capacity + EPSILON:
                plan[k - 1] = Purchase(prev.candidate, prev.gallons + plan[k].gallons)
                del plan[k]
                continue
        k += 1
    return plan


def _level_after_purchase(plan, index, mpg, start_fuel):
    """Tank level right after buying at plan[index] (an empty start's prepaid fuel is in plan[0])."""
    level, mile = start_fuel, 0.0
    for purchase in plan[:index + 1]:
        level += purchase.gallons - (purchase.candidate.mile - mile) / mpg
        mile = purchase.candidate.mile
    return level
