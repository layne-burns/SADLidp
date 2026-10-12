# SADL IDP streamer: open goals (written 2026-10-12 10:06 UTC, before the pause)

Work resumes at **14:30 UTC (8:30 AM owner time)**. Everything below is not done yet, in the order it will
be worked. Done so far: Phase 2 MVP (#2), locked suggestions + scored results + week picker + weeks 1–4
rebuilt (#3), Stats page + per-player breakdown + deploy-on-merge (#4). The live page updates by itself
after every merge to `main` that changes code or config.

## 1. Finish PR 3: ESPN as a second projection source (#5: reviewed, fixes pushed; merging)
State: committed and pushed on `claude/compassionate-archimedes-fqrz1j` (5e8a636), rebased on main,
204 tests pass. A live run worked: ESPN 1,511 IDPs, 99.7% matched to Sleeper, weeks 1–4 backfilled for
both sources, 50/50 blend with ESPN's estimated stats (TFL, QB hits) marked `*`.
To do:
- [ ] My own read of the diff (matching, imputation, ESPN adapter, backfill runner, observation rule).
- [ ] Fix what the integration agent flagged:
  - ESPN week files are ~450–500 KB: round the stored values.
  - `published_at` is not saved in source files.
  - 3 unmatched players (Jeremiah Moon, Victor Dimukeje, Keshawn Banks): add `player_overrides.toml` entries.
  - Week 5: ESPN was first read after most week-5 games, so ESPN's week 5 holds only the Monday-night
    players (by design: no post-game values). Say so on the Stats page.
- [ ] Adversarial review workflow (several lenses), fix the confirmed findings.
- [ ] Open the PR, CI + live preview checked, merge. **Must merge before Tue Oct 13, 3 PM ET**, so the
  pre-waiver sync captures ESPN's live week-6 rows. They are needed for ESPN's archive-integrity check.

## 2. Sleeper archive-integrity check (scheduled)
- 00:05 UTC Oct 13: snapshot of the Monday-night (BUF @ LAR) projections just before kickoff, pushed to the
  `integrity-checks` branch.
- 09:30 UTC Oct 13: compare with Sleeper's rows after the game. If they match, Sleeper's archive is
  trustworthy:
  - record it in SPEC §13;
  - switch `sigma_out.method` to `residuals` (calibrated START/UPGRADE odds);
  - allow late and backfilled rows in accuracy weights.
  If not, keep them display-only.

## 3. PR 4: FantasyPros ECR + rank → value mapping (built 2026-10-12, draft PR open)
- [x] FantasyPros DL/LB/DB ranking pages (free ECR, `?week=N` for past weeks, crawl-delay 5 s).
- [x] Crosswalk ids (DynastyProcess) and list-scoped merges.
- [x] Rank → points mapping (SPEC §6.4: V/Q curves, the two-list rule, censoring of observed players only).
- [x] Projections/rankings group split with the 60:40 prior.
- [x] FantasyPros D/ST ranks captured as a signal only (D18), never blended.
- [ ] CI + live preview checked, merge.

## 4. PR 5: RotoBaller + FantasyData + a Thursday sync window
- RotoBaller weekly IDP article:
  - found through its search RSS;
  - three 50-row tables;
  - team nicknames;
  - opponent taken from the schedule;
  - posted Thursday morning.
- FantasyData DL/LB/DB rankings (100 deep, matched by `fantasy_data_id`).
- Thursday 11:00 ET window so RotoBaller makes it in before TNF (amends D13).

## 5. PR 6: accuracy weights + full Stats + team-defense backtest
- Per-source weights from measured accuracy:
  - shrunk toward the priors with k = 4;
  - uses evaluated weeks up to N−2;
  - the weights in force each week are recorded.
- Calibrated START/UPGRADE odds.
- Weights over time on the Stats page.
- Archive-integrity gates per source.
- `sadlidp backtest dst` on 2025: does the owner's team-defense rank idea add anything? It is used only
  if it passes.

## 6. Later
- Phase 5, Vegas odds (D18). Needs owner decisions:
  - which signal: game lines, props, or both;
  - how it enters the tiers;
  - whether to get an Odds API key.
- Phase 6, polish: tune `z`, UI refinements.

## Owner questions still open
- The Vegas odds questions above (no rush; after PR 6).
