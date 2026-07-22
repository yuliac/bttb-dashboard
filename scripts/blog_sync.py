#!/usr/bin/env python3
"""
BttB Written Content sync: Wix Blog API -> Airtable "Written Content Management".
Idempotent upserts keyed on Wix IDs. Runs daily via GitHub Actions.

Credentials come from environment variables (GitHub Actions secrets):
  WIX_API_KEY     Wix API key
  AIRTABLE_PAT    Airtable Personal Access Token (access to the target base)
Optional overrides:
  WIX_SITE_ID     (default: the BttB site)
  AIRTABLE_BASE   (default: Written Content Management)
  SYNC_MODE       "backfill" for full history; anything else = rolling window (default)
"""
import os, json, sys, time, datetime, ssl, urllib.request, urllib.error
try:
    import certifi
    SSLCTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSLCTX = ssl.create_default_context()

WIX_KEY = os.environ["WIX_API_KEY"].strip()
AT_PAT  = os.environ["AIRTABLE_PAT"].strip()
SITE_ID = os.environ.get("WIX_SITE_ID", "a3c9d14d-943f-4a77-9070-18f36a39d99e").strip()
BASE    = os.environ.get("AIRTABLE_BASE", "appnMeSbhbj9bIMJU").strip()
BACKFILL = os.environ.get("SYNC_MODE", "").strip().lower() == "backfill"

T = {"cat":"tbllwujbyV9d8jahW","auth":"tbljYzLRXGCOAUBhU","post":"tblDSSFzUN14Bdct4","cal":"tblTXmN7W0wnmocIQ","tag":"tbl4IziUieFXejs4X"}
F = {
    "cat_name":"fldSRAWYTmsBjjDGa","cat_wix":"fld3bB8Oo0JftmMOP","cat_role":"fld5rs0hKw2zE5Qs5","cat_count":"flddpF5G3QcEkanWb",
    "au_name":"fldIByuQC1RMWDOuO","au_wix":"fldnmNGQu0HVzEqlY","au_role":"fldRi9rgUpwHmKtMV","au_rota":"fldhUKz8EKVjuM68c","au_paused":"fldmZT1wkcAGzjo4B",
    "p_title":"fldTphLZPTIFNhtHT","p_status":"fldsnLsXRhWdVPBmy","p_air":"fldpbiH2DGIgroiW0","p_url":"fldbIvvO29JH97Q5D",
    "p_slug":"fldveFBTywKZkvxzO","p_exc":"fldZitVGsBmOZJMLW","p_ttr":"fld4S9YSQJuRK7tL3","p_body":"fldDIWP1Zmkvzfhzm",
    "p_wix":"fldCqWfBoTfs9R9tG","p_cat":"fldI1tu9Cwt8VBhx8","p_auth":"fldJrYc4xGUa6duzY","p_tags":"fld3352E1SSAXhFPr",
    "tag_name":"fldJcR1ZhbBD1sva8","tag_wix":"fldm63b4sRTIfyKDW","tag_count":"fldmw1z8RscEfZrnw",
    "c_slot":"fldq8D9pV0POveJ46","c_date":"flds9LLqGy7QNK4UM","c_wd":"fldNiLwMPAI8IRZVh","c_ecat":"fldHaJYQmuoMBvENI",
    "c_status":"fldU4mGqwYbgKVC0x","c_eauth":"fldgouU02fwl8FB8N","c_post":"fldHCOO9k12SptnSe",
}
WEEKLY = {"Articles","Sunday Review","BttB Daily Article","Spiritually Fit Today Article","Alive & Sober"}
DAILY  = {"Daily Workout","Daily Prayer","Forward Devotional","Your Spiritual Encouragement","Win Today","Bible Knowledge Level 2"}
ROTA = {0:("Articles","Braden"),1:("Articles","Arnie"),2:("Articles","Chuck"),
        3:("BttB Daily Article","Braden"),4:("Spiritually Fit Today Article","House"),
        5:("Alive & Sober","House"),6:("Sunday Review","Arnie")}
AUTHOR_META = {
    "Pastor Braden Pedersen":("Columnist","Mon (Articles) + Thu (BttB Daily Article)"),
    "Arnie Cole":("Columnist","Tue (Articles) + Sun (Sunday Review)"),
    "Chuck Lawless":("Columnist","Wed (Articles)"),
    "Back to the Bible":("House account","Daily evergreen + Fri/Sat recaps"),
}
NICK = {"Pastor Braden Pedersen":"Braden","Arnie Cole":"Arnie","Chuck Lawless":"Chuck","Back to the Bible":"House"}

def http(url, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, context=SSLCTX) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503):
                time.sleep(2*(attempt+1)); continue
            raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}")
    raise RuntimeError("too many retries: "+url)

