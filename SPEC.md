# SADL IDP Streamer: Specification

## Context
SADL ("Slow Ass Draft League") is a Sleeper league whose IDP scoring differs from the formats public rankings use, and those sources don't agree with each other anyway. IDP rosters are tight: start 1 DL / 1 LB / 1 DB, max 2 rostered per position. The waiver wire is therefore always deep, and streaming the best weekly option is the main IDP edge.

**Goal:** an on-demand tool that pulls IDP projections and rankings from every reliable source it can reach, rescores them to SADL's exact Sleeper scoring, blends them into a weighted consensus with an uncertainty for each player, and publishes **weekly streaming tiers** per position to GitHub Pages. The list covers only players actually available in SADL, shown next to your own rostered IDPs.

**Kickers (added 2026-11-17, D23):** SADL also starts one kicker, scored by the yard (0.1 per yard of each made field goal), which public kicker rankings don't use either. The tool tracks kickers as a fourth position, K, beside the three IDP ones, from their own sources and with their own weights. Everything fitted or measured for DL, LB and DB stays exactly as it was.

**Status:** agreed spec (v2, 2026-10-12). Building follows the phases in §8, MVP first. Any change to decisions D1–D25 is made by amending this file (D23 and D24 added 2026-11-17, D25 2026-11-21). The spec went through two adversarial review rounds before agreement.

---

