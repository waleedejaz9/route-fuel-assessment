"""Regenerate routes/data/us_places.csv from the US Census Gazetteer files.

The output is committed to the repo; run this only to refresh it.
"""
import csv
import io
import zipfile

import requests
from django.core.management.base import BaseCommand

from routes.services.geocoding import GAZETTEER_PATH, US_STATES

CENSUS_URL = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{year}_Gazetteer/{year}_Gaz_{kind}_national.zip'
# Incorporated places/CDPs first so they take precedence over county subdivisions of the same name.
KINDS = ['place', 'cousubs']


class Command(BaseCommand):
    help = 'Download the US Census Gazetteer and write a compact US places CSV.'

    def add_arguments(self, parser):
        parser.add_argument('--year', default='2024')

    def handle(self, *args, year, **options):
        rows = []
        for kind in KINDS:
            url = CENSUS_URL.format(year=year, kind=kind)
            self.stdout.write(f'Downloading {url}')
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                text = archive.read(archive.namelist()[0]).decode('latin-1')
            for record in csv.DictReader(io.StringIO(text), delimiter='\t'):
                record = {k.strip(): v.strip() for k, v in record.items()}
                if record['USPS'] in US_STATES:
                    rows.append((record['USPS'], record['NAME'], record['INTPTLAT'], record['INTPTLONG']))

        GAZETTEER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(GAZETTEER_PATH, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerow(['state', 'name', 'lat', 'lng'])
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f'Wrote {len(rows)} places to {GAZETTEER_PATH}'))
