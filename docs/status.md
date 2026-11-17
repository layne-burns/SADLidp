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

## 3. PR 6: accuracy weights + team-defense backtest (#16, #17, merged)
- [x] 6a, accuracy weights (#16, merged): week 7 on, the source shares follow accuracy (expected: projections 70%, rankings 30%; Sleeper and ESPN about equal). Recorded per week in `weights.json`.
- [x] ESPN archive integrity: passed, 37 of 37 Monday-night rows.
- [x] 6b, team-defense backtest: neither D/ST rank nor opponent implied points helps (2025 and 2026 weeks 1–5); not used.
- [x] **Week-9 checkpoint (2026-11-17).** Re-tested on 2025 + 2026 weeks 1–9, fit on odd weeks and scored on even weeks.
  - **Health:**
    - every sync run succeeded (the last 100);
    - every source matched ≥ 99.7% of its players each week;
    - weights recorded weeks 7–10; the projection share went 0.67 → 0.63 → 0.64 → 0.59.
  - **Team-defense idea:** still no signal.
    - 2025 unchanged.
    - 2026 weeks 1–9: every |t| < 2. LB D/ST rank is −0.10 (t −1.4), the opposite sign to 2025.
    - Held-out miss is worse at DL (5.57 → 5.80) and DB.
  - **Form term:** kept. It still lowers the held-out miss (DL 6.23 → 6.01, LB 5.77 → 5.65, DB 4.95 → 4.92), and the current coefficients beat a re-fit at every position.
  - **Swing:** DL re-fitted to a 4.3, b 2.23 (was 0, 2.5). Better on held-out weeks in both directions, by 0.035 ± 0.024 log-likelihood per player-game. LB and DB stay (within noise).
  - **Tier z:** stays at 0.75 (every z within noise).
- [ ] **Week-13 checkpoint (Tue 2026-12-15, 09:30 ET):** same checks on weeks 1–13, plus the pre-registered Rousseau test below.

## 4. PR 5: RotoBaller + FantasyData + Thursday window (this PR)
- [x] RotoBaller adapter (RSS discovery, three tables, nickname teams, published_at drop); weeks 1–5 backfill live.
- [x] FantasyData adapter (100 per list, 100% matched by `fantasy_data_id`); week 6 live.
- [x] Rank-group priors FantasyPros 2 : RotoBaller 1 : FantasyData 1.
- [x] Late-source status (`expected_by`).
- [x] Thursday window, 11:00 ET to 1 h before TNF.
- [x] Thursday windows captured RotoBaller's article every week from 6 to 10, first read between 12:20 and 2:27 PM ET, before kickoff.

## 5. Research (2025 archive first)
- Does the form term still help for players whose projections already moved after their big games?
  Greg Rousseau is the example.
  - **Week-9 result:** no change.
    - LB and DB show nothing.
    - DL hints at it: the form coefficient is lower for DL players who beat their projections and whose projection has since risen 1+ point (2025: −0.86 ± 0.96 lower; 2026: −0.59 ± 0.70).
    - Neither season's difference is clear of noise, and the held-out gain is 0.015 MAE.
  - **Pre-registered for week 13:** DL only, the same definition, 2025 + 2026 weeks 1–13 pooled. Drop the form term for those players only if pooled |t| ≥ 2 and held-out improves in both seasons.

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
