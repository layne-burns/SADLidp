# SADL IDP streamer: open goals (updated 2026-10-14)

The two working sessions are now one. Merged on `main`:
- #2–#5: MVP; locked suggestions and results; Stats page with per-player breakdown; ESPN.
- #7: FantasyPros.
- #8: tier tuning.
- #9: methods review.
- #10: 2025 backtest harness.
- #11: form term.
- #12: per-player swing with floor–ceiling.

The live page updates itself after every merge that changes code or config.

Data review (weeks 1–5): a private report for the owner. Findings, in short:
- Suggestions paid off 21 of 25.
- The blend beats a player's season average at every position.
- Tiers work at LB; at DL and DB they are noise so far.
- The three sources tie on accuracy.
- The floor–ceiling ranges are the right width.
- Sleeper's archive passed the integrity check.

## 1. Week-5 frozen gap (#14, merged)
- [x] `sadlidp replay --fill-frozen`, and week 5 filled: 581 rebuilt records, 50 live records kept.
- [x] Snap share by role tested on 2025 and rejected: it predicted worse.

## 2. Interface PR (owner's asks, 2026-10-14; #15)
- [x] Stats page views: Last week / All season / any single week.
- [x] Weekly report card at the top of the main page:
  - your players vs projection;
  - suggestions paid off x of y;
  - biggest hit and miss.
- [x] DB note: projections can't separate waiver DBs (2026 r 0.04, 2025 r 0.01); lean on snaps and form.
- [x] Plainer card wording for the form term and the floor–ceiling.

## 3. PR 6: accuracy weights + team-defense backtest
- [x] 6a, accuracy weights (#16, merged): week 7 on, the source shares follow accuracy (expected: projections 70%, rankings 30%; Sleeper and ESPN about equal). Recorded per week in `weights.json`.
- [x] ESPN archive integrity: passed, 37 of 37 Monday-night rows.
- [x] 6b, team-defense backtest: neither D/ST rank nor opponent implied points helps (2025 and 2026 weeks 1–5); not used.
- [ ] Checkpoints at week 9 and week 13:
  - re-run `sadlidp backtest dst` with 2026 added;
  - re-fit form, swing and z on 2025 + 2026 (adopt only if held-out improves).

## 4. PR 5: RotoBaller + FantasyData + Thursday window
- Spec drafted at `../SADLidp-pr5` (ffdd6d6) on an old base; rebuild it on `main`, where FantasyPros is `fp_ecr`.

## 5. Research (2025 archive first)
- Does the form term still help for players whose projections already moved after their big games?
  Greg Rousseau is the example.

## How parameters get tuned (owner question, 2026-10-14)
Nothing re-fits itself today:
- the form and swing coefficients were fitted on 2025;
- z was set with `sadlidp tune`;
- the source shares are priors.

Plan:
- **Weekly, automatic (PR 6):** only the source weights. They are shrunk toward the priors, lagged two weeks and recorded.
- **Checkpoints (around week 9 and week 13):** re-fit the form, swing and z on 2025 + 2026 pooled. A new value is adopted only if it beats the current one on held-out weeks.
- **Never:** a weekly re-fit of everything. Five weeks is about 250 games per position, too few to re-fit without chasing noise.

## Owner questions still open
- Vegas odds (game lines, props, or both; an Odds API key): after PR 6.