def wix(path, payload):
    return http("https://www.wixapis.com"+path, data=payload,
                headers={"Authorization":WIX_KEY,"wix-site-id":SITE_ID,"Content-Type":"application/json"})
def wix_get(path):
    return http("https://www.wixapis.com"+path, headers={"Authorization":WIX_KEY,"wix-site-id":SITE_ID})

def at_upsert(table, records, merge_fields):
    for i in range(0,len(records),10):
        payload={"performUpsert":{"fieldsToMergeOn":merge_fields},
                 "records":[{"fields":r} for r in records[i:i+10]], "typecast":False}
        http(f"https://api.airtable.com/v0/{BASE}/{table}", data=payload,
             headers={"Authorization":"Bearer "+AT_PAT,"Content-Type":"application/json"}, method="PATCH")
        time.sleep(0.25)

def at_list(table, fields):
    out=[]; offset=None
    while True:
        q="?returnFieldsByFieldId=true&"+"&".join(f"fields%5B%5D={f}" for f in fields)+"&pageSize=100"
        if offset: q+="&offset="+offset
        res=http(f"https://api.airtable.com/v0/{BASE}/{table}{q}", headers={"Authorization":"Bearer "+AT_PAT})
        out+=res.get("records",[]); offset=res.get("offset")
        if not offset: break
    return out

def richtext_to_plain(rc):
    if not rc: return ""
    parts=[]
    def walk(n):
        if isinstance(n,dict):
            if n.get("type")=="TEXT":
                t=n.get("textData",{}).get("text");  parts.append(t) if t else None
            for c in n.get("nodes",[]) or []: walk(c)
        elif isinstance(n,list):
            for c in n: walk(c)
    walk(rc.get("nodes",rc))
    return " ".join(p for p in parts if p).strip()

def sync_categories():
    cats=wix_get("/blog/v3/categories?paging.limit=100").get("categories",[])
    recs=[{F["cat_name"]:c["label"],F["cat_wix"]:c["id"],
           F["cat_role"]:("Weekly schedule" if c["label"] in WEEKLY else "Daily evergreen" if c["label"] in DAILY else "Archive"),
           F["cat_count"]:c.get("postCount",0)} for c in cats]
    at_upsert(T["cat"],recs,[F["cat_wix"]])
    m={r["fields"].get(F["cat_wix"]):r["id"] for r in at_list(T["cat"],[F["cat_wix"]])}
    print(f"categories: {len(cats)}")
    return m, {c["id"]:c["label"] for c in cats}

def sync_tags():
    tags=[]; offset=0
    while True:
        r=wix_get(f"/blog/v3/tags?paging.limit=100&paging.offset={offset}")
        batch=r.get("tags",[]); tags+=batch
        if len(batch)<100: break
        offset+=len(batch)
    recs=[{F["tag_name"]:t["label"],F["tag_wix"]:t["id"],F["tag_count"]:t.get("postCount",0)} for t in tags]
    at_upsert(T["tag"],recs,[F["tag_wix"]])
    m={r["fields"].get(F["tag_wix"]):r["id"] for r in at_list(T["tag"],[F["tag_wix"]])}
    print(f"tags: {len(tags)}")
    return m

def member_name(mid, cache):
    if mid in cache: return cache[mid]
    try:
        m=wix_get(f"/members/v1/members/{mid}").get("member",{})
        p=m.get("profile",{}); c=m.get("contact",{})
        n=p.get("nickname") or ((c.get("firstName","")+" "+c.get("lastName","")).strip()) or mid[:8]
    except Exception:
        n=mid[:8]
    cache[mid]=n; return n

def sync_authors(member_ids, cache):
    recs=[]
    for mid in member_ids:
        name=member_name(mid,cache); role,rota=AUTHOR_META.get(name,("House account",""))
        recs.append({F["au_name"]:name,F["au_wix"]:mid,F["au_role"]:role,F["au_rota"]:rota})
    at_upsert(T["auth"],recs,[F["au_wix"]])
    m={r["fields"].get(F["au_wix"]):r["id"] for r in at_list(T["auth"],[F["au_wix"]])}
    print(f"authors: {len(member_ids)}")
    return m

