#!/usr/bin/env python3
import os,re,sys,json
from pathlib import Path
for line in Path('/Users/ky/.hermes/.env').read_text().splitlines():
 m=re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$',line)
 if m: os.environ.setdefault(m.group(1),m.group(2))
sys.path.insert(0,'/Users/ky/.hermes/plugins')
from homelab import arr
out={}
for kind,title in [('radarr','Dune Part Two'),('sonarr','Severance')]:
 fn=arr.add_movie if kind=='radarr' else arr.add_series
 try:
  d=fn({'title':title,'confirm':False})
  out[kind]={'status':d.get('status'),'candidate_count':len(d.get('candidates',[])),'write_performed':False}
 except Exception as e: out[kind]={'error':type(e).__name__,'message':str(e)}
print(json.dumps(out,indent=2))
if any('error' in v for v in out.values()): raise SystemExit(1)
