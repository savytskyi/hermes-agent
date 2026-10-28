"""LazyLibrarian and Calibre integrations using supported API/CLI surfaces."""
from __future__ import annotations
import json, os, re, subprocess, time, uuid
from pathlib import Path
from .common import env, json_request, HomelabError

LL_URL = env("LAZYLIBRARIAN_URL", "http://127.0.0.1:5299")
LL_KEY = env("LAZYLIBRARIAN_API_KEY")
CALIBRE_CONTAINER = env("CALIBRE_CONTAINER", "calibre-web")
CALIBRE_LIBRARY = env("CALIBRE_LIBRARY_PATH", "/books")
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
SAFE_METADATA_FIELDS = {"title", "authors", "tags", "publisher", "series", "series_index", "comments", "rating", "languages", "pubdate"}

def _ll(cmd, params=None):
    if not LL_KEY:
        raise HomelabError("LazyLibrarian API key is not configured")
    query = {"apikey": LL_KEY, "cmd": cmd}
    query.update(params or {})
    return json_request(LL_URL, "/api", params=query)

def _book_rows(payload):
    if isinstance(payload, list): rows = payload
    elif isinstance(payload, dict):
        rows = next((payload[k] for k in ("books", "results", "data") if isinstance(payload.get(k), list)), [])
    else: rows = []
    keys = ("bookid", "BookID", "bookname", "BookName", "booksub", "BookSub", "authorname", "AuthorName", "bookstatus", "BookStatus", "status", "isbn", "ISBN", "bookdate", "BookDate", "booklang", "BookLang")
    return [{k: row.get(k) for k in keys if row.get(k) is not None} for row in rows]

def lazylibrarian(a):
    action = a.get("action", "status")
    limit = min(max(int(a.get("limit", 50)), 1), 200)
    if action == "status":
        out = _ll("getVersion")
        return {"reachable": True, "version": out}
    if action == "search":
        query = (a.get("query") or "").strip()
        if not query: raise ValueError("query is required")
        return {"items": _book_rows(_ll("findBook", {"name": query}))[:limit]}
    if action in {"wanted", "snatched", "history"}:
        command = {"wanted": "getWanted", "snatched": "getSnatched", "history": "getHistory"}[action]
        return {"items": _book_rows(_ll(command))[:limit]}
    if action == "add":
        book_id = (a.get("book_id") or "").strip()
        if not ID_RE.fullmatch(book_id): raise ValueError("Valid book_id is required")
        if a.get("confirm") is not True:
            return {"status": "confirmation_required", "write_performed": False, "book_id": book_id}
        result = _ll("addBook", {"id": book_id})
        return {"added": True, "book_id": book_id, "result": result}
    raise ValueError("Unknown LazyLibrarian action")

def _calibre(*args, timeout=30):
    command = ["docker", "exec", CALIBRE_CONTAINER, *map(str, args)]
    p = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise HomelabError("Calibre command failed")
    return p.stdout

def _list(search=None, limit=100):
    args = ["calibredb", "--with-library", CALIBRE_LIBRARY, "list", "--for-machine", "--limit", str(limit), "--fields", "id,title,authors,formats,tags,publisher,series,series_index,languages,rating,pubdate"]
    if search:
        args.extend(["--search", search])
    try:
        rows = json.loads(_calibre(*args))
    except ValueError:
        raise HomelabError("Calibre returned invalid JSON") from None
    return rows if isinstance(rows, list) else []

def _host_import_allowed(path):
    roots = [Path(x).expanduser().resolve() for x in env("CALIBRE_IMPORT_ROOTS", "/Volumes/STORAGE/downloads/books").split(",") if x.strip()]
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file() or not any(resolved == root or root in resolved.parents for root in roots):
        raise HomelabError("Import path is outside the configured Calibre import roots")
    return resolved

