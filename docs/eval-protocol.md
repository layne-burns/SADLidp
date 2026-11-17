# How a change is judged (evaluation protocol, fixed 2026-11-17)

Owner's concern: re-fitting the model again and again chases noise. This page fixes, before the results
are in, how every number is graded and how a change gets adopted. It is written before week 10's
results settle (Sat 2026-11-21). Weeks 10 on are the fresh sample for every test registered here.

## Why most tests will say "keep current"
One season gives 9 to 17 weeks, about 450 to 850 games per position in the top 50. Realistic tweaks to μ move the
miss by about 0.05 points or the rank correlation by 0.01 to 0.03. In-season, only changes of about 0.08 to
0.10 in band rank correlation are visible. So "keep current" is the expected result of most tests, and it
is the right one. A second season held back for testing (Sleeper 2024) and FantasyPros' 2025 ranks are the
cheapest ways to add evidence.

## What is graded
- **The number the page showed:** the frozen μ (`frozen.json` `mu`, the last sync before kickoff).
  "Before form" (`base`) and the plain average of the projection sources are reference rows. Weeks 1–4
  were replayed after the fact, and week 5 is partly rebuilt. Claims about "the page as shown" rest on live weeks 6 and later.
- **The top-50 pool:** per position-week, the 50 best by the plain average of the projection sources,
  among players who played. Every row and every variant is scored on this same pool, so no variant
  picks its own pool.
- **The waiver band:** per position-week, the 25 available players the pre-waiver page showed, in shown
  order (`tiers_log.json` `prewaiver`, `[display] depth`). As a robustness check, Sleeper's own top 25 of
  the same available players. A claim must hold on both.
- **Settled weeks only.** A week counts once its stat corrections close (last kickoff + 4 d 4 h). Partial
  position-weeks, where the pool has fewer players than it should, are left out.

## One primary metric per kind of change
- **Changes to μ (the projection):** band rank skill. This is the within-week Spearman correlation between μ and actual
  points over the band players who played, averaged over position-weeks. A change is judged on the paired
  difference (variant − current), with the SE clustered by week, and t-quantiles when there are 9 weeks or fewer.
  The "value over Sleeper" line is the same statistic with Sleeper's own points as the variant.
- **Changes to σ or swing (the range and the odds):** the paired Gaussian log score per player-game on the
  top-50 pool, with the SE clustered by week.

## At most four guardrails (shown, never optimized)
1. **Band top-3 lift:** the mean actual points of a variant's top 3 in the band minus the band mean. A player
   who did not play scores 0.
2. **Range coverage:** the share of games below the floor, inside, and above the ceiling, against 20/60/20, and
   the spread ratio RMS((actual − μ)/σ_pred), against 1.00.
3. **Decisions:** suggestions counted as decisions (SPEC §6.8), shown as x of y against the odds' expected Σp, with
   net points per decision.
4. **Top-50 average miss:** the paired MAE against the plain average of the projection sources.

## Display rules (Stats page and docs)
- Every number carries its ± or its n.
- "Better", "worse", "runs low" or "runs high" only beyond 2 SE. Otherwise "can't tell apart yet".
- Season figures need 4 or more settled weeks.
- Nothing on the Stats page feeds the model, and no row on it can trigger a change by itself.

## Adoption rule
1. **Only at checkpoints:** week 13 (Tue 2026-12-15) and season end. Never mid-week.
2. **Registered first:** every test is written in the ledger (`docs/methods-review.md`, "Tested and
   rejected") or the checkpoint agenda (`docs/status.md`) before its data is looked at. One primary statistic
   per test. No variant search after seeing the data.
3. **A change is adopted only when all of these hold:**
   - its primary metric beats current by at least 2 SE on the registered sample;
   - where the 2025 archive can test it (Sleeper-only mechanics: form, snaps, swing), it also wins held out
     there (fit on odd weeks, score even, and the reverse);
   - it does not lose by more than 1 SE in any season, including 2024 once that archive is added.
4. **Changes the archive can't test** (anything about the blend of sources) need fresh weeks the idea
   wasn't found on, a stated mechanism, and a kill switch that reverts them.
5. **Ties keep current.** One exception: a structural mismatch, meaning a term applied to a different quantity
   from the one it was fitted on. The owner may switch such a term off at once, as with the LB and DB form miss term
   on 2026-11-17. It comes back only on its registered test.
6. **Multiple testing.** About 20 ideas × 3 positions have been screened, so a few |t| of about 2–3 are
   expected by chance. A pass at a single position needs |t| ≥ 2.5.
7. **Frozen definitions.** The pool, band, weeks and SE rules change only at a checkpoint. They apply from then on
   and are never re-run on past data to rescue a result.
