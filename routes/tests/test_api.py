from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse

from routes.models import FuelStation
from routes.services import trip_planner
from routes.services.ors_client import ORSError, ORSRateLimitError, RouteNotFoundError
from routes.services.stations import get_station_index

# A straight east-west road along latitude 40 from Kansas (-100) to Illinois (-90): ~530 miles.
START, FINISH = '40.0,-100.0', '40.0,-90.0'
ROAD = [[lng / 10, 40.0] for lng in range(-1000, -899, 5)]
USA, CANADA = 214, 35


class FakeORSClient:
    """Stands in for ORSClient; records calls like the real client."""
    instances = []
    route_countries = (USA, USA)
    directions_error = None
    geocode_result = None

    def __init__(self):
        self.calls = 0
        FakeORSClient.instances.append(self)

    def directions(self, points):
        self.calls += 1
        if self.directions_error:
            raise self.directions_error
        start_country, end_country = self.route_countries
        return {
            'geometry': {'coordinates': ROAD},
            'properties': {
                'summary': {'distance': 530.0, 'duration': 28800.0},
                'extras': {'countryinfo': {'values': [[0, 1, start_country], [1, len(ROAD) - 1, end_country]]}},
            },
        }

    def geocode_us(self, text):
        self.calls += 1
        return self.geocode_result


@pytest.fixture(autouse=True)
def fake_ors(monkeypatch, db):
    FakeORSClient.instances = []
    FakeORSClient.route_countries = (USA, USA)
    FakeORSClient.directions_error = None
    FakeORSClient.geocode_result = None
    monkeypatch.setattr(trip_planner, 'ORSClient', FakeORSClient)
    cache.clear()
    get_station_index.cache_clear()
    yield FakeORSClient
    get_station_index.cache_clear()


@pytest.fixture
def stations(db):
    rows = [
        (1, 'Start Stop', 40.0, -99.95, '3.500'),
        (2, 'Cheap Middle', 40.0, -95.0, '2.900'),
        (3, 'Late Stop', 40.02, -91.0, '3.200'),
        (4, 'Far Away', 45.0, -95.0, '1.000'),    # far from the route: must be ignored
    ]
    FuelStation.objects.bulk_create(
        FuelStation(opis_id=i, name=name, address='I-70, EXIT 1', city='Town', state='KS',
                    price=Decimal(price), latitude=lat, longitude=lng, geocode_source='gazetteer')
        for i, name, lat, lng, price in rows
    )


def get_plan(client, **params):
    return client.get(reverse('route-plan'), {'start': START, 'finish': FINISH, **params})


def test_plan_returns_route_stops_and_total_cost(client, stations, fake_ors):
    response = get_plan(client)
    assert response.status_code == 200
    body = response.json()

    assert body['route']['distance_miles'] == 530.0
    assert body['route']['geometry']['type'] == 'LineString'
    names = [s['name'] for s in body['fuel_stops']]
    assert names[0] == 'Start Stop' and 'Cheap Middle' in names and 'Far Away' not in names
    assert body['fuel']['total_gallons'] == pytest.approx(53.0, abs=0.01)
    assert body['fuel']['total_cost'] == pytest.approx(sum(s['cost'] for s in body['fuel_stops']), abs=0.001)
    assert body['meta'] == {**body['meta'], 'cached': False, 'external_api_calls': 1}
    assert body['map_url'].startswith('http://testserver/api/v1/route/map/?')


def test_repeated_request_is_served_from_cache(client, stations, fake_ors):
    get_plan(client)
    body = get_plan(client).json()
    assert body['meta']['cached'] is True
    assert body['meta']['external_api_calls'] == 0
    assert sum(c.calls for c in fake_ors.instances) == 1


def test_city_state_input_needs_no_geocoding_call(client, stations, fake_ors):
    response = client.get(reverse('route-plan'), {'start': 'Hays, Kansas', 'finish': 'Peoria, IL'})
    assert response.status_code == 200
    assert response.json()['start']['label'] == 'Hays, KS'
    assert response.json()['meta']['external_api_calls'] == 1   # the route only


def test_stop_penalty_parameter_changes_plan(client, stations):
    cheapest = get_plan(client, stop_penalty=0).json()
    fewest = get_plan(client, stop_penalty=500).json()
    assert cheapest['fuel']['stop_penalty_usd'] == 0
    assert cheapest['fuel']['total_cost'] <= fewest['fuel']['total_cost']
    assert len(fewest['fuel_stops']) <= len(cheapest['fuel_stops'])
    assert 'stop_penalty=500' in fewest['map_url']


def test_post_json_body(client, stations):
    response = client.post(reverse('route-plan'), {'start': START, 'finish': FINISH}, content_type='application/json')
    assert response.status_code == 200


def test_start_fuel_reduces_fuel_bought(client, stations):
    body = get_plan(client, start_fuel=50).json()
    assert body['fuel']['total_gallons'] == pytest.approx(3.0, abs=0.01)   # 53 needed - 50 in the tank


@pytest.mark.parametrize('params, field', [
    ({'start': START}, 'finish'),
    ({'start': START, 'finish': FINISH, 'start_fuel': 51}, 'start_fuel'),
    ({'start': START, 'finish': FINISH, 'stop_penalty': -1}, 'stop_penalty'),
    ({'start': START, 'finish': START}, 'finish'),
    ({'start': START, 'finish': 'Honolulu, HI'}, 'finish'),
    ({'start': '51.5,-0.12', 'finish': FINISH}, 'start'),
])
def test_invalid_input_returns_400(client, params, field):
    response = client.get(reverse('route-plan'), params)
    assert response.status_code == 400
    assert field in response.json()


def test_unknown_place_falls_back_to_geocoder(client, fake_ors):
    response = client.get(reverse('route-plan'), {'start': 'Nowhereville Xyz', 'finish': FINISH})
    assert response.status_code == 400
    assert 'Could not find' in response.json()['start'][0]


def test_endpoint_outside_usa_detected_from_route(client, stations, fake_ors):
    fake_ors.route_countries = (USA, CANADA)
    response = get_plan(client)
    assert response.status_code == 400
    assert 'outside the USA' in response.json()['finish'][0]


@pytest.mark.parametrize('error, status', [
    (RouteNotFoundError('no route'), 422),
    (ORSRateLimitError('quota'), 503),
    (ORSError('boom'), 502),
])
def test_routing_failures_map_to_http_errors(client, stations, fake_ors, error, status):
    fake_ors.directions_error = error
    assert get_plan(client).status_code == status


def test_no_stations_near_route_returns_422(client):
    response = get_plan(client)
    assert response.status_code == 422
    assert 'No fuel station' in response.json()['detail']


def test_map_page_renders_plan(client, stations):
    response = client.get(reverse('route-map'), {'start': START, 'finish': FINISH})
    assert response.status_code == 200
    assert b'Cheap Middle' in response.content and b'leaflet' in response.content


def test_map_page_shows_errors(client):
    response = client.get(reverse('route-map'), {'start': START})
    assert response.status_code == 400
    assert b'This field is required' in response.content
