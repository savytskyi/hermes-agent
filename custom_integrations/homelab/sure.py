"""Bounded Sure Finance REST integration; no destructive operations."""
from __future__ import annotations
import datetime as dt, decimal, hashlib, json
from .common import env, json_request, auth_headers, HomelabError

URL = env("SURE_API_URL", "http://127.0.0.1:3030").rstrip("/")
KEY = env("SURE_API_KEY")

def _headers(json_body=False):
    if not KEY: raise HomelabError("Sure API key is not configured")
    h = auth_headers(KEY, "X-Api-Key", "")
    h["Accept"] = "application/json"
    if json_body: h["Content-Type"] = "application/json"
    return h

def _request(method, path, *, params=None, body=None):
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    return json_request(URL, "/api/v1/" + path.lstrip("/"), method=method, headers=_headers(body is not None), params=params, data=data)

def _objects(payload, key):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict) and isinstance(payload.get(key), list): return payload[key]
    return []

def _paged(path, key, params=None, max_records=1000):
    rows=[]; page=1
    while len(rows) < max_records:
        q=dict(params or {}); q.update({"page":page,"per_page":min(100,max_records-len(rows))})
        raw=_request("GET",path,params=q); batch=_objects(raw,key); rows.extend(batch)
        pages=int((raw.get("pagination") or {}).get("total_pages") or 1) if isinstance(raw,dict) else 1
        if not batch or page>=pages: break
        page += 1
    return rows

def _compact_account(x):
    return {k:x.get(k) for k in ("id","name","classification","account_type","subtype","currency","balance","balance_cents","cash_balance","cash_balance_cents","status","institution")}

def _compact_tx(x):
    merchant=x.get("merchant") or {}; category=x.get("category") or {}
    return {**{k:x.get(k) for k in ("id","account_id","date","name","nature","amount","amount_cents","signed_amount","signed_amount_cents","currency","notes","user_modified","source")},
            "merchant":merchant.get("name") if isinstance(merchant,dict) else None,
            "category":category.get("name") if isinstance(category,dict) else None}

def _minor(tx):
    for key in ("amount_cents","signed_amount_cents"):
        if tx.get(key) is not None:
            return abs(int(tx[key]))
    try: return abs(int(decimal.Decimal(str(tx.get("amount"))) * 100))
    except Exception: return None

def execute(a):
    action=a.get("action","accounts"); limit=min(max(int(a.get("limit",100)),1),1000)
    if action=="accounts":
        rows=_paged("accounts","accounts",{"include_disabled":"false"},limit)
        return {"count":len(rows),"accounts":[_compact_account(x) for x in rows]}
    if action in {"balance_sheet","balances","cash_flow","insights"}:
        return _request("GET",action,params={k:v for k,v in {"start_date":a.get("start_date"),"end_date":a.get("end_date")}.items() if v})
    if action=="transactions":
        params={k:v for k,v in {"account_id":a.get("account_id"),"start_date":a.get("start_date"),"end_date":a.get("end_date"),"type":a.get("type"),"search":a.get("query")}.items() if v}
        rows=_paged("transactions","transactions",params,limit)
        return {"count":len(rows),"transactions":[_compact_tx(x) for x in rows]}
    if action=="spending":
        if not a.get("start_date") or not a.get("end_date"): raise ValueError("start_date and end_date are required")
        rows=_paged("transactions","transactions",{"start_date":a["start_date"],"end_date":a["end_date"],"type":"expense"},limit)
        total=sum((_minor(x) or 0) for x in rows)
        by_category={}
        for x in rows:
            cat=(x.get("category") or {}).get("name") if isinstance(x.get("category"),dict) else None
            by_category[cat or "Uncategorized"]=by_category.get(cat or "Uncategorized",0)+(_minor(x) or 0)
        return {"start_date":a["start_date"],"end_date":a["end_date"],"currency":"EUR","transaction_count":len(rows),"total_cents":total,"by_category_cents":dict(sorted(by_category.items(),key=lambda kv:-kv[1]))}
    if action=="add_expense":
        amount=decimal.Decimal(str(a.get("amount"))).quantize(decimal.Decimal("0.01")); name=(a.get("name") or "").strip()
        if amount<=0 or not name: raise ValueError("Positive amount and name are required")
        account=a.get("account_id") or env("SURE_DEFAULT_ACCOUNT_ID")
        if not account: raise HomelabError("Sure default account is not configured")
        date=a.get("date") or dt.date.today().isoformat(); cents=int(amount*100)
        candidates=_paged("transactions","transactions",{"account_id":account,"start_date":date,"end_date":date,"type":"expense"},100)
        norm=lambda s:" ".join(str(s or "").casefold().split())
        duplicates=[]
        for tx in candidates:
            names={norm(tx.get("name"))}
            if isinstance(tx.get("merchant"),dict): names.add(norm(tx["merchant"].get("name")))
            if _minor(tx)==cents and norm(name) in names: duplicates.append(_compact_tx(tx))
        if duplicates:
            return {"created":False,"duplicate_candidates":duplicates,"confirmation_required":True}
        preview={"account_id":account,"date":date,"amount":str(amount),"currency":a.get("currency") or "EUR","name":name,"category_id":a.get("category_id"),"notes":a.get("notes")}
        if a.get("confirm") is not True:
            return {"status":"confirmation_required","write_performed":False,"expense":preview}
        external="hermes-"+hashlib.sha256(f"{account}|{date}|{cents}|{norm(name)}|expense".encode()).hexdigest()[:32]
        tx={**preview,"nature":"expense","user_modified":True,"external_id":external,"source":"hermes","tag_ids":[]}
        tx={k:v for k,v in tx.items() if v is not None}
        out=_request("POST","transactions",body={"transaction":tx})
        created=out.get("transaction",out) if isinstance(out,dict) else out
        tx_id=created.get("id") if isinstance(created,dict) else None
        verified=_request("GET",f"transactions/{tx_id}") if tx_id else None
        return {"created":True,"transaction":_compact_tx(verified.get("transaction",verified) if isinstance(verified,dict) else verified),"verified":bool(tx_id)}
    raise ValueError("Unknown Sure action")
