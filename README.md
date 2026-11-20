# SADL streamer: IDP and kickers

Weekly streaming tiers for SADL ("Slow Ass Draft League") on Sleeper: the three IDP positions and, since 2026-11-17, the kicker. Each sync pulls IDP projections, rescores them with SADL's own `scoring_settings`, blends the sources into a consensus with an uncertainty for each player, and publishes tiers of the DL, LB and DB actually available in SADL, next to START / UPGRADE suggestions for your roster. It does the same for kickers, from kicker sources of their own.

**Page:** https://layne-burns.github.io/SADLidp/

The design and every decision behind it are in [SPEC.md](SPEC.md). Five IDP sources: two projections (Sleeper's own and the owner's public ESPN league, read logged out) and three rankings (FantasyPros' free weekly expert consensus, RotoBaller's Thursday-morning IDP article and FantasyData's weekly lists, each for DL, LB and DB). Kickers have six sources of their own: three projections (Sleeper, ESPN and CBS) and three rankings (FantasyPros, FantasyData and RotoBaller's weekly kicker article). The two sets are blended, weighted and graded apart, so nothing about kickers can move a DL, LB or DB number. No secrets.

## One-time setup
1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Actions → sync → Run workflow** (branch `main`) for the first sync. It finds the league, backfills this season's earlier weeks from every source that still serves them (Sleeper, ESPN, FantasyPros and RotoBaller do, for kickers too; FantasyData doesn't, and neither does CBS, which is a kicker source only), commits `data/` and deploys the page. The first sync also builds the imputation priors from last season's stats (`data/<last season>/impute_priors.json`), once.

No secrets are needed. The sync pushes its data commits straight to `main`, so if you ever protect `main`, let GitHub Actions push to it.

## The sync button and the schedule
- **Button:** Actions → sync → Run workflow. It also works from the GitHub mobile app, and it always syncs.
- **New code:** a merge to `main` that changes code or config also syncs and redeploys the page.
- **Automatic:** the workflow wakes every 3 hours (hourly on Sunday mornings), but it only syncs once in each of these windows per week:
  - **Pre-waiver:** from 12 h to 1 h before SADL's waivers process at 00:00 Pacific on Wednesday, so Tuesday noon to 11 PM PT.
  - **Saturday:** 12:00 to 6:00 PM ET, after the final injury reports.
  - **Sunday:** from 4 h to 15 min before the first Sunday kickoff, including 9:30 AM ET international games.
  - **Sunday evening:** from 4¼ h after the last afternoon kickoff, for 6 h, so the page shows the afternoon's final scores.
- **When a sync fails,** nothing is deployed and the previous page stays up. The run's log is under Actions.
- **To check the windows,** run `uv run sadlidp gate`. It prints this week's windows and whether one is due.

## Reading the page
The page runs top to bottom in the order you need it: what to do this week, then one panel per position, then folded sections for everything else.
- **Header:** the season, the week and the time of the data; the league name and the full "data as of …" line are at the bottom of the page. A yellow banner appears when the data is more than 36 hours old. The week picker shows the previous, current and next week; the rest are under "all weeks".
- **This week:** the card at the top.
  - Every move the page suggests, across positions, with its odds and what to add and drop.
  - "No move suggested at: …" for the positions with none.
  - Heads-up lines: a player of yours who is out or questionable, a starter whose game has started, a bye coming up.
  - Your projected starters, and a line about last week that jumps down the page.
  - It only repeats what the panels below say.
- **Position bar:** DL · LB · DB · K. It stays in view while you scroll, and it works with JavaScript off. Each position has one panel:
  - **Yours:** your players there, placed against the waiver tiers, with any suggestion.
  - **Available:** the waiver tiers.
  - A week saved before kickers were added shows no K.
