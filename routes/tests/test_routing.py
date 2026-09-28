from unittest.mock import Mock

import pytest

from routes.services.ors_client import ORSError
from routes.services.routing import get_route


def test_get_route_makes_one_directions_call_and_converts_to_lat_lng():
    client = Mock()
    client.directions.return_value = {
        'geometry': {'coordinates': [[-87.6, 41.8], [-95.0, 40.5], [-105.0, 39.7]]},
        'properties': {'summary': {'distance': 1002.4, 'duration': 57319.3}},
    }
    route = get_route((41.8, -87.6), (39.7, -105.0), client=client)

    client.directions.assert_called_once_with([(41.8, -87.6), (39.7, -105.0)])
    assert route.coordinates[0] == (41.8, -87.6)
    assert route.distance_miles == 1002.4


def test_get_route_rejects_response_without_distance():
    client = Mock()
    client.directions.return_value = {'geometry': {'coordinates': []}, 'properties': {}}
    with pytest.raises(ORSError):
        get_route((41.8, -87.6), (39.7, -105.0), client=client)


def test_get_route_flags_endpoints_outside_usa():
    client = Mock()
    client.directions.return_value = {
        'geometry': {'coordinates': [[-87.6, 41.8], [-83.0, 42.3], [-79.4, 43.6]]},
        'properties': {
            'summary': {'distance': 520.0, 'duration': 30000},
            'extras': {'countryinfo': {'values': [[0, 1, 214], [1, 2, 35]]}},  # USA then Canada
        },
    }
    route = get_route((41.8, -87.6), (43.6, -79.4), client=client)
    assert route.start_in_usa and not route.finish_in_usa
