"""Fixed, read-only diagnostics for infrastructure services."""
from __future__ import annotations
import json, socket, subprocess
from .common import env, request, HomelabError

ALLOWED_CONTAINERS = (
    "timescaledb", "sure-redis", "mosquitto", "homarr", "homeassistant", "frigate",
    "influxdb", "grafana", "qbittorrent", "sonarr", "radarr", "lidarr", "bazarr",
    "prowlarr", "lazylibrarian", "calibre-web", "grocy", "cookcli", "aurboda", "aurboda-db",
    "sure-web", "sure-worker", "nginx",
)

def _run(command, timeout=15):
    p=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise HomelabError("Infrastructure diagnostic failed")
    return p.stdout.strip()

def _containers():
    out=_run(["docker","inspect","--format","{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}",*ALLOWED_CONTAINERS])
    rows=[]
    for line in out.splitlines():
        name,status,health,restarts=(line.lstrip("/").split("|")+["",""])[:4]
        rows.append({"name":name,"status":status,"health":health or None,"restart_count":int(restarts or 0)})
    return rows

def execute(a):
    action=a.get("action","containers")
    if action=="containers": return {"containers":_containers()}
    if action=="timescaledb":
        ready=subprocess.run(["docker","exec","timescaledb","pg_isready","-U","postgres"],capture_output=True,text=True,timeout=10)
        sql="SELECT json_build_object('version',current_setting('server_version'),'connections',(SELECT count(*) FROM pg_stat_activity),'databases',(SELECT json_agg(json_build_object('name',datname,'bytes',pg_database_size(datname))) FROM pg_database WHERE datallowconn));"
        raw=_run(["docker","exec","timescaledb","psql","-U","postgres","-d","postgres","-Atc",sql])
        try: detail=json.loads(raw)
        except ValueError: detail={"version":"unknown"}
        return {"ready":ready.returncode==0,"detail":detail}
    if action=="redis":
        pong=_run(["docker","exec","sure-redis","redis-cli","PING"])
        info=_run(["docker","exec","sure-redis","redis-cli","INFO","memory"])
        selected={}
        for line in info.splitlines():
            if ":" in line and line.split(":",1)[0] in {"used_memory","used_memory_human","used_memory_peak_human","maxmemory_human","mem_fragmentation_ratio"}:
                k,v=line.split(":",1); selected[k]=v.strip()
        return {"ping":pong,"memory":selected}
    if action=="mosquitto":
        host=env("MOSQUITTO_HOST","127.0.0.1"); port=int(env("MOSQUITTO_PORT","1883"))
        reachable=False
        try:
            with socket.create_connection((host,port),timeout=3): reachable=True
        except OSError: pass
        row=next((x for x in _containers() if x["name"]=="mosquitto"),None)
        return {"tcp_reachable":reachable,"container":row}
    if action=="homarr":
        base=env("HOMARR_URL","http://127.0.0.1:7575")
        code,_,_=request(base,"/",timeout=5)
        return {"reachable":200<=code<500,"http_status":code}
    raise ValueError("Unknown infrastructure diagnostic")
