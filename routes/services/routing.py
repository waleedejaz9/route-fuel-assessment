"""Driving route between two points: exactly one OpenRouteService directions call."""
from dataclasses import dataclass

from .ors_client import ORSClient, ORSError


@dataclass(frozen=True)
class Route:
    coordinates: list  # [(lat, lng), ...] along the road
    distance_miles: float
    duration_seconds: float


def get_route(start, finish, client=None):
    """Return the driving Route from start to finish, each a (lat, lng) tuple."""
    client = client or ORSClient()
    feature = client.directions([start, finish])
    summary = feature['properties'].get('summary') or {}
    if 'distance' not in summary:
        raise ORSError('OpenRouteService returned a route without a distance.')
    coordinates = [(lat, lng) for lng, lat, *_ in feature['geometry']['coordinates']]
    return Route(
        coordinates=coordinates,
        distance_miles=float(summary['distance']),
        duration_seconds=float(summary.get('duration', 0.0)),
    )
