"""Cheapest refueling plan along a fixed route.

Minimises   total fuel cost + stop_penalty * number_of_stops
for a vehicle with a fixed tank (range_miles / mpg gallons).

Why a stop penalty: the pure cost optimum happily stops to buy 1 gallon at a
station 1 cent cheaper. A small per-stop cost (driver time) removes those
stops while barely changing fuel spend. With stop_penalty=0 this returns the
exact minimum-cost plan.

Algorithm: dynamic programming over stops. In an optimal plan every stop
either fills the tank or buys just enough to reach the next stop (Khuller,
Malekian & Mestre, "To fill or not to fill: the gas station problem"). So the
fuel on arrival at a stop is either 0 or "full tank minus the distance from the
previous stop", giving O(n * k) states, where k is the number of stations
within one tank's range.

For each station v we keep its arrival states (fuel g, cost c). Because fuel
can be topped up at v's price p, a state's value when buying at v is
c - g * p; a prefix minimum over states sorted by g answers "cheapest state
that arrives with at most L gallons" for every next stop in O(log k).
"""
from bisect import bisect_right
from dataclasses import dataclass
from itertools import accumulate

EPSILON = 1e-9
INF = float('inf')


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


@dataclass
class _State:
    fuel: float        # gallons on arrival
    cost: float        # total cost so far (fuel + penalties), excluding purchases here
    parent: tuple      # (station, state index, tank level after buying there) or None for the origin


def plan_fuel_stops(candidates, total_miles, range_miles, mpg, start_fuel_gallons=0.0, stop_penalty=0.0):
    """Return the list of Purchases, in route order, for the cheapest plan.

    If the tank starts empty (start_fuel_gallons == 0), the vehicle begins by
    fueling at the first station on the route; the fuel needed to get there is
    charged at that station's price, so total gallons equals total_miles / mpg.
    """
    if total_miles <= 0:
        return []
    capacity = range_miles / mpg
    if not 0 <= start_fuel_gallons <= capacity:
        raise FuelPlanError(f'Starting fuel must be between 0 and {capacity:g} gallons.')

    stations = _cheapest_per_location(c for c in candidates if 0 <= c.mile < total_miles)
    miles = [s.mile for s in stations]
    states = [[] for _ in stations]
    best_final, final_parent = INF, None

    # Origin.
    if start_fuel_gallons == 0:
        if not stations or miles[0] > range_miles + EPSILON:
            raise FuelPlanError(f'No fuel station within {range_miles:g} miles of the start.')
        prepaid = miles[0] / mpg
        states[0].append(_State(0.0, prepaid * stations[0].price, None))
    else:
        reach = start_fuel_gallons * mpg
        if total_miles <= reach + EPSILON:
            best_final = 0.0
        for w in range(bisect_right(miles, reach + EPSILON)):
            states[w].append(_State(start_fuel_gallons - miles[w] / mpg, 0.0, None))

    # Forward pass in route order: all arrivals at v are known before v is processed.
    for v, station in enumerate(stations):
        arrivals = states[v]
        if not arrivals:
            continue
        price = station.price
        order = sorted(range(len(arrivals)), key=lambda i: arrivals[i].fuel)
        fuels = [arrivals[i].fuel for i in order]
        # prefix_best[k] = (min of cost - fuel*price over the k+1 lowest-fuel states, state index)
        prefix_best = list(accumulate(
            ((arrivals[i].cost - arrivals[i].fuel * price, i) for i in order), min,
        ))

        def cheapest_arrival_with_at_most(gallons):
            k = bisect_right(fuels, gallons + EPSILON) - 1
            return prefix_best[k] if k >= 0 else None

        fill_base, fill_state = prefix_best[-1]
        fill_cost = fill_base + capacity * price + stop_penalty

        for w in range(v + 1, bisect_right(miles, miles[v] + range_miles + EPSILON)):
            leg = (miles[w] - miles[v]) / mpg
            # Option 1: fill the tank at v.
            states[w].append(_State(capacity - leg, fill_cost, (v, fill_state, capacity)))
            # Option 2: buy just enough at v to reach w empty.
            best = cheapest_arrival_with_at_most(leg)
            if best:
                states[w].append(_State(0.0, best[0] + leg * price + stop_penalty, (v, best[1], leg)))

        leg = (total_miles - miles[v]) / mpg
        if leg <= capacity + EPSILON:
            best = cheapest_arrival_with_at_most(leg)
            if best and best[0] + leg * price + stop_penalty < best_final:
                best_final = best[0] + leg * price + stop_penalty
                final_parent = (v, best[1], leg)

    if best_final == INF:
        raise FuelPlanError(_explain_infeasible(miles, total_miles, range_miles))
    return _reconstruct(stations, states, final_parent, start_fuel_gallons, mpg)


def _reconstruct(stations, states, parent, start_fuel_gallons, mpg):
    purchases = []
    while parent is not None:
        v, state_index, level_after = parent
        state = states[v][state_index]
        gallons = level_after - state.fuel
        if state.parent is None and start_fuel_gallons == 0:
            gallons += stations[v].mile / mpg   # prepaid fuel to reach the first station
        if gallons > EPSILON:
            purchases.append(Purchase(stations[v], gallons))
        parent = state.parent
    return purchases[::-1]


def _cheapest_per_location(candidates):
    """Sort by mile; of stations sharing a mile marker keep only the cheapest (the rest are dominated)."""
    kept = []
    for c in sorted(candidates, key=lambda c: (c.mile, c.price)):
        if not kept or c.mile > kept[-1].mile:
            kept.append(c)
    return kept


def _explain_infeasible(miles, total_miles, range_miles):
    points = [0.0, *miles, total_miles]
    for here, there in zip(points, points[1:]):
        if there - here > range_miles + EPSILON:
            return (f'No fuel station within {range_miles:g} miles after mile {here:.0f}; '
                    'the trip cannot be completed with this vehicle range.')
    return 'The starting fuel is not enough to reach the first fuel station.'
