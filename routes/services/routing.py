"""Driving route between two points: exactly one OpenRouteService directions call."""
from dataclasses import dataclass

import numpy as np

from .ors_client import ORSClient, ORSError

# ~50 m: invisible at route zoom levels; shrinks a route's point count ~8x.
SIMPLIFY_TOLERANCE_DEGREES = 0.0005
# OpenRouteService country id for the United States (from `extra_info: countryinfo`).
ORS_COUNTRY_USA = 214


@dataclass(frozen=True)
class Route:
    coordinates: list  # [(lat, lng), ...] along the road
    distance_miles: float
    duration_seconds: float
    start_in_usa: bool = True
    finish_in_usa: bool = True


def get_route(start, finish, client=None):
    """Return the driving Route from start to finish, each a (lat, lng) tuple."""
    client = client or ORSClient()
    feature = client.directions([start, finish])
    summary = feature['properties'].get('summary') or {}
    if 'distance' not in summary:
        raise ORSError('OpenRouteService returned a route without a distance.')
    coordinates = [(lat, lng) for lng, lat, *_ in feature['geometry']['coordinates']]
    countries = (feature['properties'].get('extras') or {}).get('countryinfo', {}).get('values') or []
    return Route(
        coordinates=coordinates,
        distance_miles=float(summary['distance']),
        duration_seconds=float(summary.get('duration', 0.0)),
        start_in_usa=not countries or countries[0][2] == ORS_COUNTRY_USA,
        finish_in_usa=not countries or countries[-1][2] == ORS_COUNTRY_USA,
    )


def simplify_line(points, tolerance=SIMPLIFY_TOLERANCE_DEGREES):
    """Ramer-Douglas-Peucker simplification of [(lat, lng), ...] for display.

    Only the geometry returned to clients is simplified; fuel-stop search
    uses the full-resolution route.
    """
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3:
        return [tuple(p) for p in pts]
    keep = np.zeros(len(pts), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        a, b = pts[first], pts[last]
        inner = pts[first + 1:last]
        ab = b - a
        length = np.hypot(*ab)
        if length == 0:
            dist = np.hypot(*(inner - a).T)
        else:
            dist = np.abs(ab[0] * (inner[:, 1] - a[1]) - ab[1] * (inner[:, 0] - a[0])) / length
        farthest = int(np.argmax(dist))
        if dist[farthest] > tolerance:
            split = first + 1 + farthest
            keep[split] = True
            stack.extend(((first, split), (split, last)))
    return [tuple(p) for p in pts[keep]]