- **Points** are SADL points, the blend of the sources: projections (Sleeper and ESPN) get 60% of a player's number and rankings 40% when both have him; within rankings FantasyPros counts double (it is a 26-expert consensus), so 20/10/10. From 3 scored weeks on, the shares follow each source's measured accuracy (the Stats page shows them week by week). A ranking becomes points by giving rank N the Nth-best projection among the players on that list; a player the list leaves out, but who projects near its end, is counted just past its end. **±** is how much the sources disagree about a player, not week-to-week luck; a player only one source projects gets a default.
- **Where a number comes from:** tap a player to see each source's points for him, the projected stats behind them, and its share of his number ; for a ranking, his rank on its list. A source with nothing for him says why. Stats marked \* are our estimates: ESPN doesn't project TFL, QB hits, defensive TDs or safeties, so those come from the player's own rates this season, shrunk toward last season's (SPEC §6.2).
- **Form:** once a player has played this season, his number also carries his earlier misses (how far his final scores ran above or below what the page projected, shrunk toward 0 by n/(n+4)) and his last game's share of his team's defensive snaps, relative to the position's 60 best-projected players; the coefficients come from Sleeper's 2025 season (`sadlidp backtest run`, SPEC §6.4). His breakdown shows both parts and ends "with form ±x".
- **How the numbers did →** (the Stats page): how far off the projections were each week and season to date, by position, against a naive guess (each player's own average), plus whether the START/UPGRADE odds held up. Each source is also scored on the same players, picked by all sources together, with how many of them it covered. Small samples say "not enough games yet". Kickers are graded there as their own rows, on their own smaller pool (the 20 best-projected kickers who played), and are never pooled with DL, LB and DB.
- **Tiers** group players who are statistically indistinguishable from the tier's top player (`z = 0.75` in the config). Where the sources disagree, the ± is wider and so are the tiers. Under a waiver player, "sources say a to b (n)" is the lowest and highest of his n sources' numbers.
- **Yours** (the old My IDPs panel, now inside each position) places your DL, LB and DB against the waiver tiers. Your kickers get the same treatment in the K panel.
  - **START:** a waiver player has at least a 60% chance to outscore your projected starter.
  - **UPGRADE:** the same test against your other player at that position.
  - These odds include game-to-game swing: each player's own, from his projected stat mix (a sack is 6 points and a solo tackle 2, so a sack-heavy line swings more; fitted on Sleeper's 2025 season, SPEC §6.7), or the position's typical spread (σ<sub>out</sub>) when no source projects his stats. The suggested move says whether to add only, add and drop, or choose the drop yourself.
  - **Floor–ceiling:** the small "range a–b" under a player's points. One game in five lands below the floor, one in five above the ceiling; the breakdown says his swing.
  - **For reference** (muted, sorted last): shown even when the move can't be made this week, with the reason: your starter's game has started (SADL starts one player per position, so nothing added there can score), or the player you'd drop is locked.
- **Scores:** once a game starts, cards show SADL points so far, then the final score (a game counts as final 4 h after kickoff). "Already played this week" lists the waiver players whose games have started, and "This week's suggestions that have played" shows each suggestion's result: ✓ paid off, ✗ didn't, or void if one of the two didn't play. A suggestion you followed counts too. A projection first captured after kickoff is marked \*.
- **Kickers** (the K panel):
  - **Sources:** six of their own. Projections from Sleeper, ESPN and CBS; rankings from FantasyPros, FantasyData and RotoBaller's weekly kicker article. They start at the same shares as the IDP sources (projections 60%, rankings 40%, FantasyPros double among the rankings), and their shares follow their own measured accuracy, apart from the IDP ones.
  - **Scoring:** SADL scores a kicker by the yard: 0.1 per yard of each made field goal, −1 per missed field goal, +1 per extra point, −1 per missed extra point. Each projection is rescored that way from its stat line. A standard-scoring kicker ranking is used as it is: the two scorings order kickers almost identically.
  - **Stats marked \*** on a kicker are worked out from the same source row, because the source doesn't publish them: Sleeper's missed field goals (attempts − makes), and CBS's missed field goals, missed extra points and made yards (its makes in each distance band × the average made distance in that band). ESPN publishes everything that is scored.
  - **No form** is added to a kicker's number.
  - **Kicker tiers are rough.** A kicker's week swings by about 5 points, while different kickers' projections sit within a point or so of each other. A kicker projected 1 point higher outscores the other only about 56% of the time, and the K panel says so. Expect few kicker suggestions.
  - **Range:** a kicker shows a floor–ceiling only once the kicker swing model is written (`swing.kicker_var`, left to the owner). Until then his odds use the typical kicker swing, 5.0 points.
  - **Moves:** the league sets no limit on kickers, so only roster room counts. With a free roster spot you just add; with a full roster you drop your other kicker, or swap the new one for your starter.
- **Folded sections,** below the positions; tap one to open it:
  - **Last week:** the week's report card (your IDPs' projected and actual points, the suggestions that paid off, your biggest beat and shortfall; each kicker of yours is listed after them on his own line and is never added into the IDP total), then a recap of last week's suggestions with final scores, provisional until Sleeper's stat corrections close (Friday night).
  - **Waiver notes:** how often a waiver player tagged questionable on Tuesday sat out, and how IDP waiver claims have gone this season.
  - **How to read this page:** the short version of this section.
  - **Sources:** see below.
- **Weeks:** every synced week keeps its page, linked at the top. A past week is re-scored with its final stats. Weeks 1–4 were rebuilt after the fact from the projections saved for them and that week's rosters (`sadlidp replay --weeks 1-4 --season 2026`, which reads every source file saved for the week), and say so.
- **Tags:**
  - Q: questionable.
  - "also LB": dual eligible. The player is listed under the position Sleeper counts toward the 2-per-position limit.
  - IR and taxi.
  - The reason a player can't play: bye, Out, locked, and so on.
