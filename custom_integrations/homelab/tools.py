"""Thin JSON-returning Hermes handlers; implementation lives in service modules."""
import logging
from .common import result, HomelabError
from . import homeassistant, influxdb, grafana as grafana_service, plex as plex_service
from . import qbittorrent as qbit_service, arr as arr_service, health as health_service
from . import frigate as frigate_service, books, servarr, sure as sure_service
from . import grocy as grocy_service, infrastructure
log = logging.getLogger(__name__)

def _run(fn, args, op):
    try: return result(fn(args))
    except Exception as exc:
        log.warning("Homelab %s failed: %s", op, type(exc).__name__)
        if isinstance(exc,(ValueError,HomelabError)):
            return result(error=f"{op} failed: {exc}")
        # Unknown exception text is omitted because libraries can embed URLs/tokens.
        return result(error=f"{op} failed ({type(exc).__name__})")

def ha(args, **_): return _run(homeassistant.execute, args, "Home Assistant query")
def influx(args, **_): return _run(influxdb.execute, args, "InfluxDB query")
def grafana(args, **_): return _run(grafana_service.execute, args, "Grafana query")
def plex(args, **_): return _run(plex_service.execute, args, "Plex query")
def qbittorrent(args, **_): return _run(qbit_service.execute, args, "qBittorrent query")
def arr(args, **_): return _run(arr_service.execute_read, args, "Sonarr/Radarr query")
def radarr_add(args, **_): return _run(arr_service.add_movie, args, "Radarr add")
def sonarr_add(args, **_): return _run(arr_service.add_series, args, "Sonarr add")
def health(args, **_): return _run(health_service.execute, args, "homelab health check")
def frigate(args, session_id=None, **_):
    return _run(lambda a: frigate_service.execute(a, session_id=session_id), args, "Frigate query")
def lazylibrarian(args, **_): return _run(books.lazylibrarian, args, "LazyLibrarian query")
def calibre(args, **_): return _run(books.calibre, args, "Calibre operation")
def lidarr(args, **_): return _run(servarr.lidarr_read, args, "Lidarr query")
def lidarr_add(args, **_): return _run(servarr.lidarr_add, args, "Lidarr add")
def bazarr(args, **_): return _run(servarr.bazarr_read, args, "Bazarr query")
def prowlarr(args, **_): return _run(servarr.prowlarr_read, args, "Prowlarr query")
def sure(args, **_): return _run(sure_service.execute, args, "Sure operation")
def grocy(args, **_): return _run(grocy_service.execute, args, "Grocy operation")
def infra(args, **_): return _run(infrastructure.execute, args, "infrastructure diagnostic")
