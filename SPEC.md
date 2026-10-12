# SADL IDP Streamer: Specification

## Context
SADL ("Slow Ass Draft League") is a Sleeper league whose IDP scoring differs from the formats public rankings use, and those sources don't agree with each other anyway. IDP rosters are tight: start 1 DL / 1 LB / 1 DB, max 2 rostered per position. The waiver wire is therefore always deep, and streaming the best weekly option is the main IDP edge.

**Goal:** an on-demand tool that pulls IDP projections and rankings from every reliable source it can reach, rescores them to SADL's exact Sleeper scoring, blends them into a weighted consensus with an uncertainty for each player, and publishes **weekly streaming tiers** per position to GitHub Pages. The list covers only players actually available in SADL, shown next to your own rostered IDPs.

**Status:** agreed spec (v2, 2026-10-12). Building follows the phases in §8, MVP first. Any change to decisions D1–D17 is made by amending this file. The spec went through two adversarial review rounds before agreement.

---

## 1. Decisions (from Q&A)
| # | Topic | Decision |
|---|---|---|
| D1 | Platform | Sleeper. Public read-only API, no auth. |
| D2 | Scoring | Pulled from the league's `scoring_settings` on every sync. Never hand-entered. |
| D3 | Lineup / roster | 1 DL, 1 LB, 1 DB starters. Max 2 rostered per IDP position. Both read from Sleeper (`roster_positions`, `settings.position_limit_{dl,lb,db}`). |
| D4 | Sources | Every source that is reliable and reachable (§5). FantasyPros Premium is available. |
| D5 | Acquisition | Automatic but on demand. Polite fetching: caching, ≥2 s between requests per host, an identifying user agent, and backoff on errors. |
| D6 | Output | Personal use. Static site on GitHub Pages. The repo `layne-burns/SADLidp` is public. |
| D7 | League identity | Resolved from Sleeper user `lbburnsy` plus a league-name match for the current season (§4.2). This also identifies **your** roster. |
| D8 | Rank-only sources | Included. They are converted to SADL points by within-list rank-to-value mapping (§6.4). |
| D9 | FantasyPros | Logged-in auto fetch. **Auto-login first:** each sync logs in with `FP_EMAIL` / `FP_PASSWORD` stored in Actions secrets, so there is no cookie to refresh. Phase 1 tests whether FP allows scripted logins from GitHub's runners. **Fallback** if it doesn't: a session cookie (`FP_COOKIE`) refreshed by a one-command local script that reads it from the user's browser and updates the secret (`gh secret set`). A web page cannot read another site's cookies, so the site itself can never collect it. The user accepts the risk of breakage and of FP's terms, and uses a password unique to FP. FP failing **never fails the sync**. |
| D10 | v1 scope | Weekly streaming tiers, **my roster vs waivers**, and **source accuracy tracking**. **Out of v1:** look-ahead weeks and an in-house matchup model. |
| D11 | Weights | The projection : rank group split starts at **60:40** as a prior. Accuracy tracking may move it, with each group kept within 20–80%, and may also move per-source weights within each group (§6.5). |
| D12 | Tiering | **Anchored significance tiers** (§6.6). |
| D13 | Sync timing | Manual button, plus an automatic run before SADL waivers process, plus weekend injury refreshes (§4.3). |
| D14 | Dual eligibility | A player's position is **the one Sleeper counts toward the max-2 roster limit**. **Verified in Phase 1 (§13): Sleeper's designated `position` field** (DE/DT/NT/DL→DL, LB/OLB/ILB/MLB→LB, CB/S/FS/SS/DB→DB), not `fantasy_positions[0]`. He appears **only** in that position's list, with a **dual-eligible** tag (e.g. "LB · also DL"). |
| D15 | Swap badges | **Both**: `START over X`, meaning the waiver player likely outscores your projected starter this week, and `UPGRADE over Y`, meaning he likely outscores your other eligible player at that position (§6.7). |
| D16 | FP data privacy | FP rows are committed **encrypted only** (key in Actions secrets). The site shows FP **only through the blend**: no FP column, and FP is excluded from the displayed min–max range. FP's aggregate accuracy metrics may be shown. |
| D17 | Rollout | **MVP first**: Sleeper projections → SADL scoring → tiers → My IDPs → page, with snapshot freezing from day one. FantasyPros and other sources come next, then accuracy weighting. Weeks 1–5 are backfilled if Sleeper serves past weekly projections. |

## 2. Defaults (engineer-chosen; changeable via config or a spec amendment)
- **Stack:** Python 3.12, `uv`, `httpx`, `selectolax`, `pydantic`, `jinja2`, `pytest`, `ruff`, and `age` for encryption. No database. Normalized JSON snapshots are committed under `data/`.
- **Pool:** available players are those on no SADL roster. All of them are tiered after filters. The page shows the top **25 per position** (config) and says how many more are hidden. Your rostered IDPs are always shown.
- **Hidden from the waiver pool:** `injury_status` ∈ {Out, Doubtful, IR, PUP, NFI, Sus, NA, DNR, COV}, or `status` ≠ Active, `active=false`, `team=null`, on bye, or **locked** (his game has kicked off). Questionable players are shown with a flag. Any other non-null code is shown with a flag and logged as unknown. Your own players always show, with a status badge.
- **Page:** mobile-first. Header "Week N · data as of <time>" (a small JS warning appears if the data is >36 h old in a game week), then the My IDPs panel, then DL / LB / DB tabs, then a sources status footer (status, row count, match rate, staleness and accuracy per source).
- **Privacy:** sources are plaintext by default. Any source can be marked `private: true` in config to get the D16 treatment, which FP always has.

