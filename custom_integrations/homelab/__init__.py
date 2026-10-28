"""Hermes homelab plugin registration."""
from pathlib import Path
from . import schemas, tools

_TOOLS = [
    ("homelab_ha", schemas.HA, tools.ha),
    ("homelab_influx", schemas.INFLUX, tools.influx),
    ("homelab_grafana", schemas.GRAFANA, tools.grafana),
    ("homelab_plex", schemas.PLEX, tools.plex),
    ("homelab_qbittorrent", schemas.QBITTORRENT, tools.qbittorrent),
    ("homelab_arr", schemas.ARR, tools.arr),
    ("homelab_radarr_add_movie", schemas.RADARR_ADD, tools.radarr_add),
    ("homelab_sonarr_add_series", schemas.SONARR_ADD, tools.sonarr_add),
    ("homelab_health", schemas.HEALTH, tools.health),
    ("homelab_frigate", schemas.FRIGATE, tools.frigate),
    ("homelab_lazylibrarian", schemas.LAZYLIBRARIAN, tools.lazylibrarian),
    ("homelab_calibre", schemas.CALIBRE, tools.calibre),
    ("homelab_lidarr", schemas.LIDARR, tools.lidarr),
    ("homelab_lidarr_add_artist", schemas.LIDARR_ADD, tools.lidarr_add),
    ("homelab_bazarr", schemas.BAZARR, tools.bazarr),
    ("homelab_prowlarr", schemas.PROWLARR, tools.prowlarr),
    ("homelab_sure", schemas.SURE, tools.sure),
    ("homelab_grocy", schemas.GROCY, tools.grocy),
    ("homelab_infrastructure", schemas.INFRASTRUCTURE, tools.infra),
]

def register(ctx):
    for name, schema, handler in _TOOLS:
        ctx.register_tool(name=name, toolset="homelab", schema=schema, handler=handler)
    skill = Path(__file__).parent / "skills" / "homelab-operations" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("homelab-operations", skill)
