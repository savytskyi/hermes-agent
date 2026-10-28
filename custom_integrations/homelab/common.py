"""Shared, secret-safe HTTP helpers for the homelab plugin."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TIMEOUT = 10
MAX_RESPONSE_BYTES = 5_000_000

class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old=urllib.parse.urlsplit(req.full_url); new=urllib.parse.urlsplit(newurl)
        if (old.scheme,old.netloc)!=(new.scheme,new.netloc):
            raise urllib.error.HTTPError(req.full_url,code,"Cross-origin redirect refused",headers,fp)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class HomelabError(RuntimeError):
    pass

def url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")

def request(base: str, path: str, *, method: str = "GET", headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None, data: bytes | None = None,
            timeout: int = TIMEOUT) -> tuple[int, bytes, dict[str, str]]:
    target = url(base, path)
    if params:
        target += ("&" if "?" in target else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(target, data=data, method=method, headers=headers or {})
    try:
        opener=urllib.request.build_opener(_SameOriginRedirect())
        with opener.open(req, timeout=timeout) as response:
            body=response.read(MAX_RESPONSE_BYTES+1)
            if len(body)>MAX_RESPONSE_BYTES: raise HomelabError("Service response exceeded size limit")
            return response.status, body, dict(response.headers)
    except urllib.error.HTTPError as exc:
        # Never include response bodies: they can echo credentials or internal details.
        raise HomelabError(f"HTTP {exc.code} from {urllib.parse.urlsplit(target).path}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HomelabError(f"Service request failed: {type(exc).__name__}") from None

def json_request(base: str, path: str, **kwargs: Any) -> Any:
    _, body, _ = request(base, path, **kwargs)
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HomelabError("Service returned invalid JSON") from None

def auth_headers(token: str, header: str = "Authorization", prefix: str = "Bearer ") -> dict[str, str]:
    return {header: prefix + token} if token else {}

def result(data: Any = None, *, error: str | None = None) -> str:
    payload = {"ok": error is None}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = data
    return json.dumps(payload, ensure_ascii=False, default=str)

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()