---

## 3. Architecture
### 3.1 Runtime
```
GitHub Actions — sync.yml (runs from default branch `main`)
  triggers: workflow_dispatch  (= the sync button; works from the GitHub mobile app;
                                input fp_mode=normal|force_invalid for testing)
            schedule '17 */3 * * *' and '17 10,11,13,14,16 * * 0' (UTC; together they give
                     hourly Sunday slots 09:17–16:17 UTC) → Python gate decides (§4.3)
  concurrency: group=sync, cancel-in-progress=false
  job sync  (permissions: contents: write):
      restore actions/cache (players DB + HTTP cache for unauthenticated, keyless hosts
                             only, keyed by America/New_York date; never FP/Odds, §7)
      uv run sadlidp sync → uv run sadlidp build
      uv run sadlidp leakcheck (staged diff + built site; abort on any hit, §7)
      commit data/** to main (git pull --rebase, ≤3 retries) → upload-pages-artifact
  job deploy (needs: sync; permissions: pages: write, id-token: write; env github-pages)
      deploy-pages
ci.yml: ruff + pytest on PRs/pushes; ignores data/** for tests; leak-guard pattern scan (§7)
```
Commits made with `GITHUB_TOKEN` don't retrigger workflows, which is the intended behaviour. If a sync fails, nothing is deployed and the last good site stays live.

**One-time setup** (documented in README): Settings → Pages → Source = GitHub Actions. Secrets: `FP_EMAIL` and `FP_PASSWORD` (auto-login), `FP_COOKIE` (fallback only), `FP_DATA_KEY` (age key), and optionally `ODDS_API_KEY`. Public repos auto-disable scheduled workflows after 60 days without activity, so the README pre-season checklist says to re-enable sync.yml in late August.

### 3.2 Pipeline modules (`src/sadlidp/`)
1. `sleeper.py`: documented /v1 endpoints (state, user, leagues, league, rosters, matchups, players) and the undocumented projections and stats endpoints.
2. `schedule.py`: nflverse schedules CSV. Kickoff = `gameday` + `gametime` as **America/New_York** local time. Provides byes and opponents. A team with zero games in the season is a hard error, never treated as "on bye".
3. `sources/`: one adapter per source behind a shared interface (§5.1).
4. `matching.py` (§6.1) · `impute.py` (§6.2) · `scoring.py` (§6.3) · `aggregate.py` (§6.4) · `accuracy.py` (§6.5) · `tiers.py` (§6.6) · `roster.py` (§6.7)
5. `site/`: Jinja templates producing static HTML plus a JSON copy of the consensus.
6. `cli.py`: `sadlidp sync [--force]`, `sadlidp build`, `sadlidp backfill --weeks 1-5`, `sadlidp gate` (dry-run: prints whether the schedule would run, and why).

### 3.3 Repo layout
```
SPEC.md  README.md  pyproject.toml
config/sadl.toml               # all tunables (§9)
config/player_overrides.yaml   # manual name → sleeper_id fixes
config/aliases/                # team and position code alias tables per source
src/sadlidp/...                # §3.2
data/<season>/league.json      # resolved league id, scoring_settings, settings (per sync)
data/<season>/week<NN>/
    players_idp.json           # slim IDP subset of the players DB (never the full ~5 MB file)
    rosters.json  run.json  consensus.json  actuals.json
    sources/<src>.json         # normalized rows only, never raw payloads
    sources/<private>.json.age # private sources (FP): encrypted normalized rows
data/<season>/accuracy.json
tests/  tests/fixtures/        # offline; recorded and scrubbed responses
.github/workflows/{sync.yml, ci.yml, probe.yml}
```

## 4. League, week and schedule logic
### 4.1 Sleeper fields used
- **League:** `scoring_settings`, `roster_positions`, `settings.position_limit_{dl,lb,db}` (a missing key means no limit; the tool then falls back to config `roster.position_limit` = 2 and shows a warning), `settings.waiver_type`, `settings.waiver_day_of_week`, `settings.daily_waivers`, `settings.daily_waivers_hour`, `settings.daily_waivers_days`, `settings.playoff_week_start`, and `status`.
- **Rosters:** `players`, `reserve` (IR: shown with a badge and excluded from starter and drop logic), `taxi` (counts as rostered), `owner_id` / `co_owners`. **My roster** is the one whose `owner_id` or `co_owners` contains the resolved user.
- **Matchups** `/league/<id>/matchups/<week>`: `starters` (used to detect your locked starter) and `players_points` (the ground truth in §10).
- **Players DB:** `position`, `fantasy_positions`, `team`, `status`, `active`, `injury_status`, `espn_id`, `gsis_id`, and names. Fetched at most once per America/New_York day via actions/cache, revalidated with ETag when Sleeper supports it.
- **Projections and stats (undocumented):** requests send `season_type=regular` plus the IDP `position[]` set confirmed in Phase 1. Only rows with `stats.gp` present and a non-null opponent count; anything else means "no projection", never 0. Rows are re-filtered by position after the fetch.

