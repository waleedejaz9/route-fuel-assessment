"""Find fuel stations along a route and where along it they are.

Route points are converted to 3D unit-sphere coordinates and indexed with a
KD-tree, so a single vectorized query gives every station's distance to the
route and the mile marker of its nearest route point.
"""
import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_MILES = 3958.8
# Route vertices can be miles apart on straight highways; interpolate so the
# nearest-vertex distance is a good approximation of distance-to-road.
DENSIFY_STEP_MILES = 0.5


def to_xyz(lat_lng):
    """(N, 2) degrees -> (N, 3) points on a sphere of radius EARTH_RADIUS_MILES."""
    lat, lng = np.radians(lat_lng[:, 0]), np.radians(lat_lng[:, 1])
    cos_lat = np.cos(lat)
    return EARTH_RADIUS_MILES * np.column_stack((cos_lat * np.cos(lng), cos_lat * np.sin(lng), np.sin(lat)))


def haversine_miles(a, b):
    """Great-circle distance between arrays of (lat, lng) degree pairs."""
    lat1, lng1, lat2, lng2 = map(np.radians, (a[:, 0], a[:, 1], b[:, 0], b[:, 1]))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(h))


def densify(points, step=DENSIFY_STEP_MILES):
    """Insert interpolated points so no segment is longer than `step` miles.

    Returns (points, cumulative_miles) as arrays.
    """
    seg_miles = haversine_miles(points[:-1], points[1:])
    pieces = np.maximum(1, np.ceil(seg_miles / step)).astype(int)
    seg_index = np.repeat(np.arange(len(seg_miles)), pieces)
    # Fraction along each segment: 0, 1/n, ..., (n-1)/n
    offsets = np.arange(len(seg_index)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    frac = offsets / pieces[seg_index]

    start, end = points[seg_index], points[seg_index + 1]
    dense = np.vstack((start + (end - start) * frac[:, None], points[-1:]))

    seg_start_miles = np.concatenate(([0.0], np.cumsum(seg_miles)))
    miles = np.concatenate((seg_start_miles[seg_index] + seg_miles[seg_index] * frac, seg_start_miles[-1:]))
    return dense, miles


def stations_along_route(route_coords, station_coords, max_distance_miles, route_distance_miles=None):
    """Locate stations within `max_distance_miles` of the route.

    Args:
        route_coords: sequence of (lat, lng) along the route.
        station_coords: (N, 2) array of station (lat, lng).
        max_distance_miles: corridor half-width.
        route_distance_miles: if given, mile markers are scaled so the route
            ends at this distance (keeps them consistent with the router's total).

    Returns:
        (indices, mile_markers, off_route_miles): indices into station_coords,
        sorted by mile marker.
    """
    route = np.asarray(route_coords, dtype=float)
    stations = np.asarray(station_coords, dtype=float)
    if len(route) < 2 or len(stations) == 0:
        return np.array([], dtype=int), np.array([]), np.array([])

    dense, miles = densify(route)
    if route_distance_miles and miles[-1] > 0:
        miles = miles * (route_distance_miles / miles[-1])

    tree = cKDTree(to_xyz(dense))
    distances, nearest = tree.query(to_xyz(stations), distance_upper_bound=max_distance_miles)
    hits = np.flatnonzero(np.isfinite(distances))

    order = np.argsort(miles[nearest[hits]], kind='stable')
    hits = hits[order]
    return hits, miles[nearest[hits]], distances[hits]
