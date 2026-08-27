#!/usr/bin/env python3
"""
Refresh data/rewards.json from the public PoE2DB Runeshape Combinations page.

This is intentionally a catalog step, not the hourly price snapshot step.
It extracts the four deterministic reward categories:
Currency, Runes, Alloys, Gems.
Generic Unique buckets are excluded because they are not deterministic items.
"""
from __future__ import annotations
import json, re, sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/rewards.json"
URL="https://poe2db.tw/Runeshape_Combinations"

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts=[]
    def handle_data(self,data):
        s=" ".join(data.split())
        if s: self.parts.append(s)
    def text(self): return "\n".join(self.parts)

def fetch():
    req=Request(URL,headers={"User-Agent":"POE2-Expedition-Radar-Catalog/1.0","Accept":"text/html"})
    with urlopen(req,timeout=30) as r:
        return r.read().decode("utf-8","replace")

def slug_name(s):
    s=re.sub(r"\s+"," ",s).strip()
    s=re.sub(r"^(?:\d+x\s+)", "", s, flags=re.I)
    return s

def main():
    html=fetch()
    p=TextParser(); p.feed(html)
    lines=[x.strip() for x in p.text().splitlines() if x.strip()]
    cats={"currency":[],"runes":[],"alloys":[],"gems":[]}
    current=None
    stop={"uniques"}
    # The page exposes category headings such as "Alloys /14".
    for line in lines:
        m=re.match(r"^(Alloys|Currency|Gems|Runes|Uniques)\s*/\d+$",line,re.I)
        if m:
            c=m.group(1).lower()
            current=None if c in stop else c
            continue
        if not current: continue
        if re.match(r"^(?:Act\d+|Lv\d|[0-9]+(?:\.[0-9]+)?|Button:|Reset|Input:|Search|Generic Reward)",line,re.I):
            continue
        if line.startswith("Image:"): continue
        # remove obvious level/quantity suffixes that are presentation metadata
        quantity = 1
        qm = re.search(r"\s+x(\d+)\s*$", line, re.I)
        if qm:
            quantity = int(qm.group(1))
            line = line[:qm.start()].strip()
        name=re.sub(r"\s+Lv(?:\d+|\d+-\d+|\d+\+)$","",line)
        if not name or len(name)>120: continue
        if name.lower() in {"very rare","rare","unique","item","act2","act4"}: continue
        # Keep actual reward-looking names only.
        if any(tok in name for tok in (" Orb"," Rune","Alloy","Flux","Gem","Saga","Boon","Tending","Hunt",
                                       "Legacy","Triumph","Sidereus","Carnage","Epiphany","Creativity","Betrayal",
                                       "Passion","Breath","Ire","Key","Lock","Pile","Verisium","Blades","Barrier",
                                       "Shell","Splinters","March","Living","Flame","Runeforged","Infusion","Extraction",
                                       "Manifestations","Powered","Remnants","Pillars","Dead","Reprieve","Leylines",
                                       "Exchange","Skyfall","Refutation","Cascade","Healing","Confrontation","Vital Flame",
                                       "Accumulation","Renown","Culmination","Acrobatics","Blossom","Prism","Foundations",
                                       "Consistency")):
            if name not in cats[current]:
                cats[current].append(name)
    # Restore metadata and validate that parsing did not silently collapse.
    old=json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    out={
      "schema_version":3,
      "league":old.get("league","Runes of Aldur"),
      "source_catalog":old.get("source_catalog",{"primary":URL}),
      "expected_category_counts":{"currency":92,"runes":131,"alloys":14,"gems":61,"uniques":23},
      "rewards":[]
    }
    for typ in ("currency","runes","alloys","gems"):
        for name in cats[typ]:
            out["rewards"].append({"name":name,"type":typ,
              "scout":{"kind":"currency" if typ=="currency" else "item","api_id":None,"item_id":None}})
    # Do not overwrite a previously resolved ID if names still match.
    oldmap={r["name"]:r.get("scout",{}) for r in old.get("rewards",[])}
    for r in out["rewards"]:
        if r["name"] in oldmap:
            r["scout"].update({k:v for k,v in oldmap[r["name"]].items() if v})
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Catalog written:",OUT)
    for k in cats:
        print(f"{k}: {len(cats[k])}")
    if len(cats["alloys"]) < 14 or len(cats["runes"]) < 100:
        print("WARNING: catalog parser found fewer entries than expected; keep the previous catalog and inspect the source HTML.",file=sys.stderr)

if __name__=="__main__":
    main()
