"""Sonarr/Radarr read APIs plus two narrowly scoped add/search actions."""
from __future__ import annotations
import datetime as dt, json, re
from .common import env, json_request, request, auth_headers, HomelabError
CFG={
 "sonarr":(env("SONARR_URL","http://127.0.0.1:8989"),env("SONARR_API_KEY")),
 "radarr":(env("RADARR_URL","http://127.0.0.1:7878"),env("RADARR_API_KEY")),
}
def _headers(service):
    key=CFG[service][1]
    if not key: raise HomelabError(f"{service} API key is not configured")
    return auth_headers(key,"X-Api-Key","")
def _get(service,path,params=None): return json_request(CFG[service][0],"/api/v3/"+path,headers=_headers(service),params=params)
def _post(service,path,obj):
    h=_headers(service); h["Content-Type"]="application/json"
    return json_request(CFG[service][0],"/api/v3/"+path,method="POST",headers=h,data=json.dumps(obj).encode())
def _compact(service,action,x):
    if action=="queue": return {k:x.get(k) for k in ("id","title","status","trackedDownloadStatus","trackedDownloadState","size","sizeleft","timeleft","estimatedCompletionTime","errorMessage","seriesId","movieId","episodeId","downloadId")}
    if action=="health": return {k:x.get(k) for k in ("source","type","message","wikiUrl")}
    if action=="history": return {k:x.get(k) for k in ("id","eventType","date","sourceTitle","seriesId","movieId","episodeId","quality")}
    if action=="upcoming": return {k:x.get(k) for k in ("id","title","airDateUtc","digitalRelease","physicalRelease","inCinemas","hasFile","monitored","seriesId","movieId")}
    return x

def _paged(service,path,params=None,max_records=1000):
    base=dict(params or {}); rows=[]; page=1
    while len(rows)<max_records:
        p=dict(base); p.update({"page":page,"pageSize":min(200,max_records-len(rows))})
        raw=_get(service,path,p); batch=raw.get("records",[]); rows.extend(batch)
        total=int(raw.get("totalRecords",len(rows)))
        if not batch or len(rows)>=total: break
        page+=1
    return rows

def _read_one(service,action,a):
    limit=min(int(a.get("limit",100)),200); q=(a.get("query") or "").lower()
    if action=="queue": rows=_paged(service,"queue",{"includeUnknownMovieItems":"true","includeUnknownSeriesItems":"true"})
    elif action=="health": rows=_get(service,"health")
    elif action=="history": rows=_paged(service,"history",{"sortKey":"date","sortDirection":"descending"})
    elif action=="wanted":
        path="wanted/missing"; rows=_paged(service,path,{"sortKey":"airDateUtc" if service=="sonarr" else "releaseDate","sortDirection":"descending","includeSeries":"true"})
    elif action=="upcoming":
        now=dt.datetime.now(dt.timezone.utc); end=now+dt.timedelta(days=min(int(a.get("days",30)),365)); path="calendar"
        rows=_get(service,path,{"start":now.isoformat(),"end":end.isoformat(),"includeSeries":"true"})
    elif action=="library": rows=_get(service,"series" if service=="sonarr" else "movie")
    else: raise ValueError("Unknown Arr action")
    if q: rows=[x for x in rows if q in json.dumps(x,default=str).lower()]
    return [_compact(service,action,x) for x in rows[:limit]]
def execute_read(a):
    services=list(CFG) if a.get("service")=="both" else [a.get("service")]
    result={}
    for service in services:
        try:
            items=_read_one(service,a.get("action"),a)
            result[service]={"items":items,"count":len(items)}
        except Exception as exc:
            if len(services)==1: raise
            result[service]={"error":f"query failed ({type(exc).__name__})"}
    return result

def _choose(results,title,year,id_key,id_value=None):
    if id_value is not None:
        hit=next((x for x in results if int(x.get(id_key,0))==int(id_value)),None)
        if not hit: raise HomelabError(f"Requested {id_key} was not found in lookup results")
        return hit
    norm=re.sub(r"[^a-z0-9]+","",title.lower())
    candidates=[x for x in results if re.sub(r"[^a-z0-9]+","",x.get("title","").lower())==norm]
    if year: candidates=[x for x in candidates if x.get("year")==year]
    if len(candidates)!=1: raise HomelabError("Lookup was ambiguous or found no exact title; specify year or provider ID")
    return candidates[0]
def _root_quality(service):
    roots=_get(service,"rootfolder"); profiles=_get(service,"qualityprofile")
    if not roots or not profiles: raise HomelabError("No root folder or quality profile configured")
    return roots[0]["path"],profiles[0]["id"]
def _preview(results,id_key):
    return [{"title":x.get("title"),"year":x.get("year"),id_key:x.get(id_key),"status":x.get("status")} for x in results[:5]]
def _existing(service,id_key,id_value):
    path="series" if service=="sonarr" else "movie"
    return next((x for x in _get(service,path) if x.get(id_key)==id_value),None)
def _search_now(a):
    value=a.get("search_now",True)
    if not isinstance(value,bool): raise ValueError("search_now must be a boolean")
    return value
def add_movie(a):
    results=_get("radarr","movie/lookup",{"term":a["title"]})
    if a.get("confirm") is not True: return {"status":"confirmation_required","write_performed":False,"candidates":_preview(results,"tmdbId")}
    search_now=_search_now(a)
    item=_choose(results,a["title"],a.get("year"),"tmdbId",a.get("tmdb_id"))
    existing=_existing("radarr","tmdbId",item.get("tmdbId"))
    if existing: return {"added":False,"already_exists":True,"id":existing.get("id"),"title":existing.get("title"),"year":existing.get("year")}
    root,quality=_root_quality("radarr")
    payload={k:v for k,v in item.items() if k not in ("id","movieFile")}; payload.update({"qualityProfileId":quality,"rootFolderPath":root,"monitored":True,"addOptions":{"searchForMovie":search_now}})
    out=_post("radarr","movie",payload); return {"added":True,"id":out.get("id"),"title":out.get("title"),"year":out.get("year"),"searched":search_now}
def add_series(a):
    results=_get("sonarr","series/lookup",{"term":a["title"]})
    if a.get("confirm") is not True: return {"status":"confirmation_required","write_performed":False,"candidates":_preview(results,"tvdbId")}
    search_now=_search_now(a)
    item=_choose(results,a["title"],a.get("year"),"tvdbId",a.get("tvdb_id"))
    existing=_existing("sonarr","tvdbId",item.get("tvdbId"))
    if existing: return {"added":False,"already_exists":True,"id":existing.get("id"),"title":existing.get("title"),"year":existing.get("year")}
    root,quality=_root_quality("sonarr")
    payload={k:v for k,v in item.items() if k!="id"}; payload.update({"qualityProfileId":quality,"rootFolderPath":root,"monitored":True,"seasonFolder":True,"addOptions":{"searchForMissingEpisodes":search_now,"monitor":"all"}})
    out=_post("sonarr","series",payload); return {"added":True,"id":out.get("id"),"title":out.get("title"),"year":out.get("year"),"searched":search_now}
