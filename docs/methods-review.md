# Methods review: what else our data can do (2026-10-12)

Owner's ask: before building the per-player swing, check whether we are using our datasets well, and
whether tools like bootstrap percolation or anything else from a quantitative degree would help.

## Headline
**The biggest unused dataset is Sleeper's own 2025 archive.** `api.sleeper.com/projections/nfl/2025/<week>`
still serves every week's IDP projections, and `v1/stats/nfl/regular/2025/<week>` the final stats:
18 weeks, **11,091 player-games** with a non-zero pre-game projection and a result (12,616 with any
stat line), against about 700 for 2026 so far. The rows are real projections, not restated results (fractional stats such as 0.31 sacks; projection
and result agree exactly only when both are 0; r = 0.54 overall). Everything below was measured on it,
with SADL's scoring applied, then re-checked on 2026 weeks 2–4. The archive now lives in
`data/2025/archive/` and `sadlidp backtest run --season 2025` reproduces the skill, form, swing and sack
numbers.

Each number is a within-week correlation between a prediction and SADL points, so the week's level is
removed first. On 2025 the margins are about ±0.05; on 2026 they are ±0.20.

## Verdicts

| Idea | Verdict | Evidence (2025) |
|---|---|---|
| **Snap share** (last game's defensive snap %) as an extra input | **Use it; the strongest single addition** | Waiver band (ranks 15–60 by projection): DL r 0.11 → 0.23, LB 0.20 → 0.30, DB 0.04 → 0.09. Worth 6–12 points per 100% of snaps on top of the projection. Already in every `actuals.json`. |
| **Player-level shrinkage** (empirical Bayes: carry a player's earlier misses forward, shrunk by n/(n+4)) | **Use it** | Held-out MAE −4% at every position; waiver band r DL 0.11 → 0.17, LB 0.20 → 0.27. The top 5% of "beating his projection" carry +4.6 of their +5.6 into the next game. Sack excess persists too (r 0.15, n 5,767). |
| **Per-player swing** from the projected stat mix (Poisson) | **Build it for odds and floor/ceiling; expect no tier change** | DL fifths by predicted swing: predicted 4.7 … 9.2, realized 5.0 … 9.2. LB decent, DB does not scale. START/UPGRADE pairwise Brier moves < 0.002 because swing tracks μ (r 0.97): similar players, similar swing. |
| **Projection calibration** (regress result on projection within week) | **Shrink the top; DB waiver tiers are nearly empty of signal** | Top-50 slope LB 0.52, DB 0.49: differences among top players are half as real as projected. DL 1.19. Waiver-band DB slope 0.16, r 0.01: Sleeper's DB projections do not separate waiver DBs at all. |
| Recency weighting (last 3 games instead of season to date) | No | Last-3 β 0.33–0.42 vs season-to-date 0.5–0.8: the longer history predicts better. |
| **Bootstrap** (resampling) for margins | Fine, not needed | Our margins are analytic 2 SE; resampling gives the same picture on 100-game tables and changes no decision. |
| **Bootstrap percolation** | Not applicable | It is a contagion process on a graph (a node activates when ≥ k neighbours are active). Nothing here is a network of players. |
| Cross-validation | Already in use | Odd/even weeks and held-out weeks throughout; the 2025 archive now allows a proper walk-forward. |
| Quantile regression / conformal intervals for floors and ceilings | Not needed (2026-11-17) | Floors and ceilings shipped from the Poisson swing (SPEC §6.7). 2026 weeks 5–9 put 14–18% of games below the floor and 21–23% above the ceiling, against an aim of 20/20, and the spread ratio is 0.95–1.02. |
| Team and opponent effects (scheme, pace, implied total) | Tested (2026-10-14 and 2026-11-17) | D/ST rank, implied points, spread, game total, home and rest were all rejected; see the ledger below. The DL opponent QB-hit rate is a shadow candidate (`docs/status.md` §6). |

### The 2026 re-check
Applying the 2025 coefficients (β 0.5 on the shrunk residual, 8 points per 100% snaps) to 2026 weeks 2–4
neither helped nor hurt measurably: waiver top-25 r moved by ≤ 0.03 at every position, inside ±0.20.
Players had 1–3 earlier games, so the residual term had little to work with. 2026 cannot confirm or deny
the 2025 result yet; it will by mid-season.

### Rousseau, concretely
After four games his mean miss is +17.9 points; shrunk by 4/(4+4) that is +8.9, times β 0.5 gives
**+4.5 points over his projection**, with his 66–81% snap share adding nothing beyond what is in it. The
earlier note in this thread that "extra sacks didn't predict next week's" rested on 467 games and had no
power; on 5,767 games sack excess does persist, with a slope of 0.37.

## Recommended order
1. **A 2025 backtest harness** (`sadlidp backtest`, generalizing the one PR 6 plans): read the archive
   once into `data/2025/`, score it under SADL settings, and give every idea a 12,600-game test and a
   walk-forward on 2026. One afternoon; every later step gets cheaper. *Shipped 2026-10-12 (PR 10).*
2. **A "form" term in μ**: snap share plus the shrunk player residual, fitted on 2025, applied walk-forward
   in 2026 with coefficients recorded per week like the source weights. This is where the tiers change.
   *Shipped 2026-10-12 (SPEC §6.4, `[form]` in the config): fitted on each position's 60 best-projected
   with the projection held at 1, miss β DL 0.45, LB 0.40, DB 0.20; snap β per 100% DL 7.5, LB 5.5, DB 4.0.*
3. **Per-player swing** for START/UPGRADE odds and a floor/ceiling on each card. *Shipped 2026-10-12
   (SPEC §6.7, `[swing]`): swing² = a + b·V per position, DL 0 + 2.5·V, LB 18 + 1.35·V, DB 27 + 0.65·V,
   which reproduces the realized spread by fifth at every position (DB barely scales, as found); floor
   and ceiling at the 20th and 80th percentiles, μ − 0.8·swing and μ + 0.75·swing.*
4. **DB handling**: say on the page that waiver DBs are not separable by projection, and lean on snap share
   and the form term there.

## Caveats
- The 2025 archive is Sleeper only; ESPN and FantasyPros archives start at 2026 week 1. Adjustments are
  fitted on Sleeper's misses, and the consensus misses do differ (2026-11-17). Against the blend, earlier
  misses barely persist at LB and DB (slope .05 ± .20 and −.08 ± .18, against .42 and .30 against Sleeper), so
  the LB and DB miss term counted the same thing twice. See the ledger.
- Sleeper's archive integrity passed: rows don't change after kickoff (2026-10-14). The full review
  re-checked it: 0 of 581 late rows changed (2026-11-17).
- Scoring is today's SADL settings applied to 2025 stats, which is what we want.

## Tested and rejected (ledger, started 2026-11-17)
Every idea tested and its result go here, including the ones that failed, so dead ends aren't re-run and
anything adopted can be seen to have survived a fair screen. The rules are in `docs/eval-protocol.md`.
- **The verdict rule** is D18's: |t| ≥ 2 with the same sign in each season or half, *and* a lower miss on held-out
  weeks.
- **Chance:** about 20 ideas × 3 positions have been screened, so a few |t| of about 2–3 are expected by chance alone.
- **Numbers:** only figures a second person reproduced are given. Ideas screened but not reproduced are marked so,
  with no verdict.

The table uses these abbreviations:
- t is the regression t, with SEs clustered by team-week or week.
- ΔMAE is the held-out change in average miss (negative = better).
- "Band" is ranks 15–60.

| Idea | Seasons, sample | Result | Verdict | Re-test only if |
|---|---|---|---|---|
| Opponent D/ST rank; opponent implied points (D18, PR 6b) | 2025; 2026 wk 1–9 | Every \|t\| < 2 in 2026; LB D/ST rank flips sign between seasons. On the 2025 archive with game-time teams (PR 22), every \|t\| < 2 and held-out is worse at every position (DL 6.510 → 6.524, LB 5.718 → 5.762, DB 4.932 → 4.939) | Rejected (2026-10-14; re-checked 2026-11-17 on corrected teams) | — |
| Snap share compared within role (edge LBs vs edge players) | 2025 | Band r .305 → .284; top-50 MAE 5.57 → 5.61 | Rejected (2026-10-14) | — |
| Recency (last 3 games instead of season to date) | 2025 | β 0.33–0.42 vs 0.5–0.8 for season to date | Rejected (2026-10-12) | — |
| Level (bias) correction at LB, or any position | 2026 wk 3–10, walk-forward | ΔMAE LB +0.019 ± 0.051, DB +0.031 ± 0.029, DL +0.002 ± 0.017 (± 2 SE). The LB median miss is +0.3 while the mean is −1.1: a skewed tail, not a shift | Rejected | The league adds an IDP flex. With one DL, LB and DB slot a shared offset can't change a decision. |
| Projection : ranking split by position (per-position α) | 2026 wk 4–10, walk-forward | Per-position minus pooled ΔMSE −0.002 ± 0.014 (band), −0.011 ± 0.022 (top 50) | Rejected | FantasyPros 2025 (PR 26) shows same-sign position shares in both seasons and a walk-forward gain beyond 2 SE. |
| Other combiners vs the prior blend: median, trimmed, equal weights, rank-only | 2026 wk 4–10, band ΔMSE | +0.66 ± 0.22, +0.38 ± 0.19, +0.36 ± 0.13, +1.55 ± 0.70 (all worse) | Rejected | — |
| Drop RotoBaller and FantasyData | 2026 wk 4–10 (FantasyData from wk 6) | Band ΔMSE −0.26 ± 0.16; top 50 −0.09 ± 0.21 | Not adopted (under 2 SE, one of about 9 comparisons) | One look at week 17: drop or down-weight only if the band gain is beyond 2 SE then. |
| Vegas spread (LB, DB) | 2025; 2026 wk 1–9 | 2025 t −2.94 / −2.49, but held-out worse (LB MAE 5.619 → 5.626, DB 4.883 → 4.993); 2026 t +0.30 / −0.20 | Rejected | A third season shows the same sign against the blend's miss. |
| Vegas game total | 2025; 2026 wk 1–9 | 2025 DL t −3.22, LB −0.99, DB +0.50; 2026 DL −1.61, LB −1.25, DB −0.50 | Rejected at LB and DB; at DL, shadow only inside the DL matchup candidate | The D18 implied-points null is explained. |
| Home field; days of rest | 2025; 2026 wk 1–9 | Home \|t\| ≤ 0.45 (2025), ≤ 1.65 (2026); rest DL +1.67, LB −0.10, DB −0.02 | Rejected | — |
| 3-game snap average instead of last game | 2025 | t +0.91 / −1.57 / +0.31 | Rejected | — |
| Realized team defensive snaps | 2025 | t +3.25 / +6.74 / +5.6 | Not usable: known only after the game. Shows volume matters, not that it can be forecast | — |
| Team volume tendencies, teammate injuries, PFR pressures and missed tackles, opponent sack rate | 2024–2026 | Screened flat by one analyst; not reproduced | No verdict | — |
| μ scale-shrink (pull differences between players toward the week's mean) | 2025; 2026 | 2026 top-50 log score −0.0003 ± 0.0009; worse in the band and on START pairs; 2025 band worse at every position. Would have removed 9 badges that went 7–2 | Rejected | — |
| Re-fit the swing to put 68% within ±1σ | 2025; 2026 | Not run: the spread ratio is already 0.95–1.02, and fat tails explain the 71–78% | Rejected by design (would make the odds overconfident) | — |
| Form coefficients set automatically at checkpoints (empirical Bayes) | 2026 wk 1–9 | τ² against the blend: LB 0.28 ± 1.55, DB −0.18 ± 1.16 | Rejected: it would switch on and off on noise | — |
| Tuesday fixes: FantasyPros-unlisted as missing, wider σ on Tuesday, "wait for Thursday" | 2026 wk 6–10 | Top-30 Spearman +0.015 ± 0.022; MAE worse; Thursday minus Tuesday −0.018 | Rejected | — |
| A Wednesday post-waiver sync window | 2026 wk 6–10 | 0 of the owner's badge targets were lost to Wednesday free-agent adds | Rejected | — |
| 3-week "hold value" in add/drop odds | 2026 | +0.62 pts/game, t 1.94, gone under other reasonable specs (−0.01, +0.02) | Rejected; only the bye look-ahead display stays | — |
| P(play) discount from 2026 Questionable rates | 2026 | n = 11 (CI 0.11–0.69); likely double counts what the sources already price in | Rejected for 2026 data; research on 2025 injury reports instead | The 2025 held-out test passes (season end). |
| DL swing re-fit (adopted 2026-11-17 at 1.46 SE) | 2024; 2025; 2026 | Log score per player-game: 2024 +0.0019 ± 0.0014, 2025 +0.0003 ± 0.0009, 2026 +0.0018 ± 0.0024 | Kept for now; revert candidate at week 13 | Rule in `docs/status.md` §6. |

| Within-group shares by least squares (projection pair) instead of error ratios | 2025 archive, synthetic second sources; 2026 wk 1–9 | Placebo 0.29 (ratio rule 0.49); noisy copies 0.37 / 0.30 / 0.29; season average stable (9 vs 17 wk) and walk-forward ΔMSE −0.15 ± 0.29; 2026 Sleeper raw share 0.27 ± 0.09 | Adopted 2026-11-17 (owner decision), with a week-13 kill switch | — |
| Sleeper 2024 as a test season: the page's settings vs settings fitted on another season | 2024 ↔ 2025 | Snap term on 2024: DL −0.07 ± 0.08, LB −0.24 ± 0.08, DB −0.04 ± 0.04 ΔMAE. A miss term fitted on 2025 helps against Sleeper on 2024 (LB −0.12 ± 0.05), as it should on a Sleeper-only archive: it can't test the blend, which is why LB and DB are off. DL swing refit vs the 2025 fit on 2024: −0.002 ± 0.003 log score | Recorded (a test set, nothing fitted) | — |

| FantasyPros 2025: Sleeper vs FantasyPros share; at LB; form fitted on a blend | 2025 archive + FantasyPros 2025 ranks (kickoff-guarded) | Share 0.42 ± 0.07 (unguarded 0.48 ± 0.07); LB 0.28 ± 0.13; miss on blend DL 0.31 / LB 0.32 / DB 0.09 vs on Sleeper 0.43 / 0.40 / 0.20 | Recorded; feeds 2027 priors; no in-season change | A second season agrees beyond 2 SE |
| DL matchup: opponent QB-hit rate allowed (nflverse play-by-play) | 2024; 2025; 2026 wk 2–10 | Slope +0.39 / pp, t 5.5; per season +0.45 / +0.30 / +0.46; LOSO ΔMAE −0.034 ± 0.036; frozen 0.30: −0.035 ± 0.028. The Sleeper-derived "hits allowed per game" stand-in kept a third of it (−0.022 ± 0.030) | Not used; go/no-go at season end (`docs/status.md` §6) | — |

Previously recorded elsewhere: tier z (`docs/tier-tuning.md`; every z within noise at week 9) and the week-9
checkpoint (`docs/status.md` §3).

### Kickers (added 2026-11-17 with SPEC D23)
Kickers are a separate scope: their own sources, weights, pool (the 20 best-projected kickers who played)
and statistics. Nothing here touches a DL, LB or DB number, and no row above changes.

**No kicker term was fitted on this season's results.** The kicker swing (a 0, b 1), the σ floor, the
60:40 split and the rank priors 2 : 1 : 1 are priors. σ_out for kickers (5.0) is a level set from 2022–25.
The yards-per-make table is a league-wide unit conversion, not a model term.

**Registered before any kicker result was graded** (the full text is in `docs/status.md` §7; results are
entered here when they exist):

| Check | Sample | Primary statistic | Rule | Result |
|---|---|---|---|---|
| K1. Archive integrity of `sleeper_k`, `espn_k`, `fp_k`, `rotoballer_k` | Rows read live in settled live kicker weeks, re-read from the archive after the games; at least 2 weeks and 40 rows a source | Share of rows equal on what feeds μ (stat line or rank) | Pass at ≥ 99%, as for the IDP archives. On a pass the source's archive rows count toward kicker weights; on a fail they stay display-only | Not run yet (week 13 if the sample is there, else season end) |
| K2. Kicker swing | Kicker pool, live settled weeks, σ_pred from the frozen record | Spread ratio RMS((actual − μ)/σ_pred), aim 1.00, ± 2 SE by week | Report at week 13. At season end keep a 0, b 1 if the margin contains 1.00; otherwise refit for 2027, as a prior to check on 2027 | Not run yet |
| K3. Do kicker weights differ from their priors | Kicker pool, the settled weeks the kicker weights count | α̂ for kickers ± its leave-one-week-out margin, against 0.6 | Report at week 13. At season end, "not distinguishable" unless beyond 2 SE (\|t\| ≥ 2.5 for a claim at this one position); no in-season change | Not run yet |

**Screened while choosing kicker sources (2026-11-17), by one analyst, not reproduced:**

| Idea | Seasons, sample | Result | Verdict | Re-test only if |
|---|---|---|---|---|
| Kicker points from game lines and roof (implied team total, favourite margin, indoor) | nflverse 2022–25, 2,148 kicker-games; fitted on 2022–24, scored on 2025 (535) | R² 0.022 in sample; on 2025 r 0.200, within-week rank correlation 0.171, RMSE 5.04 against an outcome SD of 5.11. Implied total moves extra points (r 0.36), not field goals | No verdict (not reproduced); not used | It is registered first and tested under the D18 rule |
| Wind as a kicker flag | 2022–25 outdoor games; 32 at 18+ mph | 8.11 points at 0–8 mph down to 7.10 at 18+ | No verdict (not reproduced; 32 games); not used | — |
| A kicker's own average to date | 2022–25 | Within-week rank correlation +0.056 | No verdict (not reproduced); not used | — |
| Sleeper's kicker projections against results | Five sampled weeks, 144 kicker-games (2025 weeks 3, 7, 11, 15; 2026 week 8), read from the archive | r 0.154, average miss 4.23; per-week rank correlation −0.14 to +0.28 | Recorded as the size of the problem, not as a test: the rows can't be shown to be pre-game | K1 passes for `sleeper_k` |
| Standard-scoring kicker ranks used as they are for decimal scoring | nflverse 2022–2026, 2,448 kicker-games | Game-level correlation of the two scorings 0.992; within-week rank correlation 0.988; inside week 11's sources 0.957–0.998 | Adopted with D23 as a design fact, not a fitted term | — |