### 4.2 League resolution
League name match: case-insensitive, trimmed, exact, against config `["SADL", "Slow Ass Draft League"]`, among the user's leagues for the target season. Exactly one match is required. Zero matches fails loudly during the season and exits 0 with no commit in the offseason. More than one match fails loudly and lists every candidate's name and ID. Config `league.league_id` overrides the match. `user_id` is cached after the first lookup, which survives a username change.

### 4.3 Target week and the schedule gate
- **target_week** = the regular-season week that contains the earliest game not yet kicked off, taken from the nflverse schedule. Any disagreement with Sleeper `state.week` / `display_week` is logged to `run.json`.
- **Gate:** all time logic runs in Python with `zoneinfo`. The gate reads `league.json` and `run.json` from the **current tip of main** (after `git fetch`), and calls Sleeper only when a window is due. The manual button always forces a run (`--force`).
  - **Windows:** each window is an interval [open, close) checked against the actual run time. The first gate run inside a window runs it, so a delayed slot still catches up before close. A window is recorded as done in `run.json` only when the sync succeeds (§7) and its commit landed. A failed run leaves the window open for the next slot.
  - *Pre-waiver window:* the next waiver-processing instant is computed from league settings (daily or weekly mode; field encodings confirmed in Phase 1 and pinned by a test). open = instant − 12 h, close = instant − 1 h.
  - *Saturday window:* 12:00–18:00 ET.
  - *Sunday window:* open = first Sunday kickoff − 4 h, close = first Sunday kickoff − 15 min. Hourly Sunday slots put at least three slots in this window, including for 9:30 ET international games.
  - *Off-season:* the gate exits 0 with no Sleeper league calls and no commit unless `state.season_type == regular`, the league `status` is not complete, and the week is ≤ the last fantasy week.
- **Adapters verify the week** the source claims (page label or response field). A mismatch gives status `not_yet_published`: the rows aren't ingested and the footer shows "week N not posted yet".

## 5. Sources
### 5.1 Adapter contract
Every adapter returns an `AdapterResult` with these fields:
- `status`: one of ok, skipped, not_yet_published, auth_expired, blocked, parse_error, budget, or failed.
- `kind`: projection or rank.
- `universe`: the positions covered, the list depth D per list, and `complete` (true or false) per position.
- `covered_keys`: the Sleeper stat keys it projects (projection sources only).
- `rows`: the normalized data.
- `no_projection_ids`: players the source returned whose rows were discarded as placeholders (§4.1).
- The week the source claims, and `fetched_at`.

A failed or skipped adapter never fails the sync (§7).

### 5.2 Candidate sources
Availability is unconfirmed until **Phase 1 probes each one from Actions.** The final list is a SPEC amendment approved by the owner.

| Source | Kind | Access | Notes |
|---|---|---|---|
| Sleeper projections (`api.sleeper.com/projections/nfl/{season}/{week}`) | Projection (full lines) | Free, undocumented | ✅ **MVP source.** RotoWire data; ~630 real IDP rows/week with solo, assisted, TFL, sack, QB hit, PD, INT, FF, FR. Query with `position[]=DL&LB&DB` (returns granular positions too). Universe: complete for DL/LB/DB. Past weeks retrievable and still pre-game → backfill weeks 1–4. |
| FantasyPros weekly ECR (IDP, DL, LB, DB pages) | Rank | ✅ **Free, no login** | ✅ Embedded `ecrData` JSON: ~376 IDP players, ~25 experts, weekly, STD scoring. Treated as private (D16) like all FP data. |
| FantasyPros IDP projections (`/nfl/projections/{dl,lb,db}.php`) | Projection (TACKLE, ASSIST, SACK, PD, INT, FF, FR, TD; no TFL/QB hit) | Logged-in only: logged out shows 10 rows | Depends on FP access (D9, §13 Q1). Private. |
| RotoBaller weekly IDP rankings article | Rank | ✅ Free | ✅ Three tables (DL/edge, LB, DB), top 50 each, `Rank/Player/Team/Opponent`. Article URL found weekly from its search page. |
| ESPN fantasy API IDP projections | Projection (ESPN stat ids 93–113) | Requires an **ESPN league with IDP enabled** | ESPN's default feed omits IDP players entirely (verified). Usable only if the user creates one (§13 Q2). |
| The Odds API props | Projection (partial, market-implied) | Free tier, `ODDS_API_KEY` | Optional; not configured. Rules in §6.2. |
| ~~FantasyNerds, IDP Guru~~ | | Paywalled | ❌ Dropped (no free rows). |
| ~~Razzball IDP~~ | | Free but rest-of-season only | ❌ Dropped (weekly rankings page is JS-rendered; projections are ROS). |
| ~~ESPN article, Dynasty Nerds, The Huddle~~ | | | ❌ Dropped (JS-rendered / start-sit only / blocks automated requests). |

**Redundancy:** accuracy weighting can't detect sources that copy each other. Config `prior_weight_cap` can limit aggregate sources such as FP ECR.

