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
| `movies.py` | Does all the work. `python movies.py recent` or `python movies.py history` |
| `.github/workflows/movies-daily.yml` | Daily: downloads the last three months of movies |
| `.github/workflows/movies-history.yml` | Daily: builds older history with the OMDb requests left over |
| `data/YYYY-MM.json` | One file per release month, movies sorted by All score |
| `data/index.json` | List of months and movie counts, plus the last update time |
| `state/usage.json` | OMDb requests spent today (shared by both workflows) |
| `state/history_state.json` | The next month the history job will do |
| `requirements.txt` | `requests` |

## The page

* **Tabs:** All, Rotten Tomatoes, IMDb, Box Office. Audience score is excluded. Each single-score tab ranks movies by that score. All averages the available Rotten Tomatoes, IMDb and Box Office scores with equal weight; a movie needs only 2 of those 3 scores to appear and rank (`MIN_SCORES = 2` in `index.html`).
* **Time frame:** 1, 2, 3, 6, 12 months or All, based on release date.
* **Sort:** Ranking or Newest. **★ Favorites** shows only starred movies (stored in your browser).
* **Poster:** the TMDb poster URL (OMDb's poster if TMDb has none).
* **Tiles are not clickable.** Under the score each card has three favicon links: **Rotten Tomatoes** (a search for the title, since OMDb no longer returns the exact page URL), **IMDb** (the movie's own page, built from `imdb_id`) and **YouTube** (the TMDb trailer, or a YouTube search for the trailer if TMDb has none).
* **Streaming:** a green **▶ Streaming** pill on the poster (and the first three service names on the card) when the movie is on a US subscription or free-with-ads service; a grey **Rent / Buy** pill when it can only be rented or bought. The **▶ Streaming** button next to Favorites shows only streaming movies. Data is TMDb's watch providers (from JustWatch, US only), read in the same TMDb call as the trailers, so it costs no OMDb requests. It refreshes on each daily run; older history movies show no badge until they are refreshed.
* **Descriptions** are cut to 3 lines with a **See more** link when they are longer.
* **Avg on each card:** the evenly weighted average of the scores the movie has, with how many of the three it used (for example `Avg 77 · 2 of 3`).

### Scores

All three scores are put on a 0 to 100 scale and averaged evenly. If a movie is missing one (new movies often have no Rotten Tomatoes score), the average uses what it has, and `n` says how many. The data files keep every movie, but the All tab hides those with fewer than 2 scores.

| Score | Source | Scale |
|---|---|---|
| Rotten Tomatoes | OMDb `Ratings` (critic Tomatometer) | Percent as is |
| IMDb | OMDb `imdbRating` | Rating x 10 |
| Box Office | OMDb `BoxOffice` (domestic); TMDb `revenue` (worldwide) if OMDb has none | Log scale: $100K = 0, $1B = 100 |

## How the data is built

**Recent (daily, 06:17 UTC).** TMDb discover lists theatrical movies released from the first day of the month three months ago until today, most popular first (up to `RECENT_PAGES` x 20). For each movie it calls TMDb for details (includes trailers, US streaming providers and digital release date) and asks OMDb only if the movie has no OMDb data yet or the data is `REFRESH_DAYS` (7) old. This job spends at most `OMDB_RECENT_BUDGET` (300) OMDb requests, and usually far fewer once the window is filled.

**History (daily, 09:17 UTC).** Uses whatever is left of the OMDb allowance: `OMDB_DAILY_LIMIT` (1000) minus `OMDB_MARGIN` (50) minus what the recent job spent. It works backwards one release month at a time, starting just before the recent window, taking movies with at least `HISTORY_MIN_VOTES` (50) TMDb votes, most-voted first. When the allowance runs out it saves and stops; the next day it resumes in the same month, then moves on. It stops at `HISTORY_FLOOR_YEAR` (1900). Each day adds more months, so history builds up over time.

Both workflows share one concurrency group and commit with a rebase-and-retry loop, so they never clash. Run either by hand from the **Actions** tab with *Run workflow*.

## JSON format

`data/index.json`: `{"updated": ISO time, "months": [{"m": "2026-10", "n": 142}, ...]}` newest first.

`data/YYYY-MM.json`: `{"month", "updated", "items": [...]}`. Each item:

| Field | Meaning |
|---|---|
| `id`, `imdb_id`, `title`, `release`, `overview` | Identity and summary |
| `poster`, `trailer` | Image URL and YouTube URL (empty if none) |
| `stream`, `rentbuy`, `digital` | US streaming services (subscription + free with ads), whether it can be rented or bought, and the US digital release date (empty if unknown) |
| `rt`, `imdb`, `box` | The three scores (`null` if unknown). `imdb` is out of 10, `box` is dollars |
| `bo_src` | `omdb` (domestic) or `tmdb` (worldwide) |
| `score`, `n` | Evenly weighted average (0 to 100) and how many scores it used |
| `omdb_at` | Date OMDb was last asked |

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
| `RECENT_PAGES` | 15 | TMDb pages (20 movies each) for the recent job |
| `REFRESH_DAYS` | 7 | Days before a movie's OMDb data is refreshed |
| `HISTORY_MIN_VOTES` | 50 | Minimum TMDb votes for a historical movie |
| `HISTORY_FLOOR_YEAR` | 1900 | Oldest year the history job goes back to |

## Limits and notes

* OMDb's free data is licensed for **non-commercial use** with credit (footer on the page). Check OMDb's terms before adding ads or a paywall.
* TMDb requires the "not endorsed or certified by TMDB" notice (in the footer) and is free for non-commercial use.
* Box office is often missing for small or very new releases; those movies rank on their other scores.
* The **All** time frame loads every month file, so it gets heavier as history grows. The 1 to 12 month filters only load the months they need.
* TMDb and OMDb parameters were written from memory of their docs and tested only against mock responses, so check the first workflow run's log.
