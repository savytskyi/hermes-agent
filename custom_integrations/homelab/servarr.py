"""Safe Lidarr, Bazarr, and Prowlarr API operations."""
from __future__ import annotations
import json
from .common import env, json_request, auth_headers, HomelabError

CFG = {
    "lidarr": (env("LIDARR_URL", "http://127.0.0.1:8686"), env("LIDARR_API_KEY"), "/api/v1/"),
    "bazarr": (env("BAZARR_URL", "http://127.0.0.1:6767"), env("BAZARR_API_KEY"), "/api/"),
    "prowlarr": (env("PROWLARR_URL", "http://127.0.0.1:9696"), env("PROWLARR_API_KEY"), "/api/v1/"),
}

def _headers(service):
    key = CFG[service][1]
    if not key:
        raise HomelabError(f"{service} API key is not configured")
    return auth_headers(key, "X-Api-Key", "")

def _get(service, path, params=None):
    base, _, prefix = CFG[service]
    return json_request(base, prefix + path.lstrip("/"), headers=_headers(service), params=params)

def _post(service, path, body=None, params=None):
    base, _, prefix = CFG[service]
    headers = _headers(service)
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode()
    return json_request(base, prefix + path.lstrip("/"), method="POST", headers=headers, params=params, data=data)

def _compact_health(items):
    return [{k: x.get(k) for k in ("source", "type", "message", "wikiUrl")} for x in (items or [])]

def lidarr_read(a):
    action = a.get("action", "status")
    limit = min(max(int(a.get("limit", 100)), 1), 200)
    query = (a.get("query") or "").casefold()
    if action == "status":
        status = _get("lidarr", "system/status")
        return {k: status.get(k) for k in ("version", "isDebug", "isProduction", "appData", "startupPath")}
    if action == "health":
        return {"items": _compact_health(_get("lidarr", "health"))}
    if action == "queue":
        raw = _get("lidarr", "queue", {"page": 1, "pageSize": limit, "includeUnknownArtistItems": "true"})
        rows = raw.get("records", []) if isinstance(raw, dict) else []
        return {"count": len(rows), "items": [{k: x.get(k) for k in ("id", "title", "status", "trackedDownloadStatus", "size", "sizeleft", "timeleft", "artistId", "albumId", "errorMessage")} for x in rows]}
    if action == "missing":
        raw = _get("lidarr", "wanted/missing", {"page": 1, "pageSize": limit, "sortKey": "releaseDate", "sortDirection": "descending"})
        rows = raw.get("records", []) if isinstance(raw, dict) else []
        return {"count": len(rows), "items": [{k: x.get(k) for k in ("id", "title", "releaseDate", "artistId", "monitored", "statistics")} for x in rows]}
    if action in {"artists", "albums"}:
        rows = _get("lidarr", "artist" if action == "artists" else "album")
        if query:
            rows = [x for x in rows if query in json.dumps(x, ensure_ascii=False, default=str).casefold()]
        keys = ("id", "artistName", "foreignArtistId", "status", "monitored", "statistics") if action == "artists" else ("id", "title", "foreignAlbumId", "releaseDate", "monitored", "artistId", "statistics")
        return {"count": min(len(rows), limit), "items": [{k: x.get(k) for k in keys} for x in rows[:limit]]}
    raise ValueError("Unknown Lidarr read action")

def _lidarr_lookup(term):
    return _get("lidarr", "artist/lookup", {"term": term})

