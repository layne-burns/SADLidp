# SADL IDP streamer: open goals (updated 2026-11-17)

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
- The three sources tie on average miss (MAE). In the waiver band, Sleeper orders players worst (full review, §6).
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
- [ ] **Week-13 checkpoint (Tue 2026-12-15, 09:30 ET):** the agenda in §6, written down before the data.

## 4. PR 5: RotoBaller + FantasyData + Thursday window (#18, merged)
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

## How parameters get tuned (owner question, 2026-10-14; rules fixed 2026-11-17 in `docs/eval-protocol.md`)
Nothing re-fits itself today:
- the form and swing coefficients were fitted on 2025;
- z was set with `sadlidp tune`;
- the source shares are priors.

Plan:
- **Weekly, automatic (PR 6):** only the source weights. They are shrunk toward the priors, lagged two weeks and recorded.
- **Checkpoints (week 13 and season end):** each test is registered first, judged on one primary metric, and adopted only if it beats current by 2 SE on fresh weeks, wins held out on 2025 where the archive can test it, and loses nowhere by more than 1 SE. Sleeper 2024 joins as a test season, never as fitting data.
- **Never:** a weekly re-fit of everything. Five weeks is about 250 games per position, too few to re-fit without chasing noise.

## 6. Full review (2026-11-17, owner request)
"A full analysis of the project thus far… without over-cooking the model." The review was a read-only sweep of weeks 1–9 through seven lenses, and a skeptic re-ran each finding.

**Works (beyond noise)**
- In the waiver band our number orders players better than Sleeper alone: Spearman +0.149 ± 0.039 (t ≈ 3.8).
  - Our top 3 at a position beat the rest of the shown 25 by +3.2 ± 0.8 points; Sleeper's top 3 by +1.7 ± 0.9.
  - Most of that edge comes from ESPN.
- The odds are honest: stated minus observed is +0.01 ± 0.05.
- Suggestions went 9 of 11 decisions, against 8.0 expected from the odds, at +7.9 ± 2.4 points per decision.
- The ranges are the right width: the spread ratio is 0.95–1.02.
- The form term holds on an independent season. On Sleeper 2024, LB average miss is −0.39 ± 0.09.
- Real skill shows in two places:
  - DL top-12 hits: 32 against 18.5 ± 3.4 by chance.
  - LB tiers: tier 1 beats the rest of the shown 25 by +5.2 ± 1.0.

**Noise, not to be read into**
- The published μ against the plain projection average against "before form": every difference is within ±0.07.
- Weekly swings in the average miss.
- The drift in the projection : ranking split. α̂ is 0.606 ± 0.116, so 0.6 is well inside.
- Source shares moving 3% or less.
- DB order within the band: Spearman 0.00 ± 0.10.
- Today's DL swing re-fit.

**Wrong or misleading, now being fixed (PRs 20–22)**
- **The Stats page grades a number the page never shows:** the plain Sleeper + ESPN average.
- **The tier table** scores the whole pool from week 6, and about 75% of its rows were never shown.
- **The suggestion tally overstates the evidence:**
  - "30 of 37" badges are 11 decisions, and 20 of the badges are against Bonitto;
  - the week-9 card's "7 of 8" was 2 decisions.
- **The 2025 archive** has today's team on about 30% of rows; the D/ST verdict still stands after the fix.
- **Week 5** is mostly Sleeper-only (585 of 631 records), and no warning showed.
- **Page claims to correct:**
  - "aim 68%";
  - "random would get about 3" top-12 hits;
  - a DB calibration row resting on 13 pairs.
- **The LB and DB form miss term counts persistence twice.** Its coefficients came from Sleeper's misses, but it is applied to the blend's misses:
  - persistence against Sleeper: LB .42, DB .30;
  - persistence against the blend: .05 ± .20 and −.08 ± .18;
  - the paired gap is .40 ± .06.
- **The within-group accuracy weights cannot tell signal from noise.** A shuffled, pure-noise Sleeper keeps 46% of its group; the real one keeps 49%.
- **The Tuesday page favours players who end up sitting:**
  - 9.3% of Tuesday's top 5 sat, against 3.3% for ranks 6–25;
  - 55% of players tagged Questionable on Tuesday sat.
  - Among players who played, Tuesday ranks as well as kickoff.

