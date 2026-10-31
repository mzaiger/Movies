# Movie Board

A static movie board in the same style as Trending Topics. **TMDb** supplies the movie list, posters, trailers and summaries. **OMDb** supplies the Rotten Tomatoes score, IMDb rating and box office. GitHub Actions fetch the data and commit it as JSON, and `index.html` (GitHub Pages) reads those files.

## API keys (GitHub secrets)

| Secret name | Service | Used for |
|---|---|---|
| `TMDB_Key` | [The Movie Database (TMDb)](https://www.themoviedb.org/settings/api) | Movie lists, poster URL, YouTube trailer, summary, worldwide revenue |
| `OMDB_Key` | [OMDb](https://www.omdbapi.com/) | Rotten Tomatoes %, IMDb rating, domestic box office |

Add both under **Settings > Secrets and variables > Actions**. `TMDB_Key` can be either the v3 API key or the v4 read access token; the script detects which. Locally, set environment variables with the same names (`TMDB_Key`, `OMDB_Key`).

## Files

| File | What it is |
|---|---|
| `index.html` | The page: tabs, month filters, sort, favorites, poster tiles |
| `img/hbo-max.png`, `img/amc-plus.png` | Logos used on the HBO Max and AMC+ links |
| `movies.py` | Does all the work. `python movies.py recent` or `python movies.py history`; `python movies.py index` rebuilds `index.json` and `scores.json` from the month files with no API calls or keys |
| `.github/workflows/movies-daily.yml` | Daily: downloads the last 12 months of movies |
| `.github/workflows/movies-history.yml` | Daily: builds older history with the OMDb requests left over, then starts over to refresh it |
| `data/YYYY-MM.json` | One file per release month, movies sorted by All score |
| `data/index.json` | List of months and movie counts, plus the last update time |
| `data/scores.json` | Every movie's id, release date, scores and a streaming flag (about 50 bytes a movie). The page ranks from this. Rebuilt on every run |
| `state/usage.json` | OMDb requests spent today (shared by both workflows) |
| `state/history_state.json` | The next month and pass number the history job will do |
| `requirements.txt` | `requests` |

## The page

* **Tabs:** All, Rotten Tomatoes, IMDb, Box Office. Audience score is excluded. Each single-score tab ranks movies by that score. All averages the available Rotten Tomatoes, IMDb and Box Office scores with equal weight; a movie needs only 2 of those 3 scores to appear and rank (`MIN_SCORES = 2` in `index.html`).
* **Time frame:** two rows based on release date. Top row: 1, 2, 3, 6, 12 months. Row under it: 2, 5, 10, 20 years and All.
* **Sort:** Ranking or Newest. **★ Favorites** shows only starred movies (stored in your browser).
* **Poster:** the TMDb poster URL (OMDb's poster if TMDb has none).
* **Tiles are not clickable.** Under the score each card has a **Score Links and Trailer** label above three favicon links: **Rotten Tomatoes** (a search for the title, since OMDb no longer returns the exact page URL), **IMDb** (the movie's own page, built from `imdb_id`) and **YouTube** (the TMDb trailer, or a YouTube search for the trailer if TMDb has none).
* **Release dates:** at the bottom of each card shows **Theaters** (US theatrical date, or TMDb's release date) and **Streaming** (US digital release date from TMDb; "Available now" if it is on a service but TMDb has no date, "Not yet" otherwise).
* **Streaming:** a green **▶ Streaming** pill on the poster when the movie is on a US subscription or free-with-ads service; a grey **Rent / Buy** pill when it can only be rented or bought. The **▶ Streaming** button next to Favorites shows only streaming movies. Data is TMDb's watch providers (from JustWatch, US only), read in the same TMDb call as the trailers, so it costs no OMDb requests. It refreshes on each daily run; older history movies show no badge until they are refreshed.
* **Get Tickets or Stream On:** a row of favicon + ↗ links under the Rotten Tomatoes / IMDb / YouTube row, on every card. The first is always **Fandango** (a Fandango search for the title, for tickets). After it comes one link per US streaming service the movie is on. Provider names that are the same service are merged (Netflix and Netflix Standard with Ads, Max Amazon Channel and HBO Max, Amazon Prime Video with Ads and Amazon Prime Video, and so on). HBO Max and AMC+ show their logo images from `img/` instead of a favicon. Peacock, Hulu and Disney+ do not accept a search in the URL, so those three open their search pages (`peacocktv.com/watch/search`, `hulu.com/search`, `disneyplus.com/en-gb/browse/search`). Services without an entry in the `SERVICES` list in `index.html` are combined into one JustWatch link; to add a service, add one line to `SERVICES`.
* **Descriptions** are cut to 3 lines with a **See more** link when they are longer.

### Scores

All three scores are put on a 0 to 100 scale and averaged evenly. If a movie is missing one (new movies often have no Rotten Tomatoes score), the average uses what it has, and `n` says how many. The data files keep every movie, but the All tab hides those with fewer than 2 scores.

| Score | Source | Scale |
|---|---|---|
| Rotten Tomatoes | OMDb `Ratings` (critic Tomatometer) | Percent as is |
| IMDb | OMDb `imdbRating` | Rating x 10 |
| Box Office | OMDb `BoxOffice` (domestic); TMDb `revenue` (worldwide) if OMDb has none | Log scale: $100K = 0, $1B = 100 |

## How the data is built

**Recent (daily, 06:17 UTC).** TMDb discover lists theatrical movies released from the first day of the month `RECENT_MONTHS` (12) months ago until today, most popular first (up to `RECENT_PAGES` x 20, so the top 1000). For each movie it calls TMDb for details (includes trailers, US streaming providers and digital release date) and asks OMDb only if the movie has no OMDb data yet or the data is `REFRESH_DAYS` (7) old. This job spends at most `OMDB_RECENT_BUDGET` (300) OMDb requests. The first few days fill the year in popularity order; after that it needs roughly 150 a day to keep every movie within `REFRESH_DAYS`.

**History (daily, 09:17 UTC).** Uses whatever is left of the OMDb allowance: `OMDB_DAILY_LIMIT` (1000) minus `OMDB_MARGIN` (50) minus what the recent job spent. It works backwards one release month at a time, starting just before the recent window, taking movies with at least `HISTORY_MIN_VOTES` (50) TMDb votes, most-voted first. When the allowance runs out it saves and stops; the next day it resumes in the same month, then moves on. It reaches `HISTORY_FLOOR_YEAR` (1900), then **starts over**: the pass number in `state/history_state.json` goes up by one, it returns to the edge of the recent window and re-checks every movie (new streaming services, scores, trailers). Each movie records the pass that last checked it in `hp`. A new pass only spends OMDb requests on movies whose OMDb data is older than `REFRESH_DAYS`; TMDb streaming data is refreshed for every movie. The pass ends for the day when it reaches the floor, and the next pass begins the following day.

Both workflows share one concurrency group and commit with a rebase-and-retry loop, so they never clash. Run either by hand from the **Actions** tab with *Run workflow*.

## JSON format

`data/index.json`: `{"updated": ISO time, "months": [{"m": "2026-10", "n": 142}, ...]}` newest first.

`data/YYYY-MM.json`: `{"month", "updated", "items": [...]}`. Each item:

| Field | Meaning |
|---|---|
| `id`, `imdb_id`, `title`, `release`, `overview` | Identity and summary |
| `poster`, `trailer` | Image URL and YouTube URL (empty if none) |
| `stream`, `rentbuy`, `digital`, `theatrical` | US streaming service names (subscription + free with ads), whether it can be rented or bought, and the US digital and theatrical release dates (empty if unknown) |
| `rt`, `imdb`, `box` | The three scores (`null` if unknown). `imdb` is out of 10, `box` is dollars |
| `bo_src` | `omdb` (domestic) or `tmdb` (worldwide) |
| `score`, `n` | Evenly weighted average (0 to 100) and how many scores it used |
| `omdb_at` | Date OMDb was last asked |
| `hp` | History pass that last checked this movie (history movies only) |

## Setup

1. Put these files in a new GitHub repo.
2. Add the two secrets above.
3. **Settings > Actions > General > Workflow permissions:** Read and write.
4. **Settings > Pages:** deploy from the main branch, root folder.
5. In the **Actions** tab, run *Update recent movies*, then *Build movie history*.

Run locally: `pip install -r requirements.txt`, set `TMDB_Key` and `OMDB_Key`, run `python movies.py recent`, then serve the folder (for example `python -m http.server`) and open `index.html`.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `OMDB_DAILY_LIMIT` | 1000 | Your OMDb daily allowance |
| `OMDB_MARGIN` | 50 | Requests never spent (the day boundary is not exact) |
| `OMDB_RECENT_BUDGET` | 300 | Most the recent job may spend |
| `RECENT_MONTHS` | 12 | How many months back the recent job looks |
| `RECENT_PAGES` | 50 | TMDb pages (20 movies each) for the recent job |
| `REFRESH_DAYS` | 7 | Days before a movie's OMDb data is refreshed |
| `HISTORY_MIN_VOTES` | 50 | Minimum TMDb votes for a historical movie |
| `HISTORY_FLOOR_YEAR` | 1900 | Oldest year the history job goes back to |

## Limits and notes

* OMDb's free data is licensed for **non-commercial use** with credit (footer on the page). Check OMDb's terms before adding ads or a paywall.
* TMDb requires the "not endorsed or certified by TMDB" notice (in the footer) and is free for non-commercial use.
* Box office is often missing for small or very new releases; those movies rank on their other scores.
* **Rankings cover the whole time frame.** The page first loads `data/scores.json`, one small file with every movie's scores, so 10 Years or All ranks every movie in that window at once. It then fetches the month files (poster, summary, trailer, streaming) only for the cards on screen, a few at a time as you scroll. Month files already loaded are kept. Ties on the All and Box Office tabs break by TMDb id; Rotten Tomatoes and IMDb ties break by box office.
* TMDb and OMDb parameters were written from memory of their docs and tested only against mock responses, so check the first workflow run's log.