**FantasyPros adapter:**
- **Login:** auto-login with `FP_EMAIL` / `FP_PASSWORD` into an in-memory cookie jar for the run (sent only over https to fantasypros.com). If auto-login is unavailable, `FP_COOKIE` is used. Credentials, cookies and CSRF tokens are never written anywhere.
- **Detection:** it checks for the logged-in state (a Premium-only marker) and for the week label.
- **Statuses:**
  - `auth_expired` → banner "FantasyPros login failed" (check `FP_EMAIL`/`FP_PASSWORD`, or refresh the fallback cookie with the one-command script). This is the only status that triggers that banner.
  - `blocked` (403/429/503 or a challenge page) → banner with that reason.
  - `parse_error` and `not_yet_published` → banner with that reason.
- **Privacy:** request headers and cookies are never logged.
- **Fallback:** if Phase 1 shows FP blocks GitHub's runners, you choose at the Phase 1 review between three options: drop FP Premium; a local `sadlidp fetch fantasypros` run on your own computer that pushes encrypted rows; or a self-hosted runner, which GitHub advises against on public repos.

## 6. Methods
### 6.1 Positions and matching
- **Position (D14):** the slot Sleeper counts toward `position_limit_*`. Phase 1 verifies it against SADL rosters and transactions. Until then, Sleeper's `position` is mapped as DE/DT/NT→DL, LB/OLB/ILB/MLB→LB, CB/S/FS/SS/DB→DB. `fantasy_positions[0]` is used only if `position` isn't a defensive code. Note that the order of `fantasy_positions` is canonical (DL before LB, DB before LB) and does **not** indicate the primary position. Players with more than one IDP fantasy position get the dual-eligible tag.
- **Matching:** the Sleeper ID is canonical. Sleeper's own `espn_id` / `gsis_id` are used first, then the DynastyProcess/nflverse crosswalk, then normalized name (suffixes, punctuation, nicknames) plus team plus position. `player_overrides.yaml` holds manual fixes.
- **Code tables:** all team and position codes go through per-source alias tables into Sleeper's vocabulary (e.g. nflverse `LA` → `LAR`). An unresolved code or an unmatched player is listed in the footer and never silently dropped.

### 6.2 Partial stat lines and imputation
- A key in `covered_keys` that is absent counts as **0**. Only keys outside `covered_keys` are imputed. Imputed keys are flagged per source in the snapshot.
- `imputed_k = anchor × r̂_k`. The anchor is a volume stat the source does project (total tackles; games = 1 if there is none). `r̂_k = (x_k + m_k·r_prior,k) / (exposure + m_k)`, where x_k is the player's season total of k and exposure is his season total of the same anchor stat (tackles, or games). This is the player's season rate per anchor unit from Sleeper actuals, shrunk toward the average for his granular Sleeper position (DE/DT/LB/CB/S) last season. `m_k` is set per category in config and is large for INT, FF, FR, def TD, safety and blocked kick.
- A total-tackles-only source is split into solo and assisted using the player's shrunk solo share.
- **Props:**
  - *Conversion:* de-vig each two-way market (multiplicative), take the median p_over across books, and solve for the Poisson λ with P(X > line) = p_over. Assisted = λ(tackles_assists) − λ(solo), floored at 0. If only the combined market exists, split by solo share.
  - *Fetch rules:* weekend refresh only, events not yet kicked off and within ~36 h, at most once per event per ET day.
  - *Budget:* capped by `odds.monthly_credit_budget` (default 450) using the `x-requests-remaining` header. When the budget runs out, the status is `budget`.
  - *Missing props:* a week with no props is "not available", not a failure.

