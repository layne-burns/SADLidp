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
- [x] PR 23: decision scoreboard. Settled weeks 1–4 and 6–9, the 25 shown per position:
  - **Our number ranks them better than Sleeper alone:** +0.14 ± 0.10 rank correlation.
  - **Most of the gain is ESPN's:** ESPN alone is +0.16 ± 0.15.
  - **Top-3 lift:** our top 3 beat the list by +3.5 ± 1.4 pts; Sleeper's by +1.7 ± 1.2.
  - **Tuesday vs kickoff:** Tuesday's top 5 beat the list by +1.2 ± 2.2, kickoff's by +3.5 ± 1.2. 4 of the 60 Tuesday top-5 players didn't play.
- [x] PR 24: the form miss term is off at LB and DB (owner decision); snap terms and DL unchanged.
  - Replay of weeks 6–10 with it off:
    - about 1 LB and 2–3 DBs a week trade places in and out of the shown 25, at the bottom of the list;
    - 53 tier lines shift;
    - 7 top-5 places swap;
    - 3 DB badges change.
  - μ moves by about 0.6 at LB and 0.3 at DB on average, 1.75 at most.
- [x] PR 25: decision help:
  - **"Changed since Tuesday's list"** on each position's tab after the pre-waiver page: Out or Doubtful, newly Q, claimed, μ moves of 1.5 or more, and new top-5 entries.
  - **Tuesday-Q facts:** 62 of 112 waiver players tagged Q on a pre-waiver page sat out, against 5.7% untagged.
  - **Bye look-ahead** for your players over the next 3 weeks.
  - **One move per player of yours:** e.g. 17 START badges over Kyler Gordon become one, plus "same move: …".
  - **League transactions saved each sync.** FAAB line, as shares of the starting budget ($100 here): 16 of 24 IDP claims were won with a token bid (1% or less); 4 contested (0–15%); 24 free-agent IDP adds, most on a Wednesday.
- [x] PR 26 (part 1): Sleeper 2024 is a test season (`sadlidp backtest cross`). The least-squares share for the projection pair passed its pre-registered 2025 check and is adopted (ESPN about 66 : Sleeper 34 from week 12), with a week-13 kill switch.
- [x] PR 26 (part 2):
  - **FantasyPros 2025 weekly ranks stored once.** The three registered questions, guarded (players whose list was updated after their kickoff left out; 1,345 of 4,681):
    - Sleeper's share against FantasyPros: 0.42 ± 0.07, about even;
    - at LB: 0.28 ± 0.13, leaning FantasyPros as in 2026, but within 2 SE, so no per-position share;
    - the form miss fitted against a 0.6 Sleeper / 0.4 FantasyPros base: DL 0.31, LB 0.32, DB 0.09, against 0.43 / 0.40 / 0.20 on Sleeper.

    All feed 2027.
  - **The DL matchup** is measured after the fact (`sadlidp backtest matchup`); the season-end rule is in the week-13 agenda, item 8.

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
7. **Source weights: the least-squares share's kill switch** (adopted in PR 26, live from week 12).
   - **Measure:** for each settled week from 11 on, take the Sleeper/ESPN blend at the least-squares shares and at the error-ratio shares. Both are computed walk-forward from weeks ≤ N − 2, exactly as `accuracy.weights` would.
   - **Sample and score:** the top-50 pool, squared error demeaned within position-week, and the per-week difference (least squares − ratio) with a week-clustered SE.
   - **Fires if** the cumulative difference is against the least-squares rule at t ≥ +1.
   - **Owner decision (2026-11-17, "use your best"):** if it fires, the check-in switches `[weights] projection_rule` back to `"ratio"` itself, in its results PR, without asking. Weeks already written keep their `weights.json`. If it doesn't fire, the rule stays.
   - **Limit:** three weeks can catch a reversal, not confirm a gain.
8. **DL matchup (measured after the fact, not logged live).** A past week's opponent hit rate is rebuilt exactly from nflverse play-by-play, so live logging would add a 10 MB download per sync day and no information.
   - At week 13: `sadlidp backtest matchup --season 2026` runs; report only.
   - **Season end, the go/no-go,** with the coefficient frozen at 0.30 points per percentage point:
     - add the term to DL μ for 2027 only if the pooled leave-one-season-out ΔMAE on 2024–2026 (all of 2026) is below 0 beyond its margin;
     - and only if 2026 alone is not worse.
   - Weeks 2–10, registered 2026-11-17: pooled slope +0.39 per pp (t 5.5); 2024 / 2025 / 2026 slopes +0.45 / +0.30 / +0.46. LOSO ΔMAE −0.034 ± 0.036; frozen 0.30: −0.035 ± 0.028.
