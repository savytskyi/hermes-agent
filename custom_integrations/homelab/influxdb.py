"""Bounded, generated-only Flux queries for InfluxDB 2."""
from __future__ import annotations
import csv, io, json, re
from .common import env, request, json_request, auth_headers, HomelabError
URL=env("INFLUXDB_URL","http://127.0.0.1:8086")
ORG=env("INFLUXDB_ORG")
BUCKET=env("INFLUXDB_BUCKET")
TOKEN=env("INFLUXDB_TOKEN")
SAFE_NAME=re.compile(r"^[A-Za-z0-9_.:/ -]{1,200}$")
SAFE_REL=re.compile(r"^-\d+(?:mo|[smhdwy])$")
SAFE_TIME=re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)$")
GROUP={"hour":"1h","day":"1d","week":"1w","month":"1mo"}
TZ_PREFIX='import "timezone"\noption location = timezone.location(name: "Europe/Ljubljana")\n'

def _q(s): return json.dumps(s)
def _time(s,default):
    s=s or default
    if s=="now()": return s
    if SAFE_REL.fullmatch(s): return s
    if SAFE_TIME.fullmatch(s): return f'time(v: {_q(s)})'
    raise ValueError("Time must be RFC3339 or a relative duration such as -30d")
def _name(s,label):
    if not s or not SAFE_NAME.fullmatch(s): raise ValueError(f"Invalid {label}")
    return s

def _parse_csv(text):
    """Parse Influx CSV with annotations and repeated headers between tables."""
    rows=[]; header=None
    for values in csv.reader(io.StringIO(text)):
        if not values or (values[0] and values[0].startswith("#")):
            continue
        if "_value" in values or ("error" in values and "reference" in values):
            header=values
            continue
        if header is None:
            continue
        if len(values)<len(header): values += [""]*(len(header)-len(values))
        row=dict(zip(header,values))
        if row.get("error"): raise HomelabError("InfluxDB query failed")
        clean={k:v for k,v in row.items() if k and (not k.startswith("_") or k in ("_time","_value","_field","_measurement"))}
        if clean: rows.append(clean)
    return rows

def query(flux):
    if not (TOKEN and ORG and BUCKET): raise HomelabError("InfluxDB credentials are not configured")
    body=json.dumps({"query":flux,"type":"flux"}).encode()
    headers=auth_headers(TOKEN); headers.update({"Content-Type":"application/json","Accept":"text/csv"})
    _,raw,_=request(URL,"/api/v2/query",method="POST",headers=headers,params={"org":ORG},data=body,timeout=20)
    return _parse_csv(raw.decode("utf-8","replace"))

def _base(sensor,field,start,stop):
    sensor=_name(sensor,"sensor"); field=_name(field or "value","field")
    entity=sensor.split(".",1)[1] if sensor.startswith("sensor.") else sensor
    # Home Assistant commonly stores unit as measurement and entity_id as a tag.
    return (f'from(bucket: {_q(BUCKET)}) |> range(start: {_time(start,"-30d")}, stop: {_time(stop,"now()")}) '
            f'|> filter(fn: (r) => (r._measurement == {_q(sensor)} or r.entity_id == {_q(sensor)} or r.entity_id == {_q(entity)}) and r._field == {_q(field)})')
def _aggregate(sensor,field,start,stop,stat,group=None,limit=1000):
    base=_base(sensor,field,start,stop)+' |> group(columns: [])'; stat=stat or "mean"
    if stat=="range":
        mn=query(base+' |> min()'); mx=query(base+' |> max()')
        return {"min":mn[:1],"max":mx[:1]}
    funcs={"max":"max","min":"min","mean":"mean","sum":"sum","count":"count","last":"last"}
    if stat not in funcs: raise ValueError("Unsupported statistic")
    if group:
        every=GROUP.get(group)
        if not every: raise ValueError("Unsupported group_by")
        flux=TZ_PREFIX+base+f' |> aggregateWindow(every: {every}, fn: {funcs[stat]}, createEmpty: false, timeSrc: "_start") |> limit(n: {min(limit,1000)})'
    else: flux=base+f' |> {funcs[stat]}()'
    return query(flux)
def execute(a):
    action=a.get("action"); limit=min(int(a.get("limit",500)),1000)
    if action=="metadata":
        measurements=query(f'import "influxdata/influxdb/schema"\nschema.measurements(bucket: {_q(BUCKET)}, start: -10y) |> limit(n: 500)')
        fields=query(f'import "influxdata/influxdb/schema"\nschema.fieldKeys(bucket: {_q(BUCKET)}, start: -10y) |> limit(n: 500)')
        entities=query(f'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: {_q(BUCKET)}, tag: "entity_id", start: -10y) |> limit(n: 1000)')
        buckets=json_request(URL,"/api/v2/buckets",headers=auth_headers(TOKEN),params={"name":BUCKET})
        bucket_info=next((b for b in buckets.get("buckets",[]) if b.get("name")==BUCKET),{})
        rules=bucket_info.get("retentionRules",[])
        return {"organization":ORG,"bucket":BUCKET,"retention_rules":[{"type":r.get("type"),"every_seconds":r.get("everySeconds")} for r in rules],"measurements":[r.get("_value") for r in measurements if r.get("_value")],"fields":[r.get("_value") for r in fields if r.get("_value")],"entities":[r.get("_value") for r in entities if r.get("_value")],"read_only":True}
    sensor=a.get("sensor",""); field=a.get("field","value")
    if action=="data_range":
        base=_base(sensor,field,a.get("start") or "-520w",a.get("stop"))+' |> group(columns: [])'
        return {"sensor":sensor,"first":query(base+' |> first()'),"last":query(base+' |> last()')}
    if action in ("aggregate","series"):
        stat=a.get("statistic") or ("mean" if action=="series" else "mean")
        return {"sensor":sensor,"statistic":stat,"rows":_aggregate(sensor,field,a.get("start"),a.get("stop"),stat,a.get("group_by") if action=="series" else a.get("group_by"),limit)}
    if action=="last_rain":
        base=_base(sensor or "sensor.hourly_rain",field,a.get("start") or "-520w",a.get("stop"))+' |> group(columns: [])'
        return {"sensor":sensor or "sensor.hourly_rain","rows":query(base+' |> filter(fn: (r) => r._value > 0) |> last()')}
    if action=="rain_totals":
        base=_base(sensor or "daily_rain",field,a.get("start"),a.get("stop"))+' |> group(columns: [])'
        group=a.get("group_by")
        daily=TZ_PREFIX+base+' |> aggregateWindow(every: 1d, fn: max, createEmpty: false, timeSrc: "_start")'
        if group=="day": flux=daily+f' |> limit(n: {limit})'
        elif group in ("week","month"):
            flux=daily+f' |> aggregateWindow(every: {GROUP[group]}, fn: sum, createEmpty: false, timeSrc: "_start") |> limit(n: {limit})'
        else: flux=daily+' |> sum()'
        return {"sensor":sensor or "daily_rain","method":"daily maximum of cumulative daily-rain sensor, then sum","rows":query(flux)}
    if action=="compare":
        stat=a.get("statistic","mean")
        return {"sensor":sensor,"statistic":stat,"period_a":_aggregate(sensor,field,a.get("start"),a.get("stop"),stat,None,limit),"period_b":_aggregate(sensor,field,a.get("start_b"),a.get("stop_b"),stat,None,limit)}
    raise ValueError("Unknown InfluxDB action")
