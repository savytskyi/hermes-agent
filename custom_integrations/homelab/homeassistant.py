"""Home Assistant REST client with bounded reads and allowlisted controls."""
from __future__ import annotations
import datetime as dt, json, re, sqlite3
from pathlib import Path
from .common import env, json_request, auth_headers, HomelabError

URL=env("HASS_URL","http://127.0.0.1:8123")
DB=Path(env("HASS_DB_PATH","/Users/ky/homelab/appdata/homeassistant/home-assistant_v2.db"))
ENTITY_RE=re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
CONTROL_DOMAINS={"light","switch","fan","input_boolean","cover","climate"}
SERVICE_MAP={"turn_on":{"light","switch","fan","input_boolean"},"turn_off":{"light","switch","fan","input_boolean"},"toggle":{"light","switch","fan","input_boolean"},"open_cover":{"cover"},"close_cover":{"cover"},"stop_cover":{"cover"},"set_temperature":{"climate"}}

def _token():
    token=env("HASS_TOKEN")
    if not token: raise HomelabError("HASS_TOKEN not configured")
    return token

def _api_states(): return json_request(URL,"/api/states",headers=auth_headers(_token()))

def _local_states():
    if not DB.exists(): raise HomelabError("Home Assistant database unavailable")
    con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=3); con.row_factory=sqlite3.Row
    try:
        sql="""SELECT sm.entity_id,s.state,s.last_changed_ts,s.last_updated_ts,sa.shared_attrs
        FROM states s JOIN states_meta sm ON sm.metadata_id=s.metadata_id
        LEFT JOIN state_attributes sa ON sa.attributes_id=s.attributes_id
        JOIN (SELECT metadata_id,MAX(state_id) state_id FROM states GROUP BY metadata_id) x ON x.state_id=s.state_id
        ORDER BY sm.entity_id"""
        out=[]
        for r in con.execute(sql):
            try: attrs=json.loads(r["shared_attrs"] or "{}")
            except Exception: attrs={}
            out.append({"entity_id":r["entity_id"],"state":r["state"],"attributes":attrs,"last_changed_ts":r["last_changed_ts"],"last_updated_ts":r["last_updated_ts"]})
        return out
    finally: con.close()

def _states():
    try: return _api_states(),"rest_api"
    except HomelabError: return _local_states(),"local_readonly_database"

def _timestamp(value):
    if isinstance(value,(int,float)): return dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat().replace("+00:00","Z")
    return value

def _compact(s):
    if not s: return None
    a=s.get("attributes") or {}
    keep=("friendly_name","unit_of_measurement","device_class","state_class","temperature","current_temperature","target_temp_high","target_temp_low","humidity","pressure","wind_speed","wind_bearing","condition","precipitation","precipitation_unit","forecast","brightness","color_temp_kelvin","percentage","current_position")
    return {"entity_id":s.get("entity_id"),"state":s.get("state"),"attributes":{k:a[k] for k in keep if k in a},"last_changed":_timestamp(s.get("last_changed") or s.get("last_changed_ts")),"last_updated":_timestamp(s.get("last_updated") or s.get("last_updated_ts"))}

def _entity(value):
    entity=(value or "").lower()
    if not ENTITY_RE.fullmatch(entity): raise ValueError("Invalid entity_id")
    return entity

def _history(a):
    entity=_entity(a.get("entity_id")); start=a.get("start"); end=a.get("end")
    if not start: raise ValueError("start is required")
    try:
        s=dt.datetime.fromisoformat(str(start).replace("Z","+00:00")); e=dt.datetime.fromisoformat(str(end).replace("Z","+00:00")) if end else dt.datetime.now(dt.timezone.utc)
    except ValueError: raise ValueError("start/end must be RFC3339 timestamps") from None
    if s.tzinfo is None or e.tzinfo is None or e<=s or e-s>dt.timedelta(days=366): raise ValueError("History window must be timezone-aware, positive, and at most 366 days")
    raw=json_request(URL,"/api/history/period/"+s.isoformat(),headers=auth_headers(_token()),params={"filter_entity_id":entity,"end_time":e.isoformat(),"minimal_response":"","no_attributes":""})
    rows=raw[0] if isinstance(raw,list) and raw else []; limit=min(max(int(a.get("limit",500)),1),2000)
    return {"entity_id":entity,"start":s.isoformat(),"end":e.isoformat(),"count":min(len(rows),limit),"truncated":len(rows)>limit,"states":[_compact(x) for x in rows[:limit]]}

def _allowlist(): return {x.strip().lower() for x in env("HASS_CONTROL_ALLOWLIST").split(",") if x.strip()}

def _control(a):
    entity=_entity(a.get("entity_id")); domain=entity.split(".",1)[0]; service=a.get("service")
    if entity not in _allowlist(): raise HomelabError("Entity is not in the Home Assistant control allowlist")
    if domain not in CONTROL_DOMAINS or service not in SERVICE_MAP or domain not in SERVICE_MAP[service]: raise HomelabError("Requested Home Assistant action is not allowlisted")
    preview={"entity_id":entity,"domain":domain,"service":service}
    if service=="set_temperature":
        temperature=float(a.get("temperature"))
        if not 5<=temperature<=35: raise ValueError("temperature must be between 5 and 35 C")
        preview["temperature"]=temperature
    if a.get("confirm") is not True: return {"status":"confirmation_required","write_performed":False,"action":preview}
    headers=auth_headers(_token()); headers["Content-Type"]="application/json"
    payload={"entity_id":entity}
    if "temperature" in preview: payload["temperature"]=preview["temperature"]
    out=json_request(URL,f"/api/services/{domain}/{service}",method="POST",headers=headers,data=json.dumps(payload).encode())
    state=json_request(URL,"/api/states/"+entity,headers=auth_headers(_token()))
    return {"write_performed":True,"action":preview,"state":_compact(state),"response_entities":len(out) if isinstance(out,list) else None}

def execute(args):
    action=args.get("action","search")
    if action=="history": return _history(args)
    if action=="control": return _control(args)
    states,source=_states(); limit=min(int(args.get("limit",100)),200)
    if action=="get":
        entity=_entity(args.get("entity_id")); match=next((s for s in states if s.get("entity_id")==entity),None)
        return {"source":source,"entity":_compact(match)} if match else {"source":source,"entity":None}
    q=(args.get("query") or "").lower(); domain=(args.get("domain") or "").lower()
    if action=="weather": domain="weather"
    if action=="environment" and not q: q="temperature humidity rain precipitation weather"
    terms=[t for t in re.split(r"\s+",q) if t]; selected=[]
    for s in states:
        eid=s.get("entity_id",""); attrs=s.get("attributes") or {}; text=" ".join((eid,str(attrs.get("friendly_name","")),str(attrs.get("device_class","")))).lower()
        if domain and not eid.startswith(domain+"."): continue
        if terms and not any(t in text for t in terms): continue
        if action=="environment" and not any(k in text for k in ("temp","humid","rain","precip","weather")): continue
        selected.append(_compact(s))
    return {"source":source,"count":len(selected),"entities":selected[:limit],"read_only":True}
