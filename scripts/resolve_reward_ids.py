#!/usr/bin/env python3
"""
Resolve POE2 Scout IDs for reward names.

Important:
- Currency rewards use Scout's /Currencies/{apiId}.
- Rune/Alloy/Gem rewards use Scout's numeric /Items/{itemId}.
- We resolve IDs once and store them in rewards.json; the hourly snapshot
  then performs only per-reward GETs and never downloads the full economy table.
"""
from __future__ import annotations
import json, os, re, sys, time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
FILE=ROOT/"data/rewards.json"
BASE=os.getenv("SCOUT_API_BASE","https://api.poe2scout.com").rstrip("/")
REALM=os.getenv("SCOUT_REALM","poe2")
LEAGUE=os.getenv("SCOUT_LEAGUE","Runes of Aldur")
UA=os.getenv("SCOUT_USER_AGENT","POE2-Expedition-Radar-Catalog/1.0")
TIMEOUT=30

def get(path, params=None):
    url=f"{BASE}/{path.lstrip('/')}"
    if params: url += "?" + urlencode(params)
    req=Request(url,headers={"Accept":"application/json","User-Agent":UA})
    with urlopen(req,timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))

def candidates(obj):
    if isinstance(obj,list): return obj
    if isinstance(obj,dict):
        for k in ("items","data","results","value"):
            if isinstance(obj.get(k),list): return obj[k]
        for v in obj.values():
            if isinstance(v,list): return v
    return []

def norm(s):
    return re.sub(r"[^a-z0-9]+","",s.lower())

def first(obj, keys):
    if isinstance(obj,dict):
        for k in keys:
            if obj.get(k) not in (None,""): return obj[k]
    return None

def slug(s):
    return re.sub(r"-+","-",re.sub(r"[^a-z0-9]+","-",s.lower())).strip("-")

def find_in_items(name):
    # The current Scout API exposes an aggregate /Items route. Try the common
    # search parameter spellings used by the public clients, then exact-match.
    root=f"{quote(REALM,safe='')}/Leagues/{quote(LEAGUE,safe='')}/Items"
    attempts=[
        {"search":name,"page":1,"perPage":100},
        {"query":name,"page":1,"perPage":100},
        {"name":name,"page":1,"perPage":100},
    ]
    for params in attempts:
        try:
            obj=get(root,params)
        except Exception:
            continue
        rows=candidates(obj)
        exact=[x for x in rows if isinstance(x,dict) and norm(first(x,("text","name","displayName")) or "")==norm(name)]
        if exact:
            x=exact[0]
            item_id=first(x,("itemId","item_id","id"))
            if item_id is not None: return int(item_id)
        # fuzzy only if unique
        fuzzy=[x for x in rows if isinstance(x,dict) and norm(name) in norm(first(x,("text","name","displayName")) or "")]
        if len(fuzzy)==1:
            item_id=first(fuzzy[0],("itemId","item_id","id"))
            if item_id is not None: return int(item_id)
    return None

def find_currency_api_id(name):
    # Common API ids are kebab-case, but resolve by querying the category page
    # when possible; direct slug is the fallback.
    return slug(name)

def main():
    data=json.loads(FILE.read_text(encoding="utf-8"))
    resolved=0; unresolved=[]
    for r in data["rewards"]:
        s=r["scout"]
        if r["type"]=="currency":
            if not s.get("api_id"):
                s["api_id"]=find_currency_api_id(r["name"])
                resolved += 1
        else:
            if not s.get("item_id"):
                item_id=find_in_items(r["name"])
                if item_id is None:
                    unresolved.append(r["name"])
                    continue
                s["item_id"]=item_id
                resolved += 1
        print(f"[{'OK' if (s.get('api_id') or s.get('item_id')) else 'MISS'}] {r['name']}")
        time.sleep(0.05)
    data["resolution"]={"resolved":resolved,"unresolved":unresolved}
    FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if unresolved:
        print(f"WARNING: {len(unresolved)} rewards could not be resolved. Snapshot will skip them.",file=sys.stderr)
        for x in unresolved: print("  -",x,file=sys.stderr)

if __name__=="__main__":
    main()