9. **Not at week 13:** RotoBaller/FantasyData drop (one look at week 17), outcome-based tiers and P(play) from 2025 injury reports (season end).
10. **Kickers (added 2026-11-17 with D23, before any kicker result was graded).** The three registered kicker checks are written out in §7. At week 13:
    - K1, the kicker archive check, is decided if its sample is there;
    - K2 (swing) and K3 (weights) are report only, and are decided at season end.

    None of them touches items 1–9 or any DL, LB or DB number.

## 7. Kickers and the position-first page (2026-11-17, owner decisions D23 and D24)
Owner decisions, 2026-11-17:
- **Kicker sources:** the five current sites (Sleeper, ESPN, FantasyPros, FantasyData, RotoBaller) plus CBS. FantasySharks, DraftSharks and the others looked at are not added now (SPEC D21).
- **Backfill where possible,** so each kicker source's accuracy can be measured and weighted like the IDP ones.
- **The page:** the position-first layout (SPEC D24).

**In flight** on branch `claude/kickers` (cut from `main` at 07539cb). Nothing was committed or merged when this was written; a box is ticked when its part merges.
- [ ] **Spec first:** SPEC D23 and D24, with the amendments they need.
- [ ] **Core:** slots and groups, the source registry, `sleeper_k`, per-group matching, consensus, weights and backfill, the kicker roster rule, the add-only merge of past kicker results, a page build that runs on Windows. Built in the working tree; the suite passes there.
- [ ] **The five other kicker adapters:** `espn_k`, `cbs_k`, `fp_k`, `fantasydata_k`, `rotoballer_k`.
- [ ] **Statistics:** K as a separate scope on the Stats page, the report card and the kicker note.
- [ ] **The page:** This week card, position bar, Yours and Available, folded sections.
- [ ] **Owner:** write the body of `swing.kicker_var`. Until then every kicker's odds use σ_out,K = 5.0 and no kicker has a floor–ceiling.
- [ ] **Integration (2026-11-17, in the working tree):** all five parts together. The old code and the new one give identical DL/LB/DB output on the season's real data, offline (replays of weeks 1–11, a week-11 sync, the Stats page, the audit; SPEC §10).
- [ ] **After merge:** the first sync from Actions with the kicker sources, and the kicker backfill at its normal budget. Run once so far, on 2026-11-17, from a home connection into a scratch copy (nothing committed; SPEC §10): all six kicker sources `ok` with no warning; the backfill, capped at 3 requests a host, wrote weeks 1–3 of three archives; the add-only merge added kicker results to weeks 6–9.
- [ ] **A probe from Actions** of the six kicker sources under the pipeline's user agent. They have been read from a home connection only (the probe, and that one scratch sync with the pipeline's own client).
- [ ] **Owner, before any kicker result is graded:** confirm the thresholds of K1–K3 below. Their outline was agreed; the numbers in them (99% of rows, 2 live weeks and 40 rows, the spread ratio as K2's primary statistic) were chosen when they were written down.
- [ ] **FantasySharks from Actions:** it answered a home connection on 2026-11-17. It stays out until a runner gets the same answer and the owner amends D21.
- [ ] **Follow-up, not part of this change:** the IDP "under the limit, so no drop is needed" wording ignores roster room. Your roster was full (27 of 27) on 2026-11-17.

**What did not change:** anything about DL, LB or DB. Every IDP number, stored record, weight and statistic comes out as before; the check that proves it is the first kicker item of SPEC §10. The evaluation protocol's definitions and items 1–9 of the week-13 agenda stand as registered.

**No kicker term was fitted on this season's results.**
- a_K = 0, b_K = 1, the σ floor, the rank priors 2 : 1 : 1 and the 60:40 split are priors.
- σ_out,K = 5.0 is a level set from 2022–25 (4.78 within a kicker's season). The 2026 figure (5.20 over weeks 5–10) was read beside it, not fitted.
- The yards-per-make table is a league-wide unit conversion. Its 2,398 kicks do include 530 from 2026 weeks 1–10.
- The probe looked at five sampled weeks of Sleeper kicker projections against results (r 0.15) to size how predictable kickers are. Nothing was tuned on it.

**Registered kicker checks (2026-11-17, before any kicker result was graded)**

Rules: `docs/eval-protocol.md`. One primary statistic each. Settled weeks only. Every result goes in the ledger whichever way it comes out. No variant search after seeing the data.

- **K1. Archive integrity of `sleeper_k`, `espn_k`, `fp_k` and `rotoballer_k`.**
  - **Question:** does a source's archive, read after the games, return the row that was frozen live before kickoff? Until it does, the source's backfilled and late rows don't count toward the kicker weights (`[accuracy] untrusted`).
  - **Sample:** per source, the rows it read live (not `backfill`, not `late`) in the live kicker weeks settled by the check. Each such week is read again from the archive into a scratch directory. Nothing under `data/` is changed.
  - **Primary statistic:** the share of compared rows that are equal on what feeds μ: a projection's published stat line, a ranking's rank. Labels such as team aren't compared.
  - **Pass rule,** as for the IDP archives (SPEC §5.3, §13): at least 99% equal. With fewer than 100 rows that means every row.
  - **Minimum sample:** 2 live weeks and 40 compared rows for the source. Below that it is not decided and stays untrusted.
  - **Reported beside it, outside the rule:** live rows the archive no longer returns. RotoBaller's re-dated article drops Thursday kickers, and FantasyPros' archived lists lose players.
  - **On a pass:** the source leaves `untrusted` in the checkpoint's results PR, and its archive rows count from the next week written. Weeks already written keep their `weights.json`.
  - **On a fail:** it stays untrusted and its archive rows stay display-only. The rule is not loosened afterwards.
  - **When:** the week-13 checkpoint if the sample is there, otherwise season end.
- **K2. The kicker swing.**
  - **Question:** are the kicker ranges and odds the right width?
  - **Sample:** the kicker pool (the 20 best by the plain average of the kicker projection sources, among kickers who played) on live, settled kicker weeks. σ_pred comes from the frozen record: √(σ_μ² + swing²), or σ_out,K where the record has no swing.
  - **Primary statistic:** the spread ratio RMS((actual − μ)/σ_pred), aim 1.00, with a week-clustered margin of 2 SE.
  - **Guardrail, shown beside it:** range coverage below / inside / above the floor–ceiling against 20/60/20, on the weeks where kickers had a swing of their own.
  - **Weeks before `kicker_var` was written** are graded on σ_out,K alone and reported apart.
  - **Week 13:** report only.
  - **Season end:** keep a_K = 0 and b_K = 1 (and σ_out,K = 5.0) if the margin contains 1.00. Otherwise refit them on the season's live kicker weeks for 2027. One season can't both fit and test the refit, so it enters 2027 as a prior to be checked on 2027's weeks.
  - No in-season change either way.
- **K3. Do the kicker weights differ from their priors at all?**
  - **Why ask:** kicker projections explain little of a kicker's week (r about 0.15, SPEC §11), so the expected answer is no.
  - **Sample:** the kicker pool on the settled weeks the kicker weights count: live rows, and archive rows only for a source that has passed K1.
  - **Primary statistic:** α̂ for kickers, the least-squares projection : ranking split as recorded, with its leave-one-week-out margin, against the prior 0.6.
  - **Reported beside it:** each kicker source's error ratio with its margin, against 1.
  - **Week 13:** report only.
  - **Season end:** if α̂ is within 2 SE of 0.6 and no source's ratio is 2 SE from 1, the result is recorded as "not distinguishable from the priors", and the owner decides whether the kicker weights are simply fixed at the priors for 2027. Otherwise the weights stay as the shrunk rule gives them. K is a single position, so a "differs" claim needs |t| ≥ 2.5 (protocol rule 6).
  - No in-season change either way.

## Owner questions still open
- Vegas player props (would need an Odds API key): untested. Game lines (spread, total, implied points) were tested and rejected (ledger in `docs/methods-review.md`), apart from the DL game total, which stays shadow-only inside the DL matchup candidate.
