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
| Quantile regression / conformal intervals for floors and ceilings | Untested, promising | A floor/ceiling per card could come from the Poisson swing first; quantiles on 2025 are the check. |
| Team and opponent effects (scheme, pace, implied total) | Untested | PR 6 already plans the D/ST and implied-total backtest; the 2025 harness is where to run it. |

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
   walk-forward on 2026. One afternoon; every later step gets cheaper.
2. **A "form" term in μ**: snap share plus the shrunk player residual, fitted on 2025, applied walk-forward
   in 2026 with coefficients recorded per week like the source weights. This is where the tiers change.
3. **Per-player swing** for START/UPGRADE odds and a floor/ceiling on each card.
4. **DB handling**: say on the page that waiver DBs are not separable by projection, and lean on snap share
   and the form term there.

## Caveats
- The 2025 archive is Sleeper only; ESPN and FantasyPros archives start at 2026 week 1. Adjustments are
  fitted on Sleeper's misses; the consensus misses may differ slightly.
- Sleeper's archive integrity (whether rows change after kickoff) is still the scheduled check in
  `docs/status.md` §2. The fractional values and 0.54 correlation make a restated archive very unlikely.
- Scoring is today's SADL settings applied to 2025 stats, which is what we want.