def calibre(a):
    action = a.get("action", "search")
    limit = min(max(int(a.get("limit", 50)), 1), 200)
    if action in {"search", "list"}:
        query = (a.get("query") or "").strip()
        rows = _list(query or None, limit)
        return {"count": len(rows), "books": rows}
    if action == "metadata":
        book_id = int(a.get("book_id"))
        rows = _list(f"id:{book_id}", 5)
        return {"book": next((x for x in rows if int(x.get("id", -1)) == book_id), None)}
    if action == "import":
        source = _host_import_allowed(a.get("path") or "")
        if a.get("confirm") is not True:
            return {"status": "confirmation_required", "write_performed": False, "path": str(source)}
        tmp = f"/tmp/hermes-calibre-{uuid.uuid4().hex}-{source.name}"
        cp = subprocess.run(["docker", "cp", str(source), f"{CALIBRE_CONTAINER}:{tmp}"], capture_output=True, text=True, timeout=60)
        if cp.returncode: raise HomelabError("Unable to stage Calibre import")
        try:
            output = _calibre("calibredb", "--with-library", CALIBRE_LIBRARY, "add", tmp, timeout=120)
        finally:
            subprocess.run(["docker", "exec", CALIBRE_CONTAINER, "rm", "-f", tmp], capture_output=True, timeout=20)
        return {"imported": True, "source": str(source), "result": output.strip()[:500]}
    if action == "edit_metadata":
        book_id = int(a.get("book_id")); field = (a.get("field") or "").strip(); value = str(a.get("value") or "")
        if field not in SAFE_METADATA_FIELDS: raise ValueError("Metadata field is not allowlisted")
        before = next((x for x in _list(f"id:{book_id}", 5) if int(x.get("id", -1)) == book_id), None)
        if not before: raise HomelabError("Calibre book was not found")
        if a.get("confirm") is not True:
            return {"status": "confirmation_required", "write_performed": False, "book": before, "field": field, "value": value}
        _calibre("calibredb", "--with-library", CALIBRE_LIBRARY, "set_metadata", str(book_id), "--field", f"{field}:{value}")
        after = next((x for x in _list(f"id:{book_id}", 5) if int(x.get("id", -1)) == book_id), None)
        return {"updated": True, "book": after}
    if action == "convert":
        book_id = int(a.get("book_id")); fmt = (a.get("format") or "").lower()
        if fmt not in {"epub", "mobi", "azw3", "pdf", "txt"}: raise ValueError("Unsupported output format")
        if a.get("confirm") is not True:
            return {"status": "confirmation_required", "write_performed": False, "book_id": book_id, "format": fmt}
        work = f"/tmp/hermes-convert-{uuid.uuid4().hex}"
        _calibre("mkdir", "-p", work)
        try:
            _calibre("calibredb", "--with-library", CALIBRE_LIBRARY, "export", "--to-dir", work, "--dont-save-cover", "--dont-write-opf", "--single-dir", str(book_id), timeout=120)
            found = _calibre("find", work, "-maxdepth", "1", "-type", "f").splitlines()
            source = next((x for x in found if not x.lower().endswith("." + fmt)), found[0] if found else None)
            if not source: raise HomelabError("No source format is available for conversion")
            output = f"{work}/book-{book_id}.{fmt}"
            _calibre("ebook-convert", source, output, timeout=300)
            host = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "cache" / "scratch" / "calibre"
            host.mkdir(parents=True, exist_ok=True)
            target = host / f"book-{book_id}-{int(time.time())}.{fmt}"
            cp = subprocess.run(["docker", "cp", f"{CALIBRE_CONTAINER}:{output}", str(target)], capture_output=True, text=True, timeout=60)
            if cp.returncode: raise HomelabError("Unable to retrieve converted book")
            return {"converted": True, "book_id": book_id, "format": fmt, "path": str(target), "bytes": target.stat().st_size}
        finally:
            subprocess.run(["docker", "exec", CALIBRE_CONTAINER, "rm", "-rf", work], capture_output=True, timeout=20)
    raise ValueError("Unknown Calibre action")
