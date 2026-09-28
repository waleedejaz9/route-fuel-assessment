import random

import numpy as np
import pytest
from scipy.optimize import linprog

from routes.services.optimizer import Candidate, FuelPlanError, plan_fuel_stops

RANGE, MPG = 500, 10
CAPACITY = RANGE / MPG


def total_cost(purchases):
    return sum(p.cost for p in purchases)


def total_gallons(purchases):
    return sum(p.gallons for p in purchases)


def refs(purchases):
    return [p.candidate.ref for p in purchases]


def test_buys_just_enough_to_reach_cheaper_station():
    plan = plan_fuel_stops([Candidate(0, 4.0, 'A'), Candidate(300, 3.0, 'B')], 600, RANGE, MPG)
    assert refs(plan) == ['A', 'B']
    assert plan[0].gallons == pytest.approx(30)   # only enough to reach B
    assert plan[1].gallons == pytest.approx(30)   # B -> destination


def test_fills_up_when_current_station_is_cheapest_in_range():
    stations = [Candidate(0, 3.0, 'A'), Candidate(400, 3.5, 'B'), Candidate(450, 3.2, 'C')]
    plan = plan_fuel_stops(stations, 800, RANGE, MPG)
    assert refs(plan) == ['A', 'C']
    assert plan[0].gallons == pytest.approx(CAPACITY)     # fill at the cheapest
    assert plan[1].gallons == pytest.approx(30)           # C has 5 gal left, needs 35 to finish


def test_empty_start_accounts_for_all_fuel_used():
    stations = [Candidate(12, 3.1), Candidate(480, 3.4), Candidate(700, 2.9)]
    plan = plan_fuel_stops(stations, 1100, RANGE, MPG)
    assert total_gallons(plan) == pytest.approx(1100 / MPG)


def test_full_tank_short_trip_needs_no_fuel():
    assert plan_fuel_stops([Candidate(100, 3.0)], 450, RANGE, MPG, start_fuel_gallons=CAPACITY) == []


def test_partial_start_fuel_is_used_before_buying():
    plan = plan_fuel_stops([Candidate(100, 3.0, 'A')], 300, RANGE, MPG, start_fuel_gallons=10)
    assert refs(plan) == ['A']
    assert plan[0].gallons == pytest.approx(20)


def test_gap_longer_than_range_raises():
    with pytest.raises(FuelPlanError, match='cannot be completed'):
        plan_fuel_stops([Candidate(0, 3.0), Candidate(600, 3.0)], 900, RANGE, MPG)


def test_no_station_near_start_raises():
    with pytest.raises(FuelPlanError, match='of the start'):
        plan_fuel_stops([Candidate(550, 3.0)], 900, RANGE, MPG)


def test_rejects_start_fuel_above_capacity():
    with pytest.raises(FuelPlanError):
        plan_fuel_stops([Candidate(0, 3.0)], 100, RANGE, MPG, start_fuel_gallons=CAPACITY + 1)


def lp_optimal_cost(stations, total_miles, start_fuel):
    """Exact minimum cost via linear programming (reference solution)."""
    n = len(stations)
    miles = np.array([s.mile for s in stations])
    prices = np.array([s.price for s in stations])
    lower = np.tril(np.ones((n, n)))       # row i sums purchases at stations 0..i
    strict = np.tril(np.ones((n, n)), -1)  # row i sums purchases at stations 0..i-1
    # Fuel on arrival at each station and at the destination must be >= 0.
    a_arrive = np.vstack((strict, np.ones((1, n))))
    b_arrive = np.concatenate((miles, [total_miles])) / MPG - start_fuel
    # Fuel after buying at each station must be <= capacity.
    b_capacity = CAPACITY - start_fuel + miles / MPG
    result = linprog(prices, A_ub=np.vstack((-a_arrive, lower)),
                     b_ub=np.concatenate((-b_arrive, b_capacity)), bounds=(0, None))
    return result.fun if result.success else None


@pytest.mark.parametrize('seed', range(300))
def test_greedy_matches_linear_programming_optimum(seed):
    rng = random.Random(seed)
    total = rng.uniform(200, 3000)
    stations = sorted(
        (Candidate(rng.uniform(0, total), round(rng.uniform(2.7, 4.5), 3)) for _ in range(rng.randint(1, 40))),
        key=lambda c: c.mile,
    )
    start_fuel = rng.choice([0.0, rng.uniform(0, CAPACITY)])
    if start_fuel == 0:
        # Empty start: the fuel to reach the first station is billed there. Model it for the LP
        # as starting with that fuel, and add its cost.
        prepaid = stations[0].mile / MPG
        expected = lp_optimal_cost(stations, total, prepaid)
        expected = None if expected is None else expected + prepaid * stations[0].price
        if stations[0].mile > RANGE:
            expected = None
    else:
        expected = lp_optimal_cost(stations, total, start_fuel)

    if expected is None:
        with pytest.raises(FuelPlanError):
            plan_fuel_stops(stations, total, RANGE, MPG, start_fuel)
    else:
        plan = plan_fuel_stops(stations, total, RANGE, MPG, start_fuel)
        assert total_cost(plan) == pytest.approx(expected, rel=1e-7, abs=1e-6)
        assert all(p.gallons > 0 for p in plan)


