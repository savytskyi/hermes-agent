"""Read-only qBittorrent Web API client."""
from .common import env, json_request
URL=env("QBITTORRENT_URL","http://127.0.0.1:8080")

def _torrent(t):
    # Tracker announce URLs are intentionally omitted: private trackers often
    # embed account passkeys in their path or query string.
    return {k:t.get(k) for k in ("name","hash","state","progress","dlspeed","upspeed","eta","num_seeds","num_leechs","seeds_total","leeches_total","size","downloaded","uploaded","ratio","amount_left","availability","last_activity","completion_on","category","tags")}
def execute(a):
    action=a.get("action"); limit=min(int(a.get("limit",100)),200)
    if action=="transfer":
        x=json_request(URL,"/api/v2/transfer/info")
        return {k:x.get(k) for k in ("dl_info_speed","dl_info_data","up_info_speed","up_info_data","dl_rate_limit","up_rate_limit","dht_nodes","connection_status")}
    api_filter={"downloading":"downloading","stalled":"stalled","errors":"errored"}.get(action,"all")
    rows=json_request(URL,"/api/v2/torrents/info",params={"filter":api_filter})
    q=(a.get("query") or "").lower()
    if q: rows=[t for t in rows if q in t.get("name","").lower() or q in t.get("hash","").lower()]
    if action=="downloading": rows=[t for t in rows if t.get("state") in ("downloading","metaDL","forcedDL","queuedDL","checkingDL")]
    elif action=="stalled": rows=[t for t in rows if t.get("state") in ("stalledDL","stalledUP")]
    elif action=="errors": rows=[t for t in rows if t.get("state") in ("error","missingFiles","unknown")]
    elif action!="torrents": raise ValueError("Unknown qBittorrent action")
    return {"count":len(rows),"returned_count":min(len(rows),limit),"truncated":len(rows)>limit,"torrents":[_torrent(t) for t in rows[:limit]],"read_only":True}
