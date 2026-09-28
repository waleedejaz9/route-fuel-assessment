"""Resolve user input ("Chicago, IL", "Denver, Colorado", "41.88,-87.63") to US coordinates.

Resolution order, cheapest first:
  1. "lat,lng" coordinates        - no lookup
  2. "City, ST" / "City, State"   - offline Census gazetteer
  3. anything else                - OpenRouteService geocoder (1 API call, cached)
"""
import hashlib
import re
from dataclasses import dataclass

from django.core.cache import cache

from .geocoding import US_STATES, get_gazetteer
from .ors_client import ORSError

STATE_NAMES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'district of columbia': 'DC',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN',
    'iowa': 'IA', 'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
    'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
    'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH',
    'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
    'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
}
# (min_lat, max_lat, min_lng, max_lng): contiguous US, Alaska, Hawaii.
US_BOUNDS = [(24.3, 49.5, -125.0, -66.8), (51.0, 71.6, -180.0, -129.9), (18.8, 22.4, -160.4, -154.7)]

_COORDINATES = re.compile(r'^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$')
_COUNTRY_SUFFIX = {'usa', 'us', 'united states', 'united states of america'}
GEOCODE_CACHE_SECONDS = 60 * 60 * 24 * 30


class LocationError(ValueError):
    """The input could not be resolved to a location in the USA."""


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    lat: float
    lng: float

    @property
    def point(self):
        return self.lat, self.lng


def in_usa(lat, lng):
    return any(lo_lat <= lat <= hi_lat and lo_lng <= lng <= hi_lng for lo_lat, hi_lat, lo_lng, hi_lng in US_BOUNDS)


def resolve_location(text, get_client):
    """Resolve `text` to a Location in the USA.

    `get_client` returns an ORSClient; it is only called if the offline
    lookups fail, so most requests make no geocoding API call.
    """
    query = ' '.join(text.split())
    location = _parse_coordinates(query) or _lookup_city_state(query) or _geocode_remote(query, get_client)
    if not in_usa(location.lat, location.lng):
        raise LocationError(f'"{query}" is outside the USA.')
    return location


def _parse_coordinates(query):
    match = _COORDINATES.match(query)
    if not match:
        return None
    lat, lng = float(match[1]), float(match[2])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise LocationError(f'"{query}" is not a valid "latitude,longitude" pair.')
    return Location(query=query, label=f'{lat:.5f}, {lng:.5f}', lat=lat, lng=lng)


def _lookup_city_state(query):
    parts = [p.strip() for p in query.split(',') if p.strip()]
    if parts and parts[-1].lower().replace('.', '') in _COUNTRY_SUFFIX:
        parts.pop()
    if len(parts) != 2:
        return None
    city, state = parts
    state = STATE_NAMES.get(state.lower(), state.upper().replace('.', ''))
    if state not in US_STATES:
        return None
    hit = get_gazetteer().lookup(city, state)
    if not hit:
        return None
    return Location(query=query, label=f'{city.title()}, {state}', lat=hit[0], lng=hit[1])


def _geocode_remote(query, get_client):
    cache_key = f'geocode:v1:{hashlib.sha256(query.lower().encode()).hexdigest()}'
    cached = cache.get(cache_key)
    if cached:
        return Location(query=query, **cached)
    try:
        hit = get_client().geocode_us(query)
    except ORSError as exc:
        raise LocationError(
            f'Could not find "{query}" offline, and the online geocoder is unavailable ({exc}). '
            'Try the "City, ST" format or "latitude,longitude".'
        ) from exc
    if not hit:
        raise LocationError(f'Could not find a US location matching "{query}".')
    lat, lng, label = hit
    cache.set(cache_key, {'label': label, 'lat': lat, 'lng': lng}, GEOCODE_CACHE_SECONDS)
    return Location(query=query, label=label, lat=lat, lng=lng)