def fetch_all_posts():
    posts=[]; offset=0
    while True:
        r=wix("/blog/v3/posts/query",{"sort":[{"fieldName":"firstPublishedDate","order":"DESC"}],
              "paging":{"limit":100,"offset":offset},"fieldsets":["URL","RICH_CONTENT"]})
        batch=r.get("posts",[])
        for p in batch:
            posts.append(dict(id=p.get("id"),title=p.get("title"),slug=p.get("slug"),memberId=p.get("memberId"),
                categoryIds=p.get("categoryIds") or [],tagIds=p.get("tagIds") or [],
                date=p.get("firstPublishedDate"),status="Published",
                excerpt=(p.get("excerpt") or "")[:900],ttr=p.get("minutesToRead"),
                body=richtext_to_plain(p.get("richContent"))[:9000]))
        offset+=len(batch)
        if not BACKFILL or len(batch)<100: break
    r=wix("/blog/v3/draft-posts/query",{"filter":{"status":{"$eq":"SCHEDULED"}},
          "sort":[{"fieldName":"scheduledPublishDate","order":"ASC"}],"paging":{"limit":100},
          "fieldsets":["RICH_CONTENT"]})
    pub_slugs={p["slug"] for p in posts}
    for p in r.get("draftPosts",[]):
        d=p.get("scheduledPublishDate")
        if not d or p.get("slug") in pub_slugs: continue
        posts.append(dict(id=p.get("id"),title=p.get("title"),slug=p.get("slug"),memberId=p.get("memberId"),
            categoryIds=p.get("categoryIds") or [],tagIds=p.get("tagIds") or [],
            date=d,status="Scheduled",excerpt=(p.get("excerpt") or "")[:900],ttr=p.get("minutesToRead"),
            body=richtext_to_plain(p.get("richContent"))[:9000]))
    return posts

def sync_posts(posts, catmap, authmap, tagmap):
    recs=[]
    for p in posts:
        f={F["p_title"]:p["title"] or "(untitled)",F["p_status"]:p["status"],F["p_wix"]:p["id"]}
        if p["date"]: f[F["p_air"]]=p["date"]
        cids=[catmap[c] for c in p["categoryIds"] if c in catmap]
        if cids: f[F["p_cat"]]=cids
        tids=[tagmap[t] for t in p.get("tagIds",[]) if t in tagmap]
        if tids: f[F["p_tags"]]=tids
        aid=authmap.get(p["memberId"])
        if aid: f[F["p_auth"]]=[aid]
        if p["slug"]: f[F["p_slug"]]=p["slug"]; f[F["p_url"]]="https://www.backtothebible.org/post/"+p["slug"]
        if p["excerpt"]: f[F["p_exc"]]=p["excerpt"]
        if p.get("ttr"): f[F["p_ttr"]]=p["ttr"]
        if p["body"]: f[F["p_body"]]=p["body"]
        recs.append(f)
    at_upsert(T["post"],recs,[F["p_wix"]])
    print(f"posts: {len(recs)}")

def sync_calendar(posts, id2label, authmap_by_nick):
    today=datetime.date.today()
    start=today-datetime.timedelta(days=today.weekday()+14)
    end=start+datetime.timedelta(days=7*6-1)
    by_slot={}
    for p in posts:
        if not p["date"]: continue
        try: d=datetime.datetime.fromisoformat(p["date"].replace("Z","+00:00")).date()
        except: continue
        for lbl in {id2label.get(c) for c in p["categoryIds"]} & WEEKLY:
            by_slot.setdefault((d,lbl),p["id"])
    wix2rec={r["fields"].get(F["p_wix"]):r["id"] for r in at_list(T["post"],[F["p_wix"]])}
    status_by={p["id"]:p["status"] for p in posts}
    paused_nicks=set()
    for r in at_list(T["auth"],[F["au_name"],F["au_paused"]]):
        if r["fields"].get(F["au_paused"]):
            nm=r["fields"].get(F["au_name"],""); paused_nicks.add(NICK.get(nm,nm))
    recs=[]; d=start
    while d<=end:
        cat,nick=ROTA[d.weekday()]
        f={F["c_slot"]:f"{d.strftime('%a')} {d.month}/{d.day} · {nick} · {cat}",
           F["c_date"]:d.isoformat(),F["c_wd"]:d.strftime('%a'),F["c_ecat"]:cat}
        au=authmap_by_nick.get(nick)
        if au: f[F["c_eauth"]]=[au]
        pid=by_slot.get((d,cat))
        if pid and wix2rec.get(pid):
            f[F["c_status"]]=status_by.get(pid,"Published"); f[F["c_post"]]=[wix2rec[pid]]
        elif nick in paused_nicks:
            f[F["c_status"]]="Skipped / NA"
        else:
            f[F["c_status"]]="Not received"
        recs.append(f); d+=datetime.timedelta(days=1)
    at_upsert(T["cal"],recs,[F["c_slot"]])
    print(f"calendar slots: {len(recs)}")

def main():
    catmap,id2label=sync_categories()
    tagmap=sync_tags()
    posts=fetch_all_posts()
    print(f"fetched {len(posts)} posts (backfill={BACKFILL})")
    cache={}
    authmap=sync_authors(sorted({p["memberId"] for p in posts if p["memberId"]}),cache)
    by_nick={}
    for mid,rid in authmap.items():
        nm=cache.get(mid,""); by_nick[NICK.get(nm,nm)]=rid
    sync_posts(posts,catmap,authmap,tagmap)
    sync_calendar(posts,id2label,by_nick)
    print("done.")

if __name__=="__main__":
    main()