### 6.3 Scoring engine
- `points = Σ_k scoring_settings[k] × stat[k]` over **every** key in `scoring_settings` (a missing stat counts as 0, no position filter). This covers idp_*, special-teams and return keys, and receiving for two-way players. Relevant keys R are those with a non-zero coefficient that any DL/LB/DB recorded in Sleeper actuals this season or last. A key in R with no adapter mapping and no imputation rule is reported in `run.json`.
- **Actuals:** Sleeper stat lines are used as delivered, including any precomputed bonus keys. A bonus key that isn't delivered is derived from the counts. The ground-truth test confirms which applies.
- **Projected threshold bonuses:** `bonus_tkl_10p` on idp_tkl (solo + assisted), `bonus_sack_2p` on idp_sack, and `idp_pass_def_3p` on idp_pass_def, all inclusive (≥). The underlying stat and the threshold for each are confirmed in Phase 1. Each is scored per source as `setting × P(X ≥ t)` with X ~ Poisson(the source's projected count); negative-binomial dispersion is optional in config. A bonus value supplied by the source is used as-is and never added twice. Any other threshold key is reported as unsupported.

### 6.4 Consensus
- **Rank → value (within a list):** for each list L a rank source publishes, in whatever position taxonomy it uses (e.g. separate CB and S lists), let V be the projection-group values P_i of the players in L that have one. V is sorted in descending order and **indexed from 1** (V_1 is the largest).
  - Rank r maps to V_r. Fractional ranks from averaged ties are linearly interpolated, so rank 1 always maps to V_1.
  - Let Q be the P values of the full projection pool at the list's own granular position (e.g. all S for an S list; rostered players included), sorted descending and indexed from 1. For r > |V|, whether from a list deeper than projection coverage or a censored rank, the value is min(V_|V|, Q_min(⌈r⌉,|Q|)).
  - If |V| < 10, Q replaces V and rank r maps to Q_r (rank to rank, no rescaling).
  - The curve is built from P, never from μ. The value attaches to the player's D14 position.
- **Fallback when no projection source exists that week:** for each position, rank players ex ante (by Sleeper's archived weekly projections if available, otherwise by trailing points per game). Then map rank r to last season's mean actual SADL points at that ex-ante rank, smoothed with isotonic regression plus a moving average. Accuracy isn't updated for fallback weeks.
- **Censoring rank:** before any censoring, compute P⁰_i, the within-group weighted mean of the projection sources' *listed* (uncensored) values. Players are ranked by P⁰ within each source list's own position taxonomy (e.g. among S for an S list; among the D14 position for a DL/LB/DB list). In a fallback week the ex-ante rank is used instead. Both censoring rules below use this P⁰-rank and are applied once. Censored values are never used to re-rank.
- **Absence rules (per the adapter's universe):**
  - A *complete* projection source: an active player with neither a row nor an entry in `no_projection_ids` counts as 0. A player in `no_projection_ids` is *missing* for that source, never 0.
  - A *depth-D* projection source: an unlisted player whose P⁰-rank is ≤ D+M (default M = 10) gets the source's lowest listed value in that list.
  - A *depth-D* rank source: an unlisted player whose P⁰-rank is ≤ D+M gets rank max(D+1, his P⁰-rank).
  - A player with no P⁰, or anything else not covered above, is *missing* for that source.
- **Mean:** P_i and R_i are the weighted means of the covering sources within each group (weights renormalized over those sources). μ_i = α·P_i + (1−α)·R_i when both exist, otherwise whichever exists. If a group has no sources at all that week, the page says so.
- **No projection:** a player with no value from any source has no μ. He isn't tiered and is listed under "no projection" below the tiers. A rostered player with no μ shows as "no projection" and is not eligible as S or B (§6.7).
- **Projection uncertainty** (used for tiers): s_i² = Σ_s w̃_s (x_si − μ_i)², where w̃ are the effective weights normalized to sum to 1 over covering sources. This is shrunk with s̃_i² = (ν0·s0_p² + (n_i−1)·s_i²)/(ν0 + n_i − 1). s0_p² is the median s_i² over players at position p with ≥3 sources this week, or a config default when no such players exist, e.g. in the single-source MVP. **σ_μ,i = max(s̃_i, σ_floor,p).** It is **not** divided by n_eff, because sources are correlated (aggregates, shared inputs, shared imputation). It **never** includes game-outcome variance.
- **Displayed per player:** μ, σ_μ, the min–max range over non-private sources, and the number of sources (counting censored values separately). `consensus.json` and the site's JSON copy contain only these fields plus position, tier and flags. They never contain P_i, R_i, per-source values or per-player weights.

### 6.5 Snapshots, freezing and accuracy
- **Storage:** `sources/<src>.json` holds normalized rows (sleeper_id, stats or rank, SADL value, fetched_at).
  - **On a successful fetch,** every player whose kickoff is after the new fetch's `fetched_at` takes the new fetch's state: his row is updated if he is listed and **deleted** if he isn't, so the §6.4 absence rules apply.
  - **Rows of players whose game kicked off** before the new `fetched_at` are never modified or deleted. The file is therefore the frozen snapshot.
  - **On a failed fetch** the file is left untouched. Staleness is recorded per source in `run.json` (time of the last successful fetch), not per row. A stale source stays in the live consensus up to `max_source_age_h`.
  - For accuracy, a value fetched more than 4 days before kickoff counts as missing.
  - The kickoff rule applies to syncs. `sadlidp backfill` writes past weeks from archived pre-game projections and marks those rows `backfill: true`.
- **Actuals:** week N is re-scored on every sync until 4 days after its last game, which picks up stat corrections. Weights use week N only after that.
- **Scoring for weights:** sources and actuals are scored on **core points**: SADL points excluding idp_def_td, idp_safe, idp_blk_kick, return yards and the bonuses tied to them, which are unpredictable and dominate squared error. Displayed MAE uses full points.
- **Evaluation pool:** per position, the top `N_eval` (default 50) by the frozen consensus computed with **prior** weights, which avoids a feedback loop. Players who were Out/IR/bye at freeze time and late scratches are excluded.
- **Within-group weights:**
  - For each source s and position-week, use only the pool players s covers, and require ≥15 of them. Compute `r_s = Σ(x_s − a)² / Σ(c − a)²`, where a is actual points and c is the frozen consensus on the same players.
  - w_data,s ∝ 1/mean(r_s), pooled across positions in v1.
  - w_s = n_s/(n_s+k)·w_data,s + k/(n_s+k)·w_prior,s, with k = `weights.k` (default 4, measured in weeks). **n_s = the number of evaluated weeks in which s met the ≥15-player requirement at one or more positions.** It never counts players. Priors are equal within a group, subject to `prior_weight_cap`.
  - Rank sources are scored after mapping, so their score mostly reflects ordering skill (noted on the page).
- **Group split:** α̂ = clamp(Σ(y−R)(P−R)/Σ(P−R)², 0.2, 0.8) over pool pairs where both group means exist, the least-squares mix of the two groups. α = n/(n+k)·α̂ + k/(n+k)·`weights.prior_split`, where **n = the number of evaluated weeks with ≥1 pool pair**. It never counts players.
- **Activation:** prior weights apply until 3 evaluated weeks exist (backfilled weeks count).
- **Page:** a table of MAE, coverage and current weight per source per position.

### 6.6 Anchored significance tiers (per position)
Tiers are computed over the available pool after §2 filters, locked players removed, sorted by (−μ, sleeper_id). The first player anchors Tier 1. Each next player i joins the current tier while `μ_anchor − μ_i ≤ z·√(σ_anchor² + σ_i²)`, with σ = σ_μ. Otherwise he starts a new tier as its anchor. Default z = 1.0 (config), tuned after real weeks. Comparing every player to the tier's top player prevents chaining, and the tiers are contiguous and deterministic. Display truncation doesn't change upper tiers. **Rostered players never anchor.** Let a1 be the Tier 1 anchor. A rostered player m is shown as "above Tier 1" if `μ_m − μ_a1 > z·√(σ_a1² + σ_m²)`. Otherwise he is shown in the first tier, top-down, whose anchor a satisfies `μ_a − μ_m ≤ z·√(σ_a² + σ_m²)`. If no tier satisfies it, he is shown as "below Tier N", where N is the last tier. In the single-source MVP every σ equals the default, so tiers are equal-width point bands until more sources arrive.

### 6.7 My IDPs panel (D15)
- **Outcome probabilities use predictive σ:** σ_pred,i² = σ_μ,i² + σ_out,p². σ_out,p is the RMS of (actual − frozen μ) for evaluated pool players at position p once 3 evaluated weeks exist. Before that, it is last season's within-player SD of weekly SADL points (players with ≥8 games) under the current scoring.
- **Eligible** = your rostered players at the position (D14) who pass every §2 waiver-pool filter (the same injury codes, `status`, `active`, `team`, bye and locked rules), have a μ, and are not in `reserve`.
- **Limit count** = your rostered players at the position, excluding `reserve` (IR), which Phase 1 showed Sleeper doesn't count (§13); `taxi` players are counted.
- **S (projected starter)** = the eligible player with the highest μ. If there is none, μ_S = 0 and σ_pred,S = 0.
- **B (other)** = the remaining eligible player, if any.
- **START over S badge** on waiver player W when `Φ((μ_W − μ_S)/√(σ_pred,W² + σ_pred,S²)) ≥ swap_threshold` (default 0.6). Suggested move:
  - "add W, no drop" if you're under the position limit;
  - otherwise "add W, drop B" if B exists;
  - otherwise the panel says that your other player is unavailable this week (bye/Out/IR) and leaves the drop choice to you.
- **UPGRADE over B badge** (only when B exists and W lacks START) uses the same formula against B.
- **Locked starter:** if your Sleeper starting slot at the position (from matchups `starters`) holds a locked player, START is turned off for that position ("starter locked").
- **Context shown:** each rostered player's μ, σ, tier placement and status, plus the league's waiver type. There is no FAAB advice in v1.

## 7. Failure handling and security
- **Fatal:** a failure of the documented Sleeper /v1 endpoints (state, user, leagues, league, rosters, players). The job fails, nothing deploys, and the last good site stays live.
- **Non-fatal:**
  - Every source adapter, including Sleeper projections.
  - A `/stats` failure shows a banner, skips the accuracy update, and reuses cached season rates.
  - A sync succeeds if ≥1 projection or rank source returns rows. If none do, the page shows the stale data with a banner.
- **Secrets:**
  - Authenticated responses, cookies and API keys are never committed, logged, uploaded as workflow artifacts, or stored in actions/cache. All four are readable by others in a public repo (pull requests, including from forks, can restore caches created on main).
  - Private and keyed sources (FantasyPros, The Odds API) bypass the HTTP cache. Their responses stay in memory only, and the cache never stores Cookie, Set-Cookie or Authorization headers. The Odds once-per-event rule is enforced from a fetch log in `run.json` (event id, fetched_at, credits remaining), never from a cached URL.
  - Private-source plaintext is decrypted in memory only and never written inside the working tree. FP normalized rows are committed only as `age`-encrypted files (D16).
  - The fixture recorder strips Cookie, Set-Cookie and Authorization headers and token query parameters. FP fixtures are synthetic rows.
- **Leak guard (`sadlidp leakcheck`):**
  - *When:* sync.yml, probe.yml and the local FP fetch run it on the staged changes (`git diff --cached`) **and** on the built site, immediately before every commit or `upload-pages-artifact`.
  - *On a hit:* it aborts the job, so nothing is committed or deployed, and reports only the file path and the pattern name, never the matched text.
  - *What it looks for:*
    - each cookie value in `FP_COOKIE` of 8 or more characters, `FP_EMAIL` and `FP_PASSWORD`, including their JSON/repr-escaped forms;
    - `ODDS_API_KEY`;
    - `AGE-SECRET-KEY-`;
    - `Cookie:`, `Set-Cookie:` and `Authorization:` header lines, also as quoted JSON or dict keys;
    - any file for a `private: true` source under `data/` that isn't `*.json.age`.
  - *CI:* `ci.yml` runs the same pattern scan over `data/**` and `tests/fixtures/**` only. It skips the secret-value checks when secrets aren't available (fork PRs).

## 8. Implementation phases
0. **Spec commit.** Commit this spec as `SPEC.md`. Because the repo starts empty, **push it to `main` first so that `main` becomes the default branch** (scheduled workflows, the dispatch button and the Pages deploy all need it). Then push the same commit to the first feature branch. Feature work happens on claude/* branches and merges to main by PR.
1. **Probe spike** (`probe.yml`, which also triggers on push to its own branch):
   - **Hit:** every candidate source and every Sleeper endpoint.
   - **Store:** commit scrubbed, non-FP samples to a `probe-fixtures` branch.
   - **Verify:**
     - the D14 position that counts toward the limit, and whether `reserve` and `taxi` players count toward `position_limit_*`;
     - waiver field encodings;
     - week-rollover timing;
     - injury codes;
     - bonus key semantics;
     - whether past weekly projections can be retrieved;
     - whether FP is reachable from runners, and whether a scripted login works there (auto-login, D9);
     - each source's weekly availability and parseability.
   - **FP probe output is a structure report only:** HTTP status, final URL path, whether the Premium marker and the week label are present, JSON key paths or table column headers, row counts per position, and the list of stat columns. It never contains cell values, player names, page text, headers or account details. If a full sample is needed for parser work, the probe age-encrypts the raw response to `FP_DATA_KEY`'s public recipient and commits only the `.age` file to `probe-fixtures`. You decrypt it locally and supply a value-redacted excerpt, and the synthetic FP fixtures are built from that.
   - **Deliverable:** a SPEC amendment covering the final source list, the verified facts, and the ESPN-league and FP-fallback questions, approved by the owner before Phase 3.
2. **MVP (live ASAP):** skeleton, config, Sleeper client, scoring engine plus the ground-truth test, Sleeper projections adapter, snapshot/freeze storage, consensus, tiers, My IDPs, site, sync.yml (gate, concurrency, Pages), README setup. Then backfill weeks 1–5 if possible.
3. **More sources:** FP (encrypted storage, status handling), the adapters approved in Phase 1, matching/crosswalk, imputation, rank mapping and censoring.
4. **Accuracy:** evaluation and adaptive weights, using data collected since the MVP went live plus the backfill. Accuracy table on the page.
5. **Polish:** props adapter (if a key exists), accuracy and `z` tuning, UI refinements.

## 9. Config tunables (`config/sadl.toml`)
`league.names`, `league.league_id?`, `roster.position_limit` (fallback), `display.depth=25`, `tiers.z=1.0`, `tiers.sigma_floor.{dl,lb,db}=0.75`, `tiers.s0_default`, `tiers.nu0=2`, `swap_threshold=0.6`, `censor.M=10`, `weights.prior_split=0.6`, `weights.k=4`, `weights.group_bounds=[0.2,0.8]`, `weights.prior_weight_cap`, `accuracy.N_eval=50`, `accuracy.min_covered=15`, `impute.m_k.*`, `max_source_age_h=96`, `odds.monthly_credit_budget=450`, `gate.*` (window offsets), `sources.<name>.{enabled, private}`.

## 10. Verification
- **Unit tests** (pytest, offline fixtures):
  - scoring against hand-computed lines using SADL's real `scoring_settings`;
  - threshold-bonus expectation;
  - imputation and shrinkage;
  - rank mapping: a CB/S-split list, a list deeper than projection coverage, a hybrid listed under another position;
  - censoring;
  - σ_μ: single source gives s0_p, identical sources with s0_p = 0 give the floor, disagreeing sources;
  - rank mapping: rank 1 → V_1, ranks beyond |V| use the tail rule, censored ranks D+1…D+M;
  - freeze rule: a pre-kickoff player absent from a new successful fetch is deleted, while a post-kickoff row is untouched;
  - no-μ players: not tiered, and never S or B;
  - tiers on synthetic μ/σ;
  - START/UPGRADE logic, including empty slot, bye/Out, under-limit and locked-starter cases;
  - week resolution and the gate across DST, a Wednesday opener and international games;
  - league-match failures;
  - matching edge cases: Jr./III, team changes, LAR/LA, duplicate names;
  - freeze rules, where a post-kickoff sync must not modify rows.
- **Ground-truth acceptance:** the fixture is `stats/{week}`, `matchups/{week}` and `scoring_settings` captured ≥4 days after a completed week. Assert `|ours − players_points[pid]| ≤ 0.01` for every IDP in `players_points`, and list any mismatch with its stat keys. The fixture week must include at least one threshold-bonus hit, and a defender with special-teams or return stats if one exists.
- **End to end:** run sync.yml via workflow_dispatch, then confirm:
  - the site renders on a phone with tiers, My IDPs badges, the footer and the data-as-of time;
  - `fp_mode=force_invalid` produces the cookie banner, not a failure;
  - `sadlidp gate` reports the expected next windows;
  - a second run after a game kicks off leaves that game's rows unchanged.
- **Security:** the leak guard passes on every sync, probe and local-fetch commit and on the built site, and the CI pattern scan passes. A seeded fake secret in a test fixture branch makes the guard abort. No FP plaintext exists in the repo, the site, artifacts or caches.

## 11. Known limitations (accepted)
- **Weekly decisions only.** There is no look-ahead (D10).
- **Thursday-night freezes.** TNF and holiday players are frozen from the pre-waiver run unless you press sync before kickoff.
- **Blend inversion (D16, accepted by user).** When a private source is the only member of its group for a player, its per-player value can be recovered from μ, the plaintext rows in `data/` and the public weights. For example, with FP ECR as the only rank source, R_i = (μ_i − α·P_i)/(1−α), which can then be inverted through the rank curve. D16 keeps FP's rows from being copyable wholesale, but it doesn't hide what they contain.
- **Dropped schedules.** GitHub can delay or drop scheduled runs. Each window has several slots and catches up, but if every slot in a window is dropped, that refresh doesn't happen unless you press sync.
- **Inactives.** The Sunday refresh can run before official inactives are posted (~90 min before kickoff).
- **Weight compression.** Weights based on accuracy stay close to equal, because game noise dominates IDP squared error. Shrinkage keeps this conservative.

## 12. Change log
- **2026-10-12 · v2:** agreed spec.
- **2026-10-12 · D9 amended (user decision):** FantasyPros access via auto-login with `FP_EMAIL`/`FP_PASSWORD`, tested in Phase 1, with a one-command local cookie refresh as the fallback. The manual cookie copy-paste is no longer the plan.
- **2026-10-12 · Phase 1 amendment:** verified facts in §13; final source list in §5.2; D14 resolved to Sleeper's `position` field.

## 13. Phase 1 findings (probe runs 1–3, 2026-10-12)
Evidence: `probe-fixtures` branch, `latest/report.md` and `runs/`.

**Sleeper / SADL facts**
- **League:** "Slow Ass Draft League", 12 teams, superflex offense + 1 DL / 1 LB / 1 DB, 16 bench, 4 IR, 3 taxi. `settings.position_limit_{dl,lb,db}` = 2 (also QB = 4). Your roster is `roster_id` 7.
- **D14:** replaying all 18 in-season free-agent/waiver IDP adds, only Sleeper's `position` field fits every add (15 adds ended exactly at the limit, none over). `fantasy_positions[0]` would have broken it 3 times and "counts at every eligible slot" 4 times. **IR players don't count** toward the limit (5 adds succeeded only with IR excluded). Taxi: no evidence (no taxi IDPs involved), so taxi players are counted, as the spec defaults. Sleeper enforces limits only at add time: trades and the draft can leave a roster over (one roster has 4 DL now).
- **Scoring:** SADL IDP coefficients are solo 2, assisted 1, TFL 2, sack 6, QB hit 1, PD 3, INT 6, FF 3, FR 3, def TD 6, safety 3, blocked kick 3, plus special-teams FF 1 / FR 1 / TD 6. **No threshold bonuses**, so the §6.3 bonus logic is dormant for SADL. The scoring engine reproduces Sleeper's `players_points` **exactly** for every IDP who recorded a stat in week 4 (69/69, max difference 0.00), from either stats endpoint. This is the §10 acceptance fixture.
- **Waivers:** FAAB, $100 budget, `waiver_day_of_week` = 2 processes **Wednesday 00:00 Pacific** (108 of 117 claims processed at that time; 0 = Monday). `daily_waivers` = 0, `waiver_clear_days` = 1. Failed claims cite roster size or being outbid, never position limits.
- **Week rollover:** `state.week` stays on the current week through Monday night; new-week transactions start Wednesday. Target week comes from the schedule (§4.3) as specced.
- **Injury codes seen:** IR, Out, PUP, Questionable, Sus, NA, COV, plus `status` Active/Inactive. Doubtful is valid but wasn't present this week.
- **Projections endpoint:** of 4,357 rows, 628 are real (have `gp` and an opponent); the rest are placeholders, as §4.1 assumed.

**Schedule and IDs**
- nflverse `games.csv` works; uses `LA` for the Rams (alias to `LAR`); kickoffs include 09:30 ET international games.
- The DynastyProcess crosswalk covers IDPs poorly (Sleeper id 51%, FantasyPros id 34%), and only ~21 of SADL's rostered IDPs carry an ESPN id in Sleeper. Matching relies on normalized name + team + position with `player_overrides.yaml`.

**FantasyPros access (D9)**
- The login page (`secure.fantasypros.com/accounts/login/`) runs **Cloudflare Turnstile**, a browser human-check, plus a hidden `token` field. Scripted auto-login will very likely fail; it's untested because no credentials are configured. Logged-in session cookies are named `fpjwt`, `fptoken`, `fpuserinfo`, `fp_level`.
- The weekly ECR rankings don't need a login at all. Login only unlocks the full stat projections.

**Open questions for the owner**
1. FantasyPros projections: test auto-login once with `FP_EMAIL`/`FP_PASSWORD` (likely blocked), or go straight to the cookie fallback, or skip FP projections and use only the free ECR?
2. ESPN: create a free ESPN league with IDP scoring so its IDP projections can be read?
3. Optional: sign up for The Odds API (free) for tackle/sack prop lines?

**MVP source set (if no changes):** Sleeper projections (projection group) + FantasyPros ECR and RotoBaller (rank group), 60:40.
