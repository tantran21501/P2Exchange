#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'; SNAPSHOTS=DATA/'snapshots'
BASE=os.getenv('SCOUT_API_BASE','https://api.poe2scout.com').rstrip('/')
REALM=os.getenv('SCOUT_REALM','poe2'); LEAGUE=os.getenv('SCOUT_LEAGUE','Runes of Aldur').strip()
UA=os.getenv('SCOUT_USER_AGENT','POE2-Expedition-Radar-CurrencySnapshot/1.0')
TIMEOUT=int(os.getenv('SCOUT_TIMEOUT','30')); RETRIES=int(os.getenv('SCOUT_RETRIES','3'))
RETENTION=int(os.getenv('SNAPSHOT_RETENTION_DAYS','30'))

def get(path):
    url=f'{BASE}/{path.lstrip("/")}'; err=None
    for attempt in range(RETRIES):
        try:
            req=Request(url,headers={'Accept':'application/json','User-Agent':UA})
            with urlopen(req,timeout=TIMEOUT) as r: return json.loads(r.read().decode())
        except (HTTPError,URLError,TimeoutError,json.JSONDecodeError) as e:
            err=e
            if attempt+1<RETRIES: time.sleep(2**attempt)
    raise RuntimeError(f'GET failed: {url}: {err}')

def first(obj,keys):
    if isinstance(obj,dict):
        for k in keys:
            if obj.get(k) not in (None,''): return obj[k]
        for v in obj.values():
            x=first(v,keys)
            if x is not None: return x
    elif isinstance(obj,list):
        for v in obj:
            x=first(v,keys)
            if x is not None: return x
    return None

def candidates(obj):
    if isinstance(obj,list): return obj
    if not isinstance(obj,dict): return []
    for k in ('data','items','pairs','currencies','leagues','results','value'):
        if isinstance(obj.get(k),list): return obj[k]
    return next((v for v in obj.values() if isinstance(v,list)),[])

def select_league(obj):
    if LEAGUE: return LEAGUE
    rows=candidates(obj)
    # Both SC and HC can be marked current; prefer current non-HC.
    for x in rows:
        if isinstance(x,dict):
            n=first(x,('value','name','id'))
            current=x.get('isCurrent') or x.get('active') or x.get('isActive') or x.get('current')
            if current and n and not str(n).lower().startswith('hc '): return str(n)
    for x in rows:
        n=first(x,('value','name','id')) if isinstance(x,dict) else x
        if n and not str(n).lower().startswith('hc '): return str(n)
    raise RuntimeError('Cannot determine softcore league; set SCOUT_LEAGUE explicitly.')

def normalize_currency(obj):
    out=[]
    for x in candidates(obj):
        if not isinstance(x,dict): continue
        y=dict(x); y['id']=first(x,('id','apiId','api_id','itemId')); y['name']=first(x,('name','displayName','text')); y['icon']=first(x,('icon','image','iconUrl')); out.append(y)
    return out

def normalize_pairs(obj):
    out=[]
    for x in candidates(obj):
        if not isinstance(x,dict): continue
        y=dict(x); y['currency_one_id']=first(x,('currencyOneItemId','currency_one_item_id','itemOneId','item1Id','fromId')); y['currency_two_id']=first(x,('currencyTwoItemId','currency_two_item_id','itemTwoId','item2Id','toId')); y['rate']=first(x,('rate','ratio','value','price')); y['volume']=first(x,('volume','traded','quantity','count')); out.append(y)
    return out

def numeric_price(v):
    if isinstance(v,(int,float)) and not isinstance(v,bool): return float(v)
    if isinstance(v,dict):
        for k in ('price','value','amount','currentPrice'):
            if k in v:
                x=numeric_price(v[k])
                if x is not None: return x
    return None

def extract_price_rows(obj):
    rows=[]
    def walk(x):
        if isinstance(x,dict):
            api=first(x,('apiId','api_id')); name=first(x,('text','name','displayName'))
            if api and name and ('currentPrice' in x or 'price' in x or 'priceLogs' in x):
                p=numeric_price(x.get('currentPrice')) or numeric_price(x.get('price'))
                rows.append({'api_id':str(api),'name':str(name),'price_exalted':p,'current_quantity':x.get('currentQuantity')})
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj); seen=set(); out=[]
    for r in rows:
        if r['api_id'] not in seen: seen.add(r['api_id']); out.append(r)
    return out

def divine_price(rows):
    for r in rows:
        if r['name'].strip().lower()=='divine orb': return r.get('price_exalted')
    return None

def prune():
    if RETENTION<=0: return
    cutoff=time.time()-RETENTION*86400
    for p in SNAPSHOTS.rglob('*.json'):
        try:
            if p.stat().st_mtime<cutoff: p.unlink()
        except FileNotFoundError: pass

def main():
    now=datetime.now(timezone.utc); stamp=now.strftime('%Y-%m-%dT%H:%M:%SZ'); hour=now.strftime('%Y-%m-%d/%H.json')
    realm=quote(REALM,safe=''); leagues=get(f'{realm}/Leagues'); league=select_league(leagues); lp=quote(league,safe=''); root=f'{realm}/Leagues/{lp}'
    references=get(f'{root}/ReferenceCurrencies'); exchange=get(f'{root}/ExchangeSnapshot'); pairs=get(f'{root}/SnapshotPairs'); currencies=get(f'{root}/Currencies/ByCategory')
    rows=extract_price_rows(currencies); divine_exalted=divine_price(rows)
    for r in rows:
        p=r.get('price_exalted'); r['price_divine']=p/divine_exalted if p is not None and divine_exalted else None
    snap={'schema_version':1,'source':'poe2scout','generated_at':stamp,'realm':REALM,'league':league,
          'reference_currencies':references,'exchange_snapshot':exchange,'snapshot_pairs':pairs,'currencies':currencies,
          'normalized':{'currencies':normalize_currency(currencies),'pairs':normalize_pairs(pairs),'reference_currencies':normalize_currency(references)},
          'price_index':{'base_currency':'exalted_orb','divine_price_exalted':divine_exalted,'currencies':rows}}
    DATA.mkdir(parents=True,exist_ok=True); SNAPSHOTS.mkdir(parents=True,exist_ok=True); payload=json.dumps(snap,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
    (DATA/'current.json').write_text(payload,encoding='utf-8'); hp=SNAPSHOTS/hour; hp.parent.mkdir(parents=True,exist_ok=True); hp.write_text(payload,encoding='utf-8')
    (DATA/'meta.json').write_text(json.dumps({'schema_version':1,'source':'poe2scout','generated_at':stamp,'realm':REALM,'league':league,'current_file':'data/current.json','historical_file':f'data/snapshots/{hour}'},indent=2)+'\n',encoding='utf-8')
    prune(); print(f'OK {league}: {DATA}/current.json')

if __name__=='__main__':
    try: main()
    except Exception as e: print(f'ERROR: {e}',file=sys.stderr); raise
