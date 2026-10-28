"""Concise aggregate health check with no secret-bearing output."""
import json, os, shutil, subprocess
from pathlib import Path
from .common import env, request
from . import arr
SERVICES={
    "Home Assistant":(env("HASS_URL","http://127.0.0.1:8123"),"/"),
    "Frigate":(env("FRIGATE_URL","http://127.0.0.1:5001"),"/api/version"),
    "InfluxDB":(env("INFLUXDB_URL","http://127.0.0.1:8086"),"/health"),
    "Grafana":(env("GRAFANA_URL","http://127.0.0.1:3000"),"/api/health"),
    "Plex":(env("PLEX_URL","http://127.0.0.1:32400"),"/identity"),
    "qBittorrent":(env("QBITTORRENT_URL","http://127.0.0.1:8080"),"/api/v2/app/version"),
    "Lidarr":(env("LIDARR_URL","http://127.0.0.1:8686"),"/"),
    "Bazarr":(env("BAZARR_URL","http://127.0.0.1:6767"),"/"),
    "Prowlarr":(env("PROWLARR_URL","http://127.0.0.1:9696"),"/"),
    "LazyLibrarian":(env("LAZYLIBRARIAN_URL","http://127.0.0.1:5299"),"/"),
    "Calibre-Web":(env("CALIBRE_WEB_URL","http://127.0.0.1:8083"),"/"),
    "Homarr":(env("HOMARR_URL","http://127.0.0.1:7575"),"/"),
    "Aurboda":(env("AURBODA_URL","http://127.0.0.1:9081"),"/api/status"),
    "Sure":(env("SURE_API_URL","http://127.0.0.1:3030"),"/up"),
    "Grocy":(env("GROCY_API_URL","http://127.0.0.1:9283"),"/"),
    "Cooklang":(env("COOKLANG_URL","http://192.168.68.56:9080"),"/"),
}
CONTAINERS=("homeassistant","frigate","influxdb","grafana","qbittorrent","sonarr","radarr","lidarr","bazarr","prowlarr","lazylibrarian","calibre-web","homarr","aurboda","aurboda-db","sure-web","sure-worker","sure-redis","grocy","cookcli","timescaledb","mosquitto","nginx")
def execute(_):
    checks={}
    for name,(base,path) in SERVICES.items():
        try: code,_,_=request(base,path,timeout=4); checks[name]={"ok":200<=code<400}
        except Exception: checks[name]={"ok":False}
    for service,label in (("sonarr","Sonarr"),("radarr","Radarr")):
        try:
            status=arr._get(service,"system/status")
            warnings=arr._get(service,"health")
            checks[label]={"ok":True,"version":status.get("version"),"health_warnings":len(warnings)}
        except Exception: checks[label]={"ok":False}
    storage=Path(env("HOMELAB_STORAGE","/Volumes/STORAGE"))
    mounted=storage.exists() and os.path.ismount(storage)
    if mounted:
        usage=shutil.disk_usage(storage); checks["Storage"]={"ok":True,"mounted":True,"free_bytes":usage.free,"free_tb":round(usage.free/10**12,2)}
    else: checks["Storage"]={"ok":False,"mounted":False}
    containers={name:{"running":False,"status":"missing","health":""} for name in CONTAINERS}
    docker_ok=False
    try:
        p=subprocess.run(["docker","inspect","--format","{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",*CONTAINERS],capture_output=True,text=True,timeout=8)
        for line in p.stdout.splitlines():
            parts=line.lstrip("/").split("|")
            if len(parts)<2: continue
            name,status=parts[:2]; health=parts[2] if len(parts)>2 else ""
            if name in containers: containers[name]={"running":status=="running","status":status,"health":health}
        docker_ok=all(x["running"] and x["health"]!="unhealthy" for x in containers.values())
    except Exception: pass
    services_ok=all(x.get("ok") for x in checks.values())
    return {"all_services_ok":services_ok and docker_ok,"all_endpoints_ok":services_ok,"docker_ok":docker_ok,"has_application_warnings":any(x.get("health_warnings",0)>0 for x in checks.values()),"services":checks,"containers":containers}
