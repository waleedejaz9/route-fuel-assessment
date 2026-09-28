"""Plan a trip: resolve locations, fetch the route once, choose the cheapest fuel stops.

External API budget per request: 1 directions call, plus 1 geocoding call per
location only if it isn't a "City, ST" or "lat,lng" input. Routes and whole
plans are cached, so repeated requests make no external calls.
"""
import time
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from django.conf import settings
from django.core.cache import cache

from .corridor import haversine_miles, stations_along_route
from .locations import LocationError, resolve_location
from .optimizer import Candidate, plan_fuel_stops
from .ors_client import ORSClient
from .routing import Route, get_route, simplify_line
from .stations import get_station_index

CENT = Decimal('0.01')
CACHE_VERSION = 'v1'
MIN_TRIP_MILES = 0.1


class TripInputError(ValueError):
    """Invalid user input; `errors` maps field name -> list of messages."""

    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


class _LazyORSClient:
    """Creates the ORS client only if an external call is actually needed."""

    def __init__(self):
        self._client = None

    def get(self):
        if self._client is None:
            self._client = ORSClient()
        return self._client

    @property
    def calls(self):
        return self._client.calls if self._client else 0


def plan_trip(start, finish, start_fuel_gallons=0.0, stop_penalty=None):
    started = time.perf_counter()
    if stop_penalty is None:
        stop_penalty = settings.FUEL_STOP_PENALTY_USD
    ors = _LazyORSClient()
    origin = _resolve('start', start, ors)
    destination = _resolve('finish', finish, ors)
    if _miles_between(origin, destination) < MIN_TRIP_MILES:
        raise TripInputError({'finish': ['Start and finish are the same location.']})

    plan_key = (f'trip:{CACHE_VERSION}:{_point_key(origin)}:{_point_key(destination)}'
                f':{start_fuel_gallons:g}:{stop_penalty:g}')
    result = cache.get(plan_key)
    cached = result is not None
    if not cached:
        route = _cached_route(origin, destination, ors)
        _ensure_endpoints_in_usa(route, origin, destination)
        result = _build_plan(origin, destination, route, start_fuel_gallons, stop_penalty)
        cache.set(plan_key, result)

    return {
        **result,
        'meta': {
            'cached': cached,
            'external_api_calls': ors.calls,
            'response_time_ms': round((time.perf_counter() - started) * 1000, 1),
        },
    }


def _resolve(field, text, ors):
    try:
        return resolve_location(text, ors.get)
    except LocationError as exc:
        raise TripInputError({field: [str(exc)]}) from exc


def _ensure_endpoints_in_usa(route, origin, destination):
    errors = {}
    if not route.start_in_usa:
        errors['start'] = [f'"{origin.query}" is outside the USA.']
    if not route.finish_in_usa:
        errors['finish'] = [f'"{destination.query}" is outside the USA.']
    if errors:
        raise TripInputError(errors)


def _cached_route(origin, destination, ors):
    key = f'route:{CACHE_VERSION}:{_point_key(origin)}:{_point_key(destination)}'
    route = cache.get(key)
    if route is None:
        route = get_route(origin.point, destination.point, client=ors.get())
        cache.set(key, route)
    return route


def _build_plan(origin, destination, route: Route, start_fuel_gallons, stop_penalty):
    index = get_station_index()
    found, mile_markers, off_route = stations_along_route(
        route.coordinates, index.coords, settings.STATION_MAX_DETOUR_MILES, route.distance_miles,
    )
    candidates = [
        Candidate(mile=float(mile), price=float(index.stations[i]['price']), ref=(int(i), float(off)))
        for i, mile, off in zip(found, mile_markers, off_route)
    ]
    purchases = plan_fuel_stops(
        candidates,
        total_miles=route.distance_miles,
        range_miles=settings.VEHICLE_RANGE_MILES,
        mpg=settings.VEHICLE_MPG,
        start_fuel_gallons=start_fuel_gallons,
        stop_penalty=stop_penalty,
        first_stop_max_miles=settings.FIRST_STOP_MAX_MILES,
    )

    stops = []
    for number, purchase in enumerate(purchases, start=1):
        station_index, off_route_miles = purchase.candidate.ref
        station = index.stations[station_index]
        gallons = Decimal(f'{purchase.gallons:.3f}')
        stops.append({
            'stop': number,
            'station_id': station['opis_id'],
            'name': station['name'],
            'address': station['address'],
            'city': station['city'],
            'state': station['state'],
            'lat': station['latitude'],
            'lng': station['longitude'],
            'mile_marker': round(purchase.candidate.mile, 1),
            'distance_from_route_miles': round(off_route_miles, 1),
            'price_per_gallon': float(station['price'].quantize(Decimal('0.001'))),
            'gallons': float(gallons),
            'cost': float((gallons * station['price']).quantize(CENT, ROUND_HALF_UP)),
        })

    total_cost = sum(Decimal(str(s['cost'])) for s in stops)
    total_gallons = sum(Decimal(str(s['gallons'])) for s in stops)
    return {
        'start': _location_payload(origin),
        'finish': _location_payload(destination),
        'route': {
            'distance_miles': round(route.distance_miles, 1),
            'duration_hours': round(route.duration_seconds / 3600, 1),
            'geometry': {
                'type': 'LineString',
                'coordinates': [[round(lng, 5), round(lat, 5)] for lat, lng in simplify_line(route.coordinates)],
            },
        },
        'fuel': {
            'vehicle_range_miles': settings.VEHICLE_RANGE_MILES,
            'miles_per_gallon': settings.VEHICLE_MPG,
            'start_fuel_gallons': start_fuel_gallons,
            'stop_penalty_usd': stop_penalty,
            'total_gallons': float(total_gallons),
            'total_cost': float(total_cost),
            'average_price_per_gallon': float((total_cost / total_gallons).quantize(Decimal('0.001'))) if stops else None,
            'currency': 'USD',
            'stations_considered': len(candidates),
        },
        'fuel_stops': stops,
    }


def _location_payload(location):
    return {'query': location.query, 'label': location.label, 'lat': location.lat, 'lng': location.lng}


def _point_key(location):
    return f'{location.lat:.5f},{location.lng:.5f}'


def _miles_between(a, b):
    return float(haversine_miles(np.array([a.point]), np.array([b.point]))[0])