def lidarr_add(a):
    term = (a.get("artist") or "").strip()
    if not term:
        raise ValueError("artist is required")
    results = _lidarr_lookup(term)
    candidates = [{"artistName": x.get("artistName"), "foreignArtistId": x.get("foreignArtistId"), "status": x.get("status"), "disambiguation": x.get("disambiguation")} for x in results[:8]]
    if a.get("confirm") is not True:
        return {"status": "confirmation_required", "write_performed": False, "candidates": candidates}
    foreign_id = a.get("foreign_artist_id")
    if foreign_id:
        chosen = next((x for x in results if x.get("foreignArtistId") == foreign_id), None)
    else:
        exact = [x for x in results if (x.get("artistName") or "").casefold() == term.casefold()]
        chosen = exact[0] if len(exact) == 1 else None
    if not chosen:
        raise HomelabError("Artist lookup is ambiguous; specify foreign_artist_id")
    existing = next((x for x in _get("lidarr", "artist") if x.get("foreignArtistId") == chosen.get("foreignArtistId")), None)
    if existing:
        return {"added": False, "already_exists": True, "id": existing.get("id"), "artistName": existing.get("artistName")}
    roots = _get("lidarr", "rootfolder")
    quality = _get("lidarr", "qualityprofile")
    metadata = _get("lidarr", "metadataprofile")
    if not roots or not quality or not metadata:
        raise HomelabError("Lidarr root, quality, or metadata profile is not configured")
    payload = {k: v for k, v in chosen.items() if k != "id"}
    payload.update({
        "rootFolderPath": roots[0]["path"], "qualityProfileId": quality[0]["id"],
        "metadataProfileId": metadata[0]["id"], "monitored": True,
        "addOptions": {"monitor": "all", "searchForMissingAlbums": bool(a.get("search_now", False))},
    })
    out = _post("lidarr", "artist", payload)
    return {"added": True, "id": out.get("id"), "artistName": out.get("artistName"), "searched": bool(a.get("search_now", False))}

def bazarr_read(a):
    action = a.get("action", "status")
    limit = min(max(int(a.get("limit", 100)), 1), 200)
    if action == "status":
        return _get("bazarr", "system/status")
    if action == "health":
        return _get("bazarr", "system/health")
    if action == "providers":
        return _get("bazarr", "providers")
    if action == "history":
        raw = _get("bazarr", "episodes/history", {"start": 0, "length": limit})
        rows = raw.get("data", raw if isinstance(raw, list) else [])
        return {"count": len(rows), "items": rows[:limit]}
    if action in {"episodes", "movies"}:
        path = "episodes" if action == "episodes" else "movies"
        raw = _get("bazarr", path, {"start": 0, "length": limit})
        rows = raw.get("data", raw if isinstance(raw, list) else [])
        return {"count": len(rows), "items": rows[:limit]}
    if action in {"wanted_episodes", "wanted_movies"}:
        path = "episodes/wanted" if action == "wanted_episodes" else "movies/wanted"
        raw = _get("bazarr", path, {"start": 0, "length": limit})
        rows = raw.get("data", raw if isinstance(raw, list) else [])
        return {"count": len(rows), "items": rows[:limit]}
    if action in {"search_episode", "search_movie"}:
        ident = a.get("episode_id") if action == "search_episode" else a.get("radarr_id")
        if ident is None: raise ValueError("episode_id or radarr_id is required")
        path = "providers/episodes" if action == "search_episode" else "providers/movies"
        param = "episodeid" if action == "search_episode" else "radarrid"
        raw = _get("bazarr", path, {param: int(ident)})
        if isinstance(raw, dict): rows = raw.get("data", [])
        elif isinstance(raw, list): rows = raw
        else: rows = []
        keys = ("provider", "subtitle", "score", "language", "hearing_impaired", "forced", "release_info", "uploader", "matches", "original_format")
        return {"count": min(len(rows), limit), "items": [{k:x.get(k) for k in keys if k in x} for x in rows[:limit]]}
    raise ValueError("Unknown Bazarr action")

def prowlarr_read(a):
    action = a.get("action", "status")
    if action == "status":
        status = _get("prowlarr", "system/status")
        return {k: status.get(k) for k in ("version", "isDebug", "isProduction", "appData", "startupPath")}
    if action == "health":
        return {"items": _compact_health(_get("prowlarr", "health"))}
    if action == "indexers":
        rows = _get("prowlarr", "indexer")
        return {"count": len(rows), "items": [{k: x.get(k) for k in ("id", "name", "enable", "protocol", "privacy", "priority", "appProfileId", "tags")} for x in rows]}
    if action == "stats":
        return _get("prowlarr", "indexerstats")
    if action == "applications":
        rows = _get("prowlarr", "applications")
        return {"count": len(rows), "items": [{k: x.get(k) for k in ("id", "name", "syncLevel", "implementation", "tags")} for x in rows]}
    raise ValueError("Unknown Prowlarr action")
