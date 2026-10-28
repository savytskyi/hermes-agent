"""Read-only Grafana API with SQLite dashboard fallback."""
import json, re, sqlite3
from pathlib import Path
from .common import env, json_request, auth_headers
URL=env("GRAFANA_URL","http://127.0.0.1:3000")
TOKEN=env("GRAFANA_TOKEN")
DB=Path(env("GRAFANA_DB_PATH","/Users/ky/homelab/appdata/grafana/grafana.db"))
UID_RE=re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SENSITIVE_RE=re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key)",re.I)
def _redact(value):
    if isinstance(value,dict): return {k:("[REDACTED]" if SENSITIVE_RE.search(str(k)) else _redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [_redact(v) for v in value]
    return value
def _headers(): return auth_headers(TOKEN)
def _db_dashboards(query="",limit=100):
    con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True); con.row_factory=sqlite3.Row
    try:
        rows=con.execute("SELECT uid,title,data,updated FROM dashboard WHERE is_folder=0 AND lower(title) LIKE ? ORDER BY title LIMIT ?",(f"%{query.lower()}%",limit)).fetchall()
        return [dict(r) for r in rows]
    finally: con.close()
def execute(a):
    action=a.get("action"); limit=min(int(a.get("limit",100)),100)
    if action=="health": return json_request(URL,"/api/health")
    if action=="dashboards":
        q=a.get("query","")
        if TOKEN:
            rows=json_request(URL,"/api/search",headers=_headers(),params={"type":"dash-db","query":q,"limit":limit})
            return {"source":"api","dashboards":rows}
        rows=_db_dashboards(q,limit)
        return {"source":"local_readonly_database","dashboards":[{"uid":r["uid"],"title":r["title"],"updated":r["updated"]} for r in rows]}
    if action=="dashboard":
        uid=a.get("uid","")
        if not UID_RE.fullmatch(uid): raise ValueError("Invalid dashboard UID")
        if TOKEN: return {"source":"api","dashboard":_redact(json_request(URL,f"/api/dashboards/uid/{uid}",headers=_headers()))}
        con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True); con.row_factory=sqlite3.Row
        try: row=con.execute("SELECT data FROM dashboard WHERE is_folder=0 AND uid=?",(uid,)).fetchone()
        finally: con.close()
        return {"source":"local_readonly_database","dashboard":_redact(json.loads(row["data"])) if row else None}
    raise ValueError("Unknown Grafana action")
