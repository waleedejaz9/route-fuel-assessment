"""Load in-memory lookup data at server start so the first request isn't slower."""
import logging
import threading

from .geocoding import get_gazetteer
from .stations import get_station_index

logger = logging.getLogger(__name__)


def _warm():
    try:
        get_gazetteer()
        get_station_index()
    except Exception:  # e.g. migrations not applied yet; loading will retry on first request
        logger.warning('Cache warm-up failed; data will load on first request.', exc_info=True)


def warm_caches_in_background():
    threading.Thread(target=_warm, name='cache-warmup', daemon=True).start()
