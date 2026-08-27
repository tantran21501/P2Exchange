#!/usr/bin/env python3
from __future__ import annotations
import json, os, time, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; SNAP=DATA/"snapshots"; REWARDS=DATA/"rewards.json"
BASE=os.getenv("SCOUT_API_BASE","https://api.poe2scout.com").rstrip("/")
REALM=os.getenv("SCOUT_REALM","poe2"); LEAGUE=os.getenv("SCOUT_LEAGUE","Runes of Aldur")
UA=os.getenv("SCOUT_USER_AGENT","POE2-Expedition-Radar-CurrencySnapshot/2.0")
TIMEOUT=int(os.getenv("SCOUT_TIMEOUT","30")); RETRIES=int(os.getenv("SCOUT_RETRIES","3"))
RETENTION=int(os.getenv("SNAPSHOT_RETENTION_DAYS","30"))

def get(path):
    url=f"{BASE}/{path.lstrip('/')}"
    last=None
    for attempt in range(RETRIES):
        try:
            req=Request(url,headers={"Accept":"application/json","User-Agent":UA})
            with urlopen(req,timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last=e
            if attempt+1<RETRIES: time.sleep(2**attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")

def price(obj):
    if not isinstance(obj,dict): return None
    for k in ("currentPrice","current_price","price"):
        v=obj.get(k)
        if isinstance(v,(int,float)): return float(v)
        if isinstance(v,dict):
            for q in ("price","value","amount"):
                if isinstance(v.get(q),(int,float)): return float(v[q])
    item=obj.get("item")
    if isinstance(item,dict): return price(item)
    return None

def fetch_currency(api_id):
    root=f"{quote(REALM,safe='')}/Leagues/{quote(LEAGUE,safe='')}"
    return get(f"{root}/Currencies/{quote(api_id,safe='')}")

def fetch_item(item_id):
    root=f"{quote(REALM,safe='')}/Leagues/{quote(LEAGUE,safe='')}"
    return get(f"{root}/Items/{int(item_id)}")

def main():
    cfg=json.loads(REWARDS.read_text(encoding="utf-8"))
    now=datetime.now(timezone.utc); stamp=now.strftime("%Y-%m-%dT%H:%M:%SZ")
    results=[]; failed=[]
    # Divine reference is fetched once. Scout's currency prices are normalized
    # to the league base; divide reward EX by Divine EX to get Divine value.
    divine_obj=fetch_currency("divine-orb")
    divine_exalted=price(divine_obj)
    if not divine_exalted or divine_exalted<=0:
        raise RuntimeError("Could not obtain Divine Orb price in Exalted.")
    for r in cfg["rewards"]:
        s=r.get("scout",{})
        try:
            if r["type"]=="currency":
                api=s.get("api_id")
                if not api: raise RuntimeError("missing api_id")
                obj=fetch_currency(api)
            else:
                iid=s.get("item_id")
                if iid is None: raise RuntimeError("missing item_id")
                obj=fetch_item(iid)
            p=price(obj)
            results.append({
                "name":r["name"],"type":r["type"],
                "price_exalted":p,
                "price_divine":(p/divine_exalted if p is not None else None)
            })
        except Exception as e:
            failed.append({"name":r["name"],"type":r["type"],"error":str(e)})
        time.sleep(0.05)
    results.sort(key=lambda x:(x["price_exalted"] is None,-(x["price_exalted"] or 0)))
    out={"schema_version":4,"source":"poe2scout","generated_at":stamp,"realm":REALM,
         "league":LEAGUE,"reference":{"base":"exalted-orb","divine_price_exalted":divine_exalted},
         "rewards":results,"stats":{"requested":len(cfg["rewards"]),"priced":len(results),"failed":len(failed)}}
    if failed: out["failed"]=failed
    DATA.mkdir(parents=True,exist_ok=True); SNAP.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(out,ensure_ascii=False,indent=2)+"\n"
    (DATA/"current.json").write_text(payload,encoding="utf-8")
    hp=SNAP/now.strftime("%Y-%m-%d/%H.json"); hp.parent.mkdir(parents=True,exist_ok=True); hp.write_text(payload,encoding="utf-8")
    cutoff=time.time()-RETENTION*86400
    for p in SNAP.rglob("*.json"):
        try:
            if p.stat().st_mtime<cutoff: p.unlink()
        except FileNotFoundError: pass
    (DATA/"meta.json").write_text(json.dumps({
      "schema_version":4,"generated_at":stamp,"league":LEAGUE,
      "current_file":"data/current.json","historical_file":str(hp.relative_to(ROOT)),
      "reward_count":len(results),"failed_count":len(failed),"divine_price_exalted":divine_exalted
    },indent=2)+"\n",encoding="utf-8")
    print(f"OK {LEAGUE}: {len(results)} prices, {len(failed)} failures, Divine={divine_exalted:.4f} EX")

if __name__=="__main__":
    main()
