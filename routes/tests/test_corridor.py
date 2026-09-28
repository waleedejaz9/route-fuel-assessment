import numpy as np
import pytest

from routes.services.corridor import densify, haversine_miles, stations_along_route

# A straight east-west road along latitude 40 from lng -100 to -90 (~530 miles).
ROAD = [(40.0, -100.0), (40.0, -95.0), (40.0, -90.0)]


def test_densify_limits_segment_length_and_preserves_distance():
    points = np.array(ROAD)
    dense, miles = densify(points, step=0.5)
    gaps = haversine_miles(dense[:-1], dense[1:])
    assert gaps.max() <= 0.5 + 1e-6
    assert miles[-1] == pytest.approx(haversine_miles(points[:-1], points[1:]).sum())
    assert np.all(np.diff(miles) > 0)


def test_finds_nearby_stations_with_mile_markers_in_order():
    stations = np.array([
        (40.02, -91.0),   # ~1.4 mi north of the road, near the end
        (40.0, -99.0),    # on the road, near the start
        (41.0, -95.0),    # ~69 mi away: excluded
    ])
    indices, miles, off_route = stations_along_route(ROAD, stations, max_distance_miles=5)
    assert list(indices) == [1, 0]
    assert miles[0] == pytest.approx(52.9, abs=1)     # 1 degree of longitude at 40N ~ 53 mi
    assert miles[1] == pytest.approx(476, abs=2)
    assert off_route[1] == pytest.approx(1.4, abs=0.1)


def test_mile_markers_are_scaled_to_router_distance():
    stations = np.array([(40.0, -90.0)])
    _, miles, _ = stations_along_route(ROAD, stations, 5, route_distance_miles=600)
    assert miles[0] == pytest.approx(600)


def test_no_stations():
    indices, miles, off_route = stations_along_route(ROAD, np.empty((0, 2)), 5)
    assert len(indices) == len(miles) == len(off_route) == 0