## 1. Decisions (from Q&A)
| # | Topic | Decision |
|---|---|---|
| D1 | Platform | Sleeper. Public read-only API, no auth. |
| D2 | Scoring | Pulled from the league's `scoring_settings` on every sync. Never hand-entered. |
| D3 | Lineup / roster | 1 DL, 1 LB, 1 DB starters. Max 2 rostered per IDP position. Both read from Sleeper (`roster_positions`, `settings.position_limit_{dl,lb,db}`). **Amended 2026-11-17 (D23):** plus 1 K starter, also from `roster_positions`. The league sets no `position_limit_k`, so a kicker is limited by roster room alone (§6.7). |
| D4 | Sources | Every source that is reliable and reachable (§5). FantasyPros Premium is available. |
| D5 | Acquisition | Automatic but on demand. Polite fetching: caching, ≥2 s between requests per host, an identifying user agent, and backoff on errors. |
| D6 | Output | Personal use. Static site on GitHub Pages. The repo `layne-burns/SADLidp` is public. |
| D7 | League identity | Resolved from Sleeper user `lbburnsy` plus a league-name match for the current season (§4.2). This also identifies **your** roster. |
| D8 | Rank-only sources | Included. They are converted to SADL points by within-list rank-to-value mapping (§6.4). |
| D9 | FantasyPros | **Free weekly consensus rankings (ECR) only:** the public `/nfl/rankings/{idp,dl,lb,db}.php` pages and their embedded `ecrData`. No login, no cookie, no FP projections, so no FantasyPros credentials are stored. (Owner decision closing Phase 1: FP's login page runs Cloudflare Turnstile. This supersedes the earlier auto-login plan; see §12.) FP failing **never fails the sync**. **Amended 2026-11-17 (D23):** the public kicker page `/nfl/rankings/k.php` is read the same way, as its own source (`fp_k`, §5.2). Still no login and no FP projections: FantasyPros' kicker projections serve only 10 rows logged out (D21). |
| D10 | v1 scope | Weekly streaming tiers, **my roster vs waivers**, and **source accuracy tracking**. **Out of v1:** look-ahead weeks and an in-house matchup model. **Amended 2026-11-17 (D23):** kickers join all three (tiers, my roster vs waivers, source accuracy) as their own group; the IDP scope is unchanged. |
| D11 | Weights | The projection : rank group split starts at **60:40** as a prior. Accuracy tracking may move it, with each group kept within 20–80%, and may also move per-source weights within each group (§6.5). |
| D12 | Tiering | **Anchored significance tiers** (§6.6). |
| D13 | Sync timing | Manual button, plus an automatic run before SADL waivers process, plus weekend injury refreshes (§4.3). **Amended 2026-10-12 (PR 5):** a Thursday window (§4.3) so RotoBaller's Thursday-morning rankings are captured before TNF. **Added 2026-10-12 (D22):** a Sunday-evening results window after the afternoon games. |
| D14 | Dual eligibility | A player's position is **the one Sleeper counts toward the max-2 roster limit**. **Verified in Phase 1 (§13): Sleeper's designated `position` field** (DE/DT/NT/DL→DL, LB/OLB/ILB/MLB→LB, CB/S/FS/SS/DB→DB), not `fantasy_positions[0]`. He appears **only** in that position's list, with a **dual-eligible** tag (e.g. "LB · also DL"). **Amended 2026-11-17 (D23):** a kicker is a player whose Sleeper `position` is K and who has no IDP eligibility. `fantasy_positions` never makes a kicker (Sleeper lists punters as ["K", "P"]), and a kicker has no dual-eligible tag. Everyone who had an IDP slot before kickers were tracked keeps it (§6.1). |
| D15 | Swap badges | **Both**: `START over X`, meaning the waiver player likely outscores your projected starter this week, and `UPGRADE over Y`, meaning he likely outscores your other eligible player at that position (§6.7). **Amended 2026-10-12 (owner):** suggestions are still shown when the player they compare against is locked ("still neat to see"). A locked player is compared on his frozen pre-kickoff μ and status, and a suggestion that can't pay off this week (your starter there is locked, or the drop target is) is marked *for reference* (§6.7). |
| D16 | FP data privacy | **Closed 2026-10-12 (owner): the free FantasyPros ECR rows are stored like every other free source** (plain JSON in `data/`, included in the displayed range). No source is private now. The generic `sources.<name>.private` flag remains for any future private source: its rows are never written to `data/`, it is used only in the live blend, and it is left out of the range, the breakdown and `data.json`. The earlier encryption design (age, `FP_DATA_KEY`) is retired. |
| D17 | Rollout | **MVP first**: Sleeper projections → SADL scoring → tiers → My IDPs → page, with snapshot freezing from day one (done). **Phases 3 and 4 are built together** in six PRs (§8): more sources, accuracy weighting, the Stats page and the per-player breakdown. Weeks 1–4 are backfilled from every source with an archive. |
| D18 | Vegas odds (docket) | Owner request: Vegas odds feed tier creation and analysis, in a **post-MVP phase** (§8 Phase 5). Two candidate signals, to be designed when that phase starts: **game lines** (spread, total, moneylines → implied team totals and expected game script), which the nflverse schedule already provides free for every game (week 6 lines present on 2026-10-12); and **player props** (tackles + assists, solo tackles, sacks) from The Odds API as a market-implied projection source (§6.2). This amends D10, which kept matchup modelling out of v1. **Amended 2026-10-12 (owner idea):** team-defense (D/ST) rankings, e.g. FantasyPros D/ST ECR, are a third candidate signal, as a matchup scalar on IDP projections. It is a signal, not a player source: captured with backfill from PR 4, never blended until it proves itself. A pilot on weeks 1–4 found no detectable effect (|r| ≤ 0.1, every CI includes 0; Sleeper's projections already move with the matchup). PR 6 backtests D/ST rank and opponent implied total on 2025 (position-week fixed effects, clustered SEs, both halves of the season); a signal is used only if that passes **and** walk-forward 2026 consensus residuals improve, as an additive term fitted on consensus residuals. **Result (2026-10-14, PR 6b):** `sadlidp backtest dst-fetch` / `dst` ran on 2025 (18 weeks, the 60 best-projected per position each week, misses regressed on the team's D/ST rank and the opponent's implied points within week, SEs clustered by team-week). A signal passes at a position only with |t| ≥ 2 over the season, the same sign in both halves and a lower held-out miss (fit on odd weeks, scored on even). **Neither signal passes anywhere:** every |t| ≤ 1.2, the DB implied-points effect flips sign between halves, and adding both raises the held-out miss at LB (5.85 → 5.88) and DB (4.96 → 5.01) and leaves DL flat (6.418 → 6.415). On 2026 weeks 1–5 (about 230 player-games per position) both correlations with the consensus miss are −0.03 to −0.08, inside noise. So the idea stays out of the consensus. The Stats page shows the result, and the backtest is re-run at the week-9 and week-13 checkpoints (`docs/status.md`). |
| D19 | Per-player breakdown | Owner request: each player shown on the page expands to show every enabled source's number converted to SADL points (projections: points and projected stats, estimated keys marked; rankings: the rank and its mapped points), its share of his blend, and the line "weighted average = μ". This relaxes §6.4's rule that the site JSON never holds per-source values (that rule served FP privacy, D16, now closed). **Wording (amended 2026-10-14, owner request):** the card's points say "form ±x" under the number when the form term moves him by 0.5 or more, and the floor–ceiling reads "range a–b". The breakdown ends with one plain sum, "μ = a from the sources ± b form"; its form line says in words how he has done against his projection and how his snaps compare, and its range line says that 3 games in 5 land inside the range. |
| D20 | Stats page | Owner request: a Stats page showing the source weights week over week (as recorded when in force) and how well the predictions did, with standard metrics (MAE against a naive baseline, RMSE, bias, rank correlation, interval hit rates, top-k hits, Brier score and calibration, START/UPGRADE results, tier separation), per source and for the consensus, by position, weekly and season to date. Plain-language headlines, a glossary and small-sample display rules (§6.8, PR 2 and PR 6). |
| D21 | Sources after Phase 1 + live checks | **In:** Sleeper (projection), ESPN league 942104146 (projection), FantasyPros free ECR (rank), RotoBaller weekly article (rank), FantasyData weekly IDP rankings (rank; new, free, current week only). **Out (checked live 2026-10-12):** Razzball (weekly IDP rankings and Pigskinonator IDP are members-only, even rendered; its free page is last season's rest-of-season), Fantasy Sharks (Cloudflare bot challenge, which we don't work around), CBS (no IDP projections), FFToday (IDP rankings are season-long only). **Amended 2026-11-17 (D23, owner decision; kickers only, the IDP list above is unchanged).** **In for kickers:** the same five sites, each through its own kicker page or request (Sleeper and ESPN projections; FantasyPros, FantasyData and RotoBaller rankings), plus **CBS** (projection). CBS moves In for kickers only: its weekly kicker projections are free, server-rendered and allowed by its robots.txt. It stays Out for IDP, where it has nothing. **Fantasy Sharks stays Out, pending a probe from Actions:** on 2026-11-17 it served kicker projections with a full archive (2025 included) to a home connection with no challenge, under a robots.txt crawl delay of 60 s; whether a runner gets the same answer is not known, and we still don't work around a challenge. **Looked at and skipped (2026-11-17):** Subvertadown (the ordered ranks are paid; the free page is an unordered list); 4for4 and FantasyPros' consensus projections (top 10 only when logged out); FFToday (whole-number lines, and week 11 wasn't posted by Tuesday evening); FanDuel Research, NFL.com and Reddit (no kicker data reachable politely); RotoWire (already inside Sleeper, whose projection rows are RotoWire's); DraftSharks (points only, under a scoring it doesn't state); Fantasy Football Calculator (prose only, numbers of unknown origin). |
| D22 | Scored results | Owner request 2026-10-12: on Sunday evening the page shows the week so far: each of your players' score (live or final), this week's issued suggestions that have played with both players' results and whether the suggestion paid off, and per position an "already played" list of available players with their pre-kickoff tier, projection and score. After the week rolls over, a "last week" recap below the tiers lists every issued suggestion with both players' final scores (from `actuals.json`, provisional until the stat-correction window closes), whether it paid off (void if either didn't play; reference suggestions not counted), and a "paid off X of Y" line linked from the header. **Week picker (owner request, same day):** every synced week keeps its own page (`week-<season>-<NN>.html`, `index.html` = the current week) built from that week's committed data, so a week stays viewable after the sources move on; a past week's page is re-scored from its `actuals.json`, showing every listed player's final score and each suggestion's outcome. Weeks 1–4 (before the first live sync) are rebuilt by `sadlidp replay` from the saved projection snapshots, that week's rosters and lineups (Sleeper matchups) and the saved league settings, as of one minute before the week's first kickoff; projected players are treated as healthy because that week's injury news isn't known, and the page says so. A projection first captured after kickoff is kept and marked (§6.5), so players who played before the first sync don't show "no projection". |
| D23 | Kickers | **Owner decision, 2026-11-17.** The league starts one K, scored by decimal keys only: `fgm_yds` 0.1, `fgmiss` −1, `xpm` 1, `xpmiss` −1, every `fgm_<bucket>` key 0 (§13). K is a fourth slot, tracked by the pipeline and shown on the page. **Isolation rule:** `IDP_SLOTS` (DL, LB, DB) stays the fitted IDP model and every IDP statistic; `SLOTS` = `IDP_SLOTS` + K is what the pipeline tracks and the page shows. Each slot belongs to one group (`idp` or `k`). Sources, players, matching, rank curves, weights, accuracy and statistics are kept apart per group, so every DL/LB/DB number and stored record comes out exactly as before, and nothing measured on kickers can move an IDP number, or the other way round. **Six sources, as siblings:** Sleeper, ESPN and CBS (projections); FantasyPros, FantasyData and RotoBaller (rankings) (D21, §5.2). Each is its own module with its own request beside the IDP one (`sleeper_k`, `espn_k`, `cbs_k`, `fp_k`, `fantasydata_k`, `rotoballer_k`), so nothing a kicker fetch does can change an IDP source's rows, completeness or match report. **Weights** are measured like the IDP ones but apart, in their own record; rows from the four kicker archives are untrusted until each archive is checked (§6.5). **Form** is off for K. **Swing** comes from the kicker's own projected line, swing² = a_K + b_K · `kicker_var`, with a_K = 0 and b_K = 0.85 and the kicker's own floor and ceiling multipliers, measured on past seasons and this season's pre-kicker weeks 1–10 (2026-11-21); **σ_out,K = 5.0** (§6.7). **Roster rule:** no position limit; roster room decides, with a new *swap* action (§6.7). **Past weeks:** sources with an archive are backfilled; settled weeks gain their kicker results add-only; no frozen record is invented and no page already written is rewritten (§5.3, §6.5). No K term was fitted on this season's live kicker weeks; the kicker checks are registered in `docs/status.md` §7. |
| D24 | Page order | **Owner decision, 2026-11-17 (the position-first layout, owner-approved).** It amends the page order fixed in §2 and §6.8. The page now runs: header; a **This week** card; a sticky position bar, DL · LB · DB · K, driving one panel per position (**Yours**, then **Available**); then folded sections: Last week, Waiver notes, How to read this page, Sources. The report card moves from the top of the page into Last week. No number, rounding, tier order, odds cap, actionable/reference split or card wording changes. One label does: a tier row's "range a–b (n sources)" becomes "sources say a to b (n)", so "range" means only the floor–ceiling (D19). The page works without JavaScript (§2, §6.8). |
| D25 | Other leagues' kickers | **Owner decision, 2026-11-21.** The K tab also covers **Kusch's Crooked League** (Sleeper league 1364651976209473536, found by name among user `lbburnsy`'s leagues of the season; `[kicker_leagues.crooked]`), the only other league of the owner's that starts a K. It scores kickers exactly as SADL does (`fgm_yds` 0.1, `fgmiss` −1, `xpm` 1, `xpmiss` −1, every bucket key 0; it lists one 50+ bucket where SADL lists 50–59 and 60+), so a kicker's number (μ, ±, swing, range, breakdown and frozen record) is computed once, by SADL's run, and shared. **Combined, with labels (owner):** one kicker list of every kicker free in either league, each tagged with the leagues he is free in; *Yours* shows each league's kickers under its own label, with that league's roster count and room; each league's START, UPGRADE and swap moves follow §6.7 against the kickers free in that league, your kicker there, its lineup and its roster room, and name their league ("START · Crooked"). The kicker tiers are counted over the combined list. The This week card lists the league's moves (tagged "K · Crooked"), its starter and its heads-ups; Waiver notes gain its kicker FAAB line; Last week lists its kicker suggestions with their own tally, and the report card names your kicker there (§2, §6.7, §6.8). **Isolation:** nothing about DL, LB or DB changes, and SADL's own kicker suggestions weigh SADL's free kickers exactly as before. A league that can't be read, isn't found, doesn't start a K or scores kickers differently is a note on the K tab, and the list is SADL's alone; it never fails the sync or adds a page banner. It is not replayed or backfilled (its rosters of past weeks weren't saved), and the Stats page doesn't change (kicker accuracy doesn't depend on the league). |

## 2. Defaults (engineer-chosen; changeable via config or a spec amendment)
- **Stack:** Python 3.12, `uv`, `httpx`, `selectolax`, `pydantic`, `jinja2`, `pytest`, `ruff`, and `age` for encryption. No database. Normalized JSON snapshots are committed under `data/`.
- **Pool:** available players are those on no SADL roster. All of them are tiered after filters. The page shows the top **25 per position** (config) and says how many more are hidden. Your rostered IDPs are always shown. *Kickers (2026-11-17):* the same depth applies and your kickers are always shown, but the depth rarely cuts the list: in week 11 only 10 of the 26 projected kickers were on no roster.
- **Hidden from the waiver pool:** `injury_status` ∈ {Out, Doubtful, IR, PUP, NFI, Sus, NA, DNR, COV}, or `status` ≠ Active, `active=false`, `team=null`, on bye, or **locked** (his game has kicked off). Questionable players are shown with a flag. Any other non-null code is shown with a flag and logged as unknown. Your own players always show, with a status badge.
- **Page:** mobile-first. Header "Week N · data as of <time>" (a small JS warning appears if the data is >36 h old in a game week), then the My IDPs panel, then DL / LB / DB tabs, then a sources status footer (status, row count, match rate, staleness and accuracy per source). Once games start (D22): scores next to your players, a "this week's suggestions that have played" list with outcomes, and an "already played this week" list under each position's tiers.
  - **Page order amended 2026-11-17 (D24, owner-approved position-first layout).** The contents above are unchanged; their order is now:
    1. **Header:** the title "SADL streamer", one meta line (season, week and the data-as-of time), a compact week picker that shows the current week without scrolling (previous, current, next; the rest under "all weeks"), then the banners as before.
    2. **This week card:** every suggestion that can still be made, across positions, each with its position, badge, odds and move; then the reference ones, muted; "No move suggested at: …" for the positions with none; heads-up lines from flags the page already had (a player of yours who is out or questionable, a starter whose game has started, byes ahead, no playable player at a position); a strip of your projected starters; and a last line about last week that links down the page, with the link to the Stats page. It says only what the panels below say. On a past week's page the card is headed "Week N at a glance".
    3. **Position bar,** sticky: DL · LB · DB · K. It drives one panel per position: **Yours** (what the My IDPs panel showed for that position) and **Available** (what that position's tab showed: changed since Tuesday, the tiers, the hidden counts, already played). A one-line legend sits under the bar.
    4. **Folded sections:** *Last week* (the report card and last week's suggestions), *Waiver notes* (the Tuesday-questionable line and the FAAB line, wording unchanged), *How to read this page* (the old footer paragraph as bullets, plus the kicker sentences) and *Sources* (a one-line status summary; the table inside, grouped into IDP sources and kicker sources).
  - A page shows the positions its own data has, so a week saved before kickers were tracked has no K.
  - *Other leagues' kickers (D25, 2026-11-21):* the K panel's *Yours* has one labelled block per league (SADL, then Crooked: its players, notes, moves and played moves, with that league's roster count and room); its *Available* list is the combined list, each card tagged with the leagues he is free in, a badge naming its league. A league that isn't shown has a one-line note under its label. Weeks synced before D25 have neither.
  - The bar and the folds work without JavaScript. A small script may remember the chosen position; without it the page still works.
- **Privacy:** sources are plaintext by default. Any source can be marked `private: true` in config to get the D16 treatment, which FP always has.

---

## 3. Architecture
### 3.1 Runtime
```
GitHub Actions — sync.yml (runs from default branch `main`)
  triggers: workflow_dispatch  (= the sync button; works from the GitHub mobile app;
                                input fp_mode=normal|force_fail for testing)
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

**One-time setup** (documented in README): Settings → Pages → Source = GitHub Actions. Secrets: `FP_DATA_KEY` (age key, while FP rows are encrypted per D16) and, if props are ever added, `ODDS_API_KEY`. No FantasyPros or ESPN credentials are needed: FP is read logged out (D9) and the ESPN league is public. Public repos auto-disable scheduled workflows after 60 days without activity, so the README pre-season checklist says to re-enable sync.yml in late August.

### 3.2 Pipeline modules (`src/sadlidp/`)
1. `sleeper.py`: documented /v1 endpoints (state, user, leagues, league, rosters, matchups, players) and the undocumented projections and stats endpoints.
2. `schedule.py`: nflverse schedules CSV. Kickoff = `gameday` + `gametime` as **America/New_York** local time. Provides byes and opponents. A team with zero games in the season is a hard error, never treated as "on bye".
3. `sources/`: one adapter per source behind a shared interface (§5.1).
4. `matching.py` (§6.1) · `impute.py` (§6.2) · `scoring.py` (§6.3) · `aggregate.py` (§6.4) · `accuracy.py` (§6.5) · `tiers.py` (§6.6) · `roster.py` (§6.7) · `backfill.py` (§5.3)
5. `site/`: Jinja templates producing static HTML plus a JSON copy of the consensus.
6. `cli.py`: `sadlidp sync [--force]`, `sadlidp build`, `sadlidp backfill [--source NAME] [--weeks 1-5] [--redo]`, `sadlidp replay --weeks 1-4 --season YYYY`, `sadlidp gate` (dry-run: prints whether the schedule would run, and why).
7. *(added 2026-11-17, D23)* `positions.py`: the slots and the two groups (`IDP_SLOTS`, `SLOTS`, `GROUPS`) and each group's evaluation pool sizes. `sources/registry.py`: every source with its group, kind and page label; a source is fetched, blended, weighted and measured with its own group only. `kicker.py`: the kicking keys, the derived miss, the yards-per-make table and the add-only merge of past kicker results (§6.2, §6.5).
8. *(added 2026-11-21, D25)* `other_leagues.py`: your other leagues whose kickers the K tab covers. It finds each league, checks that it starts a K and scores kickers as SADL does, reads its rosters, this week's matchups and transactions, and saves them in the league's own folder. It never raises: a failure is the league's `status` and `note`. `sync.py` builds the league's kicker panel with `roster.panel`; `site/build.py` renders it.

### 3.3 Repo layout
```
SPEC.md  README.md  pyproject.toml
config/sadl.toml               # all tunables (§9)
config/player_overrides.toml   # manual source id / name → sleeper_id fixes (§6.1)
config/aliases/                # team and position code alias tables per source (espn_teams.json: proTeamId → code)
data/<prev season>/impute_priors.json  # imputation priors from last season's actuals, built once (§6.2)
src/sadlidp/...                # §3.2
data/<season>/league.json      # resolved league id, scoring_settings, settings (per sync)
data/<season>/week<NN>/
    players.json               # slim IDP subset of the players DB, frozen per player at his kickoff (§6.5);
                               #   kickers too from 2026-11-17 (D23)
    rosters.json  run.json  consensus.json  actuals.json
    weights.json               # the weights in force, written once (§6.5); its "kickers" key is the kicker record (D23)
    sources/<src>.json         # normalized rows only, never raw payloads; kicker sources are <site>_k.json (D23)
    frozen.json                # each player's μ/σ/range/status at his last sync before kickoff (§6.5)
    badges.json  tiers_log.json  # START/UPGRADE and tier logs for evaluation (§6.5, §6.7)
site/stats.html, stats.json   # the Stats page (§6.8), rebuilt from the committed week files on every build
data/<season>/<key>/league.json        # another league's id, settings and scoring (D25; key = `crooked`)
data/<season>/week<NN>/<key>/          # its rosters.json (compact), transactions.json and badges.json
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
- **Kickers (added 2026-11-17, D23):**
  - **Limit:** `settings.position_limit_k` is read only if the league sets it. SADL doesn't, and for K there is no config fallback and no "no position limit" warning.
  - **Roster room:** the roster size is the number of `roster_positions` entries, bench included (27). Your free spots are that number less your `players` who are on neither `reserve` nor `taxi`. This is what limits a kicker add (§6.7).
  - **Lineup:** the K slot of `roster_positions` and matchups `starters` gives your starting kicker, as it does at DL, LB and DB.
  - **Projections:** kickers have their own request, `position[]=K`. The IDP request's query string is unchanged. The K request also returns punters (listed ["K", "P"]), so rows are kept only for players whose `position` is K. The `gp`-and-opponent rule is the same.
  - **Stats:** `/v1/stats` already returns every player, kickers included. It also holds `TEAM_<ABBR>` rows that carry kicking keys, so a kicker is always chosen by his position, never by his stat keys.

### 4.2 League resolution
League name match: case-insensitive, trimmed, exact, against config `["SADL", "Slow Ass Draft League"]`, among the user's leagues for the target season. Exactly one match is required. Zero matches fails loudly during the season and exits 0 with no commit in the offseason. More than one match fails loudly and lists every candidate's name and ID. Config `league.league_id` overrides the match. `user_id` is cached after the first lookup, which survives a username change.

### 4.3 Target week and the schedule gate
- **target_week** = the regular-season week that contains the earliest game not yet kicked off, taken from the nflverse schedule. Any disagreement with Sleeper `state.week` / `display_week` is logged to `run.json`.
- **Gate:** all time logic runs in Python with `zoneinfo`. The gate reads `league.json` and `run.json` from the **current tip of main** (after `git fetch`), and calls Sleeper only when a window is due. The manual button always forces a run (`--force`).
  - **Windows:** each window is an interval [open, close) checked against the actual run time. The first gate run inside a window runs it, so a delayed slot still catches up before close. A window is recorded as done in `run.json` only when the sync succeeds (§7) and its commit landed. A failed run leaves the window open for the next slot.
  - *Pre-waiver window:* the next waiver-processing instant is computed from league settings (daily or weekly mode; field encodings confirmed in Phase 1 and pinned by a test). open = instant − 12 h, close = instant − 1 h.
  - *Saturday window:* 12:00–18:00 ET.
  - *Sunday window:* open = first Sunday kickoff − 4 h, close = first Sunday kickoff − 15 min. Hourly Sunday slots put at least three slots in this window, including for 9:30 ET international games.
  - *Thursday window (D13, added 2026-10-12, PR 5):* Thursday 11:00 ET to 1 h before the week's first Thursday kickoff (none in a week without a Thursday game). RotoBaller posts its IDP rankings Thursday morning; the 3-hourly 15:17 UTC (EDT) and 18:17 UTC slots fall inside it.
  - *Results window (D22, added 2026-10-12):* open = the last Sunday kickoff before 17:00 ET + 4¼ h, close = open + 6 h (`gate.results_after_h`, `gate.results_window_h`). A game counts as final 4 h after kickoff, so the afternoon games are final when it opens; the 3-hourly 03:17 UTC slot falls inside it in both EDT and EST weeks. If Sleeper's live stats can't be fetched, the results window stays open so the next slot retries.
  - *Off-season:* the gate exits 0 with no Sleeper league calls and no commit unless `state.season_type == regular`, the league `status` is not complete, and the week is ≤ the last fantasy week.
- **Adapters verify the week** the source claims (page label or response field). A mismatch gives status `not_yet_published`: the rows aren't ingested and the footer shows "week N not posted yet".

## 5. Sources
### 5.1 Adapter contract
Every adapter returns an `AdapterResult` with these fields:
- `status`: one of ok, skipped, not_yet_published, suspect (§7), auth_expired, blocked, parse_error, budget, or failed.
- `kind`: projection or rank.
- `universe`: the positions covered, the list depth D per list, and `complete` (true or false) per position.
- `covered_keys`: the Sleeper stat keys it projects (projection sources only).
- `rows`: the normalized data.
- `no_projection_ids`: players the source returned whose rows were discarded as placeholders (§4.1).
- The week the source claims, and `fetched_at`.

A failed or skipped adapter never fails the sync (§7).

*Calling convention (added 2026-10-12, PR 3):* every adapter is `fetch(ctx) -> AdapterResult`, where `ctx` carries the HTTP client, the Sleeper client, season, week, `now`, the week's IDP players, the source's config, the matcher (§6.1), the imputer (§6.2) and `archive` (true when the backfill runner asks for a past week). An adapter may also return `published_at` (when the source says it posted the week's data) and a `report` (pages, match counts by method, the unmatched players, imputed keys) that `run.json` and the page footer show.

*Groups (added 2026-11-17, D23):* `ctx` carries the players of the source's own group. An IDP source gets what it always got. A kicker source gets the kickers alone, a matcher that knows only kickers, no imputer (a kicker line is never imputed, §6.2) and the `[kicker]` config table. Its lists and its `universe` are keyed "K", and any key it derives goes in the row's `imputed` list. Every source is declared in `sources/registry.py` with its group, kind and page label.

### 5.2 Candidate sources
Availability is unconfirmed until **Phase 1 probes each one from Actions.** The final list is a SPEC amendment approved by the owner.

| Source | Kind | Access | Notes |
|---|---|---|---|
| Sleeper projections (`api.sleeper.com/projections/nfl/{season}/{week}`) | Projection (full lines) | Free, undocumented | ✅ **MVP source.** RotoWire data; ~630 real IDP rows/week with solo, assisted, TFL, sack, QB hit, PD, INT, FF, FR. Query with `position[]=DL&LB&DB` (returns granular positions too). Universe: complete for DL/LB/DB. Past weeks retrievable and still pre-game → backfill weeks 1–4. |
| FantasyPros weekly ECR (DL, LB, DB pages) | Rank | ✅ **Free, no login** | ✅ Embedded `ecrData` JSON: ~375 IDP players, ~25 experts, weekly, STD scoring. Stored like other free sources (D16 closed). PR 4 source. |
| ~~FantasyPros IDP projections~~ | | Logged-in only | ❌ Dropped (owner decision; the login sits behind Cloudflare Turnstile). |
| RotoBaller weekly IDP rankings article | Rank | ✅ Free | ✅ Three tables (DL/edge, LB, DB), top 50 each, `Rank/Player/Team/Opponent`. Article found through its search RSS (PR 5 adapter below). |
| FantasyData weekly DL/LB/DB rankings | Rank | ✅ Free | ✅ PR 5: `fantasydata.com/nfl/rankings/{dl,lb,db}`, 100 deep each, player links carry the FantasyData id (Sleeper's `fantasy_data_id`). No archive. |
| ESPN league 942104146 (owner's public league, IDP enabled) | Projection (solo, assisted, total tackles, sack, INT, FF, FR, PD; no TFL or QB hit) | Free, no secrets | ✅ **Verified (probe run 4):** readable logged out; 398 of 400 IDPs returned had a week projection; stat-id mapping in §13. Phase 3 source. |
| The Odds API props | Projection (partial, market-implied) | Free tier, `ODDS_API_KEY` | ⏸ Docketed for Phase 5 (Vegas odds, D18). Rules in §6.2. |
| ~~FantasyNerds, IDP Guru~~ | | Paywalled | ❌ Dropped (no free rows). |
| ~~Razzball IDP~~ | | Free but rest-of-season only | ❌ Dropped (weekly rankings page is JS-rendered; projections are ROS). |
| ~~ESPN article, Dynasty Nerds, The Huddle~~ | | | ❌ Dropped (JS-rendered / start-sit only / blocks automated requests). |

**Redundancy:** accuracy weighting can't detect sources that copy each other. Config `prior_weight_cap` can limit aggregate sources such as FP ECR.

**FantasyPros adapter (free ECR only, D9; built in PR 4, checked live 2026-10-12):**
- **Fetch:** the public DL, LB and DB ranking pages (`/nfl/rankings/{dl,lb,db}.php?week=N`), logged out, no cookies, 5 s apart (robots.txt crawl delay). Parse the embedded `ecrData` JSON: players, `rank_ecr`, positions, week, expert count and `last_updated_ts`. The combined IDP page isn't read: it is the same experts' ranks across positions, so it would count FP twice.
- **Detection:** each page's `ecrData.year` and `ecrData.week` must match the target season and week and its `players` must be non-empty, otherwise that list is `not_yet_published` (on 2026-10-12, `?week=6` returned `week` 6 with 0 players). Past weeks stay available through `?week=N` (week 1's ranks, last updated 9/13, were served on 2026-10-12), so FP supports backfill (§5.3). `published_at` = the earliest list's `last_updated_ts`.
- **Statuses:** `blocked` (403/429/503 or a challenge page), `parse_error` (no `ecrData`), `not_yet_published` and `failed`, per list. The result is `ok` when at least one list is. There is no login state.
- **Rows:** per matched player, `ranks` {list: `rank_ecr`} (a player can sit on two lists, e.g. DL and LB), the FP id, team and `rank_ave`/`rank_std`; `universe` per list: `complete` false, depth D = the list's length. Team codes go through `config/aliases/fp_teams.json` (JAC → JAX; FA = no team); FP's DE/DT → DL, CB/S → DB are used only for matching guards.
- **List-scoped merges:** a list that wasn't read this sync (any status but ok) keeps its stored ranks (§6.5's deletion applies only to the lists the fetch read).
- **D/ST signal (D18):** the DST page (`dst.php?week=N`) is stored as `sources/fp_dst.json` (kind `signal`: team → rank, opponent), frozen at each team's kickoff and backfilled like a source. It never enters the consensus; PR 6 backtests it. Its failure is a footer line, never a banner.

**ESPN adapter (owner's league, PR 3; re-verified live 2026-10-12):**
- **Fetch:** `lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{espn.league_id}?view=kona_player_info&scoringPeriodId={week}` with an `X-Fantasy-Filter` of `filterSlotIds` [10, 11, 14, 15] (LB, DL, DB, DP), sorted by percent owned, `limit` 400 and `offset` 0, 400, 800, … until a page has fewer than 400 players (at most 6 pages). On 2026-10-12 this returned 1,511 IDPs (400/400/400/311), no duplicates, 964 with a week-6 projection line. The league is public: no cookies or secrets.
- **Week check:** the week is published when at least one returned player has a line with `scoringPeriodId` = week, `statSourceId` 1 and `statSplitTypeId` 1; otherwise `not_yet_published`. Past weeks stay available through `scoringPeriodId` (week 4's projections were still served on 2026-10-12), so ESPN supports backfill (§5.3).
- **All or nothing:** any page error fails the whole fetch (no merge). The result is *complete* only if paging ended on a short page, no ESPN id repeats, and ≥ 98% of the returned players who have an NFL team matched a Sleeper player; and, per slot, at least 60% of Sleeper's active players at that slot with a team and an `espn_id` came back (live 2026-10-12: 82% DL, 82% LB, 73% DB; the rest are stale Sleeper records). An incomplete slot still contributes its rows but zero-fills nobody. Zero-fills alone never create a number for a player another source lists with no projection (§6.4).
- **Rows:** each matched player's target-week projection, ESPN stat ids translated to Sleeper keys by the §13 mapping (108 solo, 107 assisted, 109 total, 99 sack, 95 INT, 106 FF, 96 FR, 113 PD, 97 blocked kick). `covered_keys` are those keys; TFL, QB hits, defensive TDs and safeties are imputed (§6.2) and listed per row as `imputed`. Rows keep the raw ESPN stat ids and a `map_version`. A matched player whose line is missing or has no IDP stat ids is a placeholder (`no_projection_ids`).
- **Team and position:** `proTeamId` through `config/aliases/espn_teams.json` (0 = free agent, no team); `defaultPositionId` 9 DT, 10 DE, 11 LB, 12 CB, 13 S (§13) is used only for matching guards; the slot is always Sleeper's (D14).
- **Absence:** an observed (§5.3) active Sleeper IDP whose Sleeper record has an `espn_id` but who isn't among a complete result's rows or placeholders counts as 0. Everyone else not listed is missing.
- **Matching:** §6.1 with Sleeper's `espn_id` as the source id.

**RotoBaller adapter (PR 5; verified live 2026-10-12, re-checked 2026-10-14):**
- **Discovery:** the search RSS `www.rotoballer.com/?s=idp+rankings&feed=rss2&paged=N` (N = 1..3): the item whose link matches `/week-{week}-idp-rankings-...-{season}/<id>`. Week 5's article (posted Thu 2026-10-08 09:40 ET) was on page 1 with weeks 2–4. No item → `not_yet_published`.
- **Article:** three tables in page order DL, LB, DB (each checked against its heading: defensive linemen / linebackers / defensive backs), 50 rows of `Rank, Player, Team, Opponent`. Teams are nicknames ("Vikings", one "Buffalo") through `config/aliases/rotoballer_teams.json`; a trailing "(Q)" etc. on a name is stripped and kept as a flag; the Opponent column is ignored (the schedule is used). A table that fails its checks fails that list only (list-scoped merge).
- **published_at** = the article's JSON-LD `datePublished`. Rows of players whose kickoff is at or before it are dropped (the article came out after their game: week 2's was posted on Sunday). Past weeks come from the same RSS (paged), so RotoBaller supports backfill.
- **Rows:** {`rank`, `list`, `name`, `team`, `flag`}; depth 50 per list, so §6.4 censoring applies to observed players ranked by P⁰ within 50 + M.
- **Expected by:** Thursday 12:00 ET. Until then "not posted yet" is normal; after it the footer says it is late.

**FantasyData adapter (PR 5; verified live 2026-10-12, re-checked 2026-10-14: 100 of 100 week-5 DL rows matched by `fantasy_data_id`):**
- **Fetch:** `fantasydata.com/nfl/rankings/{dl,lb,db}`, one table of 100 rows each: rank, player link `/nfl/<slug>-fantasy/<id>`, team, opponent, position rank. The week comes from the page title ("Week 5 DL Fantasy Football Rankings"); another week → that list `not_yet_published`.
- **Matching:** the FantasyData id through Sleeper's `fantasy_data_id` (§6.1 step 2), then names.
- **No archive:** live weeks only. **Expected by:** Wednesday 12:00 ET.

**Late sources (added 2026-10-12, PR 5):** each source may set `expected_by` (weekday and ET time). After that moment, a source still `not_yet_published` for the target week is shown as *late* in the footer and a banner; before it, "not posted yet" is shown without a banner. The footer lists each source's usual posting time and whether it was in time for the pre-waiver run.

**Kicker sources (D23, added 2026-11-17).** Six sources, each a sibling module of an IDP adapter with its own request, its own stored file (`sources/<name>.json`) and its own match report. All six were probed live on 2026-11-17 from a home connection, logged out, with a research user agent, read once that evening by the pipeline's own client in a scratch sync, and read once from Actions that night by the branch's preview sync, where all six were `ok` (§10). The page labels them by site (Sleeper, ESPN, CBS, FantasyPros, FantasyData, RotoBaller) under "Kicker sources". A banner about a source names it by its code name, as the IDP banners do (`espn_k: …`), which tells the two groups' Sleeper and ESPN apart.
- **`sleeper_k` (projection):**
  - **Fetch:** `api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular&position[]=K`. A real row has `stats.gp` and an opponent; everything else is a placeholder ("no projection", never 0). Week 11 returned 157 rows: 153 kickers and 4 punters, 26 of them real.
  - **Read:** the line as published, without the `pts_*`, `adp_*` and `pos_adp_*` keys: `fga`, `fgm`, `fgm_yds`, `xpa`, `xpm`, `xpmiss` and the made buckets `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50p` (`fgm_0_19` on a rare row). `fgm_yds` is used as published.
  - **Derived and marked:** `fgmiss` = max(0, `fga` − `fgm`). Sleeper never projects a total `fgmiss`, and the league scores nothing else for a miss (§6.2).
  - **Universe:** complete for K, so the zero-fill rule applies to an observed, active kicker it neither lists nor gives a placeholder (§6.4).
  - **Statuses:** a response with no kicker row at all, or with placeholders only, is `suspect` (§7).
  - **Config:** `archive = true`, `late_rows = true`. No `expected_by`.
  - **Traps:** the miss buckets (`fgmiss_*`) undercount, so they are not summed; the 2026 rows run about 8% low on yards (§11).
- **`espn_k` (projection):**
  - **Fetch:** the same league endpoint and paging as `espn_proj`, as its own request with `filterSlotIds` [17]. Week 11 returned 58 players on one short page, every one with `defaultPositionId` 5.
  - **Read:** stat ids 214 → `fgm_yds`, 85 → `fgmiss`, 86 → `xpm`, 88 → `xpmiss`, 83 → `fgm`, 84 → `fga`, 87 → `xpa`, and the made buckets 80 → `fgm_0_39`, 77 → `fgm_40_49`, 74 → `fgm_50p` (§13). The same week-line selector as `espn_proj`.
  - **Derived:** nothing. ESPN projects every scored key, and the imputer is never called. Rows keep the raw ESPN stat ids (attempts and misses per band and missed yards too) and a `map_version`, as `espn_proj` rows do.
  - **Placeholders:** a week entry with `stats: {}` is no projection (kickers on bye, backups, free agents: 19 of the 44 week entries in week 11).
  - **Statuses:** `not_yet_published` when no returned kicker has an entry for the week; `suspect` when the response holds no kicker, or no matched kicker's line has kicking stats.
  - **Universe:** incomplete for K. ESPN never zero-fills a kicker in v1.
  - **Matching:** Sleeper's `espn_id`, then name + team + slot (§6.1). Its completeness and match report are its own; nothing it does can change `espn_proj`'s.
  - **Config:** `archive = true` (past weeks through `scoringPeriodId`; the week-10 projection was still served on 2026-11-17). No `expected_by`.
  - **Traps:** `proTeamId` 0 is a free agent (22 of the 58). The row has no opponent. ESPN's projected yards carry no distance of their own (§11).
- **`cbs_k` (projection):**
  - **Fetch:** `https://www.cbssports.com/fantasy/football/stats/K/{season}/{week}/projections/nonppr/`, one request, at most one every 3 s. The page's sort links are never followed: robots.txt disallows `sortcol` and `sortdir`.
  - **Read:** the server-rendered `table.TableBase-table`. The player is in `span.CellPlayerName--long`: a name link, a position span and a team span. The number cells, in order: gp, fgm, fga, lng, then made and attempted for 1–19, 20–29, 30–39, 40–49 and 50+, then xpm, xpa, fpts, fppg. Week 11 listed 25 kickers.
  - **Week check:** the page ignores the week in the URL and serves the current week. The week is read from `<title>` ("Week 11 Proj …"); another week is `not_yet_published`.
  - **Derived and marked, all three:** `fgmiss` = `fga` − `fgm`; `xpmiss` = `xpa` − `xpm`; `fgm_yds` = Σ over buckets of (made in the bucket × the mean made distance in that bucket, §6.2). The made counts per bucket are stored; the attempts per bucket and CBS's own fantasy points are not used.
  - **Matching:** name + team (§6.1); team codes go through `config/aliases/fp_teams.json`. A kicker listed with no game (gp 0) is a placeholder, never 0.
  - **Statuses:** `parse_error` for a page without the title, the columns or at least 10 kicker rows; `suspect` when no row matches a kicker with a game.
  - **Universe:** incomplete for K.
  - **Config:** `archive = false`. No `expected_by`; week 11 was up on Tuesday.
  - **Traps:** no archive, so no backfill, and its accuracy history starts with its first live week. CBS publishes to one decimal, so a row's buckets need not add up to its `fgm`; the derived yards follow the buckets.
- **`fp_k` (rank):**
  - **Fetch:** `/nfl/rankings/k.php?week=N`, read by the FantasyPros adapter's own page reader. The embedded `ecrData` has the same shape as the IDP pages, with `position_id` "K". One list, named "K".
  - **Read:** `rank_ecr` per player, the FP id, team, `rank_ave` and `rank_std`, as for IDP. An "FA" row has no team, so it matches only through its FP id. `published_at` = the list's `last_updated_ts`. A list none of whose rows matches a kicker is `suspect`.
  - **Depth:** one kicker per team playing plus up to two free agents: 26 in week 11, 31 in week 9.
  - **Universe:** incomplete, depth = the list's length.
  - **Config:** `archive = true` (`?week=9` served week 9's final pre-kickoff state on 2026-11-17). No `expected_by`: 9 experts had ranked week 11 by Tuesday 8 PM ET, and 42 had ranked week 9 by its Sunday.
  - **Traps:** an archived page shows each kicker's team today, not his team that week, so a backfilled row is matched by its FP id through the crosswalk first (§6.1). A stale free-agent kicker sits at the bottom of both weeks. The list is standard scoring, which orders kickers almost exactly as decimal scoring does (§13), so its ranks are used as they are.
- **`fantasydata_k` (rank):**
  - **Fetch:** `https://fantasydata.com/nfl/rankings/k`. The week comes from the title ("Week 11 K Fantasy Football Rankings"); another week is `not_yet_published`.
  - **Read:** the same table as the IDP lists: RK, NAME, TEAM, OPP, POS RK, PROJECTED. Rows whose PROJECTED is 0.0 are dropped (backups and second kickers: 4 of 29 in week 11), and the rest are re-ranked 1..n in page order. PROJECTED itself is FantasyData's own scoring and is not used.
  - **Matching:** the FantasyData id in the player link, through Sleeper's `fantasy_data_id`, then names.
  - **Statuses:** `parse_error` for a table without a PROJECTED column (a backup couldn't be told from a starter) or with fewer than 10 projected kickers.
  - **Universe:** incomplete, depth = the rows kept.
  - **Config:** `archive = false`, `expected_by = "Wed 12:00"`. Week 11 was showing on Tuesday evening.
- **`rotoballer_k` (rank):**
  - **Discovery:** a separate weekly article (the IDP article has no kickers), found through the search RSS `www.rotoballer.com/?s=kicker+streamers+tiers&feed=rss2&paged=N` (N = 1..3). The item's link must match `/week-(\d+)-kicker-streamers-starters-and-rankings-(\d{4})-[a-z0-9-]*/\d+/?$`. Page 1 held weeks 8–11 on 2026-11-17. No item → `not_yet_published`.
  - **Read:** the article's one table, whose first four header cells, lowercased, are `kicker tier`, `kicker rank`, `kicker name`, `team`. A trailing `*` on a name is stripped. Teams are abbreviations, through `config/aliases/rotoballer_teams.json` (which also knows `wsh`, `jac` and `la`). 26 rows in week 11, 30 in week 9: one kicker per team playing.
  - **Matching:** name + team. A "(Q)"-style tag on a name is stripped and kept as a flag. The article's player links carry FantasyData ids, but not every row has a link, so they aren't used.
  - **published_at** = the article's JSON-LD `datePublished`, with the same rule as the IDP article: rows of kickers whose kickoff is at or before it are dropped. The whole list, those kickers included, still builds the rank curve (§6.4).
  - **Statuses:** `parse_error` for an article without that table or with fewer than 10 rows.
  - **Universe:** incomplete, depth = the table's length.
  - **Config:** `archive = true`, `expected_by = "Thu 12:00"`. It was first posted on Tuesday morning in weeks 10 and 11, and on Thursday in week 8.
  - **Traps:** RotoBaller re-dates the article when it updates it (§11). The link pattern is tight on purpose: a looser one also matches a different Sunday piece ("best kicker pickups") and a "weekend updates" table with no team column.
- **Censoring on short lists:** the three rank lists name nearly every team's kicker, so a kicker a list leaves out is usually a backup or on bye, not "ranked below the cutoff". The §6.4 rule is applied unchanged; this case is untested for kickers (§11).

### 5.3 Multi-source core (added 2026-10-12, PR 3)
- **Observation rule.** A player counts as *observed* by a source in a week only if his kickoff is after max(the source's first successful fetch that week, its `published_at`). The §6.4 absence rules (zero-fill, censoring) apply only to observed players; an unobserved player who isn't listed is missing, never 0. Late rows (§6.5) stay limited to sources with `late_rows = true`.
- **Backfill runner (`backfill.py`).** For each enabled source with `archive = true`, past weeks of the season with no snapshot are fetched once with `ctx.archive`, as of the backfill time, and every row is marked `backfill: true`. Its imputation uses only actuals from earlier weeks. The runner runs after the live fetches inside every sync, within `backfill.budget_s` (300 s) and `backfill.max_requests` per host; what's left waits for the next sync. `sadlidp backfill --source espn_proj --weeks 1-5 --redo` re-fetches named weeks by hand. Backfilled weeks feed the Stats page; they feed accuracy weights (PR 6) only after the source passes its archive-integrity check (ESPN: its live week-6 rows must equal the week-6 archive for ≥ 99% of players).
- **Gap fill (added 2026-11-17, full review).** `sadlidp backfill --source S --weeks N --fill-gaps` repairs a live week that a source was first read in after most games had started.
  - The archive's rows are added only for players the week's file has no row for. They are marked `backfill: true` and stamped with the fetch time.
  - It goes ahead only if the archive matches at least 99% of the week's live rows on what feeds μ: a projection's stats, a ranking's ranks. Labels such as team aren't compared.
  - Live rows are never changed.
  - Weeks a source fully backfilled are left to `--redo`.
  - Used once so far, 2026 week 5:
    - ESPN matched 37 of 37 live rows, and 502 rows were added.
    - FantasyPros' archive matched 23 of 31: three players have since left its archived lists, so deep ranks shifted by one. It was left unfilled.
- **Shrink guard.** Applies to every source (§7).
- **Replays (D22)** read every saved source file of the week (no refetch), so a rebuilt week includes every source that was backfilled.
- **Kickers (added 2026-11-17, D23):**
  - **Backfill:** the runner also fills the past weeks of the four kicker sources with an archive: `sleeper_k`, `espn_k`, `fp_k` and `rotoballer_k`. They run after the IDP sources and the D/ST signal, inside the same time budget and the same per-host request cap (they share their hosts with the IDP sources), so what doesn't fit waits for the next sync. `cbs_k` and `fantasydata_k` have no archive: their history starts with their first live week.
  - **Each source gets its own group** there too: a kicker source is fetched with the kickers, the kicker matcher and no imputer.
  - **Untrusted until checked:** backfilled kicker rows are stored, marked `backfill: true` and shown on the Stats page as backfilled. They do not count toward the kicker weights until the source's archive passes its integrity check (§6.5; registered in `docs/status.md` §7). Whether these four archives return pre-game numbers for kickers has not been checked.
  - **Past results:** settled weeks gain their kicker stat lines through an add-only merge (§6.5 Actuals).
  - **Past pages:** a week's `consensus.json` already written is never rewritten, so its page has no K (§2). No kicker `frozen.json` record is invented for a past week.
  - **Replays:** a replay reads a week's kicker source files only if the week tracked kickers live, that is, if at least one of those files holds a row that is not marked `backfill`. A week with no kicker file, or with backfilled kicker rows only, has no K in its rebuilt page, and its DL/LB/DB output is what it was. So a past week is never replayed into a kicker page, and `--fill-frozen` adds no kicker record to it. `replay.py` enforces this, and a test covers it (§10).
  - **Observation rule:** applies to kicker sources as written above.

## 6. Methods
### 6.1 Positions and matching
- **Position (D14):** the slot Sleeper counts toward `position_limit_*`. Phase 1 verifies it against SADL rosters and transactions. Until then, Sleeper's `position` is mapped as DE/DT/NT→DL, LB/OLB/ILB/MLB→LB, CB/S/FS/SS/DB→DB. `fantasy_positions[0]` is used only if `position` isn't a defensive code. Note that the order of `fantasy_positions` is canonical (DL before LB, DB before LB) and does **not** indicate the primary position. Players with more than one IDP fantasy position get the dual-eligible tag.
- **Matching (amended 2026-10-12, PR 3):** the Sleeper ID is canonical. In order:
  1. `config/player_overrides.toml` (`[<source>] "<source id>" = "<sleeper id>"`, or `"<name>|<TEAM>" = "<sleeper id>"`);
  2. Sleeper's own id fields (`espn_id`, `fantasy_data_id`), accepted only if they land on an IDP;
  3. a crosswalk id, same guard: FantasyPros ids through the DynastyProcess `db_playerids.csv` (`fantasypros_id` → `sleeper_id`), fetched at most once per America/New_York day through the file cache; if it can't be read, matching goes on without it (PR 4);
  4. normalized name (lowercase, punctuation and suffixes Jr/Sr/II–V dropped) + team + a slot the player is eligible for;
  5. normalized name + team;
  6. normalized name alone, only if it is unique among Sleeper IDPs, the source row has a team, and the slot is eligible;
  7. the nickname table (`config/aliases/nicknames.json`, e.g. Cam ↔ Cameron), flagged as such;
  8. otherwise unmatched (or "no NFL team"). Unmatched players are always listed with counts in `run.json` and the footer, never silently dropped. Two source rows matching one Sleeper player keep the higher-precedence match; the other is reported as a conflict.
- **Code tables:** all team and position codes go through per-source alias tables into Sleeper's vocabulary (e.g. nflverse `LA` → `LAR`). An unresolved code or an unmatched player is listed in the footer and never silently dropped.
- **Kickers (added 2026-11-17, D23):**
  - **Position:** a kicker is a player whose Sleeper `position` is K and who has no IDP eligibility. There is no `fantasy_positions` fallback into K: Sleeper lists punters as ["K", "P"], and they must not become kickers.
  - **Nobody changes slot:** everyone who had an IDP slot before kickers were tracked keeps it. Sleeper id 5251 has `position` "K" and `fantasy_positions` ["DL"]; Sleeper projects IDP stats for him every week and he has frozen DL records in weeks 6, 9 and 10, so he stays DL.
  - **One matcher per group:** the IDP matcher indexes the IDPs alone, as it always did, and the kicker matcher the kickers alone. A kicker's name or id can't change how an IDP source's row matches, or the other way round.
  - **The steps are the same,** read for kickers: an id (steps 2 and 3) is accepted only if it lands on a kicker, and a name alone (step 6) only if it is unique among Sleeper's kickers. The slot is always "K".
  - **Ids:** ESPN's kicker rows match through `espn_id`, FantasyData's through `fantasy_data_id`, FantasyPros' through the DynastyProcess crosswalk, which is loaded when either FantasyPros source is enabled. CBS and RotoBaller rows match by name and team. On 2026-11-17 the crosswalk held 23 of week 11's 26 projected kickers, 21 with a FantasyPros id and 23 with an ESPN id. Names + team cover the rest (the probe matched ESPN to Sleeper 25 of 25 and 29 of 29 by name alone). In the live sync of 2026-11-17 (§10) every row with a team matched: ESPN 27 by `espn_id`, 20 by name + team and 1 by name alone (10 more had no team); FantasyData 25 of 25 by `fantasy_data_id`; FantasyPros 21 by the crosswalk and 5 by name + team; CBS 25 and RotoBaller 26 by name + team.
  - **Teams:** a kicker team code missing from the schedule is logged, not fatal; that kicker simply has no game. For IDPs it is still a hard error (§3.2).

### 6.2 Partial stat lines and imputation
- A key in `covered_keys` that is absent counts as **0**. Only keys outside `covered_keys` are imputed. Imputed keys are flagged per source in the snapshot.
- `imputed_k = anchor × r̂_k` (anchors amended 2026-10-12, PR 3): TFL on the source's projected tackles (solo + assisted); QB hits on its projected sacks for players with ≥ 2 sacks over last season and this one so far, otherwise on games (= 1); defensive TDs, safeties and blocked kicks on games. Exposure is the player's total of the same anchor in the same games. Rates use actuals from weeks before the target week only. `r̂_k = (x_k + m_k·r_prior,k) / (exposure + m_k)`, where x_k is the player's season total of k and exposure is his season total of the same anchor stat (tackles, or games). This is the player's season rate per anchor unit from Sleeper actuals, shrunk toward a prior. The prior is hierarchical: his granular Sleeper position's rate last season (DE/DT/LB/CB/S), then his own last-season rate shrunk toward it. `m_k` is set by empirical Bayes (method of moments) from last season's player rates, saved with the position rates in `data/<prev>/impute_priors.json`; `impute.m_k.*` in config overrides it. Validation: imputing Sleeper's own TFL and QB hits from its projected tackles and sacks must be close to the values Sleeper projects (unit test on the recorded fixtures).
- A total-tackles-only source is split into solo and assisted using the player's shrunk solo share.
- **Props:**
  - *Conversion:* de-vig each two-way market (multiplicative), take the median p_over across books, and solve for the Poisson λ with P(X > line) = p_over. Assisted = λ(tackles_assists) − λ(solo), floored at 0. If only the combined market exists, split by solo share.
  - *Fetch rules:* weekend refresh only, events not yet kicked off and within ~36 h, at most once per event per ET day.
  - *Budget:* capped by `odds.monthly_credit_budget` (default 450) using the `x-requests-remaining` header. When the budget runs out, the status is `budget`.
  - *Missing props:* a week with no props is "not available", not a failure.
- **Kickers (added 2026-11-17, D23): derivations, never the imputer.** A kicker line is stored as the source published it. A key the league scores but the source leaves out is derived inside the adapter from the same row, by arithmetic, and listed in the row's `imputed` list, so the page marks it `*`. No rate, prior or shrinkage is involved, and `impute.py` is never called for a kicker.
  - **Missed field goals:** `fgmiss` = `fga` − `fgm` (Sleeper, floored at 0; CBS). Sleeper publishes misses only by distance band, and those bands undercount (§13).
  - **Missed extra points:** `xpmiss` = `xpa` − `xpm` (CBS). Sleeper and ESPN publish it.
  - **Made yards from bucket counts (CBS):** `fgm_yds` = Σ over buckets of made-in-bucket × the mean distance of a made field goal in that bucket, `[kicker.yards_per_make]`:

    | Bucket | Yards per make (config) | Measured mean, 2024–26 | Made kicks (n) |
    |---|---|---|---|
    | 0–19 | 19.0 | 19.00 | 7 |
    | 20–29 | 25.4 | 25.40 ± 0.11 | 540 |
    | 30–39 | 34.4 | 34.42 ± 0.11 | 712 |
    | 40–49 | 44.4 | 44.38 ± 0.11 | 648 |
    | 50+ | 53.9 | 53.89 | 491 |

    The means are every made regular-season field goal of 2024, 2025 and 2026 weeks 1–10 in nflverse's weekly kicker stats (2,398 kicks; ± is one SE; §13). Within a bucket the distance of a make has an SD of about 2.6–2.9 yards. They are league-wide unit conversions, the same for every kicker, and not a fitted model term.
  - **How good the conversion is:** on 2022–25 games, yards rebuilt from bucket counts miss the real yards by an SD of 0.36 points a game. Rebuilding from the make count alone (makes × 38.3) misses by 1.46. (Measured with the 2022–25 bucket means, which are within 0.3 yards of the table's.)
  - **ESPN derives nothing.** Its `fgm_yds` (stat 214) is published, and `[kicker.yards_per_make]` has no entry for its 0–39 bucket.
  - **Sleeper's `fgm_yds` is used as published,** although its 2026 rows run low (§11). Rebuilding it from `fgm` and the table was considered and not done.

### 6.3 Scoring engine
- `points = Σ_k scoring_settings[k] × stat[k]` over **every** key in `scoring_settings` (a missing stat counts as 0, no position filter). This covers idp_*, special-teams and return keys, and receiving for two-way players. Relevant keys R are those with a non-zero coefficient that any DL/LB/DB recorded in Sleeper actuals this season or last. A key in R with no adapter mapping and no imputation rule is reported in `run.json`.
- **Actuals:** Sleeper stat lines are used as delivered, including any precomputed bonus keys. A bonus key that isn't delivered is derived from the counts. The ground-truth test confirms which applies.
- **Projected threshold bonuses:** `bonus_tkl_10p` on idp_tkl (solo + assisted), `bonus_sack_2p` on idp_sack, and `idp_pass_def_3p` on idp_pass_def, all inclusive (≥). The underlying stat and the threshold for each are confirmed in Phase 1. Each is scored per source as `setting × P(X ≥ t)` with X ~ Poisson(the source's projected count); negative-binomial dispersion is optional in config. A bonus value supplied by the source is used as-is and never added twice. Any other threshold key is reported as unsupported.
- **Kickers (added 2026-11-17, D23):** the engine is unchanged. The sum over every scored key already covers kicking, and for a kicker only four keys are non-zero: points = 0.1 × `fgm_yds` − `fgmiss` + `xpm` − `xpmiss`. The league's `fgm_<bucket>` keys are all 0, so a bucket count stored beside the line adds nothing and no make is counted twice. No kicking key has a threshold bonus.
  - **Actual lines:** Sleeper omits a key whose value is 0, which the engine already reads as 0 (a kicker who went 4 for 4 with no extra point has no `xpa` or `xpm` key). A blocked kick counts as a miss in `fgmiss`.
  - **Acceptance check on real lines:** a unit test scores recorded week-10 stat lines and checks each against the four-key sum by hand, e.g. 170 made yards and 4 extra points = 21.0. The stricter check against Sleeper's own `players_points` has not been run for a kicker yet (§10).

### 6.4 Consensus
- **Rank → value (within a list):** for each list L a rank source publishes, in whatever position taxonomy it uses (e.g. separate CB and S lists), let V be the projection-group values P_i of the players in L that have one. V is sorted in descending order and **indexed from 1** (V_1 is the largest).
  - Rank r maps to V_r. Fractional ranks from averaged ties are linearly interpolated, so rank 1 always maps to V_1.
  - Let Q be the P values of the full projection pool at the list's own granular position (e.g. all S for an S list; rostered players included), sorted descending and indexed from 1. For r > |V|, whether from a list deeper than projection coverage or a censored rank, the value is min(V_|V|, Q_min(⌈r⌉,|Q|)).
  - If |V| < 10, Q replaces V and rank r maps to Q_r (rank to rank, no rescaling).
  - The curve is built from P, never from μ. The value attaches to the player's D14 position.
  - *A player on two of a source's lists (added 2026-10-12, PR 4):* the list of his D14 position is used; if he isn't on it, the list where he ranks best.
  - *Q's granular position (PR 4):* FantasyPros lists are DL, LB and DB, so Q is the projection pool at that D14 position.
- **Fallback when no projection source exists that week:** for each position, rank players ex ante (by Sleeper's archived weekly projections if available, otherwise by trailing points per game). Then map rank r to last season's mean actual SADL points at that ex-ante rank, smoothed with isotonic regression plus a moving average. Accuracy isn't updated for fallback weeks.
- **Censoring rank:** (PR 4: every projection source so far is complete, so no projection value is censored and P⁰ = P, zero-fills included.) Before any censoring, compute P⁰_i, the within-group weighted mean of the projection sources' *listed* (uncensored) values. Players are ranked by P⁰ within each source list's own position taxonomy (e.g. among S for an S list; among the D14 position for a DL/LB/DB list). In a fallback week the ex-ante rank is used instead. Both censoring rules below use this P⁰-rank and are applied once. Censored values are never used to re-rank.
- **Absence rules (per the adapter's universe):**
  - A *complete* projection source: an active player with neither a row nor an entry in `no_projection_ids` counts as 0. A player in `no_projection_ids` is *missing* for that source, never 0.
  - A *depth-D* projection source: an unlisted player whose P⁰-rank is ≤ D+M (default M = 10) gets the source's lowest listed value in that list.
  - A *depth-D* rank source: an unlisted player whose P⁰-rank is ≤ D+M gets rank max(D+1, his P⁰-rank). Only players the source observed (§5.3) and who aren't on another of its lists are censored; the page marks the value "not on its list".
  - A player with no P⁰, or anything else not covered above, is *missing* for that source.
- **Mean:** P_i and R_i are the weighted means of the covering sources within each group (weights renormalized over those sources). μ_i = α·P_i + (1−α)·R_i when both exist, otherwise whichever exists. If a group has no sources at all that week, the page says so.
- **Form term (added 2026-10-12; docs/methods-review.md step 2):** the μ shown is base_i + form_i, where base_i is the mean above and form_i = β_miss,p · n/(n+k) · mean(actual − base over his n earlier games this season) + β_snap,p · (his share of his team's defensive snaps in his last game − the mean share among the position's 60 best by base this week). Misses are read against each earlier week's frozen `base` (§6.5), so they are the sources' misses, never the term's own; a player with no earlier game this season gets no term; μ never drops below 0; σ_μ is unchanged (it measures source disagreement). Coefficients in `[form]`, fitted on Sleeper's 2025 archive with the projection held at 1 (`sadlidp backtest run` prints them): k = 4, β_miss DL 0.45, LB 0.40, DB 0.20, β_snap per 100% of snaps DL 7.5, LB 5.5, DB 4.0. `run.json` records the coefficients in force, the position snap averages and how many players each part touched. A replay carries the earlier weeks' results and records into the rebuilt week, so it has the term it would have had, and `sadlidp tune` reports the μ order with the term off and on. **LB and DB miss term off (2026-11-17, full review; owner decision):** `[form.miss]` LB and DB are 0.
  - **The mismatch:** the coefficients were fitted on Sleeper's own misses (the 2025 archive is Sleeper-only), but the term is applied to the blend's misses. A miss has to be measured against the base the term corrects.
  - **The evidence:** on 2026 weeks 2–9 (top 60 by base), the persistence of a player's earlier shrunk miss into this week's was:
    - against Sleeper: LB .42 ± .22, DB .30 ± .18;
    - against the blend: .05 ± .20 and −.08 ± .18.
  - **The paired gap is .40 ± .06** (LB and DB pooled, stable for the top 40, 60 and 80). The other sources already price in what the term added, so it counted it twice.
  - **On the page:** a replay of weeks 6–10 with the term off moved about 1 LB and 2–3 DBs a week in or out of the shown 25, all near the bottom of the list. It shifted 53 tier lines, swapped 7 top-5 places and changed 3 DB badges, all against Kyler Gordon.
  - **No accuracy claim is made.** The DL miss term and every snap term are unchanged, and the code path stays, so it's a one-line revert.
  - **Coming back:** a position's term returns only if its persistence against the blend on weeks 10–13 is at least 2 SE above 0 (`docs/status.md` §6, week-13 agenda).
  - **For 2027:** this season's `frozen.json` records are the multi-source archive any refit has to use.
- **No projection:** a player with no value from any source has no μ. He isn't tiered and is listed under "no projection" below the tiers. A rostered player with no μ shows as "no projection" and is not eligible as S or B (§6.7).
- **Projection uncertainty** (used for tiers): s_i² = Σ_s w̃_s (x_si − μ_i)², where w̃ are the effective weights normalized to sum to 1 over covering sources. This is shrunk with s̃_i² = (ν0·s0_p² + (n_i−1)·s_i²)/(ν0 + n_i − 1). s0_p² is the median s_i² over players at position p with ≥3 sources this week, or a config default when no such players exist, e.g. in the single-source MVP. **σ_μ,i = max(s̃_i, σ_floor,p).** It is **not** divided by n_eff, because sources are correlated (aggregates, shared inputs, shared imputation). It **never** includes game-outcome variance.
- **Displayed per player:** μ, σ_μ, the min–max range over non-private sources, and the number of sources (counting censored values separately), plus (D19, amended 2026-10-12) a breakdown: per enabled source, his SADL points from it, the projected stats behind them (projection sources) or the rank and its mapped points (rank sources), its share of μ (its effective weight, `eff`), and, when it has nothing for him, why (nothing posted yet, too old, no projection for him). A zero-filled player (§6.4 absence rules) is marked as such. The page ends each breakdown with "weighted average = μ", or, when the form term applies, with the term's parts (the misses' mean, count and shrunk value; the snap share and the position's average) and "weighted average = base; with form ±x = μ".
- **Kickers (added 2026-11-17, D23): the same consensus, run on its own group.** The steps above run a second time over the kickers with the kicker sources only. The two runs share no input: a kicker source's value never enters an IDP player's P, R, rank curve, P⁰, s0 or weight, and the other way round.
  - **Rank → value:** each kicker list is mapped onto the kicker projection values (the equal-weight mean of `sleeper_k`, `espn_k` and `cbs_k` for the players on the list). Q is the kicker projection pool.
  - **Absence:** `sleeper_k` is complete, so an observed, active kicker it neither lists nor gives a placeholder counts as 0 from it, with the same guard as before (a zero-fill alone never creates a number for a player another source lists with no projection). `espn_k` and `cbs_k` are incomplete: a kicker they don't list is missing from them, never 0. The three rank lists are censored at depth + M like any list (§5.2 on short lists).
  - **Mean:** μ = α_K·P + (1 − α_K)·R with the kicker group's own weights and split (§6.5). When no kicker source has usable rows yet, the page says so and the kicker tiers are empty; the IDP page is unaffected.
  - **Form: off.** A kicker has no miss term and no snap term, no snap share is read for him, and his `base` equals his μ. The breakdown never shows a form line or the "defensive snaps" sentence for a kicker. No coefficient exists; adding one would need a kicker archive from several sources and a registered test (`docs/eval-protocol.md`).
  - **σ_μ:** the same formula, with s0 taken from the kickers that have 3 or more sources and the floor `[tiers.sigma_floor] K` = 0.75.
  - **Breakdown:** a kicker's parts are the kicker sources only. A projection part shows the scored line (FG yards, XP, missed FG, missed XP), with a derived value marked `*`.
  - **Priors, untested for kickers:** the σ floor (0.75) and `s0_default` (1.0) were set at IDP scale; M = 10 and the 60:40 split are the IDP priors. None was fitted or checked on kicker data.

### 6.5 Snapshots, freezing and accuracy
- **Storage:** `sources/<src>.json` holds normalized rows (sleeper_id, stats or rank, SADL value, fetched_at).
  - *File format (amended 2026-10-12, PR 2):* `schema_version` 2; per-player maps are written one player per line, so a sync's diff shows only the players that changed. A row whose content is unchanged by a fetch keeps its `fetched_at` (when that content was first seen); the file's `fetches` list records every successful fetch, and "the value as of a time" is the row plus the latest fetch at or before it.
  - **On a successful fetch,** every player whose kickoff is after the new fetch's `fetched_at` takes the new fetch's state: his row is updated if he is listed and **deleted** if he isn't, so the §6.4 absence rules apply.
  - **Rows of players whose game kicked off** before the new `fetched_at` are never modified or deleted. The file is therefore the frozen snapshot.
  - **On a failed fetch** the file is left untouched. Staleness is recorded per source in `run.json` (time of the last successful fetch), not per row. A stale source stays in the live consensus up to `max_source_age_h`.
  - For accuracy, a value counts as missing when the source's last successful fetch (the file's `fetches`) before the player's kickoff was more than 4 days before it.
  - The kickoff rule applies to syncs. `sadlidp backfill` writes past weeks from archived pre-game projections and marks those rows `backfill: true`.
- **Late rows (added 2026-10-12, D22):** for a source configured with `late_rows = true` (Sleeper projections), a player first seen after his kickoff is kept once with `late: true` instead of being skipped, then frozen like any other row. Sleeper's 47 pre-kickoff MNF rows of week 5 were unchanged after its batch restamp; whether its rows ever change after kickoff is re-checked post-game, and until that check passes, late rows are display-only and never used for accuracy weights. The page marks a late projection with "*".
- **Frozen week record (added 2026-10-12):** `frozen.json` holds, per player with a μ, his μ, σ_μ, range, source count and status as of his last sync before kickoff, plus his `base` (μ before the form term) and the term's parts when it applied (§6.4; a replayed week writes the rebuilt record when the week has none), under the same rule as the source files (updated while unlocked, never after kickoff). Every frozen row stores the kickoff it was written for, so a player cut or traded after playing stays frozen. For a locked player, the page, the My IDPs comparison and later evaluation of "the consensus as shown" read this record, so his numbers can't drift after he locks. **Gap repair (added 2026-10-14):** a live week whose first saved sync came after most games (2026 week 5: first sync Monday, 50 records) is filled by `sadlidp replay --weeks N --fill-frozen`: the week is replayed as of its first kickoff and the rebuilt records are added, marked `rebuilt: true`, for the players its record lacks. Live records and the week's page are never replaced. This is safe because Sleeper's archive does not change after kickoff (§13); sources that joined mid-week contribute only the players they observed, so most rebuilt week-5 records are Sleeper alone. **Refresh (added 2026-11-17):** `--fill-frozen --refresh-rebuilt` rebuilds the records rebuilt before as well, from the sources saved now. Live records are never replaced. After ESPN's week-5 gap fill (§5.3), week 5's 581 rebuilt records were rebuilt with ESPN in them, and 37 ESPN-only records were added. Their μ rose by 0.7 on average, mostly at LB, where Sleeper runs low. Weeks already shown keep their pages and `weights.json`; the rebuilt records feed later weeks' form term and the Stats page.
- **Decision logs (added 2026-10-12):** `badges.json` records every START/UPGRADE suggestion per (week, player, badge, compared player) with its odds, μs, σ_out, action and reference flags; until its lock time (the earlier kickoff of the two players compared) it takes the latest state and is marked withdrawn if it disappears, and after that it is frozen. A suggestion that disappears because its player was claimed (by you or anyone; `claimed_by`) is not withdrawn: it is frozen at the claim as it stood, so the suggestions you followed are judged too. A badge counts as issued if it was active at lock (or at its claim). `tiers_log.json` records the waiver tiers shown at the pre-waiver sync and, per player, at his last sync before kickoff.
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
- **Implementation (PR 6, added 2026-10-14):**
  - **Errors are demeaned:** r_s and α̂ use errors demeaned within each position-week (§F of the plan). A level offset shared by every player, like Sleeper's LBs running 3 points low, changes no decision, so it doesn't earn or cost weight.
  - **The evaluation pool** is the Stats page's: top 50 per position by the equal-weight mean of the projection sources, among players who played. c is the prior-weight blend of every source covering the player.
  - **A position-week counts** for a source when it covers ≥ 15 pool players; for α, when ≥ 15 pool players have both groups.
  - **Data weights:** w_data,s = (1/mean r_s) divided by the mean of 1/mean r over the evaluated sources of its group, so equal skill gives 1 and only relative skill moves a share.
  - **Which weeks are used:** for week N, settled weeks ≤ N − 2 only (stat corrections closed, §6.5 Actuals). A source's backfilled and late rows count only if its archive passed its integrity check (`[accuracy] untrusted` lists any that hasn't; all pass so far, §13).
  - **Recorded write-once:** `weights.json` in the week's directory holds the weights in force: method (`accuracy`, or `prior` before activation or for a week whose first sync came before PR 6), α, α̂ and its n, and per source w, w_data, n_s and mean r_s. It is written at the week's first sync and never recomputed, so a week's numbers don't move as later results come in, and the Stats page shows the history exactly as applied.
  - **Prior weights (added 2026-10-14, PR 5):** `[weights.prior]` gives each source a starting weight within its group: FantasyPros 2, RotoBaller 1, FantasyData 1, so 50/25/25 when all three rank a player. FantasyPros is a 26-expert consensus; the others are one analyst each. Projection sources default to 1. These are the shares before activation, the blend c that r_s is measured against, and the target the data weights shrink toward: w_s = n_s/(n_s+k)·w_data,s·p_s + k/(n_s+k)·p_s, with p_s the prior.
  - **What the weights move:** the consensus μ (the within-group weights and α). Rank sources are still mapped onto the equal-weight projection mean (§6.4), and σ_μ is unchanged apart from the shares.
  - **The projection pair's least-squares share (2026-11-17, owner decision "test on 2025, then adopt"; `[weights] projection_rule = "ls"`):** the error-ratio rule couldn't tell a source from noise. A shuffled, pure-noise copy of Sleeper kept 46% of its group in 2026, and 49% on the 2025 archive.
    - **The rule:** with two projection sources (Sleeper, ESPN), their shares now follow the least-squares mix, θ = Σ(y−b)(a−b)/Σ(a−b)², demeaned within each position-week over the pool players both cover. It uses the same weeks, lag and pool as the other weights. θ is clamped to .2–.8 and shrunk n/(n+k) toward even (n = weeks measured). Each source's `w` carries its share, its prior being 1.
    - **Recorded in `weights.json`:** `projection_rule` (rule, raw θ, its leave-one-week-out SE, the share, n), from the next week written. Earlier weeks keep their records, and the audit re-checks each record under the rule it was written with.
    - **Pre-registered check on 2025,** all passed:
      - **(a) Placebo:** a shuffled Sleeper gets 0.29 under the LS rule, against 0.49 under the ratio rule (bar: ≤ .35, against > .40).
      - **(b) Noisy copies:** Sleeper plus noise of SD 1, 2 and 4 pts gets 0.37, 0.30 and 0.29, falling with the noise; the ratio rule gives 0.50, 0.49 and 0.45.
      - **(c) Real independent view:** a season-to-date average has a 9-week raw share of 0.22 ± 0.09 against 0.28 over 17 weeks. Walk-forward ΔMSE of the LS blend against the ratio blend is −0.15 ± 0.29, so not worse.
    - **On 2026 weeks 1–9:** Sleeper's raw share is 0.27 ± 0.09, so from week 12 the pair splits about ESPN 66 : Sleeper 34.
    - **Kill switch, at the week-13 checkpoint:** revert to `"ratio"` if the cumulative walk-forward difference against the ratio rule on weeks 11+ turns against it at t ≥ +1. Three or four weeks can catch a reversal, not confirm a gain.
- **Kickers (added 2026-11-17, D23):**
  - **Storage and freezing:** the kicker source files, `frozen.json` records, `badges.json` and `tiers_log.json` rows follow the rules above; their rows carry slot "K". Kicker frozen records start with the first live week that has a kicker source. Late rows apply to `sleeper_k` only.
  - **Actuals, new weeks:** `actuals.json` holds every tracked player's line, kickers included.
  - **Actuals, past weeks (add-only merge):** a week captured after its stat-correction window is final and never refetched, so the weeks saved before kickers hold no kicker lines. Each such week is refetched from `/v1/stats` once:
    - lines are added only for kickers that have no line yet, chosen by Sleeper `position`, never by stat key;
    - no existing line is changed, and no IDP line is added (a missing IDP line added to a settled week could move an IDP statistic);
    - the week is marked `k_added_at`, with `k_added` = how many lines were added, even when that is 0;
    - a week that already holds a kicker line was captured with kickers and is left alone (no refetch, no marker);
    - newest weeks first, at most 4 a sync; a failed fetch is retried at the next sync.
  - **Core points:** none of the keys left out of core points is a kicking key, so a kicker's core points are his full points.
  - **Evaluation pool:** the top 20 kickers by the equal-weight mean of the kicker projection sources, among those who played. A source counts in a week when it covers 10 or more of them. The IDP sizes (50 and 15) would leave every kicker week partial: about 28 kickers play a week.
  - **Weights, measured like the IDP ones but apart:** the same error ratios, the same α̂, the same lag (weeks ≤ N − 2), shrinkage (k = 4) and activation (3 evaluated weeks), over the kicker sources and the K pool only. Neither group reads the other's source files, players or results.
    - **Priors:** projection : rank 0.6. The three projection sources are equal. The rank sources start FantasyPros 2 : RotoBaller 1 : FantasyData 1 (`[weights.prior]` `fp_k`, `rotoballer_k`, `fantasydata_k`).
    - **Projection shares:** the error-ratio rule. The least-squares pair rule applies only in a group that declares exactly two projection sources; kickers have three.
    - **Recorded write-once** under one new key of the week's `weights.json`, `"kickers"`, with the same shape as the IDP record and its own `computed_at`. (The IDP record's own `"k"` is the shrinkage in weeks, so that name was taken.) No existing key of the file changes. The record is stored once the week's IDP record exists; until then it is measured again each sync.
    - **Untrusted archives:** `[accuracy] untrusted` lists `sleeper_k`, `espn_k`, `fp_k` and `rotoballer_k`. Their backfilled and late rows don't count; only rows read live before kickoff do. So the kicker weights are the priors until three live kicker weeks have settled, unless an archive passes its check first (`docs/status.md` §7).
    - **Audit:** `sadlidp audit` reproduces the kicker record on its own, as the check `weights k`.
  - **What to expect:** kicker projections explain little of a kicker's week (§11), so the measured kicker weights will probably stay close to the priors. Whether they differ at all is a registered question (`docs/status.md` §7).

### 6.6 Anchored significance tiers (per position)
Tiers are computed over the available pool after §2 filters, locked players removed, sorted by (−μ, sleeper_id). The first player anchors Tier 1. Each next player i joins the current tier while `μ_anchor − μ_i ≤ z·√(σ_anchor² + σ_i²)`, with σ = σ_μ. Otherwise he starts a new tier as its anchor. Default z = 0.75 (config; 1.0 until it was tuned on weeks 1–4 with `sadlidp tune`, 2026-10-12, see `docs/tier-tuning.md`). Comparing every player to the tier's top player prevents chaining, and the tiers are contiguous and deterministic. Display truncation doesn't change upper tiers. **Rostered players never anchor.** Let a1 be the Tier 1 anchor. A rostered player m is shown as "above Tier 1" if `μ_m − μ_a1 > z·√(σ_a1² + σ_m²)`. Otherwise he is shown in the first tier, top-down, whose anchor a satisfies `μ_a − μ_m ≤ z·√(σ_a² + σ_m²)`. If no tier satisfies it, he is shown as "below Tier N", where N is the last tier. In the single-source MVP every σ equals the default, so tiers are equal-width point bands until more sources arrive.

- **Changed since Tuesday (added 2026-11-17, owner decision; `sync.since_tuesday`):** on every page after the week's pre-waiver page, each position's tab opens with what changed for the pre-waiver page's top 5:
  - now Out or Doubtful (or another hiding status);
  - newly questionable;
  - claimed, by you or another team;
  - a μ move of 1.5 pts or more (fixed once, about σ_μ's scale; never tuned);
  - players new to the top 5, with their Tuesday place.

  It states facts only: nothing is re-ranked or discounted.
- **Tuesday's tags:** the pre-waiver tiers log records each player's injury tag as shown (`flags`, with `tags: true`). Weeks 6–10 were filled from the players file saved at each week's pre-waiver commit (2026-11-17).
  - From the logs, the main page says how often a waiver player tagged questionable on a pre-waiver page sat out: 62 of 112 through week 10, against 5.7% of untagged ones. It is shown once 20 or more were tagged.
  - No μ discount follows from it: a P(play) factor is research on 2025's injury reports (`docs/status.md` §6).
- **Kickers (added 2026-11-17, D23):** the same algorithm over the available kickers, with the shared z (0.75) and the K floor (0.75). Both are IDP values used as priors; neither was tuned on kickers.
  - **Expect rough tiers.** A week's kicker projections sit close together (an SD of 1.0 to 1.5 points across kickers), while one kicker's week swings by about 5 (§11). The tiers show where the sources agree, not who will score more, and the page says so in the kicker note (§6.8).
  - **Changed since Tuesday** is shown for K like any position. The Tuesday-questionable count stays an IDP figure.
  - **The combined list (D25, 2026-11-21):** with another league on the page, the kicker pool is every kicker free in SADL or in that league, and the tiers are counted over it, so a SADL kicker can sit a tier lower when a better one is free only in the other league. The tiers log records each kicker's `free_in`. A claim since Tuesday names its league; when Tuesday's list and today's are of different kinds (one combined, one SADL's alone), the K panel's "changed since Tuesday" is left out for that week.

### 6.7 My IDPs panel (D15)
- **Outcome probabilities use predictive σ:** σ_pred,i² = σ_μ,i² + σ_out,p². σ_out,p is the RMS of (actual − frozen μ) for evaluated pool players at position p once 3 evaluated weeks exist. Before that, it is last season's within-player SD of weekly SADL points (players with ≥8 games) under the current scoring.
  - *Per-player swing (added 2026-10-12; docs/methods-review.md step 3):* with `swing.enabled`, σ_out,p is replaced for every player a projection source has stats for by his own swing_i² = a_p + b_p · V_i, where V_i = Σ_k coef_k² · λ_k,i is the variance of his points if each projected stat were an independent Poisson count (SADL pays 6 for a sack and 2 for a solo tackle, so sack-heavy players swing more) and λ_i is the projection sources' projected line for him, averaged with his effective shares (the breakdown's parts). a_p and b_p are fitted per position on Sleeper's 2025 archive (`sadlidp backtest run` prints them; `[swing]`): DL 0 + 2.5·V, LB 18 + 1.35·V, DB 27 + 0.65·V, which reproduces the realized SD by fifth of predicted swing at every position. **Re-fitted 2026-11-17 (week-9 checkpoint):** on 2025 + 2026 weeks 1–9 pooled, the DL fit is 4.3 + 2.23·V. It beats 0 + 2.5·V on held-out weeks in both fold directions (Gaussian log-likelihood better by 0.035 ± 0.024 per player-game), mainly by no longer giving low-volume DL players a near-zero swing. LB and DB re-fits are within noise of the current values, so they stay. lo and hi stay at −0.8 and 0.75 (the DL quantiles under the new fit are ±0.74). A player with no projected stats (rank-only, zero-filled) keeps σ_out,p. Each card shows a floor and ceiling, μ + lo·swing (never below 0) and μ + hi·swing, with lo = −0.8 and hi = 0.75 the 20th and 80th percentiles of (actual − μ)/swing on 2025: one game in five lands below the floor, one in five above the ceiling. `frozen.json` keeps swing, floor and ceiling; the badge log records both players' swings; the Stats page scores σ_pred with the swing the week's page used. Tiers are unchanged (σ_μ only).
  - *Residual σ_out (added 2026-10-12, PR 2):* with `sigma_out.method = "residuals"`, σ_out,p² = mean(e²) − σ_μ,p², where e = actual − predicted over each settled week's evaluation pool, demeaned within the position-week (a level offset changes no decision; it is shown on the Stats page instead). Week N uses settled weeks ≤ N−2 only, and a position with fewer than 3 such weeks keeps last season's value. The method stays `last_season` until Sleeper's archived projections pass the integrity check (§13), because weeks 1–4 are backfilled.
- **Comparable** (amended 2026-10-12; formerly "eligible") = your rostered players at the position (D14) who are not in `reserve`, have a μ, and pass every §2 waiver-pool filter **except** the locked rule: the same injury codes, `status`, `active`, `team` and bye rules. A locked player is judged on his μ and status from the frozen week record (§6.5) at his kickoff, so a later injury tag can't empty the slot.
- **Limit count** = your rostered players at the position, excluding `reserve` (IR), which Phase 1 showed Sleeper doesn't count (§13); `taxi` players are counted.
- **S (projected starter)** = the comparable player with the highest μ. If there is none, μ_S = 0 and σ_pred,S = 0.
- **B (other)** = the remaining comparable player, if any.
- **START over S badge** on waiver player W when `Φ((μ_W − μ_S)/√(σ_pred,W² + σ_pred,S²)) ≥ swap_threshold` (default 0.6). Suggested move:
  - "add W, no drop" if you're under the position limit;
  - otherwise "add W, drop B" if B exists;
  - otherwise the panel says that your other player is unavailable this week (bye/Out/IR) and leaves the drop choice to you.
- **UPGRADE over B badge** (only when B exists and W lacks START) uses the same formula against B.
- **Locked players (amended 2026-10-12, D15):** suggestions are never suppressed. A suggestion whose move can't be made this week is flagged `reference` with its reasons, sorted after the actionable ones, muted, and explained in words:
  - `starter_locked` (START and UPGRADE): your Sleeper starting slot at the position (matchups `starters`) holds a locked player, or, when no lineup is known at all, S is locked. An empty slot in a known lineup is not locked. SADL starts exactly one DL/LB/DB, so nothing added at that position can score this week.
  - `drop_locked`: the player the move would drop is locked (for "you choose the drop": every other non-IR player at the position is locked); Sleeper won't drop him.
  - A locked rostered player with no frozen record but a pre-kickoff projection (the first week only) is judged as playable at kickoff; later injury tags are ignored.
  - Waiver players whose game has started stay out of the pool.
- **Context shown:** each rostered player's μ, σ, tier placement and status, plus the league's waiver type. There is no FAAB advice in v1, only FAAB context (below).
- **Bye look-ahead (added 2026-11-17):** a rostered player (IR left out) whose team has a bye in the next 3 weeks is named under his position, from the schedule.
- **One move, many badges (added 2026-11-17):** badges of the same kind against the same player of yours are one move. The likeliest stands for it, and the others are named under it ("same move, slightly lower odds: …"), up to four and a count. The badge log still records each one.
- **FAAB context (added 2026-11-17, owner decision; amends "no FAAB advice"; `league_tx.py`):**
  - Every sync saves the previous and current leg's league transactions from Sleeper's public endpoint (`week<NN>/transactions.json`, compact), and fills up to 3 earlier legs that have none.
  - The tiers section shows one line of counts, once 5 or more IDP claims are decided:
    - how many IDP waiver claims were won with a token bid;
    - how many were contested (two or more teams claimed the same player in a leg) and what those went for;
    - how many IDPs were picked up as free agents, and on which day most often.
  - **Normalized to the starting budget (2026-11-17, owner request):** leagues start with different FAAB budgets, so every bid is shown as a share of the league's starting budget (Sleeper's `waiver_budget`), with the budget in small print.
    - A token bid is at most 1% of the budget, or $1 if that is more: $1 in a $100 league, $10 in a $1,000 league, $1 (2%) in a $50 league.
    - A league without FAAB (`waiver_type` other than 2) gets no line.
  - It never gives a bid for a particular player, never tags a player "likely contested" (2 of 15 precision; logged for a test at week 17), and never says "safe to wait".
- **Kickers (added 2026-11-17, D23):** the same panel for K (S, B, comparable, START, UPGRADE, the reference flags), with its own noise and its own add rule.
  - **σ_out,K = 5.0** (`[sigma_out] K`). It is a set level, not a fit: the SD of weekly decimal points within a kicker-season was 4.78 on 2022–25 (2,148 kicker-games), and the SD over 173 kicker-games of 2026 weeks 5–10 was 5.20. Last season's cache (`data/2025/sigma_out.json`) has no K and is not rebuilt for it; a slot the cache lacks takes its config value.
  - **Swing:** a kicker's points are not a sum of counts (a made field goal scores a tenth of its distance), so the Poisson V above doesn't apply. swing_K² = a_K + b_K · `kicker_var`(his projected kicking line), with **a_K = 0 and b_K = 0.85**, measured (`[swing.a]`, `[swing.b]`; below).
    - **His line** is his projection sources' kicking keys (`fga`, `fgm`, `fgm_yds`, `fgmiss`, `xpa`, `xpm`, `xpmiss` and any `fgm_<bucket>` key present), averaged with his effective shares.
    - **`swing.kicker_var`** (written 2026-11-21) is a compound-Poisson model. The makes in each distance bucket, the misses, the made and the missed extra points are independent Poisson counts, so the variance is Σ λ · E[X²]: a make from a bucket with mean distance m adds λ · 0.01 · (m² + 2.8²), a miss or an extra point adds λ. The buckets give the shape and `fgm` the count (Sleeper's 2026 buckets run about 15% short of its `fgm`, which sits at the real level). ESPN's 0–39 band is 30.47 ± 5.3 yards; a line with makes but no bucket uses 39.0 ± 10.5.
    - **Measured on real kicks (2026-11-21):** the distance facts hold and each bucket alone is close to Poisson, but a game has only so many scoring drives: makes per game vary less than Poisson (variance/mean 0.87) and trade off against extra points (r −0.21). The real variance is below the model's: 0.92 of it on Sleeper's archive 2024–26 (1,366 kicker-games; SE 0.04 within, seasons 0.84–1.03), 0.84 in 2026, and 0.83 on real counts 2022–26 with no projection error (2,300 games). **b_K = 0.85.** From this season only weeks 1–10 entered; they were never live kicker weeks and are graded on σ_out,K, so no graded kicker statistic rests on data that set b_K. Differences between kickers carry little signal (√V spans about 4.8–6.0, and corr(V, e²) is 0.02 where a perfect V would show about 0.10), so the swing is mostly its level.
    - **No answer:** a line with no kicks, or a league that scores a kicking stat the model doesn't price (a bucket bonus), gives None: the kicker has no swing of his own, no floor–ceiling, and his odds use σ_out,K, exactly as for an IDP no projection source has stats for. A zero, negative, non-finite or missing variance is treated the same way, so there is never a zero-width range; so is an error raised inside `kicker_var`, which never stops a sync.
    - **lo and hi:** kickers have their own, −0.88 and 0.87 (`[swing.kicker]`), the 20th and 80th percentiles of (actual − μ)/swing at b_K = 0.85 on the same 1,366 kicker-games. His points are near normal; the IDP ones (−0.8 and 0.75, unchanged for DL/LB/DB) would put about 23% of his games outside each side.
    - **The page records the model in force** (`swing.kicker`: lo, hi and `model`). The Stats page grades a kicker on today's model only for pages that carry it; a frozen kicker record with no swing is graded on σ_out,K, as that page showed.
  - **No position limit.** The league sets none for K, so the block shows "N rostered" with no "/limit". What limits an add is roster room (§4.1).
  - **The move** for a START or UPGRADE on waiver kicker W:
    - *add* (no drop) when you have a free roster spot (if the roster size can't be read, room is assumed);
    - otherwise *add and drop your other kicker*: B, your other comparable kicker, when you have one; with no B, a kicker of yours who can't play this week (bye, out, no number; not on IR or taxi, and not locked), so the kicker who can play is the one kept;
    - otherwise *swap*, a new action: add W and drop S, the starter he would replace;
    - otherwise *you choose the drop*: the roster is full and no kicker of yours can be compared this week (he can't play, or you roster none), so the drop can come from anywhere on the roster. It is never flagged `drop_locked`.
  - **UPGRADE over B** fires as for IDP, only when you roster a second comparable kicker; its move is *add* with room, else *add and drop B*.
  - **Reference flags:** `starter_locked` as for IDP (SADL starts exactly one K too); `drop_locked` when the kicker the move would drop, B or S, is locked.
  - **If the league ever sets `position_limit_k`,** *add* also needs the count to be under it.
  - **IDP moves are unchanged,** including the "under the limit, no drop" wording, which ignores roster room. Your roster was full (27 of 27) on 2026-11-17, so that wording can be wrong for IDP today. It is noted as a follow-up, not fixed here.
  - **Bye look-ahead and one move, many badges** apply to K as written. **The FAAB line counts IDP claims only,** as before.
- **Other leagues' kickers (added 2026-11-21, D25).** For each league of `[kicker_leagues]` that is read:
  - **Inputs:** the league (by name among your leagues of the season, or its id saved earlier that season, or `league_id`), its rosters, this week's matchups (your lineup) and its transactions, all from Sleeper's public /v1 endpoints, read after SADL's own fetches and before the tiers. The kicker numbers, statuses, frozen records, σ_out,K and live scores are SADL's run's.
  - **The panel:** `roster.panel` for K on that league's roster, lineup (its K slot), `position_limit_k` if it sets one, and roster room (its `roster_positions` count less your players on neither IR nor taxi), against the kickers free there. Every rule above applies as written, including *swap* and *you choose the drop*. Its suggestions are logged in its own `badges.json`.
  - **Kusch's Crooked League (2026-11-21):** 16 roster spots and 2 IR; your roster was full with one kicker, so a START there is a swap. FAAB, a $1,000 budget; waivers Wednesday 00:00 PT, the same instant as SADL's, so SADL's windows (§4.3) serve it too.
  - **Its FAAB line** (Waiver notes): the same counts as SADL's line over that league's kicker claims, as shares of its own starting budget, once 5 claims are decided.
  - **Not shown, with a note:** not found, no K slot, kicker scoring that differs from SADL's (any `fg…` or `xp…` key, a missing key counting as 0), your roster not found, or a read that failed. The list is then SADL's alone and every file SADL's sync writes is as it would have been without the league, apart from the note.

### 6.8 Stats page (D20; v0 in PR 2, full in PR 6)
`stats.html` is rebuilt from the committed week files on every site build; nothing on it feeds back into the numbers on the main page.
- **Evaluation pool:** per week with final stats (`actuals.json`) and a projection snapshot, the top `N_eval` (50) per position by prediction among players who played (no stat line = didn't play, left out). Late rows are included and counted; backfilled weeks are marked. A position-week whose pool has fewer than `N_eval` players (a snapshot that misses games) is shown as *partial* and left out of the season numbers, the residual σ_out and top-12 hits. σ_pred uses the σ_out the week's page used.
- **Tiers as shown:** the tiers at the pre-waiver sync (`tiers_log.json`), when the owner sets waivers; for a rebuilt week, its page. A live week without a pre-waiver log is skipped (its final page lists only players yet to play).
- **Top-12 hits** count a player tied with the 12th-best score as in the top 12.
- **Metrics (per position, weekly and season to date):** avg miss (MAE) against a naive baseline on the same players (his average SADL points over his earlier games this season, 2+ games), RMSE, bias (predicted − actual: positive = our number runs high), share within ±1σ and ±2σ of σ_pred, top-12 hits (our top 12 vs the actual top 12 among everyone at the position), Pearson, Spearman and out-of-sample R² (season to date only, n ≥ 100), Brier score of the pairwise "who outscores whom" odds (n ≥ 30 pairs) with calibration buckets, START/UPGRADE results ("x of y"; reference badges and void ones apart; live from the decision log, rebuilt for replayed weeks, shown separately), and average actual points per tier as shown.
- **Per source (PR 3):** every metric is shown for each projection source and for the consensus, on the same evaluation pool (chosen by the equal-weight consensus of the sources' values, so no source picks its own pool); a source is scored on the pool players it covers, and its coverage is shown.
- **Weights:** the share of each source (v0: Sleeper 100%; PR 3: equal priors within the projection group); PR 6 adds the recorded weights in force each week.
- **Readability:** each section opens with a one-line plain headline; at most four default columns with plain labels; the rest under "More stats"; a "What these mean" glossary says what no skill looks like for each metric. The newest week is marked provisional until its stat corrections close (last kickoff + 4 d 4 h). Small samples show "not enough games yet" instead of a number.
- `stats.json` is the same data without internals.
- **Views (added 2026-10-14, owner request):** the page opens on **Last week** (the newest week with stats), with tabs for **All season** and for each single week. Each view shows the same sections over its own weeks: how close (with top-12 hits), each source, the suggestions (a single-week view lists each one with both players' scores) and the tiers as shown. Correlation and R² keep the season-only n ≥ 100 rule, so a single week shows "not enough games yet". The odds check, the weights and the glossary are season-wide and sit below the views. The tabs work without JavaScript, like the position tabs. `stats.json` gains `views`.
- **DB note (added 2026-10-14; methods-review step 4):** while the season's DB rank correlation is below 0.1, the Stats page and the main page's DB tab say that projections barely tell waiver DBs apart (2025's waiver band: r 0.01). They point to snap share and form, both in each player's breakdown.
- **Weekly report card (added 2026-10-14, owner request):** the main page opens with a card for the newest week that has stats. A past week's page shows its own week. **Amended 2026-11-17 (D24):** the card no longer opens the page. It sits in the folded *Last week* section below the positions, and the This week card's last line links to it. Its IDP contents are unchanged. The card holds:
  - your rostered IDPs, projected (as shown at the week's last sync) vs scored, with the total for those who played;
  - the suggestions that paid off, x of y (live from the decision log, rebuilt for replayed weeks, as on the Stats page);
  - your biggest beat and biggest shortfall against projection;
  - the best and worst call;
  - a link to the Stats page's view of that week.

  It is marked provisional until the week's stat corrections close.
- **Evaluation protocol (added 2026-11-17, full review; `docs/eval-protocol.md`):** the definitions below are fixed before week 10 settles. They change only at a checkpoint, and from then on.
  - **Headline = the number the page showed.** Every "how close" metric grades the frozen μ (`frozen.json` `mu`). "Before form" (`base`) and the plain average of the projection sources are reference rows. The pool stays the plain average's top 50 among players who played, so no row picks its own pool. σ_pred = √(σ_μ² + swing²) from the frozen record, or the week's σ_out where the record has no swing. Weeks 1–4 are marked replayed and week 5 rebuilt. Comparisons between rows are paired per-week differences ± 2 SE, labelled "can't tell apart" when the interval crosses 0. The old headline (projection sources only) stays in a collapsed table for 2026.
  - **Suggestions are counted as decisions.** Badges are grouped by (week, position, compared player, kind). The group's highest-p badge stands for it, with ties broken by the waiver player's μ and then his id. A push counts as decided but not paid off, and reference badges are left out. The page shows "x of y decisions, the odds expected z, +p points each", with live and rebuilt decisions apart. A Wilson interval is shown once there are 5 or more decided; below that the page says "too few to judge". The badge count stays as a second line. The report card uses the same rule.
  - **Tiers as shown means the 25 shown.** The tier table scores only the 25 players per position the pre-waiver page showed, ordered by (tier, −μ), `[display] depth`. It does this every week, so the whole-pool log from week 6 on is cut to the shown 25. It adds rank buckets 1–5, 6–10 and 11–25, and the within-week rank correlation ± SE. Its wording comes from the SE ("no clear order yet" when |r| < 2 SE). This table never reopens tier z.
  - **Ranges:** the floor–ceiling coverage (below / inside / above, aim 20/60/20) and the spread ratio RMS((actual − μ)/σ_pred), aim 1.00, replace "within ±1σ (aim 68%)". IDP scores are fat-tailed, so honest ranges put 71–78% within ±1σ. The raw within-1σ/2σ values stay in `stats.json`.
  - **DB note:** prints the within-week rank correlation of the published μ ± 2 SE, and stays on until the lower end of that range clears 0.1. It is measured among the 25 DBs the page showed (the scoreboard's band), or on the top-50 pool before a band exists. A pooled value crossing 0.1 by noise no longer switches it off.
  - **Adoption:** a model change is adopted only at a checkpoint and only under the rules of `docs/eval-protocol.md`. Every test is recorded in the ledger of `docs/methods-review.md` before it runs.
  - **Settled weeks only:** the All-season figures (every verdict, margin and the DB note) use the weeks whose stat corrections have closed. A provisional week shows under Last week and its own tab until then.
  - **Decision scoreboard (PR 23; `scoreboard.py`):** the first section of every multi-week view, "Do the picks rank the waiver pool right?"
    - **Players:** the 25 each position showed when you set waivers, the band of `docs/eval-protocol.md`. Weeks 1–4 use their rebuilt pages; week 5 saved no list.
    - **The ladder,** each rung scored from the frozen record:
      - Sleeper alone;
      - ESPN alone;
      - all sources at their starting shares;
      - with the accuracy shares (`base`);
      - with the form term (`mu`, the number shown).
    - **Measures per rung:**
      - rank skill: the within-week rank correlation with the scores among band players who played;
      - its paired difference with Sleeper alone;
      - top-3 and top-5 lift over the band's average, a player who didn't play scoring 0.
    - **Pooling:** a week's three positions are averaged first, so the week is the unit.
    - **Pick timing:** on live weeks, Tuesday's top 5 (the pre-waiver log's μ) against the kickoff order's top 5 on the same band. It is also shown among players who played, with the count of Tuesday top-5 players who didn't play.
    - **Wording:** "better than Sleeper alone" only beyond the margin.
    - **Calibration of the margin rule:** on the 2025 archive, 400 comparisons of two equally good rungs crossed it 6.2% of the time (aim about 5%).
    - Nothing here feeds the model. Weekly starters (for points against the player actually started) are not stored yet.
  - **Honest uncertainty (PR 21):**
    - Every season figure carries a week-clustered margin (t-quantile at 9 weeks or fewer, 2 SE beyond). This covers bias, the within-week rank correlation, the paired differences against the reference rows, and the odds score (Brier), whose pairs within a week share that week's surprises.
    - "Better than a coin flip", "runs high/low", "ours better/worse" and "better than Sleeper alone" are said only beyond that margin, and only with 4 or more weeks behind them. A clearly reversed order is called backwards, not unclear.
    - The headline and every tally keep live and rebuilt decisions apart.
    - The median miss is shown beside each position's bias. "A few big games pull the average" is said only when the median is nearer 0 than the mean, or on the other side of it.
    - A calibration row resting on fewer than 20 distinct favoured player-weeks shows "too few".
    - The top-12 random baseline is 12·K/50 ± 2 hypergeometric SDs (about 95%, like every ± on the page), K being the pool players who made the actual top 12.
    - Pooled Pearson and R² stay in `stats.json` only.
    - Next to the bias, the page shows the median miss (actual − μ): a few big games can pull the mean while the typical player lands near his number.
    - The report card shows that week's median miss per position, and the decisions of the weeks up to it, live and rebuilt apart. Its best and worst call come from the badges that stand for decisions.
    - The margin on points per decision is clustered by week, since decisions in one week share its games.
    - A week where any pool player lacks a frozen record has the plain average standing in for him, counted (`fallback`). It is not called live.
    - The weights section shows α̂ measured on every settled week ± its leave-one-week-out margin, and each source's error ratio ± its margin, with "not distinguishable from 0.6" when that holds.
    - `weights.json` gains `alpha_se` and each source's `r_se` from the next week written. Weeks already written are never rewritten.
- **Kickers: a separate scope (added 2026-11-17, D23).** Kickers are graded by the same methods on their own players, sources and pool. No kicker row, pair or week enters an IDP figure, and none of the definitions above changes.
  - **Pool sizes per group:** the evaluation pool is the best 20 kickers (IDP: 50); top-k hits are top 5 (IDP: 12); a source needs 10 covered pool players (IDP: 15). A kicker week is *partial* only against 20.
  - **Where K appears:** as its own row or column wherever the page lists positions (how close, each source, the suggestions, the tiers as shown, the weights).
  - **What stays DL/LB/DB only:** every pooled figure (the scoreboard's ladder and its pooled rows, the season verdicts, the pooled weights and α̂, the residual σ_out, the D/ST section, the DB note, `sadlidp tune`, the backtests, the DL matchup), and the Tuesday-questionable count. Where the text said "all positions" it now names DL, LB and DB.
  - **Scoreboard:** the by-position table gains a K row, built from the kicker sources (its Sleeper and ESPN columns are `sleeper_k` and `espn_k`). It never enters a pooled row.
  - **Sources:** a kicker source is scored among the kicker sources, on the kicker pool. Its backfilled weeks are marked backfilled. The IDP source rows and weight history list the five IDP sources only.
  - **FAAB line:** counts IDP claims only; the numbers and wording are unchanged.
  - **Report card:** the "Your IDPs scored X against Y" sentence and its totals stay IDP-only, and so do the biggest beat, the biggest shortfall and the best and worst call. Your kickers are listed after the IDPs, one line each ("Your kicker: …", projected against scored), and are never added in.
  - **Last week's suggestions:** kicker suggestions are listed with the others and tallied apart (`summary_k`); the IDP "paid off x of y" count is unchanged.
  - **Other leagues (D25, 2026-11-21):** each league's kicker suggestions are listed under their own heading with their own tally, from that league's badge log, and never added to SADL's. The report card lists your kicker in each league by name ("Your SADL kicker: …", "Your Crooked kicker: …"). The Stats page is unchanged.
  - **Kicker note,** on the K panel, like the DB note and from numbers the build already has: with a typical game-to-game swing of σ_out,K points, a kicker projected 1 point higher outscores the other Φ(1/(√2·σ_out,K)) of the time, 56% at 5.0. The note says that plainly, and that the kicker tiers are rough. Once 4 or more settled weeks exist whose kicker numbers the page showed before the games, it adds the measured figure the way the DB note does: the within-week rank correlation of the published μ with its margin, among the kickers the page showed, or on the pool of 20 where no such band exists. Weeks graded from the kicker archives alone never count toward the 4 (`site/build.py` `kicker_note`): the page showed no kicker order in them, and those archives are unchecked (§6.5). The Stats page shows those weeks, marked as from the archive.
  - **Display rules** are the ones above: every number with its ± or n, season figures only from 4 settled weeks, "better" or "worse" only beyond the margin.
  - **Old weeks:** a week with no kicker data shows no K row; a week whose kicker rows are all backfilled is marked so.

## 7. Failure handling and security
- **Fatal:** a failure of the documented Sleeper /v1 endpoints (state, user, leagues, league, rosters, players). The job fails, nothing deploys, and the last good site stays live.
- **Non-fatal:**
  - Every source adapter, including Sleeper projections. An adapter that raises is recorded as a `failed` source (error = step and exception type, traceback in the job log only); actuals refresh, σ_out, matchups, backfill and the decision logs are isolated the same way (added 2026-10-12).
  - A `/stats` failure shows a banner, skips the accuracy update, and reuses cached season rates.
  - A sync succeeds if ≥1 projection or rank source returns rows. If none do, the page shows the stale data with a banner.
  - *Incomplete fetches (added 2026-10-12):* a fetch that looks incomplete is `suspect` and isn't merged; the earlier rows stay, with a banner. Two cases: a Sleeper response with no real rows at all (Sleeper rewrites a week's rows in a batch, and mid-batch serves only placeholders: seen live at 09:10 UTC on 2026-10-12, and its batches also run during game days), and any source whose fetch has fewer rows than `sources.shrink_guard` (0.6) × the rows already stored for the week. A gate window counts as done only when at least one source fetched successfully in that run, so the next slot retries.
- **A source read mostly after kickoff (added 2026-11-17):** when a sync's fetch of a source skips more rows for games already started than the source had saved before them, and those skipped rows are over half of what it fetched, the page warns: "<source>: N of M rows were for games already started and can't be used". This is how 2026 week 5's ESPN and FantasyPros gap would have shown up.
- **Data audit (added 2026-11-17; `sadlidp audit`, `audit.py`):** read-only checks over a season's saved weeks, printed as pass/warn. It writes nothing, fixes nothing and exits 0.
  - **Checks:**
    - each source's rows against half the median of its earlier weeks;
    - a source read mostly after kickoff (the rule above);
    - frozen kickoffs against the schedule;
    - pages missing the swing or a live week's pre-waiver tiers log;
    - each recorded `weights.json` reproduced from the rows that existed when it was written;
    - names unmatched in 3 or more weeks.
  - **Fixed thresholds:** they are relative rules set in advance and are never relaxed to silence a warning.
  - **Acknowledged weeks:** a week in `[audit] acknowledged` has been looked at and written up. Its warnings show as `known`.
  - **Where it runs:** CI runs it on every push, without blocking. It is step 1 of every checkpoint.
  - **Excluding data:** a warning never drops data by itself. A test leaves a week out only if its registration says so in advance (`docs/eval-protocol.md`).
- **Network (added 2026-10-12, PR 3):** per-host request spacing (`net.host_interval_s`, default 2 s; FantasyPros 5 s per its robots.txt). A challenge (403/503 with `cf-mitigated`, or a "Just a moment" page) is never retried and blocks that host for the rest of the run; its source reports `blocked`.
- **Kickers (added 2026-11-17, D23):**
  - **A kicker source never fails the sync** and never closes a gate window: "at least one source fetched successfully" still means an IDP source, so a run where only kicker sources answered leaves the window open for the next slot.
  - **No kicker rows yet:** when no kicker source has usable rows, the page says "no kicker source has usable rows for this week yet; the kicker tiers are empty until one does". The IDP "no source" banner still looks at the IDP sources alone.
  - **Not a kicker response:** a kicker fetch whose response holds no kicker at all, or none that matches a Sleeper kicker, is never `ok` (it is `suspect`, or `parse_error` for a page without its table), so an empty file is never saved over good rows.
  - **Banners** name a kicker source by its code name, like any source (`espn_k: failed …`). The one banner written with a site label, the count of rows that came in after their games had started, says "ESPN kickers", and so does the backfill line in the job log.
  - **Shared hosts:** five kicker sources use hosts the IDP sources already use, under the same per-host spacing and backfill request cap. CBS is new: `www.cbssports.com` at 3 s. A sync asks for the IDP sources first, then the D/ST signal, then the kicker sources, and the backfill takes the kicker archives last. So every IDP source request is made as it was before kickers, and a kicker page that challenges or rate-limits its host can only cost later kicker requests and that sync's backfill on the host. The one kicker request made earlier is the add-only merge of past results (§6.5): up to 4 `/v1/stats` requests a sync, on Sleeper's documented host, right after the actuals refresh that already calls it.
  - **Audit:** the `weights k` check (§6.5). The other checks read kicker source files like any source's.
  - **With no kicker source enabled** (a replay of a week saved before kickers) nothing kicker-related is computed or written, which keeps such a replay identical to what it was.
- **Secrets:**
  - Authenticated responses, cookies and API keys are never committed, logged, uploaded as workflow artifacts, or stored in actions/cache. All four are readable by others in a public repo (pull requests, including from forks, can restore caches created on main).
  - Private and keyed sources (FantasyPros, The Odds API) bypass the HTTP cache. Their responses stay in memory only, and the cache never stores Cookie, Set-Cookie or Authorization headers. The Odds once-per-event rule is enforced from a fetch log in `run.json` (event id, fetched_at, credits remaining), never from a cached URL.
  - Private-source plaintext is decrypted in memory only and never written inside the working tree. FP normalized rows are committed only as `age`-encrypted files (D16).
  - The fixture recorder strips Cookie, Set-Cookie and Authorization headers and token query parameters. FP fixtures are synthetic rows.
- **Leak guard (`sadlidp leakcheck`):**
  - *When:* sync.yml and probe.yml run it on the staged changes (`git diff --cached`) **and** on the built site, immediately before every commit or `upload-pages-artifact`.
  - *On a hit:* it aborts the job, so nothing is committed or deployed, and reports only the file path and the pattern name, never the matched text.
  - *What it looks for:*
    - every configured secret value (`ODDS_API_KEY`, and the probe's legacy `FP_COOKIE`/`FP_EMAIL`/`FP_PASSWORD` if they are ever set), including their JSON/repr-escaped forms;
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
3–4. **More sources and accuracy, built together (D17, amended 2026-10-12)** in six PRs, each with its SPEC amendment as the first commit and a live preview:
   1. Locked suggestions (D15) and the frozen week record, badge and tier logs, per-step error isolation.
   2. Stats page v0 and the breakdown UI on Sleeper alone (D19, D20); frozen per-week players file; compact storage.
   3. Multi-source core and ESPN: per-host request spacing, matching, imputation, the observation rule, the backfill runner (ESPN weeks 1–5), ESPN in the consensus, breakdown and Stats page (per source). The crosswalk and list-scoped merges move to PR 4, where the first multi-list source arrives.
   4. FantasyPros ECR and rank → value mapping; FantasyPros D/ST captured as a D18 signal.
   5. RotoBaller and FantasyData; the Thursday window (D13).
   6. Accuracy weights, calibrated START/UPGRADE odds, the full Stats page, archive-integrity checks, the D18 team-defense backtest.
5. **Vegas odds (D18):** design, then build, the odds signal for tiers: a game-line adjustment from the nflverse spread/total (free) and/or a props projection source (needs `ODDS_API_KEY`). Accuracy tracking (Phase 4) decides whether each signal earns weight: it must lower projection error, not just move numbers.
6. **Polish:** accuracy and `z` tuning, UI refinements.
7. **Kickers and the position-first page (D23, D24; added 2026-11-17).** One change, spec first, built in parts:
   1. **Core:** the slots and groups, the source registry, `sleeper_k`, per-group matching, consensus, weights and backfill, the kicker roster rule, the add-only merge of past results, and a portable page build (Windows).
   2. **The five other kicker adapters:** `espn_k`, `cbs_k`, `fp_k`, `fantasydata_k`, `rotoballer_k`, each tested on a trimmed recorded response.
   3. **Statistics:** K as a separate scope on the Stats page, the report card and the kicker note (§6.8).
   4. **The page:** the position-first order (D24).
   5. **`swing.kicker_var`:** written 2026-11-21, with b_K and the kicker floor and ceiling multipliers measured (§6.7).
   6. **After merge:** the first sync from `main` with the kicker sources (the branch's preview sync has run one from Actions, with the kicker backfill at its normal budget, §10); the archive-integrity check of the four kicker archives; the season-end swing check (`docs/status.md` §7).

## 9. Config tunables (`config/sadl.toml`)
`league.names`, `league.league_id?`, `espn.league_id=942104146`, `roster.position_limit` (fallback), `display.depth=25`, `tiers.z=0.75`, `tiers.sigma_floor.{dl,lb,db}=0.75`, `tiers.s0_default`, `tiers.nu0=2`, `form.{enabled, k=4, miss.{DL,LB,DB}, snap.{DL,LB,DB}}`, `swing.{enabled, lo=-0.8, hi=0.75, a.{DL,LB,DB}, b.{DL,LB,DB}}`, `swap_threshold=0.6`, `censor.M=10`, `weights.prior_split=0.6`, `weights.k=4`, `weights.group_bounds=[0.2,0.8]`, `weights.prior_weight_cap`, `accuracy.N_eval=50`, `accuracy.min_covered=15`, `impute.m_k.*`, `max_source_age_h=96`, `sources.shrink_guard=0.6`, `sources.<name>.{archive, late_rows}`, `backfill.{budget_s=300, max_requests}`, `net.host_interval_s.<host>`, `odds.monthly_credit_budget=450`, `gate.*` (window offsets), `sources.<name>.{enabled, private}`, `sources.fp_ecr` and `sources.fp_dst` (`archive = true`; PR 4).

**Kickers (added 2026-11-17, D23):** `tiers.sigma_floor.K=0.75`, `sigma_out.K=5.0` (beside `sigma_out.{method, DL, LB, DB}`), `swing.a.K=0.0`, `swing.b.K=1.0`, `weights.prior.{fp_k=2.0, rotoballer_k=1.0, fantasydata_k=1.0}`, `accuracy.untrusted=["sleeper_k", "espn_k", "fp_k", "rotoballer_k"]`, `sources.{sleeper_k, espn_k, cbs_k, fp_k, fantasydata_k, rotoballer_k}.{enabled, archive, late_rows, expected_by}` (archive: all but `cbs_k` and `fantasydata_k`; late rows: `sleeper_k` only; expected by: `fantasydata_k` Wed 12:00, `rotoballer_k` Thu 12:00), `net.host_interval_s."www.cbssports.com"=3.0`, `kicker.yards_per_make.{"0_19"=19.0, "20_29"=25.4, "30_39"=34.4, "40_49"=44.4, "50p"=53.9}`. Shared with IDP, unchanged: `tiers.z`, `swap_threshold`, `censor.M`, `weights.prior_split`, `weights.k`, `swing.lo`, `swing.hi`, `display.depth`. The kicker pool sizes (20, top 5, 10 covered) are constants in `positions.py`, not config. `roster.position_limit` never applies to K.

**Other leagues' kickers (added 2026-11-21, D25):** `kicker_leagues.<key>.{label, names, league_id?, enabled}`; today `kicker_leagues.crooked` = label "Crooked", names ["Kusch's Crooked League"]. The key is the league's data folder. A replay sets `kicker_leagues` to nothing.

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
- **Ground-truth acceptance:** the fixture is `stats/{week}`, `matchups/{week}` and `scoring_settings` captured ≥4 days after a completed week. Assert `|ours − players_points[pid]| ≤ 0.01` for every IDP in `players_points`, and list any mismatch with its stat keys. If SADL scores threshold bonuses, the fixture week must include at least one hit (it currently scores none, §13). It should include a defender with special-teams or return stats if one exists.
- **End to end:** run sync.yml via workflow_dispatch, then confirm:
  - the site renders on a phone with tiers, My IDPs badges, the footer and the data-as-of time;
  - `fp_mode=force_fail` produces a FantasyPros source banner, not a failed sync;
  - `sadlidp gate` reports the expected next windows;
  - a second run after a game kicks off leaves that game's rows unchanged.
- **Security:** the leak guard passes on every sync and probe commit and on the built site, and the CI pattern scan passes. A seeded fake secret in a test fixture branch makes the guard abort. No FP plaintext exists in the repo, the site, artifacts or caches.
- **Kickers (added 2026-11-17, D23):**
  - **IDP isolation, the first check.** The same syncs are run with and without kicker players, rows and routes (a Tuesday, a Thursday and a Friday on the recorded fixtures), and every DL/LB/DB part of every written file must be identical: the consensus, tiers, badges, frozen records, logs, the five IDP source files and every existing key of `weights.json`. A replay of a week saved before kickers must have no K.
  - **Old code against new code on real data,** done on 2026-11-17, offline, the code before kickers against the code with them, each on its own copy of the season's data and at the same instant:
    - replays of 2026 weeks 1–11, keeping everything the replay's inner sync writes: all 497 JSON files byte-identical;
    - a forced week-11 sync on the whole season's saved data (every fetch refused, so the saved rows are used): 203 of 206 files byte-identical, and the other three (`consensus.json`, `run.json`, `weights.json`) differ only in K slots, kicker source entries, the kicker warnings and the `"kickers"` record;
    - the same sync on the data of the live check below, which holds real kicker rows, 27 kicker frozen records and kicker tiers (the old code was given that data without the kicker source files): every DL/LB/DB leaf identical;
    - the site built from the committed data: `stats.json` and every `data-*.json` byte-identical, `stats.html` identical outside the shared style block, and all 11 week pages render with three positions and no kicker text; the audit output is identical too.
    - re-run on 2026-11-19 on the data of the preview sync from Actions (below), which holds real kicker rows in weeks 1–11 and kicker results in weeks 6–10; the old code was given the same data with everything kicker taken out: replays of weeks 6, 9, 10 and 11 and a forced week-11 sync at Thursday 7 PM ET, every DL/LB/DB leaf identical (weeks 6, 9 and 10 replay with no K, since their kicker rows are all backfilled); the site built from it, no leaf of the old code's `stats.json` or `data-*.json` differs (the new one only adds kicker keys).
  - **A replay never builds kickers from archive rows alone** (§5.3): a test replays one week with no kicker file, with a backfilled one and with a live one.
  - **Unit tests:**
    - the kicker slot rule: `position` K only, no `fantasy_positions` fallback, punters left out, id 5251 still DL;
    - the two groups and the registry: the IDP sources as they were, the kicker ones declared;
    - `sleeper_k`: kickers kept, `fgmiss` derived and marked, its statuses, its own request, the IDP query string unchanged;
    - kicker points from real stat lines (§6.3);
    - the roster rule: add with room, add and drop, swap, you choose the drop, and a league that does set a limit;
    - the add-only merge: lines added once, nothing else changed, at most 4 weeks a sync;
    - no form for a kicker; σ_out,K from the config when the cache lacks it; a kicker without a variance keeps σ_out,K;
    - one matcher per group, and backfill handing each source its own group;
    - kicker weights measured on kickers with their own pool; backfilled rows waiting for the archive check; `weights.json` gaining one key; the audit reproducing both records.
  - **`kicker_var`:** pinned by hand-computed values (the docstring's Sleeper line, 42.89; ESPN's 0–39 band; a line with no bucket), its None cases and its edge cases (values that aren't numbers, NaN, infinity, the league's 50–59 and 60+ bands).
  - **Each other kicker adapter** is tested on a trimmed recorded response: the week check, the rows, the derived keys and their marks, and a response with no kicker in it.
  - **Ground truth, still to run:** `|ours − players_points[pid]| ≤ 0.01` for every kicker in `players_points`, on a settled week that has a made field goal, a missed one and a missed extra point.
  - **Live, run once (2026-11-17, 11:01 PM ET):** one forced sync with the pipeline's own client and user agent, from a home connection, into a scratch copy of the data (nothing was committed), with the backfill capped at 3 requests per host and 60 s.
    - All six kicker sources were `ok`, with no warning: Sleeper 26 rows (127 placeholders), ESPN 25 (23 placeholders), CBS 25, FantasyPros 26, FantasyData 25 (4 unprojected rows dropped), RotoBaller 26. Every row with a team matched a Sleeper kicker (§6.1). No kicker was zero-filled.
    - 10 available kickers had a number, in 4 tiers; a card's parts added up to its μ, and a part's points matched its stored line by hand (0.1 × 64.91 − (2.19 − 1.87) + 2.58 − 0.13 = 8.62).
    - The add-only merge added 31 to 36 kicker lines to each of weeks 6–9 and changed no existing line; week 10, still inside its correction window, was rewritten whole with its 35 kicker lines.
    - The backfill wrote weeks 1–3 of `sleeper_k`, `espn_k` and `fp_k` and stopped at the cap. `rotoballer_k` found no week-1 article in the feed's first 3 pages, so how far back its feed reaches is not known.
  - **Live from Actions, run once (2026-11-17, 11:09 PM ET):** the branch's CI preview job ran a forced sync with the branch's code on a GitHub runner, under the pipeline's own user agent, on a copy of the committed data, and published the result to `previews/claude/kickers` (nothing merged).
    - All six kicker sources were `ok`, with no warning, with the same row counts as the run above. Every row with a team matched a Sleeper kicker; 10 ESPN free agents, who have no team, matched none and were left out. No kicker was zero-filled.
    - The backfill, at its normal budget, wrote weeks 1–10 of `sleeper_k`, `espn_k` and `fp_k` and weeks 2–10 of `rotoballer_k` (again no week-1 article in the feed's first 3 pages).
    - The add-only merge added kicker lines to weeks 9, 8, 7 and 6 (31 to 36 each) and changed no existing line; week 10 was rewritten whole with its 35 kicker lines. Weeks 1–5 follow in the next syncs (4 a sync).
    - DL, LB and DB: the same tier counts as the committed week-11 page of three hours earlier; μ moved by 0.04–0.08 points on average, from that later fetch of the IDP sources.
  - **Live, still to run:** the first sync from `main`; a week in which CBS has not yet turned to the new week.
  - **Page:** the position bar and the folds work with JavaScript off; a week saved before kickers shows three positions; every string and number of the DL/LB/DB cards is unchanged.
- **Other leagues' kickers (added 2026-11-21, D25):**
  - **Main against the branch on real data,** offline, each on its own copy of the committed data (main 89aa344), at the same instant, with the Crooked League's responses recorded on 2026-11-21 served to both and every source fetch refused (saved rows used):
    - a forced week-11 sync, Saturday 4 PM ET: 248 of 251 files byte-identical; `consensus.json`, `run.json` and `tiers_log.json` differ in kicker leaves only (1,076, 5 and 65; none DL/LB/DB); the branch adds only files in `crooked/` folders;
    - the same on Sunday at 5:30 PM ET, with the early games locked: again 248 of 251, kicker leaves only;
    - the Crooked League unreachable: 249 of 251, the other two differing only in the new `k_leagues` and `kicker_leagues` notes, every K leaf included;
    - replays of weeks 1–11: all 251 data files and all 309 files the replays' inner syncs write byte-identical;
    - the site: on the committed data, every `data-*.json` and `stats.json` byte-identical and all 13 pages identical outside the shared style block (two new rules); with the league read, the week-11 data differs in kicker leaves only and the DL, LB and DB panels' HTML is identical.
  - **Unit and end-to-end tests** (`tests/unit/test_other_leagues.py`): the scoring check, the combined list and its tags, each league's moves on its own free kickers, the league's own files, every DL/LB/DB part identical to SADL alone (league read, not found, scoring differs), the not-shown notes, the FAAB line, the recap, the This week card, the page, and a replay that never reads another league.
  - **Phone:** the K panel at 375 px, no sideways scroll; checked with the league read, and with a what-if (your Crooked kicker Out) that draws Crooked moves.
  - **Live, still to run:** the first sync from Actions with the league (the branch's preview sync).

## 11. Known limitations (accepted)
- **Weekly decisions only.** There is no look-ahead (D10).
- **Thursday-night freezes.** TNF and holiday players are frozen from the pre-waiver run unless you press sync before kickoff.
- **Blend inversion (D16, accepted by user).** When a private source is the only member of its group for a player, its per-player value can be recovered from μ, the plaintext rows in `data/` and the public weights. For example, with FP ECR as the only rank source, R_i = (μ_i − α·P_i)/(1−α), which can then be inverted through the rank curve. D16 keeps FP's rows from being copyable wholesale, but it doesn't hide what they contain.
- **Dropped schedules.** GitHub can delay or drop scheduled runs. Each window has several slots and catches up, but if every slot in a window is dropped, that refresh doesn't happen unless you press sync.
- **Inactives.** The Sunday refresh can run before official inactives are posted (~90 min before kickoff).
- **Weight compression.** Weights based on accuracy stay close to equal, because game noise dominates IDP squared error. Shrinkage keeps this conservative.
- **Kickers are barely predictable (added 2026-11-17, D23).** Figures from the 2026-11-17 probe (§13), none of them fitted into the model:
  - **Noise:** a kicker's weekly decimal points have an SD of 5.01 (2022–25, 2,148 kicker-games), 4.78 within one kicker's season. The season averages of different kickers differ by an SD of only 1.43, and most of that is sampling noise.
  - **Signal:** Sleeper's kicker projections, rescored to SADL points, correlated 0.15 with the outcome over five sampled weeks (144 kicker-games; per-week rank correlations from −0.14 to +0.28), with an average miss of 4.2 points. Those rows were read from the archive and can't be shown to be pre-game. A three-input model of game lines and roof, fitted on 2022–24, reached 0.20 on 2025 (535 kicker-games). So a projection explains about 2–4% of a kicker's week.
  - **What it means on the page:** at σ_out,K = 5.0 a kicker projected 1 point higher outscores the other about 56% of the time, and 2 points higher about 61%. A START needs 60%, so a waiver kicker has to project about 1.8 points above your starter (both at the σ floor). That will be rare, and the kicker tiers are rough.
  - **The weights** will most likely stay at their priors for the same reason (§6.5).
- **Sleeper's 2026 kicker yards run low (2026-11-17).** On about four rows in five (21 of 26 in week 11, 21 of 27 in week 10, 24 of 28 in week 8) Sleeper's made-distance buckets add up to about 15% fewer field goals than its own `fgm`, and `fgm_yds` follows the buckets. Its mean projected yards were 64.8 a kicker in week 11, against 70.3 actual over 2026 weeks 5–10: about 8% low, or half a point a kicker. Its 2025 rows were consistent. `fgm_yds` is used as published. The shortfall is not the same for every kicker: the five consistent rows of week 11 don't have it.
- **ESPN's kicker yards carry no distance of their own.** Its projected yards are exactly 30 × makes under 40 + 45 × makes from 40–49 + 54 × makes from 50 up, with the same bucket shares for every kicker (about 38.5 yards a make for everyone). ESPN adds information on volume, accuracy and extra points only.
- **RotoBaller re-dates its kicker article.** Rows of kickers who kicked off before the article's date are dropped. Week 9's article carried a Saturday date although it ranked both kickers of that week's Thursday game, so a read after a re-date, and every backfilled read, can lose the Thursday kickers. Only a sync made before the re-date keeps them.
- **CBS has no archive and ignores the week in its URL.** A past week can never be read, so CBS can't be backfilled or re-checked, and the week is taken from the page title.
- **FantasyPros' archived kicker pages show today's teams,** so a backfilled row for a kicker who has moved can fail its name-and-team match; the FantasyPros id through the crosswalk is tried first (21 of week 11's 26 projected kickers have one there).
- **Kicker numbers that are priors, not measurements:** a_K = 0, the σ floor 0.75, z, M = 10, the 60:40 split and the rank priors 2 : 1 : 1. σ_out,K = 5.0 is a level read off past seasons. b_K = 0.85 and the kicker floor and ceiling multipliers (−0.88, 0.87) are measured on past seasons and 2026 weeks 1–10 (§6.7).
- **Kicker lists are short and nearly complete,** which the censoring rule wasn't designed for (§5.2). Its effect on kickers is untested, and so is the zero-fill of a kicker Sleeper leaves out with no placeholder.
- **The kicker sources have been read from Actions once:** probed from a home connection under a research user agent, read once there by the pipeline's own client, then once from a GitHub runner by the branch's preview sync of 2026-11-17, where all six were `ok` (§10). One week from a runner can't show how often a source challenges or rate-limits one.

## 12. Change log
- **2026-10-12 · v2:** agreed spec.
- **2026-10-12 · D9 amended (user decision):** FantasyPros access via auto-login with `FP_EMAIL`/`FP_PASSWORD`, tested in Phase 1, with a one-command local cookie refresh as the fallback. The manual cookie copy-paste is no longer the plan.
- **2026-10-12 · Phase 1 amendment:** verified facts in §13; final source list in §5.2; D14 resolved to Sleeper's `position` field.
- **2026-10-12 · Owner decisions closing Phase 1:** FantasyPros = free ECR only (D9 superseded; no FP credentials); The Odds API deferred past the MVP; ESPN projections through the owner's public IDP league 942104146 (§5.2), pending probe run 4.
- **2026-10-12 · D18 added (owner request):** Vegas odds docketed for tier creation and analysis as Phase 5 (game lines from the nflverse schedule and/or Odds API props); polish moves to Phase 6.
- **2026-10-12 · ESPN verified (probe run 4):** league 942104146 readable logged out; stat-id mapping recorded in §13; ESPN rows impute TFL and QB hits.
- **2026-10-12 · Phase 2 (MVP) build notes:**
  - *Stack:* the probe's stdlib HTTP client (per-host spacing, retries, deadline, circuit breakers) and dataclasses stand in for httpx and pydantic. selectolax waits for the first HTML source (Phase 3). §2 stays the target list.
  - *Modules:* `consensus.py` is §3.2's `aggregate.py`. Added `pool.py` (§2 filters), `gate.py` (§4.3), `sigma.py` (σ_out, §6.7) and `positions.py` (D14).
  - *Gate state:* windows already run are recorded in `data/<season>/gate.json` (not `run.json`), committed with the sync's data, so a failed run or push leaves the window open. The gate decides from the committed `league.json`. Sleeper league calls happen only once a window is due, or when no `league.json` exists yet.
  - *Last fantasy week:* playoff_week_start + playoff weeks − 1, with ⌈log2 playoff_teams⌉ rounds. Any `playoff_round_type` other than 0 is read as two weeks per round, which can only make the season longer.
  - *No usable source:* the page still publishes, with a banner, but the window stays open so the next slot retries.
  - *Backfill:* runs inside every sync for this season's past weeks that have no Sleeper snapshot yet (rows marked `backfill: true`). There is no separate `sadlidp backfill` command.
  - *No-projection players:* available players with no μ are shown as a count per position, not listed.
  - *CI:* `ci.yml` adds a live preview on claude/** pushes. It runs a forced sync into a scratch directory and publishes it to the `preview` branch. (Amended in PR 2: per-branch previews, below.)
- **2026-10-12 · D22 added (owner request):** scored results on the page, a Sunday-evening results window, and late rows for projections first seen after kickoff (§2, §4.3, §6.5).
- **2026-10-12 · Phase 3+4 plan approved (owner):** D16 closed (FantasyPros ECR stored like other free sources); D15, D17, D18 amended; D19 (per-player breakdown), D20 (Stats page) and D21 (source list after live checks: FantasyData in; Razzball, Fantasy Sharks, CBS, FFToday out) added; §8 rewritten as six PRs. PR 1: locked suggestions as *for reference*, comparable players and the frozen week record (§6.5, §6.7), badge and tier logs, error isolation (§7), nflverse schedule read from raw.githubusercontent.com.
- **2026-10-12 · PR 2 (Stats v0 + breakdown):** per-player breakdown on every card (D19, §6.4); `stats.html` (§6.8) from data already committed; `players.json` frozen per week replaces `players_idp.json` (§3.3); compact source files with `schema_version` 2 and a `fetches` list, unchanged rows keep their first-seen `fetched_at` (§6.5); residual σ_out behind `sigma_out.method` (§6.7). CI previews are per branch: the branch's committed data is copied to a scratch directory, a forced live sync runs on top, and the result is published to `previews/<branch>`. `sync.yml` also runs (forced) on a push to `main` that changes `src/`, `config/`, the lockfile or the workflow, so merged features reach the page without waiting for a window (owner request).
- **2026-10-12 · PR 3 (multi-source core + ESPN):** `fetch(ctx)` adapters (§5.1); ESPN adapter re-verified live (paging 1,511 IDPs, archive via `scoringPeriodId`, §5.2); observation rule, backfill runner and replays from saved sources (§5.3); matching precedence with TOML overrides and nickname table (§6.1); imputation anchors and empirical-Bayes priors (§6.2); per-host spacing and challenge breaker (§7); per-source Stats rows (§6.8). Crosswalk and list-scoped merges move to PR 4.
- **2026-10-12 · PR 4 (FantasyPros ECR + rank → value):** FP DL/LB/DB pages read live and archived through `?week=N` (§5.2; the combined IDP page isn't read); crosswalk ids from DynastyProcess (§6.1 step 3); rank → value curves, the two-list rule and censoring of observed players (§6.4); the 60:40 group split now applies; list-scoped merges; FP D/ST ranks stored as a `signal` file, never blended (D18). The Stats page scores FP on its mapped points like a projection source.
- **2026-10-12 · Tier tuning (owner request: "the tiers are poor"):** the Stats page's tier table was measuring the Sleeper-only tiers that weeks 1–4 were first rebuilt with; those weeks are now replayed from all three saved sources. `sadlidp tune` (read-only) re-tiers every settled week under a grid of z and projection:rank splits and reports the tier and order signal with margins, the order left inside tiers, and how often a table that size would be out of order by chance alone. z goes from 1.0 to 0.75: at 1.0 a share of the DL and LB order was left inside tiers (r +0.19 and +0.21), at 0.75 little was (−0.01 and +0.11; each ±0.20), and DB has no measurable order at either. Source weights and the 60:40 split are unchanged: no setting beat them by more than the noise, and per-source weights wait for PR 6 and its archive-integrity gates. The Stats page shows each tier average with its margin of error. Findings: `docs/tier-tuning.md`.
- **2026-10-12 · Backtest harness (owner request, after the methods review):** Sleeper still serves every week of last season's IDP projections and final stats. `sadlidp backtest fetch` stores 2025 compactly under `data/2025/archive/week<NN>.json` (one player per line: slot, team, projected IDP stats, the stat line if he played, defensive snaps; 2.5 MB for 18 weeks), and `sadlidp backtest run` scores it under the current settings and runs the standard checks on ~11,000 player-games: skill by position and band, the "form" signals (shrunk earlier misses, last game's snap share) held out by week parity, the Poisson per-player swing against one SD per position, and sack persistence. Nothing in it feeds the page; it is where an idea earns its place (`docs/methods-review.md`). PR 6's D/ST and implied-total backtest runs on this archive.
- **2026-10-12 · Form term (owner request, methods review step 2):** each player's μ now carries his earlier misses this season (shrunk by n/(n+4), read against the frozen `base`) and his last game's defensive snap share relative to the position's 60 best-projected (§6.4), with coefficients fitted on the 2025 archive (`[form]`; `sadlidp backtest run` refits them). The breakdown shows both parts; `frozen.json` keeps `base` and the term; replays carry earlier weeks' results and records in and write the rebuilt record back (§6.5); `sadlidp tune` reports the μ order with the term off and on. Weeks 2–4 were replayed with it.
- **2026-10-12 · Per-player swing (owner request, methods review step 3):** START/UPGRADE odds use each player's own game-to-game swing from his projected stat mix, swing² = a + b·Σ coef²·λ fitted per position on the 2025 archive (`[swing]`), in place of one σ_out per position; every card shows a floor and ceiling (one game in five below, one in five above). The frozen record and the badge log keep the swing; the Stats page scores σ_pred with it (§6.7). Tiers are unchanged.
- **2026-10-14 · Week-5 frozen gap repaired (data review):** the form term read only the 50 Monday-night records of week 5, so a player's week-5 game never counted (Nik Bonitto: 27 points missing, μ 8.2). `sadlidp replay --fill-frozen` adds rebuilt records for the players a live week lacks (§6.5); week 5 was filled. Tested and rejected the same day: comparing an edge LB's snap share with edge players only (Sleeper DL+LB eligibility) instead of all LBs. On the 2025 archive it predicted worse (LB top 50 held-out r 0.232 → 0.214, MAE 5.57 → 5.61; ranks 15–60 r 0.305 → 0.284), so the position average stays.
- **2026-10-14 · Interface (owner requests after the data review):**
  - Stats page views: Last week / All season / each week (§6.8).
  - Weekly report card on the main page (§6.8).
  - DB note on the Stats page and the DB tab (§6.8).
  - Plainer card wording for form and floor–ceiling (D19).
- **2026-10-14 · PR 6a, accuracy weights (owner said "proceed"):** the within-group source weights and the projection : ranking split now follow measured accuracy (§6.5 Implementation), recorded write-once per week in `weights.json` and shown week by week on the Stats page. ESPN's archive passed its integrity check (§13). The team-defense backtest follows as PR 6b.
- **2026-10-14 · PR 6b, team-defense backtest (D18):** `sadlidp backtest dst-fetch` stores 2025's weekly FantasyPros D/ST ranks and nflverse implied points (`data/2025/archive/dst.json`); `sadlidp backtest dst` tests both on the IDP misses and writes `data/2025/dst_backtest.json`, which the Stats page shows. Neither signal passes, so nothing enters the consensus.
- **2026-10-14 · PR 5 (RotoBaller + FantasyData + Thursday window):** RotoBaller and FantasyData rank adapters (§5.2), late-source status, the Thursday window (D13, §4.3). Rank-group prior weights FantasyPros 2 : RotoBaller 1 : FantasyData 1 (50/25/25 when all three rank a player) are the starting shares and the target the accuracy weights shrink toward (§6.5). RotoBaller backfills weeks 1–6 from its RSS; FantasyData has no archive.
- **2026-11-17 · Week-9 tuning checkpoint (owner-scheduled):**
  - Re-tested on 2025 + 2026 weeks 1–9, fit on odd weeks and scored on even weeks; a change is adopted only if it beats the current value on held-out weeks.
  - **Changed:** the DL swing, `[swing]` DL a 0 → 4.3 and b 2.5 → 2.23 (§6.7).
  - **Unchanged:**
    - the form coefficients (current values beat a re-fit on held-out weeks at every position, and the term still lowers the miss: DL 6.23 → 6.01, LB 5.77 → 5.65);
    - tier z 0.75 (every z within noise);
    - the LB and DB swing;
    - the team-defense idea (D18: still no signal, with 2026 weeks 1–9 added; every |t| < 2).
  - **Pre-registered for the week-13 checkpoint:** whether the form term should be dropped for DL players whose projection has already risen 1+ point above their earlier projections while they beat them (the Rousseau case). Adopted only if pooled |t| ≥ 2 and held-out improves in both seasons.
- **2026-11-17 · PR 22 (data hygiene, full review):**
  - **2025 archive teams.** `backtest.fetch_week` stores each row's game-time team and opponent; it had stored the embedded player's team, which is his team today. `sadlidp backtest patch-teams` set them in the 2025 archive. 4,242 rows (about a third) changed team, and no other value changed (checked).
    - D/ST verdict re-run on the corrected joins (D18): it stands. Every |t| < 2, and the held-out miss is worse at every position (DL 6.510 → 6.524, LB 5.718 → 5.762, DB 4.932 → 4.939).
  - **Late-source banner** (§7).
  - **`sadlidp audit`** (§7).
  - **Week-5 gap fill and refresh** (§5.3, §6.5).
- **2026-11-17 · PR 24 (owner decision):** the form miss term is off at LB and DB (§6.4); DL and the snap terms are unchanged.
- **2026-11-17 · Full review (owner request: "a full analysis… without over-cooking the model"):**
  - **The review:** a read-only sweep of weeks 1–9 through seven lenses, each finding reproduced by a skeptic. Findings and the plan are in `docs/status.md` §6.
  - **The model is better than the Stats page showed:** in the waiver band it orders players better than Sleeper alone, by +0.149 ± 0.039 Spearman. The odds and ranges are honest.
  - **Wrong or overstated on the page:**
    - The page graded the plain projection average, not the published μ.
    - It counted badges rather than decisions.
    - Its tier table scored the whole pool.
  - **This PR (docs only):**
    - the evaluation protocol (`docs/eval-protocol.md`, §6.8);
    - the ledger of tested and rejected ideas (`docs/methods-review.md`);
    - the week-13 agenda, ordered and pre-registered (`docs/status.md`).
  - **Owner decisions** (each in its own PR):
    - the form miss term off at LB and DB;
    - the source weights tested on 2025 before any change;
    - Sleeper 2024 and FantasyPros 2025 as test data;
    - league transactions;
    - a DL matchup term in shadow;
    - "changed since Tuesday", a bye look-ahead, and one factual FAAB line.
- **2026-11-17 · PR 26 (research, part 1):**
  - **Sleeper 2024 joins as a test season:**
    - **The archive:** 11,633 projected-and-played rows, every one with its game-time team and opponent. Scored under the newest league's settings (`--scoring-season`).
    - **`sadlidp backtest cross`:** scores the page's settings on a season they weren't fitted on, against settings fitted on another season, with paired per-player differences clustered by week.
    - **Use:** consulted only at checkpoints; nothing is fitted on it.
  - **The projection pair's least-squares share** (§6.5).
- **2026-11-17 · FAAB line normalized (owner request):** bids are shares of the league's starting budget, and a token bid is at most 1% of it ($1 if more) (§6.7).
- **2026-11-17 · PR 26, research part 2:**
  - **FantasyPros' 2025 weekly DL, LB and DB ranks** were pulled once through the live adapter (`&year=`). They are stored with each player's kickoff and each list's last update as `data/2025/archive/fp_weekNN.json`, and never re-fetched to change a result.
    - `sadlidp backtest blend` answers the three registered questions, kickoff-guarded and not.
  - **DL matchup (opponent QB-hit rate allowed), measured after the fact:**
    - `matchup.py` reads nflverse play-by-play (regular-season dropbacks without spikes, shrunk by 4 team-games toward the league rate). `sadlidp backtest matchup` scores it on 2024–2026.
    - It is not logged live: a past week's rate is rebuilt exactly from play-by-play, so live logging would only add a daily download.
    - Its coefficient is frozen at 0.30 per percentage point. It never touches μ unless the season-end test in `docs/status.md` §6 passes.
- **2026-11-17 · PR 25 (decision help, owner decisions):** changed since Tuesday and Tuesday's questionable tags (§6.6); the bye look-ahead, one move per player of yours, and the FAAB context line from saved league transactions (§6.7).
- **2026-11-17 · PR 23 (decision scoreboard):** the waiver band scored rung by rung, from Sleeper alone to the number shown, and Tuesday's picks against kickoff's (§6.8).
- **2026-11-17 · PR 21 (Stats page grades what you see):** the §6.8 evaluation protocol, implemented.
  - The headline is the frozen μ, with "before form" and the plain average as reference rows.
  - Suggestions are counted as decisions.
  - The tier table scores the 25 shown, with rank buckets.
  - Range coverage and a spread check replace "aim 68%".
  - Margins everywhere, and the DB note's new rule.
  - Nothing feeds back into the model.
- **2026-11-17 · Kickers and the position-first page (owner decisions; D23 and D24 added):**
  - **D23, kickers.** K is a fourth tracked position, kept apart from the IDP model end to end (`IDP_SLOTS` against `SLOTS`, two groups; §3.2).
    - **Sources:** six sibling adapters, Sleeper, ESPN and CBS projections and FantasyPros, FantasyData and RotoBaller rankings (§5.2). D21 amended: CBS is In for kickers only; Fantasy Sharks stays Out pending a probe from Actions; the sources looked at and skipped are listed there.
    - **Scoring:** unchanged; the missing keys are derived inside each adapter and marked (§6.2, §6.3).
    - **Model:** the same consensus on its own group; no form term; weights measured apart and recorded under `"kickers"` in `weights.json`, with the four kicker archives untrusted until checked (§6.4, §6.5).
    - **Odds and ranges:** swing² = 0.85 · `kicker_var` with the kicker's own floor and ceiling multipliers; σ_out,K = 5.0 for a kicker with no line (§6.7).
    - **Roster:** no kicker limit; roster room decides, and *swap* is a new move (§4.1, §6.7).
    - **Past weeks:** archives backfilled, results added add-only, no frozen record invented, no page rewritten (§5.3, §6.5).
    - **Statistics:** K is a separate scope with its own pool sizes; a kicker note on the K panel (§6.8).
    - **Also amended:** D3, D9, D10, D14; §2, §4.1, §5.1, §6.1, §6.6, §7, §9, §10, §11; §13 gains the kicker probe and its lineup line now names the K starter.
  - **D24, page order.** Header, This week card, position bar with one panel per position, then folded sections; the report card moves into Last week (§2, §6.8). Presentation only: no number or card wording changes, except that a tier row's source range reads "sources say a to b (n)".
  - **Nothing about DL, LB or DB changed.** No IDP number, record, weight or statistic moves; the isolation check is the first item of §10. Every definition of `docs/eval-protocol.md` and every item of the week-13 agenda stands as registered.
  - **No kicker term was fitted on this season's results.** The kicker checks were registered before any kicker result was graded (`docs/status.md` §7, `docs/methods-review.md`).
  - **Portability:** the page build no longer needs glibc's `%-d` date format, and every site file is written as UTF-8, so the suite runs on Windows (with `tzdata` installed and `PYTHONUTF8=1`).
- **2026-11-21 · D25 added (owner decision): the Crooked League's kickers on the K tab.** One combined kicker list tagged by league, each league's own Yours block and moves, its moves and starter in the This week card, its kicker FAAB line, its last-week suggestions and report-card line (§2, §3.2, §3.3, §6.6, §6.7, §6.8, §9, §10). The kicker tiers are now counted over the combined list. Nothing about DL, LB or DB changed (§10).

## 13. Phase 1 findings (probe runs 1–4, 2026-10-12)
Evidence: `probe-fixtures` branch, `latest/report.md` and `runs/`.

**Sleeper / SADL facts**
- **League:** "Slow Ass Draft League", 12 teams, superflex offense + 1 K + 1 DL / 1 LB / 1 DB, 16 bench, 4 IR, 3 taxi. `settings.position_limit_{dl,lb,db}` = 2 (also QB = 4). Your roster is `roster_id` 7. *(Corrected 2026-11-17: this line left out the K starter. `roster_positions` is QB, RB, RB, WR, WR, TE, SUPER_FLEX, K, DL, LB, DB and 16 BN: 27 spots. There is no `position_limit_k`.)*
- **D14:** replaying all 18 in-season free-agent/waiver IDP adds, only Sleeper's `position` field fits every add (15 adds ended exactly at the limit, none over). `fantasy_positions[0]` would have broken it 3 times and "counts at every eligible slot" 4 times. **IR players don't count** toward the limit (5 adds succeeded only with IR excluded). Taxi: no evidence (no taxi IDPs involved), so taxi players are counted, as the spec defaults. Sleeper enforces limits only at add time: trades and the draft can leave a roster over (one roster has 4 DL now).
- **Scoring:** SADL IDP coefficients are solo 2, assisted 1, TFL 2, sack 6, QB hit 1, PD 3, INT 6, FF 3, FR 3, def TD 6, safety 3, blocked kick 3, plus special-teams FF 1 / FR 1 / TD 6. **No threshold bonuses**, so the §6.3 bonus logic is dormant for SADL. The scoring engine reproduces Sleeper's `players_points` **exactly** for every IDP who recorded a stat in week 4 (69/69, max difference 0.00), from either stats endpoint. This is the §10 acceptance fixture.
- **Waivers:** FAAB, $100 budget, `waiver_day_of_week` = 2 processes **Wednesday 00:00 Pacific** (108 of 117 claims processed at that time; 0 = Monday). `daily_waivers` = 0, `waiver_clear_days` = 1. Failed claims cite roster size or being outbid, never position limits.
- **Week rollover:** `state.week` stays on the current week through Monday night; new-week transactions start Wednesday. Target week comes from the schedule (§4.3) as specced.
- **Injury codes seen:** IR, Out, PUP, Questionable, Sus, NA, COV, plus `status` Active/Inactive. Doubtful is valid but wasn't present this week.
- **Projections endpoint:** of 4,357 rows, 628 are real (have `gp` and an opponent); the rest are placeholders, as §4.1 assumed.

**Schedule and IDs**
- nflverse `games.csv` works; uses `LA` for the Rams (alias to `LAR`); kickoffs include 09:30 ET international games.
- The DynastyProcess crosswalk covers IDPs poorly (Sleeper id 51%, FantasyPros id 34%), and only ~21 of SADL's rostered IDPs carry an ESPN id in Sleeper. Matching relies on normalized name + team + position with `player_overrides.yaml`.

**ESPN league 942104146 (probe run 4)**
- **Access:** settings and players read logged out (HTTP 200), so no cookies or secrets. Lineup slots enabled: LB (10) ×2, DL (11) ×2, DB (14) ×2, DP (15) ×1.
- **Coverage:** 400 IDPs returned (the request's cap, so the adapter must page), 398 with a week-5 projection. 392 of the 400 matched a Sleeper player (155 by `espn_id`, 237 by unique normalized name; 7 ambiguous names, 1 unmatched).
- **Stat-id mapping**, learned from 366 player pairs on week-4 actuals (exact agreement where either side is non-zero; the runner-up candidate stayed below 0.26 for every accepted id):

| ESPN stat id | Sleeper key | Agreement | n |
|---|---|---|---|
| 108 | `idp_tkl_solo` | 0.936 | 281 |
| 107 | `idp_tkl_ast` | 0.943 | 263 |
| 109 | `idp_tkl` | 0.911 | 315 |
| 99 | `idp_sack` | 1.000 | 51 |
| 95 | `idp_int` | 1.000 | 22 |
| 106 | `idp_ff` | 1.000 | 18 |
| 96 | `idp_fum_rec` | 1.000 | 9 |
| 113 | `idp_pass_def` | 1.000 | 96 |
| 97 | `idp_blk_kick` (provisional) | 1.000 | 2 |

  Tackle ids agree about 94% of the time rather than 100%: the two sites' official-stat feeds differ slightly, which doesn't matter for projections. Ids 94, 103 and 105 each matched one defensive TD (too few to confirm); 98 (likely safeties) had no events. Ids 100, 110–112, 155, 156 and 210 match no Sleeper key.
- **SADL keys ESPN doesn't project:** `idp_tkl_loss` (TFL, 2 pts) and `idp_qb_hit` (1 pt) are imputed for ESPN rows (§6.2). Defensive TDs, safeties and blocked kicks are too rare to confirm and are imputed the same way until confirmed.

**Archive integrity (checked 2026-10-13/14)**
- **Sleeper:** 42 of 42 Monday-night BUF/LAR rows saved 6 minutes before week 5's kickoff equal Sleeper's archive after the game, and the 581 week-5 rows read late on Monday also match. Evidence is on the `integrity-checks` branch. Sleeper rewrites projections before games (37 of 46 changed in the 15 hours before kickoff), never after.
- **ESPN:** 37 of 37 live week-5 Monday-night rows, last fetched 4 hours before kickoff, equal ESPN's `scoringPeriodId=5` archive read on Oct 14, after the game.
- **FantasyPros:** passes by timestamp, because only rows observed before a player's kickoff are kept (§5.3).

**FantasyPros access (D9)**
- The login page (`secure.fantasypros.com/accounts/login/`) runs **Cloudflare Turnstile**, a browser human-check, plus a hidden `token` field. Scripted auto-login will very likely fail; it's untested because no credentials are configured. Logged-in session cookies are named `fpjwt`, `fptoken`, `fpuserinfo`, `fp_level`.
- The weekly ECR rankings don't need a login at all. Login only unlocks the full stat projections.

**Owner decisions (closing Phase 1)**
1. FantasyPros: free ECR rankings only; no FP projections, login or cookie (D9).
2. ESPN: projections through the owner's public league 942104146 with IDP enabled; verified by probe run 4 (above).
3. The Odds API: deferred until after the MVP.

**Answered 2026-10-12:** the free FantasyPros ECR rows are committed like other public sources (D16 closed).

**Still open (before Phase 5, D18):** which odds signal(s): game lines, props, or both; how they enter the tiers (an adjustment to μ, a separate projection source, or display only); and whether to get an Odds API key for props.

**Source plan:** the MVP uses Sleeper projections only (D17). Phase 3 adds FantasyPros ECR and RotoBaller (rank group) and the ESPN league (projection group), with the 60:40 group prior.

**Kicker probe (2026-11-17, D23)**

Evidence: live requests on Tuesday 2026-11-17, about 9 PM ET, in NFL week 11, from a home connection, logged out, under a research user agent, with each host's robots.txt read first. The probe ran nothing from Actions or under the pipeline's own user agent; the pipeline's client read the six sources once later that evening (§10). The raw responses are kept outside the repo; the trimmed ones the tests use are in `tests/fixtures/`. "Verified" below means read in a live response or computed from one.

- **League:** one K starter; `fgm_yds` 0.1, `fgmiss` −1, `xpm` 1, `xpmiss` −1; `fgm_0_19`, `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50_59` and `fgm_60p` all 0. No `fgm_50p`, `xpa` or per-bucket miss key is scored. No `position_limit_k`.
- **Rosters (week 11):** your roster held 27 of 27 active spots (32 players less 3 on IR and 2 on taxi), with two kickers. 19 kickers were rostered league-wide, and 10 of Sleeper's 26 projected kickers were on no roster.
- **Sleeper kicker projections** (`position[]=K`; RotoWire data):
  - **Rows:** 157 in week 11: 153 with `position` K and 4 punters listed ["K", "P"]; 26 real, the rest placeholders whose stats are only an `adp_*` key. The payload has no `full_name` and no `espn_id`.
  - **Keys on every real row:** `fga`, `fgm`, `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50p`, `fgm_yds`, `fgmiss_50p`, `xpa`, `xpm`, `xpmiss`, `gp`. On most: `fgmiss_30_39`, `fgmiss_40_49`. Rare: `fgm_0_19`, `fgmiss_20_29`.
  - **Never projected:** a total `fgmiss`; `fgm_50_59` and `fgm_60p` (only the merged `fgm_50p`).
  - **Identities:** `xpa` − `xpm` − `xpmiss` is within ±0.01 on every row. The made buckets do not add up to `fgm` in 2026 (§11); they did on the five 2025 weeks read (3, 7, 10, 11, 15). `fgm_yds` is 41.2–42.9 × the sum of the made buckets on every row of both seasons.
  - **Scored, week 11:** 6.57 to 10.59 SADL points, mean 8.49 with `fgmiss` = `fga` − `fgm`. Scoring the raw row with no miss drops about 0.33 points a kicker.
  - **Archive:** week 10 of 2026 and week 10 of 2025 were served with the same keys. A past-week row (2025 week 11) carried an `updated_at` after its games, so the archive can't be shown to be pre-game from the response alone.
- **Sleeper kicker results** (`/v1/stats`, the pipeline's own endpoint; weeks 5–9 read, and the `position[]=K` stats endpoint for week 10 and the 2025 season):
  - **Keys:** `fga`, `fgm`, `fgm_yds`, `fgmiss`, `xpa`, `xpm`, `xpmiss`, the made and missed buckets (`fgm_50_59`, `fgm_60p` and `fgm_50p` all present), `fgm_lng`, `fgm_yds_over_30`. A key whose value is 0 is omitted. `fgm_yds` is on every row with a make.
  - **Identities, zero violations** on 173 kicker-week rows (2026 weeks 5–10) and 42 season rows (2025): `fgm` = the sum of the six made buckets; `fgm_50p` = `fgm_50_59` + `fgm_60p` (never add both); `fga` = `fgm` + `fgmiss`; `xpa` = `xpm` + `xpmiss`; `fgmiss` = the sum of the miss buckets, with blocked kicks counted as misses.
  - **Trap:** each week's file also holds 28–30 `TEAM_<ABBR>` rows with team-total `fga`, `fgm` and `fgm_yds`. A kicker is chosen by position, never by stat key.
  - **Levels, 2026 weeks 5–10** (173 kicker-games): 1.78 makes, 0.35 misses, 70.3 made yards (39.5 a make), 2.15 extra points and 0.11 missed; 8.72 SADL points a game, SD 5.20.
- **ESPN league 942104146, kickers:**
  - **Slot 17 and position 5 are the kicker:** the `filterSlotIds` [17] request returned 58 players, every one with `defaultPositionId` 5 and `eligibleSlots` containing 17. Readable logged out, one page.
  - **Week 11:** 44 of the 58 had a week-11 projection entry; 25 of those had stats and 19 had `stats: {}`. ESPN projected 25 kickers against Sleeper's 26.
  - **Stat ids,** each verified two ways: on week-10 actual rows matched by name to Sleeper's week-10 results (29 of 29 kickers agree on every mapped id), and by arithmetic that holds on every projected row (25 of 25) and actual row (29 of 29):

| ESPN stat id | Meaning | Sleeper key |
|---|---|---|
| 83 / 84 / 85 | field goals made / attempted / missed | `fgm` / `fga` / `fgmiss` |
| 86 / 87 / 88 | extra points made / attempted / missed | `xpm` / `xpa` / `xpmiss` |
| 214 | total yards of made field goals (29 of 29 exact) | `fgm_yds` |
| 80 / 81 / 82 | made / attempted / missed, under 40 yards | `fgm_0_19` + `fgm_20_29` + `fgm_30_39` (stored as `fgm_0_39`) |
| 77 / 78 / 79 | made / attempted / missed, 40–49 | `fgm_40_49` |
| 74 / 75 / 76 | made / attempted / missed, 50+ | `fgm_50p` |
| 198 / 199 / 200 | made / attempted / missed, 50–59 (in projections a copy of 74–76) | `fgm_50_59` |
| 215 / 216 | yards of missed / of attempted field goals | none |
| 210 | games played | `gp` |

  - **Arithmetic that holds on every row:** 74 + 77 + 80 = 83; 83 + 85 = 84; 86 + 88 = 87; made + missed = attempted in each bucket; 214 + 215 = 216.
  - **Projected 214 is a formula:** 30.0 × [80] + 45.0 × [77] + 54.0 × [74], exact on all 25 rows, with the same bucket shares for every kicker (54.8% / 25.7% / 19.5%). Those weights are sound: on real bucket counts they give the true yards within 0.5%.
  - **Not verified:** ids 201–203 (presumably 60+; no such kick in week 10). `robots.txt` on this host returns 403 from the load balancer; the data calls return 200.
  - **The two sources disagree:** over the 25 kickers both projected in week 11, ESPN averaged 9.35 SADL points and Sleeper 8.53, with a correlation of 0.64.
- **Mean distance of a made field goal, by bucket** (every regular-season make in nflverse's weekly kicker stats; each row's list of distances sums to its made yards, zero failures; 2025 matches Sleeper's season totals exactly: 931 makes, 39.18 yards):

| Season | Makes | All | 0–19 | 20–29 | 30–39 | 40–49 | 50–59 | 60+ |
|---|---|---|---|---|---|---|---|---|
| 2022 | 903 | 37.91 | 19.00 (4) | 24.96 (233) | 34.54 (265) | 44.53 (247) | 52.93 (149) | 60.60 (5) |
| 2023 | 911 | 37.65 | 19.00 (1) | 25.11 (239) | 34.64 (294) | 44.13 (219) | 53.10 (153) | 60.80 (5) |
| 2024 | 937 | 38.53 | 19.00 (2) | 25.20 (229) | 34.36 (280) | 44.38 (231) | 53.24 (191) | 62.25 (4) |
| 2025 | 931 | 39.18 | 19.00 (4) | 25.58 (202) | 34.47 (278) | 44.36 (264) | 53.71 (171) | 62.83 (12) |
| 2026, weeks 1–10 | 530 | 39.63 | 19.00 (1) | 25.50 (109) | 34.46 (154) | 44.42 (153) | 53.77 (109) | 61.00 (4) |
| 2024–26 pooled | 2,398 | 39.02 | 19.00 (7) | 25.40 (540) | 34.42 (712) | 44.38 (648) | 53.53 (471) | 62.35 (20) |

  - **50 and up as one bucket** (what Sleeper, CBS and ESPN publish): 53.89 over 491 makes. **Under 40 as one bucket** (ESPN): 30.47 over 1,259.
  - **The drift is in the mix, not within a bucket:** the average make went from 37.9 yards in 2022 to 39.6 in 2026 while each bucket's mean barely moved, so fixed per-bucket constants hold.
- **Decimal against standard scoring** (nflverse weekly kicker stats 2022–2026, 2,448 kicker-games; standard = 3/4/5 by distance, −1 a miss):
  - **Actual results:** the two scorings correlate 0.992 game by game. Within a week the mean rank correlation is 0.988 (lowest 0.969), and the two top 12s share 11.3 kickers on average.
  - **Season ranks:** 0.967 to 0.987 a season (2022–25); 2 to 5 kickers a season move three or more places.
  - **Inside a source, week 11:** rescoring to decimal against the source's own points gives a rank correlation of 0.998 for Sleeper and 0.957 for CBS.
  - **So a standard-scoring kicker ranking is used as it is.** A kicker's average made distance is not a stable trait (year-to-year correlation 0.13, 74 kickers), so ranks are not adjusted for a "big leg".
  - Decimal pays 0.34 points a game more on average, with an SD of 0.70.
- **List depths and posting days:**

| Source | Kickers listed | Posted |
|---|---|---|
| Sleeper | 26 real rows (week 11), 27 (week 10) | up by Tuesday |
| ESPN | 25 with a projection of 58 returned (week 11), 28 (week 10) | up by Tuesday |
| CBS | 25 (week 11) | up by Tuesday; current week only |
| FantasyPros | 26 (week 11), 31 (week 9 archive) | 9 experts by Tuesday 8 PM ET; 42 by Sunday of week 9 |
| FantasyData | 29, of which 25 with a projection (week 11) | showing week 11 on Tuesday evening; no archive |
| RotoBaller | 26 (week 11), 30 (week 9) | Tuesday 7:09 AM ET (week 11), Tuesday (week 10), Thursday (week 8); week 9 carried a Saturday date after an update |

- **Ids for kickers:** the DynastyProcess crosswalk has 282 kicker rows; of week 11's 26 projected kickers it has 23, 21 with a FantasyPros id and 23 with an ESPN id. RotoBaller's player links carry the FantasyData id (23 of 23 linked week-11 kickers), but not every row is linked.
- **Not determined:** whether the four archives (Sleeper, ESPN, FantasyPros, RotoBaller) return pre-game numbers for kickers, which is why their rows are untrusted (§6.5); how the six sources answer a runner under the pipeline's user agent (read once since: all six `ok` in the branch's preview sync from Actions, 2026-11-17, §10); how many kickers carry `espn_id` and `fantasy_data_id` in Sleeper's players DB; exact agreement of a kicker's points with Sleeper's `players_points`; the weekday each source first posts a new week's kicker list.
