import pytest

from routes.services.geocoding import Gazetteer, get_gazetteer, normalize_place_name


@pytest.mark.parametrize('a, b', [
    ('St. Louis', 'Saint Louis'),
    ('Mc Lean', 'McLean'),
    ('La Salle', 'LaSalle'),
    ('Ft Worth', 'Fort Worth'),
    ('Winston-Salem', 'winston salem'),
])
def test_normalize_place_name_equivalences(a, b):
    assert normalize_place_name(a) == normalize_place_name(b)


def test_census_suffixes_and_consolidated_names():
    gazetteer = Gazetteer([
        ('UT', 'Salt Lake City city', 40.77, -111.93),
        ('GA', 'Macon-Bibb County', 32.81, -83.69),
        ('OK', 'Big Cabin town', 36.54, -95.23),
    ])
    assert gazetteer.lookup('Salt Lake City', 'UT') == (40.77, -111.93)
    assert gazetteer.lookup('Macon', 'ga') == (32.81, -83.69)
    assert gazetteer.lookup('Big Cabin', 'OK') == (36.54, -95.23)
    assert gazetteer.lookup('Big Cabin', 'TX') is None


def test_first_entry_wins_for_duplicate_names():
    gazetteer = Gazetteer([('IL', 'Springfield city', 39.8, -89.6), ('IL', 'Springfield township', 40.0, -88.0)])
    assert gazetteer.lookup('Springfield', 'IL') == (39.8, -89.6)


def test_bundled_gazetteer_knows_major_cities():
    gazetteer = get_gazetteer()
    lat, lng = gazetteer.lookup('Chicago', 'IL')
    assert lat == pytest.approx(41.84, abs=0.2) and lng == pytest.approx(-87.68, abs=0.2)
    assert gazetteer.lookup('New York', 'NY') is not None
