#!/usr/bin/env python3
"""Secret-safe read-only smoke test for the r1 homelab plugin."""
import json, os, re, sqlite3, sys
from pathlib import Path

for line in Path('/Users/ky/.hermes/.env').read_text().splitlines():
    m=re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$',line)
    if m: os.environ.setdefault(m.group(1),m.group(2))
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from homelab import homeassistant, influxdb, grafana, plex, qbittorrent, arr, servarr, books, sure, grocy, infrastructure, health, frigate

def probe(name, fn):
    try:
        value=fn()
        if isinstance(value,dict):
            summary={k:value[k] for k in ('count','version','reachable','ready','ping','all_services_ok','all_endpoints_ok','docker_ok') if k in value}
            for k in ('accounts','books','items','entities','containers','transactions'):
                if isinstance(value.get(k),list): summary[k+'_count']=len(value[k])
        elif isinstance(value,list): summary={'count':len(value)}
        else: summary={'type':type(value).__name__}
        return {'ok':True,'summary':summary}
    except Exception as exc:
        return {'ok':False,'error_type':type(exc).__name__,'error':str(exc)[:180]}

con=sqlite3.connect('file:/Users/ky/.hermes/state.db?mode=ro',uri=True)
row=con.execute("select id from sessions where source='cli' order by last_activity_at desc limit 1").fetchone(); con.close()
cli_session=row[0] if row else None
checks={
 'homeassistant':lambda:homeassistant.execute({'action':'search','domain':'sensor','limit':3}),
 'homeassistant_history':lambda:homeassistant.execute({'action':'history','entity_id':homeassistant.execute({'action':'search','domain':'sensor','limit':1})['entities'][0]['entity_id'],'start':'2026-10-27T00:00:00+01:00','end':'2026-10-28T00:00:00+01:00','limit':3}),
 'influxdb':lambda:influxdb.execute({'action':'metadata','limit':3}),
 'grafana':lambda:grafana.execute({'action':'health'}),
 'plex':lambda:plex.execute({'action':'status'}),
 'qbittorrent':lambda:qbittorrent.execute({'action':'transfer'}),
 'sonarr_radarr':lambda:arr.execute_read({'service':'both','action':'health','limit':5}),
 'lidarr':lambda:servarr.lidarr_read({'action':'health'}),
 'bazarr':lambda:servarr.bazarr_read({'action':'status'}),
 'prowlarr':lambda:servarr.prowlarr_read({'action':'health'}),
 'lazylibrarian':lambda:books.lazylibrarian({'action':'status'}),
 'calibre':lambda:books.calibre({'action':'list','limit':3}),
 'sure':lambda:sure.execute({'action':'accounts','limit':5}),
 'grocy':lambda:grocy.execute({'action':'health'}),
 'timescaledb':lambda:infrastructure.execute({'action':'timescaledb'}),
 'redis':lambda:infrastructure.execute({'action':'redis'}),
 'mosquitto':lambda:infrastructure.execute({'action':'mosquitto'}),
 'homarr':lambda:infrastructure.execute({'action':'homarr'}),
 'containers':lambda:infrastructure.execute({'action':'containers'}),
 'frigate':lambda:frigate.execute({'action':'status'},session_id=cli_session),
 'aggregate_health':lambda:health.execute({}),
}
print(json.dumps({name:probe(name,fn) for name,fn in checks.items()},indent=2,default=str))
