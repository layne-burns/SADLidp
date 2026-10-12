# SADL IDP streamer

Weekly IDP streaming tiers for SADL ("Slow Ass Draft League") on Sleeper. Each sync pulls IDP projections, rescores them with SADL's own `scoring_settings`, blends the sources into a consensus with an uncertainty for each player, and publishes tiers of the DL, LB and DB actually available in SADL, next to START / UPGRADE suggestions for your roster.

**Page:** https://layne-burns.github.io/SADLidp/

The design and every decision behind it are in [SPEC.md](SPEC.md). Three sources so far: two projections (Sleeper's own and the owner's public ESPN league, read logged out) and one ranking (FantasyPros' free weekly expert consensus for DL, LB and DB). No secrets. RotoBaller and FantasyData come next.

## One-time setup
1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Actions → sync → Run workflow** (branch `main`) for the first sync. It finds the league, backfills this season's earlier weeks from every source that still serves them (Sleeper, ESPN and FantasyPros do), commits `data/` and deploys the page. The first sync also builds the imputation priors from last season's stats (`data/<last season>/impute_priors.json`), once.

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
- **Week N · data as of …:** a yellow banner appears when the data is more than 36 hours old.
- **Points** are SADL points, the blend of the sources: projections (Sleeper and ESPN) get 60% of a player's number and rankings (FantasyPros) 40% when both have him. A ranking becomes points by giving rank N the Nth-best projection among the players on that list; a player the list leaves out, but who projects near its end, is counted just past its end. **±** is how much the sources disagree about a player, not week-to-week luck; a player only one source projects gets a default.
- **Where a number comes from:** tap a player to see each source's points for him, the projected stats behind them, and its share of his number (equal shares within projections and within rankings for now); for FantasyPros, his rank on its list. A source with nothing for him says why. Stats marked \* are our estimates: ESPN doesn't project TFL, QB hits, defensive TDs or safeties, so those come from the player's own rates this season, shrunk toward last season's (SPEC §6.2).
- **How the numbers did →** (the Stats page): how far off the projections were each week and season to date, by position, against a naive guess (each player's own average), plus whether the START/UPGRADE odds held up. Each source is also scored on the same players, picked by all sources together, with how many of them it covered. Small samples say "not enough games yet".
- **Tiers** group players who are statistically indistinguishable from the tier's top player (`z = 1` in the config). Where the sources disagree, the ± is wider and so are the tiers.
- **My IDPs** places your DL, LB and DB against the waiver tiers.
  - **START:** a waiver player has at least a 60% chance to outscore your projected starter.
  - **UPGRADE:** the same test against your other player at that position.
  - These odds include game-to-game spread (σ<sub>out</sub>, measured from last season's actual SADL points). The suggested move says whether to add only, add and drop, or choose the drop yourself.
  - **For reference** (muted, sorted last): shown even when the move can't be made this week, with the reason: your starter's game has started (SADL starts one player per position, so nothing added there can score), or the player you'd drop is locked.
- **Scores:** once a game starts, cards show SADL points so far, then the final score (a game counts as final 4 h after kickoff). "Already played this week" lists the waiver players whose games have started, and "This week's suggestions that have played" shows each suggestion's result: ✓ paid off, ✗ didn't, or void if one of the two didn't play. A suggestion you followed counts too. A projection first captured after kickoff is marked \*.
- **Last week:** a recap of last week's suggestions with final scores, provisional until Sleeper's stat corrections close (Friday night).
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
- **Footer:** each source's status, how many players it covers, how many of its players were matched to Sleeper (with the unmatched ones listed), and when it last succeeded. If a source fails, the page keeps its earlier projections and says so; if it has none for the week, the page uses the other sources.
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
uv run sadlidp backtest run --season 2025                # last season's Sleeper archive (data/2025/archive): skill, form, swing, sacks
uv run sadlidp tune --season 2026                        # re-tier this season's settled weeks under candidate z and splits
uv run sadlidp gate
```
Pushes to `claude/**` branches also run a live preview in CI: the branch's committed data is copied to a scratch directory, a forced sync runs on top of it, and the site is published to the `previews/<branch>` branch, which each run of that branch replaces.

## Layout
| Path | What |
|---|---|
| `src/sadlidp/` | the sync: `sleeper`, `schedule`, `sources/` (`sleeper_proj`, `espn_proj`, `fp_ecr`, `fp_dst`), `crosswalk`, `rankmap`, `matching`, `impute`, `backfill`, `scoring`, `consensus`, `tiers`, `roster`, `gate`, `site/` |
| `config/sadl.toml` | all tunables (SPEC §9) |
| `config/player_overrides.toml`, `config/aliases/` | manual source → Sleeper player fixes, team code and nickname tables (SPEC §6.1) |
| `data/<season>/` | `league.json`, `schedule.json`, `gate.json` (windows already run) |
| `data/<season>/week<NN>/` | `consensus.json` (what the page shows), `run.json` (with each source's match report), `rosters.json`, `players.json` (each player as of his kickoff), `actuals.json`, `sources/<source>.json` (projections and ranks frozen at kickoff; `fp_dst.json` holds FantasyPros' D/ST ranks, kept for a later test and never blended; `backfill: true` rows came from an archive), `frozen.json`, `badges.json`, `tiers_log.json` |
| `probe/` | the Phase 1 data-source probe (SPEC §13) |
