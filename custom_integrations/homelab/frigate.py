"""Frigate read-only camera/event integration with session-level authorization."""
from __future__ import annotations
import json, os, re, sqlite3, time
from pathlib import Path
from .common import env, json_request, request, auth_headers, HomelabError

URL = env("FRIGATE_URL", "http://127.0.0.1:5001")
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
CAMERA_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

def _headers():
    token = env("FRIGATE_API_TOKEN")
    return auth_headers(token) if token else {}

def _allowed_chat_ids():
    return {x.strip() for x in env("FRIGATE_ALLOWED_CHAT_IDS").split(",") if x.strip()}

def _session_origin(session_id):
    if not session_id:
        raise HomelabError("Frigate access requires a routed Hermes session")
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    db = home / "state.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        row = con.execute("SELECT source,chat_id,origin_json FROM sessions WHERE id=?", (session_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise HomelabError("Unable to verify Frigate session origin")
    source, chat_id, origin = row
    try:
        meta = json.loads(origin or "{}")
    except ValueError:
        meta = {}
    return str(meta.get("platform") or source or ""), str(meta.get("chat_id") or chat_id or "")

def authorize(session_id):
    platform, chat_id = _session_origin(session_id)
    if platform in {"cli", "local"}:
        return
    if platform == "telegram" and chat_id in _allowed_chat_ids():
        return
    raise HomelabError("Frigate camera data is not authorized for this chat")

def _get(path, params=None):
    return json_request(URL, "/api/" + path.lstrip("/"), headers=_headers(), params=params)

def _events(a):
    limit = min(max(int(a.get("limit", 25)), 1), 100)
    params: dict[str, object] = {"limit": limit}
    camera = a.get("camera")
    label = a.get("object")
    if camera:
        if not CAMERA_RE.fullmatch(camera): raise ValueError("Invalid camera")
        params["camera"] = camera
    if label:
        if not CAMERA_RE.fullmatch(label): raise ValueError("Invalid object label")
        params["label"] = label
    for key in ("after", "before"):
        if a.get(key) is not None:
            value = float(a[key])
            if value < 0: raise ValueError(f"Invalid {key}")
            params[key] = value
    rows = _get("events", params)
    return {"count": len(rows), "events": [{k: x.get(k) for k in (
        "id", "camera", "label", "sub_label", "start_time", "end_time", "score",
        "false_positive", "has_clip", "has_snapshot", "zones", "retain", "plus_id")}
        for x in rows]}

def _media(event_id, kind):
    if not ID_RE.fullmatch(event_id or ""):
        raise ValueError("Invalid event_id")
    if kind == "snapshot":
        path, suffix = f"events/{event_id}/snapshot.jpg", ".jpg"
    elif kind == "recording":
        path, suffix = f"events/{event_id}/clip.mp4", ".mp4"
    else:
        raise ValueError("Unknown Frigate media kind")
    _, body, headers = request(URL, "/api/" + path, headers=_headers(), timeout=30)
    if not body:
        raise HomelabError("Frigate returned empty media")
    root = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "cache" / "scratch" / "frigate"
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"{event_id}-{int(time.time())}{suffix}"
    output.write_bytes(body)
    return {"event_id": event_id, "kind": kind, "path": str(output), "bytes": len(body), "content_type": headers.get("Content-Type")}

def execute(a, *, session_id=None):
    authorize(session_id)
    action = a.get("action", "status")
    if action == "status":
        stats = _get("stats")
        cameras = {}
        for name, data in (stats.get("cameras") or {}).items():
            cameras[name] = {k: data.get(k) for k in ("camera_fps", "process_fps", "skipped_fps", "detection_fps", "pid", "capture_pid")}
        detectors = {name: {k: data.get(k) for k in ("inference_speed", "detection_start", "pid")} for name, data in (stats.get("detectors") or {}).items()}
        return {"version": stats.get("service", {}).get("version"), "uptime": stats.get("service", {}).get("uptime"), "cameras": cameras, "detectors": detectors}
    if action == "events":
        return _events(a)
    if action in {"snapshot", "recording"}:
        return _media(a.get("event_id"), action)
    raise ValueError("Unknown Frigate action")