def test_stations_at_same_location_keep_only_the_cheapest():
    stations = [Candidate(0, 3.5, 'pricey'), Candidate(0, 3.1, 'cheap'), Candidate(0, 3.3, 'mid')]
    plan = plan_fuel_stops(stations, 300, RANGE, MPG)
    assert refs(plan) == ['cheap']


def test_stop_penalty_skips_marginal_top_ups():
    # B is 1 cent cheaper than A just 10 miles on: the pure optimum makes an extra stop to save 30 cents.
    stations = [Candidate(0, 3.10, 'A'), Candidate(10, 3.09, 'B')]
    assert refs(plan_fuel_stops(stations, 400, RANGE, MPG)) == ['A', 'B']
    assert refs(plan_fuel_stops(stations, 400, RANGE, MPG, stop_penalty=5)) == ['A']


def test_stop_penalty_still_pays_for_big_savings():
    stations = [Candidate(0, 4.00, 'A'), Candidate(10, 3.00, 'B')]
    assert refs(plan_fuel_stops(stations, 400, RANGE, MPG, stop_penalty=5)) == ['A', 'B']


def brute_force_cost(stations, total, start_fuel, penalty):
    """min over every subset of stops of (LP fuel cost using only those stops) + penalty * stops."""
    best = None
    n = len(stations)
    for mask in range(1 << n):
        chosen = [stations[i] for i in range(n) if mask >> i & 1]
        if start_fuel == 0:
            if not mask & 1:
                continue  # empty start: the first station is always a stop
            prepaid = chosen[0].mile / MPG
            if chosen[0].mile > RANGE:
                continue
            fuel_cost = lp_optimal_cost(chosen, total, prepaid)
            fuel_cost = None if fuel_cost is None else fuel_cost + prepaid * chosen[0].price
        else:
            fuel_cost = lp_optimal_cost(chosen, total, start_fuel) if chosen else (
                0.0 if total <= start_fuel * MPG else None)
        if fuel_cost is not None:
            cost = fuel_cost + penalty * len(chosen)
            best = cost if best is None else min(best, cost)
    return best


@pytest.mark.parametrize('seed', range(150))
def test_stop_penalty_plan_matches_brute_force(seed):
    rng = random.Random(1000 + seed)
    total = rng.uniform(300, 1600)
    stations = sorted(
        {round(rng.uniform(0, total), 1): Candidate(0, 0) for _ in range(rng.randint(1, 8))}.keys()
    )
    stations = [Candidate(m, round(rng.uniform(2.7, 4.5), 3)) for m in stations]
    start_fuel = rng.choice([0.0, rng.uniform(0, CAPACITY)])
    penalty = rng.choice([0.0, 2.0, 10.0, 40.0])
    expected = brute_force_cost(stations, total, start_fuel, penalty)

    if expected is None:
        with pytest.raises(FuelPlanError):
            plan_fuel_stops(stations, total, RANGE, MPG, start_fuel, stop_penalty=penalty)
        return
    plan = plan_fuel_stops(stations, total, RANGE, MPG, start_fuel, stop_penalty=penalty)
    # A stop that buys nothing is not counted, so the plan can only be as good or better.
    actual = total_cost(plan) + penalty * len(plan)
    assert actual == pytest.approx(expected, rel=1e-7, abs=1e-6)


@pytest.mark.parametrize('seed', range(100))
def test_plans_are_physically_feasible(seed):
    rng = random.Random(seed)
    total = rng.uniform(300, 3000)
    stations = [Candidate(rng.uniform(0, total), round(rng.uniform(2.7, 4.5), 3)) for _ in range(60)]
    stations.append(Candidate(0, 3.5))
    try:
        plan = plan_fuel_stops(stations, total, RANGE, MPG, stop_penalty=rng.choice([0, 5, 20]))
    except FuelPlanError:
        return
    level, mile = -plan[0].candidate.mile / MPG, 0.0  # empty start: fuel to reach stop 1 is prepaid there
    for p in plan:
        level -= (p.candidate.mile - mile) / MPG
        assert level >= -1e-6
        level += p.gallons
        assert level <= CAPACITY + 1e-6
        mile = p.candidate.mile
    assert level - (total - mile) / MPG == pytest.approx(0, abs=1e-6)
