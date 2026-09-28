"""In-memory snapshot of geocoded fuel stations, loaded once per process.

~6.5k rows fit easily in memory; holding them as a NumPy array avoids a DB
query per request. Call `get_station_index.cache_clear()` after re-importing.
"""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from routes.models import FuelStation


@dataclass(frozen=True)
class StationIndex:
    coords: np.ndarray   # (N, 2) lat/lng
    stations: tuple      # N dicts, aligned with coords


@lru_cache(maxsize=1)
def get_station_index():
    rows = FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False).values(
        'id', 'opis_id', 'name', 'address', 'city', 'state', 'price', 'latitude', 'longitude',
    )
    stations = tuple(rows)
    coords = np.array([(s['latitude'], s['longitude']) for s in stations], dtype=float).reshape(-1, 2)
    return StationIndex(coords=coords, stations=stations)