**Plan (owner-approved 2026-11-17), one PR each, spec first**
- [ ] PR 20: the evaluation protocol (`docs/eval-protocol.md`), the tested-and-rejected ledger (`docs/methods-review.md`) and this agenda.
- [ ] PR 21: the Stats page grades the published μ, counts decisions, scores the shown 25 tiers, and checks range coverage with honest ± everywhere.
- [x] PR 22: data hygiene:
  - **The 2025 archive's game-time team:** 4,242 rows fixed, no other value moved. The D/ST verdict stands.
  - **`sadlidp audit`:** it flags exactly week 5, which is acknowledged, plus three ESPN DL names unmatched since week 5.
  - **A banner** when a source is read mostly after kickoff.
  - **The week-5 repair:**
    - ESPN filled from its archive (37 of 37 live rows matched; 502 added);
    - FantasyPros left as is (its archive matched 23 of 31);
    - 581 rebuilt records refreshed.
- [ ] PR 23: a decision scoreboard: does each layer (Sleeper → blend → weights → form) rank the waiver band better?
- [x] PR 24: the form miss term is off at LB and DB (owner decision); snap terms and DL unchanged.
  - Replay of weeks 6–10 with it off:
    - about 1 LB and 2–3 DBs a week trade places in and out of the shown 25, at the bottom of the list;
    - 53 tier lines shift;
    - 7 top-5 places swap;
    - 3 DB badges change.
  - μ moves by about 0.6 at LB and 0.3 at DB on average, 1.75 at most.
- [ ] PR 25: decision help:
  - "changed since Tuesday" and Tuesday-Q facts;
  - a bye look-ahead;
  - league transactions with one factual FAAB line.
- [ ] PR 26: research on archives:
  - Sleeper 2024 as a test season;
  - FantasyPros 2025 ranks;
  - the least-squares source share checked on 2025 before any change;
  - a DL matchup term (opponent QB-hit rate) shadow-logged only.

**Week-13 checkpoint agenda (Tue 2026-12-15), registered 2026-11-17 before week 10 settled**

Rules: settled weeks only, and the definitions of `docs/eval-protocol.md`. Every result goes in the ledger whichever way it comes out. No variant search after seeing the data. "Keep current" is the expected answer to most items.
1. **Data audit** (`sadlidp audit`, PR 22). A failing week is excluded only by a rule written in the spec beforehand.
2. **Form miss term at LB and DB** (off since PR 24).
   - Measure: the slope of this week's miss (actual − base) on the shrunk earlier miss, n/(n+4) · mean(actual − base).
   - Sample: the top 60 by frozen base among players who played, demeaned within week, with SEs from a player-cluster bootstrap.
   - Weeks 10–13 only. A position comes back only if its slope is at least 2 SE above 0. The SE is about 0.3, so both will most likely stay off.
3. **Form miss term at DL.** One adoptable variant only, the "Sleeper-share" term:
   - Definition: the miss is measured against Sleeper's own points and multiplied by Sleeper's effective share of the player's μ. The snap term is unchanged.
   - Adopt only if, on weeks 10–13, its level-free average miss is lower and its within-week r is not lower, and the pooled weeks 3–13 difference has |t| ≥ 2.
   - "Snap only" and "0.5 × the term" are reported for information and cannot be adopted.
   - 2025 can't test this (Sleeper-only), so if adopted it carries a kill switch.
   - Also reported, not adoptable: miss coefficients fitted against a 0.6 Sleeper / 0.4 FantasyPros base on 2025 (PR 26), which feed 2027.
4. **The Rousseau DL rule**, as registered on 2026-11-17 (§5), applied to whichever DL form term survives item 3.
5. **Report only, no action:**
   - ESPN's share with and without week 1;
   - Sleeper's band correlation on its own ranks 15–60, never inside the blend's band;
   - the DL and DB snap terms against the blend (weeks 1–9: DL 3.1 ± 2.8 and DB −2.1 ± 2.7 per 100% of snaps, against 7.5 and 4.0 in the config).
6. **DL swing re-fit:**
   - Score: paired log score of 4.3 + 2.23·V against 0 + 2.5·V on 2024, on 2025 held out and on 2026 weeks 1–13.
   - Keep the re-fit only if the pooled gain is at least 2 SE; otherwise return to 0 + 2.5·V.
7. **Source weights:** if the least-squares share is adopted in PR 26, check its kill switch. Revert if the cumulative walk-forward difference against the old rule on weeks 11 and later turns against it at t ≥ +1.
8. **DL matchup (shadow).** A sanity gate only:
   - the live opponent hit rate matches the backtest;
   - no team-mapping errors;
   - the shadow average miss is not worse by more than 0.1.
   - The go/no-go comes at season end, on the pooled leave-one-season-out result.
9. **Not at week 13:** RotoBaller/FantasyData drop (one look at week 17), outcome-based tiers and P(play) from 2025 injury reports (season end).

## Owner questions still open
- Vegas player props (would need an Odds API key): untested. Game lines (spread, total, implied points) were tested and rejected (ledger in `docs/methods-review.md`), apart from the DL game total, which stays shadow-only inside the DL matchup candidate.
