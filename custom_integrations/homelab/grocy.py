"""Grocy integration delegating to the established bounded REST helper."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
from .common import HomelabError

HELPER = Path.home() / ".hermes" / "skills" / "productivity" / "grocy" / "scripts" / "grocy_api.py"
WRITES = {"list_add", "list_done", "stock_add", "consume", "undo"}

def _run(args):
    p = subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True, timeout=60)
    text=(p.stdout or p.stderr).strip()
    try: payload=json.loads(text)
    except ValueError: raise HomelabError("Grocy helper returned invalid JSON") from None
    if p.returncode:
        return {"ok":False,"detail":payload,"write_performed":False}
    return payload

def execute(a):
    action=a.get("action","stock")
    if action in WRITES and a.get("confirm") is not True:
        return {"status":"confirmation_required","write_performed":False,"action":action}
    if action=="health": args=["health"]
    elif action=="have":
        items=a.get("items") or ([] if not a.get("item") else [a["item"]])
        if not items: raise ValueError("item or items is required")
        args=["have",*map(str,items)]
    elif action=="stock":
        args=["stock"]
        if a.get("location"): args += ["--location",str(a["location"])]
    elif action=="products":
        args=["products"]
        if a.get("query"): args += ["--query",str(a["query"])]
    elif action=="list": args=["list"]
    elif action=="list_add":
        if not a.get("item"): raise ValueError("item is required")
        args=["list-add",str(a["item"]),"--amount",str(a.get("amount",1))]
        if a.get("note"): args += ["--note",str(a["note"])]
        if a.get("product_id") is not None: args += ["--product-id",str(int(a["product_id"]))]
        if a.get("free_text") is True: args += ["--free-text"]
    elif action=="list_done":
        ids=a.get("ids") or []
        if not ids: raise ValueError("ids are required")
        args=["list-done",*map(lambda x:str(int(x)),ids)]
        if a.get("delete") is True: args += ["--delete"]
    elif action=="stock_add":
        if not a.get("item"): raise ValueError("item is required")
        args=["stock-add",str(a["item"]),"--amount",str(a.get("amount",1))]
        for key,flag in (("best_before","--best-before"),("price","--price"),("location","--location"),("unit","--unit"),("product_id","--product-id")):
            if a.get(key) is not None: args += [flag,str(a[key])]
        if a.get("create") is True: args += ["--create"]
    elif action=="consume":
        if not a.get("item"): raise ValueError("item is required")
        args=["consume",str(a["item"]),"--amount",str(a.get("amount",1))]
        if a.get("product_id") is not None: args += ["--product-id",str(int(a["product_id"]))]
        if a.get("spoiled") is True: args += ["--spoiled"]
    elif action=="undo":
        if not a.get("transaction_id"): raise ValueError("transaction_id is required")
        args=["undo",str(a["transaction_id"])]
    elif action in {"locations","units"}: args=[action]
    else: raise ValueError("Unknown Grocy action")
    out=_run(args)
    if action in WRITES and isinstance(out,dict): out.setdefault("write_performed",bool(out.get("status") not in {"already_on_list"} and out.get("ok",True)))
    return out
