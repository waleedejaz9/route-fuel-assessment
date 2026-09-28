"""Thin client for the OpenRouteService HTTP API."""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GEOCODE_LAYERS = 'locality,localadmin,neighbourhood,borough,county'


class ORSError(Exception):
    """Raised when OpenRouteService is unreachable or returns an error."""


class ORSRateLimitError(ORSError):
    """Raised when the OpenRouteService quota or rate limit is exhausted."""


class RouteNotFoundError(ORSError):
    """OpenRouteService could not build a driving route between the points."""


class ORSClient:
    def __init__(self, api_key=None, base_url=None, timeout=None, session=None):
        self.api_key = api_key or settings.ORS_API_KEY
        self.base_url = (base_url or settings.ORS_BASE_URL).rstrip('/')
        self.timeout = timeout or settings.ORS_TIMEOUT_SECONDS
        self.session = session or requests.Session()
        self.calls = 0  # number of HTTP requests made, reported in API responses
        if not self.api_key:
            raise ORSError('ORS_API_KEY is not configured.')

    def _request(self, method, path, **kwargs):
        self.calls += 1
        headers = {'Authorization': self.api_key, 'Accept': 'application/json, application/geo+json'}
        try:
            response = self.session.request(
                method, f'{self.base_url}{path}', headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise ORSError(f'OpenRouteService request failed: {exc}') from exc
        quota_exceeded = response.status_code == 403 and 'quota' in response.text.lower()
        if response.status_code == 429 or quota_exceeded:
            raise ORSRateLimitError('OpenRouteService rate limit or quota exceeded.')
        if response.status_code in (400, 404) and path.startswith('/v2/directions'):
            # e.g. code 2010 "could not find routable point", 2004 "route too long"
            raise RouteNotFoundError(_ors_error_message(response) or 'No drivable route between these locations.')
        if response.status_code != 200:
            logger.warning('ORS %s %s -> %s: %s', method, path, response.status_code, response.text[:500])
            raise ORSError(f'OpenRouteService returned HTTP {response.status_code}.')
        return response.json()

    def directions(self, points, profile='driving-car'):
        """Return the GeoJSON route feature through points [(lat, lng), ...], distances in miles."""
        data = self._request('POST', f'/v2/directions/{profile}/geojson', json={
            'coordinates': [[lng, lat] for lat, lng in points],
            'units': 'mi',
            'instructions': False,
            'extra_info': ['countryinfo'],
        })
        features = data.get('features') or []
        if not features:
            raise RouteNotFoundError('No drivable route between these locations.')
        return features[0]

    def geocode_us(self, text):
        """Return (lat, lng, label) for free-form text within the US, or None."""
        data = self._request('GET', '/geocode/search', params={'text': text, 'boundary.country': 'US', 'size': 1})
        features = data.get('features') or []
        if not features:
            return None
        lng, lat = features[0]['geometry']['coordinates']
        return lat, lng, features[0]['properties'].get('label', text)

    def geocode_us_locality(self, city, state):
        """Return (lat, lng) for a US city/state, or None if no confident match in that state."""
        data = self._request('GET', '/geocode/search', params={
            'text': f'{city}, {state}',
            'boundary.country': 'US',
            'layers': GEOCODE_LAYERS,
            'size': 1,
        })
        features = data.get('features') or []
        if not features:
            return None
        props = features[0]['properties']
        if props.get('region_a') != state:
            return None
        lng, lat = features[0]['geometry']['coordinates']
        return lat, lng


def _ors_error_message(response):
    try:
        error = response.json().get('error')
    except ValueError:
        return None
    return error.get('message') if isinstance(error, dict) else error
