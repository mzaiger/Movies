#!/usr/bin/env python3
"""Movie Board data builder.   python movies.py recent    |    python movies.py history    |    python movies.py index

TMDb (secret TMDB_Key): movie lists, poster, trailer, overview, revenue fallback.
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
SKIP_FILE = STATE / "skipped.json"                                # TMDb ids that failed the board rules and are old enough to leave alone
OMDB_LIMIT = int(os.environ.get("OMDB_DAILY_LIMIT", "1000"))
OMDB_MARGIN = int(os.environ.get("OMDB_MARGIN", "50"))          # requests never spent
RECENT_BUDGET = int(os.environ.get("OMDB_RECENT_BUDGET", "300"))  # most the daily job may spend
RECENT_MONTHS = int(os.environ.get("RECENT_MONTHS", "12"))      # how far back the daily job looks
RECENT_PAGES = int(os.environ.get("RECENT_PAGES", "50"))        # 20 movies per page (50 = top 1000 by popularity)
REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "7"))         # re-ask OMDb for a movie after this
NEW_DAYS = int(os.environ.get("NEW_MOVIE_DAYS", "14"))         # a movie this new that is not ranked yet is re-asked every day
HIST_MIN_VOTES = int(os.environ.get("HISTORY_MIN_VOTES", "50"))
MIN_BOX = 1_000_000    # box office below this never counts
SURE_BOX = 2_000_000   # from MIN_BOX up to this, box office only counts if the movie also has an RT or IMDb score (TMDb revenue is community-edited)
MIN_VOTES = int(os.environ.get("MIN_VOTES", "0"))               # IMDb votes needed to stay on the board (0 = off); see `python movies.py report`
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "60"))              # a movie younger than this is never dropped: ratings and box office may still arrive
VOTES_GRACE_DAYS = int(os.environ.get("VOTES_GRACE_DAYS", "14"))  # a movie this new is exempt from MIN_VOTES
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


def has_box(m: dict) -> bool:
    box = m.get("box") or 0
    return box >= SURE_BOX or (box >= MIN_BOX and (m.get("rt") is not None or m.get("imdb") is not None))


def ranked(m: dict) -> bool:
    """On the board: a counted box office ($2M or more, or $1M or more with an RT or IMDb score), or both RT and IMDb."""
    return has_box(m) or (m.get("rt") is not None and m.get("imdb") is not None)


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
        tmdb_revenue=d.get("revenue") or 0, tmdb_votes=d.get("vote_count") or 0)
    if "watch/providers" in d:      # US streaming (JustWatch data via TMDb); keep old values if TMDb didn't answer
        prov = ((d.get("watch/providers") or {}).get("results") or {}).get("US") or {}
        names = lambda k: [x["provider_name"] for x in prov.get(k, []) if x.get("provider_name")]
        flat = names("flatrate")
        it["stream"] = flat + [n for n in names("ads") if n not in flat]      # subscription + free-with-ads
        it["rentbuy"] = bool(prov.get("rent") or prov.get("buy"))
        us = next((x for x in (d.get("release_dates") or {}).get("results", []) if x.get("iso_3166_1") == "US"), {})
        dates = lambda t: sorted(r["release_date"][:10] for r in us.get("release_dates", []) if r.get("type") in t and r.get("release_date"))
        digital, theatrical = dates((4,)), dates((2, 3))      # 4 = digital, 3 = theatrical, 2 = limited theatrical
        it["digital"] = digital[0] if digital else ""
        it["theatrical"] = theatrical[0] if theatrical else ""
    if o:
        rt = next((num(r.get("Value")) for r in o.get("Ratings", []) if r.get("Source") == "Rotten Tomatoes"), None)
        it.update(rt=rt, imdb=num(o.get("imdbRating")), omdb_box=num(o.get("BoxOffice")), votes=num(o.get("imdbVotes")), omdb_at=TODAY.isoformat())
        if not it["poster"] and o.get("Poster", "N/A") != "N/A":
            it["poster"] = o["Poster"]
    for k in ("rt", "imdb", "omdb_box", "votes", "omdb_at"):
        it.setdefault(k, None)
    return rescore(it)


def verified(it: dict) -> bool:
    """In both APIs: TMDb has it (we are holding it) and OMDb recognised its IMDb id (omdb_at is only set when OMDb
    answered). TMDb is community-edited, so a fake entry can carry invented revenue; it has no IMDb page, so OMDb never
    answers and it never counts. A real movie OMDb knows but has no ratings for yet is still verified."""
    return bool(it.get("imdb_id")) and bool(it.get("omdb_at"))


def rescore(it: dict) -> dict:
    ok = verified(it)
    box = (it.get("omdb_box") or it.get("tmdb_revenue") or None) if ok else None   # domestic (OMDb) first, worldwide (TMDb) if missing
    it["box"], it["bo_src"] = box, ("omdb" if ok and it.get("omdb_box") else "tmdb" if box else "")
    it.pop("aud", None); it.pop("tmdb_user", None)   # audience score was removed; drop it from older saved items
    parts = [it.get("rt"), it["imdb"] * 10 if it.get("imdb") is not None else None, box_score(box)]
    parts = [p for p in parts if p is not None]
    it["score"] = round(sum(parts) / len(parts), 1) if parts else None   # all available scores, weighted evenly
    it["n"] = len(parts)
    return it


def age_days(it: dict) -> int:
    rel = it.get("release") or ""
    return (TODAY - date.fromisoformat(rel)).days if len(rel) == 10 else 0


def asked_no(it: dict) -> bool:
    """OMDb was really asked about this movie and gave no answer (not merely out of requests that day).
    History-job movies are only saved after OMDb was asked, so hp counts too."""
    return bool(it.get("omdb_no")) or it.get("hp") is not None


def droppable(it: dict) -> bool:
    """True if the movie fails the board rules AND is old enough that it will not change. Younger movies are kept."""
    if age_days(it) < KEEP_DAYS:
        return False
    if not it.get("imdb_id"):                      # TMDb-only: no IMDb page at all
        return True
    if not it.get("omdb_at"):                      # IMDb id, but OMDb never recognised it
        return asked_no(it)
    if not ranked(it):                             # in both, but too little box office and missing RT or IMDb
        return True
    return bool(MIN_VOTES) and it.get("votes") is not None and it["votes"] < MIN_VOTES and age_days(it) >= VOTES_GRACE_DAYS




# ---------- storage: one file per release month ----------
SCORE_COLS = ["id", "release", "rt", "imdb", "box", "score", "st"]


def score_row(m: dict) -> list:
    """One compact row per movie for data/scores.json (st = 1 if on a US subscription/free-with-ads service)."""
    return [m["id"], m["release"], m.get("rt"), m.get("imdb"), m.get("box"), m.get("score"), 1 if m.get("stream") else 0]


class Store:
    def __init__(self):
        self.s, self.dirty, self.rows = {}, set(), {}
        self.skip = set(read(SKIP_FILE, []))

    def month(self, ym):
        if ym not in self.s:
            self.s[ym] = {str(m["id"]): m for m in read(DATA / f"{ym}.json", {}).get("items", [])}
        return self.s[ym]

    def put(self, it):
        if droppable(it):                          # not worth keeping: remember the id so it is not fetched again
            self.skip.add(it["id"])
            self.month(it["release"][:7]).pop(str(it["id"]), None)
        else:
            self.month(it["release"][:7])[str(it["id"])] = it
        self.dirty.add(it["release"][:7])

    def _rows(self, ym):
        items = self.s[ym].values() if ym in self.s else read(DATA / f"{ym}.json", {}).get("items", [])
        return [score_row(m) for m in items if m.get("release")]

    def save(self):
        """Write changed month files, then rebuild data/index.json and data/scores.json (every movie's scores, for the page's ranking)."""
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for ym in self.dirty:
            items = sorted(self.s[ym].values(), key=lambda m: -(m.get("score") or -1))
            write(DATA / f"{ym}.json", {"month": ym, "updated": stamp, "items": items})
            self.rows[ym] = self._rows(ym)
        self.dirty.clear()
        write(SKIP_FILE, sorted(self.skip))
        for p in DATA.glob("????-??.json"):
            if p.stem not in self.rows:
                self.rows[p.stem] = self._rows(p.stem)
        months = sorted(((ym, len(r)) for ym, r in self.rows.items() if r), reverse=True)
        write(DATA / "index.json", {"updated": stamp, "months": [{"m": m, "n": n} for m, n in months]})
        write(DATA / "scores.json", {"updated": stamp, "cols": SCORE_COLS,
                                     "rows": [r for ym, _ in months for r in self.rows[ym]]})


def checkpoint(store: Store, o: Omdb):
    store.save()
    write(STATE / "usage.json", {"date": TODAY.isoformat(), "omdb": usage_today() + o.used})
    o.used = 0


def stale(old) -> bool:
    if not old or not old.get("omdb_at"):
        return True
    asked = date.fromisoformat(old["omdb_at"])
    if (TODAY - asked).days >= REFRESH_DAYS:
        return True
    # a brand-new movie that is not on the board yet: ask again every day (once a day) until it is
    rel = old.get("release") or ""
    if asked < TODAY and not ranked(old) and len(rel) == 10:
        age = (TODAY - date.fromisoformat(rel)).days
        return 0 <= age <= NEW_DAYS
    return False


def refresh(mid, store: Store, o: Omdb, hp=None):
    if mid in store.skip:
        return
    d = tmdb(f"/movie/{mid}", append_to_response="videos,watch/providers,release_dates", language="en-US")
    if not d.get("id") or len(d.get("release_date") or "") < 10:
        return
    old = store.month(d["release_date"][:7]).get(str(mid))
    need = stale(old) and bool(d.get("imdb_id")) and not o.exhausted()
    od = o.get(d.get("imdb_id")) if stale(old) else None
    it = build(d, old, od)
    if need and not od and not o.blocked:         # OMDb was asked and had nothing: remember that
        it["omdb_no"] = TODAY.isoformat()
    elif od:
        it.pop("omdb_no", None)
    if hp is not None:
        it["hp"] = hp                       # which history pass last checked this movie
    store.put(it)


# ---------- jobs ----------
def run_recent():
    start = add_months(TODAY.strftime("%Y-%m"), -RECENT_MONTHS) + "-01"
    q = {"primary_release_date.lte": TODAY.isoformat(), "with_release_type": "2|3", "sort_by": "popularity.desc"}
    new_start = (TODAY - timedelta(days=NEW_DAYS)).isoformat()
    newest = list(discover(**q, **{"primary_release_date.gte": new_start}))      # new theatrical releases go first,
    rest = list(discover(**q, **{"primary_release_date.gte": start}))[: RECENT_PAGES * 20]   # so they get OMDb first
    ids = newest + [i for i in rest if i not in set(newest)]
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
    """Walk backwards one release month at a time. When it reaches the floor year it starts a new pass
    (pass 2, 3, ...) from the edge of the recent window and re-checks every movie, so streaming changes get picked up."""
    store, o = Store(), Omdb(budget_left())
    state = read(STATE / "history_state.json", {})
    pas = state.get("pass", 1)
    fresh_start = lambda: add_months(TODAY.strftime("%Y-%m"), -(RECENT_MONTHS + 1))
    cur = state.get("next_month") or fresh_start()
    print(f"History pass {pas} from {cur}; OMDb budget {o.left}")
    n, finished_pass = 0, False
    try:
        while True:
            if int(cur[:4]) < HIST_FLOOR:          # reached the oldest year: the pass is done
                finished_pass = True
                break
            a, b = month_bounds(cur)
            ids = list(discover(**{"primary_release_date.gte": a, "primary_release_date.lte": b,
                                   "vote_count.gte": HIST_MIN_VOTES, "sort_by": "vote_count.desc"}))
            have = store.month(cur)
            # Do a movie if it is new, or if an earlier pass did it (items saved before passes existed count as pass 1).
            todo = [i for i in ids if i not in store.skip and (str(i) not in have or have[str(i)].get("hp", 1) != pas)]
            finished = True
            for mid in todo:
                if stale(have.get(str(mid))) and o.exhausted():     # only stop when this movie would need OMDb
                    finished = False
                    break
                refresh(mid, store, o, hp=pas)
                n += 1
                if n % 100 == 0:
                    checkpoint(store, o)
            if not finished:
                break
            print(f"{cur} complete ({len(store.month(cur))} movies)")
            cur = add_months(cur, -1)
            write(STATE / "history_state.json", {"next_month": cur, "pass": pas})
    finally:
        checkpoint(store, o)
    if finished_pass:
        pas += 1
        write(STATE / "history_state.json", {"next_month": fresh_start(), "pass": pas})
        print(f"Pass {pas - 1} reached {HIST_FLOOR}. Pass {pas} will start again from the recent window tomorrow.")
    else:
        print(f"Stopped at {cur}; OMDb allowance used for today.")


def run_clean():
    """Re-apply the board rules to every saved movie, drop the ones that fail (and are old enough), and remove duplicate
    copies of one TMDb id (left behind when TMDb moves a release date into another month). No API calls, no keys."""
    store = Store()
    first, dropped, dupes, rescored = {}, 0, 0, 0
    for p in sorted(DATA.glob("????-??.json")):
        for it in list(store.month(p.stem).values()):
            prev = first.get(it["id"])
            if prev:                                   # keep the copy OMDb was asked about most recently
                a, b = prev, (p.stem, it)
                lose, win = (b, a) if (a[1].get("omdb_at") or "") >= (b[1].get("omdb_at") or "") else (a, b)
                store.s[lose[0]].pop(str(lose[1]["id"]), None); store.dirty.add(lose[0])
                first[it["id"]] = win
                dupes += 1
            else:
                first[it["id"]] = (p.stem, it)
    for ym in list(store.s):
        for k, it in list(store.s[ym].items()):
            before = (it.get("box"), it.get("score"))
            rescore(it)
            if droppable(it):
                store.skip.add(it["id"]); del store.s[ym][k]; dropped += 1; store.dirty.add(ym)
            elif before != (it.get("box"), it.get("score")):
                rescored += 1; store.dirty.add(ym)
    store.save()
    print(f"Dropped {dropped} movies, removed {dupes} duplicate copies, re-scored {rescored}. Skip list: {len(store.skip)} ids.")


def run_report():
    """Past 2 months on the board, with their IMDb vote counts, to help pick MIN_VOTES. No API calls."""
    start = (TODAY - timedelta(days=61)).isoformat()
    rows = []
    for p in sorted(DATA.glob("????-??.json"))[-4:]:
        for it in read(p, {}).get("items", []):
            if it.get("release", "") >= start and verified(it) and ranked(it):
                rows.append(it)
    have = sorted((it for it in rows if it.get("votes") is not None), key=lambda it: it["votes"])
    print(f"{len(rows)} movies on the board since {start}; {len(have)} have an IMDb vote count so far.")
    for it in have[:25]:
        print(f"{int(it['votes']):>8,}  {it['release']}  {it['title'][:50]}")


def run_index():
    """Rebuild data/index.json and data/scores.json from the month files. No API calls, no keys needed."""
    Store().save()
    print("Rebuilt data/index.json and data/scores.json.")


if __name__ == "__main__":
    if sys.argv[1:] == ["index"]:
        run_index(); sys.exit(0)
    if sys.argv[1:] == ["clean"]:
        run_clean(); sys.exit(0)
    if sys.argv[1:] == ["report"]:
        run_report(); sys.exit(0)
    if not TMDB_KEY:
        sys.exit("TMDB_Key is not set.")
    if not OMDB_KEY:
        print("Warning: OMDB_Key is not set, so there will be no Rotten Tomatoes, IMDb or domestic box office data.")
    job = {"recent": run_recent, "history": run_history}.get(sys.argv[1] if len(sys.argv) > 1 else "")
    job() if job else sys.exit("usage: python movies.py recent | history | index")
