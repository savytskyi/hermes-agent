"""Read-only Plex XML API."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from .common import env, request
URL=env("PLEX_URL","http://127.0.0.1:32400"); TOKEN=env("PLEX_TOKEN")
def _xml(path,params=None):
    headers={"X-Plex-Token":TOKEN} if TOKEN else {}
    _,body,_=request(URL,path,params=dict(params or {}),headers=headers,timeout=12)
    return ET.fromstring(body)
def _media(node):
    title=node.get("title") or node.get("grandparentTitle"); episode=node.get("parentIndex"); index=node.get("index")
    out={"type":node.get("type"),"title":title,"year":node.get("year"),"added_at":node.get("addedAt"),"duration_ms":node.get("duration"),"view_offset_ms":node.get("viewOffset")}
    if episode or index: out["episode"]=f"S{int(episode or 0):02d}E{int(index or 0):02d}"
    user=node.find("User"); player=node.find("Player"); session=node.find("Session")
    if user is not None: out["user"]=user.get("title")
    if player is not None: out.update({"device":player.get("title") or player.get("device"),"state":player.get("state"),"address":player.get("address")})
    if session is not None: out["bandwidth_kbps"]=session.get("bandwidth")
    media=node.find("Media"); decision={}
    if media is not None:
        decision.update({"video_decision":media.get("videoDecision"),"audio_decision":media.get("audioDecision"),"protocol":media.get("protocol"),"container":media.get("container"),"bitrate_kbps":media.get("bitrate")})
    for stream in node.findall("./Media/Part/Stream"):
        if stream.get("streamType")=="1": decision["video_decision"]=stream.get("decision")
        elif stream.get("streamType")=="2": decision["audio_decision"]=stream.get("decision")
    trans=node.find("TranscodeSession")
    if trans is not None: decision.update({"transcoding":True,"speed":trans.get("speed"),"throttled":trans.get("throttled"),"video_decision":trans.get("videoDecision"),"audio_decision":trans.get("audioDecision"),"transcode_container":trans.get("container")})
    else: decision["transcoding"]=False
    out.update(decision); return out
def execute(a):
    action=a.get("action"); limit=min(int(a.get("limit",50)),100)
    if action=="status":
        root=_xml("/identity"); return {"machine_identifier":root.get("machineIdentifier"),"version":root.get("version")}
    if action=="sessions":
        root=_xml("/status/sessions"); items=[_media(x) for x in list(root) if x.tag in ("Video","Track","Photo")]
        return {"count":len(items),"transcoding_count":sum(bool(x.get("transcoding")) for x in items),"sessions":items}
    if action in ("libraries","scan_status"):
        root=_xml("/library/sections"); libs=[]
        for x in root.findall("Directory"):
            lib={"key":x.get("key"),"title":x.get("title"),"type":x.get("type"),"scanning":x.get("refreshing")=="1"}
            if action=="libraries":
                listing=_xml(f'/library/sections/{x.get("key")}/all',{"X-Plex-Container-Start":0,"X-Plex-Container-Size":0})
                lib["count"]=int(listing.get("totalSize") or listing.get("size") or 0)
            libs.append(lib)
        return {"libraries":libs}
    if action=="search": root=_xml("/hubs/search",{"query":a.get("query","")}); nodes=root.findall(".//Video")+root.findall(".//Directory")
    elif action=="recent": root=_xml("/library/recentlyAdded",{"X-Plex-Container-Size":limit}); nodes=root.findall("Video")+root.findall("Directory")
    else: raise ValueError("Unknown Plex action")
    return {"items":[_media(x) for x in nodes[:limit]]}
