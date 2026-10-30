#!/usr/bin/env python3
"""Movie Board data builder.   python movies.py recent    |    python movies.py history

TMDb (secret TMDB_Key): movie lists, poster, trailer, overview, user score, revenue fallback.
OMDb (secret OMDB_Key): Rotten Tomatoes %, IMDb rating, domestic box office.
Movies are stored one JSON file per release month (data/YYYY-MM.json) plus data/index.json.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

TMDB_KEY = os.environ.get("TMDB_Key", "").strip()      # GitHub secret TMDB_Key
OMDB_KEY = os.environ.get("OMDB_Key", "").strip()      # GitHub secret OMDB_Key
DATA, STATE = Path("data"), Path("state")
OMDB_LIMIT = int(os.environ.get("OMDB_DAILY_LIMIT", "1000"))
OMDB_MARGIN = int(os.environ.get("OMDB_MARGIN", "50"))          # requests never spent
RECENT_BUDGET = int(os.environ.get("OMDB_RECENT_BUDGET", "300"))  # most the daily job may spend
RECENT_MONTHS = 3
RECENT_PAGES = int(os.environ.get("RECENT_PAGES", "15"))        # 20 movies per page
REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "7"))         # re-ask OMDb for a movie after this
HIST_MIN_VOTES = int(os.environ.get("HISTORY_MIN_VOTES", "50"))
HIST_FLOOR = int(os.environ.get("HISTORY_FLOOR_YEAR", "1900"))
TODAY = datetime.now(timezone.utc).date()


def read(path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def add_months(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    k = y * 12 + m - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def month_bounds(ym: str):
    last = date.fromisoformat(add_months(ym, 1) + "-01") - timedelta(days=1)
    return ym + "-01", last.isoformat()


# ---------- HTTP ----------
def fetch(url, params, headers=None):
    for i in range(4):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 + 3 * i)
                continue
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(1 + i)
    return {}


def tmdb(path, **params):
    headers = {}
    if len(TMDB_KEY) > 40:                     # a v4 "read access token" works too
        headers["Authorization"] = "Bearer " + TMDB_KEY
    else:
        params["api_key"] = TMDB_KEY
    return fetch("https://api.themoviedb.org/3" + path, params, headers)


def discover(**params):
    """Yield TMDb movie ids for a discover query. A failed page raises, so a month is never skipped."""
    page = 1
    while True:
        d = tmdb("/discover/movie", page=page, **params)
        if "results" not in d:
            raise RuntimeError(f"TMDb discover failed: {d.get('status_message', 'no answer')}")
        yield from (m["id"] for m in d["results"])
        if page >= min(d.get("total_pages", 0), 500):
            return
        page += 1


class Omdb:
    def __init__(self, budget: int):
        self.left, self.used, self.blocked = max(0, budget), 0, False

    def exhausted(self) -> bool:
        return self.blocked or self.left <= 0

    def get(self, imdb_id: str):
        if self.exhausted() or not imdb_id or not OMDB_KEY:
            return None
        self.left -= 1
        self.used += 1
        d = fetch("https://www.omdbapi.com/", {"apikey": OMDB_KEY, "i": imdb_id})
        if d.get("Response") != "True":
            if "limit" in str(d.get("Error", "")).lower():
                self.blocked = True
            return None
        return d


# ---------- daily OMDb allowance shared by both jobs ----------
def usage_today() -> int:
    u = read(STATE / "usage.json", {})
    return u.get("omdb", 0) if u.get("date") == TODAY.isoformat() else 0


def budget_left() -> int:
    return max(0, OMDB_LIMIT - OMDB_MARGIN - usage_today())


# ---------- scoring ----------
def num(s):
    try:
        return float(re.sub(r"[^\d.]", "", str(s or "")))
    except ValueError:
        return None


def box_score(v):
    """Box office on a 0-100 log scale: $100K = 0, $1B = 100."""
    return None if not v or v <= 0 else max(0.0, min(100.0, (math.log10(v) - 5) / 4 * 100))


def build(d: dict, old: dict | None, o: dict | None) -> dict:
    it = dict(old or {})
    vids = [v for v in (d.get("videos") or {}).get("results", [])
            if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("key")]
    vids.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    vids.sort(key=lambda v: not v.get("official"))
    pp = d.get("poster_path")
    it.update(
        id=d["id"], title=d.get("title") or "", release=d.get("release_date") or "",
        overview=d.get("overview") or "", imdb_id=d.get("imdb_id") or "",
        poster=("https://image.tmdb.org/t/p/w342" + pp) if pp else it.get("poster", ""),
        trailer=("https://www.youtube.com/watch?v=" + vids[0]["key"]) if vids else "",
        tmdb_user=round(d["vote_average"] * 10, 1) if (d.get("vote_count") or 0) >= 10 else None,
        tmdb_revenue=d.get("revenue") or 0)
    if o:
        rt = next((num(r.get("Value")) for r in o.get("Ratings", []) if r.get("Source") == "Rotten Tomatoes"), None)
        it.update(rt=rt, imdb=num(o.get("imdbRating")), omdb_box=num(o.get("BoxOffice")), omdb_at=TODAY.isoformat())
        if not it["poster"] and o.get("Poster", "N/A") != "N/A":
            it["poster"] = o["Poster"]
    for k in ("rt", "imdb", "omdb_box", "omdb_at"):
        it.setdefault(k, None)
    box = it["omdb_box"] or it["tmdb_revenue"] or None      # domestic (OMDb) first, worldwide (TMDb) if missing
    it["box"], it["bo_src"] = box, ("omdb" if it["omdb_box"] else "tmdb" if box else "")
    it["aud"] = it["tmdb_user"]          # Popcornmeter is not in OMDb or TMDb; TMDb user score stands in (README)
    parts = [it["rt"], it["aud"], it["imdb"] * 10 if it["imdb"] is not None else None, box_score(box)]
    parts = [p for p in parts if p is not None]
    it["score"] = round(sum(parts) / len(parts), 1) if parts else None   # all available scores, weighted evenly
    it["n"] = len(parts)
    return it


# ---------- storage: one file per release month ----------
class Store:
    def __init__(self):
        self.s, self.dirty = {}, set()

    def month(self, ym):
        if ym not in self.s:
            self.s[ym] = {str(m["id"]): m for m in read(DATA / f"{ym}.json", {}).get("items", [])}
        return self.s[ym]

    def put(self, it):
        self.month(it["release"][:7])[str(it["id"])] = it
        self.dirty.add(it["release"][:7])

    def save(self):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for ym in self.dirty:
            items = sorted(self.s[ym].values(), key=lambda m: -(m.get("score") or -1))
            write(DATA / f"{ym}.json", {"month": ym, "updated": stamp, "items": items})
        self.dirty.clear()
        months = sorted(((p.stem, len(read(p, {}).get("items", []))) for p in DATA.glob("????-??.json")), reverse=True)
        write(DATA / "index.json", {"updated": stamp, "months": [{"m": m, "n": n} for m, n in months]})


def checkpoint(store: Store, o: Omdb):
    store.save()
    write(STATE / "usage.json", {"date": TODAY.isoformat(), "omdb": usage_today() + o.used})
    o.used = 0


def stale(old) -> bool:
    return not old or not old.get("omdb_at") or (TODAY - date.fromisoformat(old["omdb_at"])).days >= REFRESH_DAYS


def refresh(mid, store: Store, o: Omdb):
    d = tmdb(f"/movie/{mid}", append_to_response="videos", language="en-US")
    if not d.get("id") or len(d.get("release_date") or "") < 10:
        return
    old = store.month(d["release_date"][:7]).get(str(mid))
    store.put(build(d, old, o.get(d.get("imdb_id")) if stale(old) else None))


# ---------- jobs ----------
def run_recent():
    start = add_months(TODAY.strftime("%Y-%m"), -RECENT_MONTHS) + "-01"
    ids = list(discover(**{"primary_release_date.gte": start, "primary_release_date.lte": TODAY.isoformat(),
                           "with_release_type": "2|3", "sort_by": "popularity.desc"}))[: RECENT_PAGES * 20]
    store, o = Store(), Omdb(min(RECENT_BUDGET, budget_left()))
    print(f"{len(ids)} movies since {start}; OMDb budget {o.left}")
    try:
        for n, mid in enumerate(ids, 1):
            refresh(mid, store, o)
            if n % 100 == 0:
                checkpoint(store, o)
    finally:
        checkpoint(store, o)
    print("Done.")


def run_history():
    store, o = Store(), Omdb(budget_left())
    state = read(STATE / "history_state.json", {})
    cur = state.get("next_month") or add_months(TODAY.strftime("%Y-%m"), -(RECENT_MONTHS + 1))
    print(f"History from {cur}; OMDb budget {o.left}")
    n = 0
    try:
        while int(cur[:4]) >= HIST_FLOOR and not o.exhausted():
            a, b = month_bounds(cur)
            ids = list(discover(**{"primary_release_date.gte": a, "primary_release_date.lte": b,
                                   "vote_count.gte": HIST_MIN_VOTES, "sort_by": "vote_count.desc"}))
            have = store.month(cur)
            finished = True
            for mid in (i for i in ids if str(i) not in have):
                if o.exhausted():
                    finished = False
                    break
                refresh(mid, store, o)
                n += 1
                if n % 100 == 0:
                    checkpoint(store, o)
            if not finished:
                break
            print(f"{cur} complete ({len(store.month(cur))} movies)")
            cur = add_months(cur, -1)
            write(STATE / "history_state.json", {"next_month": cur})
    finally:
        checkpoint(store, o)
    print("History is complete." if int(cur[:4]) < HIST_FLOOR else f"Stopped at {cur}; OMDb allowance used for today.")


if __name__ == "__main__":
    if not TMDB_KEY:
        sys.exit("TMDB_Key is not set.")
    if not OMDB_KEY:
        print("Warning: OMDB_Key is not set, so there will be no Rotten Tomatoes, IMDb or domestic box office data.")
    job = {"recent": run_recent, "history": run_history}.get(sys.argv[1] if len(sys.argv) > 1 else "")
    job() if job else sys.exit("usage: python movies.py recent | history")