- **Hidden from the waiver tiers:**
  - Out, Doubtful, IR, PUP, NFI and suspended players.
  - Inactive players, and players with no team.
  - Teams on bye, and anyone whose game has started.
- **Sources** (folded, at the bottom; its heading says how many are in): each source's status, how many players it covers, how many of its players were matched to Sleeper (with the unmatched ones listed), and when it last succeeded, grouped into IDP sources and kicker sources. If a source fails, the page keeps its earlier projections and says so; if it has none for the week, the page uses the other sources.
- **A source's silence:** a player a source could have listed but didn't counts as 0 from it, but only if the source was first read before his game; a source first read after kickoff says nothing about him (SPEC §5.3).

## Each season
- **In late August,** re-enable the schedule: Actions → sync → Enable workflow, then Run workflow once. GitHub turns off scheduled workflows in public repos after 60 days without activity.
- **The league is found by name** (`[league] names` in `config/sadl.toml`) among `lbburnsy`'s leagues for the new season. If the name changes, set `league_id` there instead.

## Local development
```
uv sync
uv run pytest                                          # offline: tests use recorded fixtures
uv run ruff check . && uv run ruff format --check .
uv run sadlidp sync --force --data-dir /tmp/sadl       # live: needs network access to Sleeper
uv run sadlidp build --data-dir /tmp/sadl --site-dir /tmp/site
uv run sadlidp backfill --source espn_proj --weeks 1-5  # past weeks from a source's archive (--redo refetches)
uv run sadlidp backtest run --season 2025                # last season's Sleeper archive (data/2025/archive): skill, form, swing, sacks, [form] and [swing] coefficients
uv run sadlidp tune --season 2026                        # re-tier this season's settled weeks under candidate z and splits; form term off vs on
uv run sadlidp gate
```
`sadlidp backfill --source sleeper_k` (or `espn_k`, `fp_k`, `rotoballer_k`) does the same for a kicker source's archive.

**On Windows** the tests need two things (checked 2026-11-17):
- `tzdata`. Windows has no time-zone database of its own and `tzdata` is not a project dependency, so install it into the environment: `.venv\Scripts\python -m pip install tzdata`.
- `PYTHONUTF8=1`. Without it several tests fail on reading and writing files with ✓ and → in them: `$env:PYTHONUTF8 = 1`, then `.venv\Scripts\python -m pytest -q`.

Pushes to `claude/**` branches also run a live preview in CI: the branch's committed data is copied to a scratch directory, a forced sync runs on top of it, and the site is published to the `previews/<branch>` branch, which each run of that branch replaces.

## Layout
| Path | What |
|---|---|
| `src/sadlidp/` | the sync: `sleeper`, `schedule`, `positions` (the slots and the two groups, IDP and kickers), `sources/` (IDP: `sleeper_proj`, `espn_proj`, `fp_ecr`, `rotoballer`, `fantasydata`, and the `fp_dst` signal; kickers: `sleeper_k`, `espn_k`, `cbs_k`, `fp_k`, `fantasydata_k`, `rotoballer_k`; `registry` lists every source with its group), `kicker` (the kicking keys, the derived stats, past kicker results), `crosswalk`, `rankmap`, `matching`, `impute`, `backfill`, `scoring`, `consensus`, `form`, `swing`, `tiers`, `roster`, `gate`, `site/`; offline checks: `stats`, `tune`, `backtest`, `replay` |
| `config/sadl.toml` | all tunables (SPEC §9) |
| `config/player_overrides.toml`, `config/aliases/` | manual source → Sleeper player fixes, team code and nickname tables (SPEC §6.1) |
| `data/<season>/` | `league.json`, `schedule.json`, `gate.json` (windows already run) |
| `data/<season>/week<NN>/` | `consensus.json` (what the page shows), `run.json` (with each source's match report), `rosters.json`, `players.json` (each player as of his kickoff), `actuals.json` (final stat lines; `k_added_at` marks a week saved before kickers whose kicker lines were added later, with nothing else in it changed), `sources/<source>.json` (projections and ranks frozen at kickoff; the kicker sources are the `*_k.json` files; `fp_dst.json` holds FantasyPros' D/ST ranks, kept for a later test and never blended; `backfill: true` rows came from an archive), `weights.json` (the source weights in force that week, written once; its `"kickers"` key is the kicker sources' own record), `frozen.json` (each player's μ, its `base` before the form term, the term's parts and his swing, as of his kickoff), `badges.json`, `tiers_log.json` |
| `probe/` | the Phase 1 data-source probe (SPEC §13) |
