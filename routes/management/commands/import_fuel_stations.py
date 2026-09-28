"""Import the fuel prices CSV into FuelStation, geocoding each station by city/state.

Cleaning rules (the source file is left untouched):
  * rows outside the 50 US states + DC (e.g. Canadian provinces) are skipped;
  * duplicate OPIS IDs are collapsed, keeping the lowest price;
  * stations are geocoded from the offline Census gazetteer; places it doesn't
    know are geocoded once via OpenRouteService and cached in
    routes/data/geocode_cache.json (committed), so re-imports make no API calls.

The import is an idempotent upsert keyed on OPIS ID.
"""
import csv
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routes.models import FuelStation
from routes.services.geocoding import DATA_DIR, US_STATES, get_gazetteer
from routes.services.ors_client import ORSClient, ORSError, ORSRateLimitError
from routes.services.stations import get_station_index

DEFAULT_CSV = Path(settings.BASE_DIR) / 'fuel-prices-for-be-assessment.csv'
GEOCODE_CACHE_PATH = DATA_DIR / 'geocode_cache.json'
ORS_GEOCODE_DELAY_SECONDS = 1.0  # stay safely under the ORS free-tier geocode rate limit


class Command(BaseCommand):
    help = 'Load fuel stations from the fuel prices CSV and geocode them.'

    def add_arguments(self, parser):
        parser.add_argument('--csv', dest='csv_path', default=str(DEFAULT_CSV), help='Path to the fuel prices CSV.')
        parser.add_argument('--offline', action='store_true',
                            help='Do not call OpenRouteService for places missing from the gazetteer/cache.')

    def handle(self, *args, csv_path, offline, **options):
        stations, stats = self._read_csv(Path(csv_path))
        coords = self._geocode(stations, offline, stats)

        with transaction.atomic():
            FuelStation.objects.bulk_create(
                self._build_models(stations, coords),
                update_conflicts=True,
                unique_fields=['opis_id'],
                update_fields=['name', 'address', 'city', 'state', 'rack_id', 'price',
                               'latitude', 'longitude', 'geocode_source'],
                batch_size=1000,
            )
        get_station_index.cache_clear()

        geocoded = sum(1 for s in stations.values() if coords.get((s['state'], s['city'])))
        self.stdout.write(self.style.SUCCESS(
            f"Rows read: {stats['rows']}\n"
            f"Skipped (non-US): {stats['non_us']}\n"
            f"Skipped (invalid): {stats['invalid']}\n"
            f"Duplicate OPIS IDs merged: {stats['duplicates']}\n"
            f"Stations imported: {len(stations)}\n"
            f"  geocoded via gazetteer: {stats['gazetteer']}\n"
            f"  geocoded via ORS/cache: {stats['ors']}\n"
            f"  not geocoded (excluded from routing): {len(stations) - geocoded}"
        ))

    def _read_csv(self, path):
        if not path.exists():
            raise CommandError(f'CSV not found: {path}')
        stats = {'rows': 0, 'non_us': 0, 'invalid': 0, 'duplicates': 0, 'gazetteer': 0, 'ors': 0}
        stations = {}
        with open(path, newline='', encoding='utf-8-sig') as fh:
            for row in csv.DictReader(fh):
                stats['rows'] += 1
                state = row['State'].strip().upper()
                if state not in US_STATES:
                    stats['non_us'] += 1
                    continue
                try:
                    opis_id = int(row['OPIS Truckstop ID'])
                    price = Decimal(row['Retail Price'].strip())
                except (ValueError, InvalidOperation):
                    stats['invalid'] += 1
                    continue
                if price <= 0:
                    stats['invalid'] += 1
                    continue

                existing = stations.get(opis_id)
                if existing:
                    stats['duplicates'] += 1
                    if price >= existing['price']:
                        continue
                rack_id = row['Rack ID'].strip()
                stations[opis_id] = {
                    'opis_id': opis_id,
                    'name': row['Truckstop Name'].strip(),
                    'address': row['Address'].strip(),
                    'city': row['City'].strip(),
                    'state': state,
                    'rack_id': int(rack_id) if rack_id.isdigit() else None,
                    'price': price,
                }
        return stations, stats

    def _geocode(self, stations, offline, stats):
        """Map (state, city) -> (lat, lng, source) for every distinct place."""
        gazetteer = get_gazetteer()
        cache = self._load_cache()
        client = None
        coords = {}
        places = sorted({(s['state'], s['city']) for s in stations.values()})

        for state, city in places:
            hit = gazetteer.lookup(city, state)
            if hit:
                coords[(state, city)] = (*hit, FuelStation.GeocodeSource.GAZETTEER)
                continue

            cache_key = f'{state}|{city}'
            if cache_key not in cache and not offline:
                try:
                    client = client or ORSClient()
                    cache[cache_key] = client.geocode_us_locality(city, state)
                    time.sleep(ORS_GEOCODE_DELAY_SECONDS)
                except ORSRateLimitError:
                    offline = True
                    self.stderr.write(self.style.WARNING(
                        'ORS rate limit reached; remaining places are skipped. '
                        'Re-run the command later to geocode them (successful lookups are cached).'
                    ))
                except ORSError as exc:
                    self.stderr.write(f'  ORS geocode failed for {city}, {state}: {exc}')
            if cache.get(cache_key):
                coords[(state, city)] = (*cache[cache_key], FuelStation.GeocodeSource.ORS)

        self._save_cache(cache)
        for station in stations.values():
            hit = coords.get((station['state'], station['city']))
            if hit:
                stats['gazetteer' if hit[2] == FuelStation.GeocodeSource.GAZETTEER else 'ors'] += 1
        return coords

    @staticmethod
    def _build_models(stations, coords):
        for s in stations.values():
            lat, lng, source = coords.get((s['state'], s['city']), (None, None, ''))
            yield FuelStation(**s, latitude=lat, longitude=lng, geocode_source=source)

    @staticmethod
    def _load_cache():
        if GEOCODE_CACHE_PATH.exists():
            return json.loads(GEOCODE_CACHE_PATH.read_text(encoding='utf-8'))
        return {}

    @staticmethod
    def _save_cache(cache):
        GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding='utf-8')
