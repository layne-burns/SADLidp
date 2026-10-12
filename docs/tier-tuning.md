# Tier tuning, weeks 1–4 (2026-10-12)

Owner's question: the "Do the tiers mean anything?" table showed higher tiers scoring less as often as more.
Can the data we have tune the tier settings? Reproduce everything here with `sadlidp tune --season 2026`.

## The short answer
1. **The table was measuring a retired model.** Weeks 1–4 were rebuilt before ESPN and FantasyPros were
   added, so their tiers came from Sleeper alone. Sleeper is the weakest of the three sources at every
   position, and among waiver DBs its order ran backwards (r −0.17 ± 0.2). Those weeks are now replayed from
   all three saved sources.
2. **Four weeks can't tune much.** The 25 waiver players a position shows are projected within 2–4 points
   of each other, while one game swings a score by about 7. Even if our numbers were exactly each
   player's expected score, a table this size (current blend, z 1.0) would come out of order by chance alone 15% (LB) to 56%
   (DL) of the time. Every correlation below has a margin of about ±0.20.
3. **One setting moved: z 1.0 → 0.75.** Source weights and the 60:40 split stay.

## What the data shows (waiver pool: top 25 available per position, as the page would list them)

Order signal: within-week correlation of μ with points.

| Model | DL | LB | DB |
|---|---|---|---|
| Sleeper's numbers alone, same players (the old table's model) | +0.13 | +0.23 | −0.17 |
| Current blend (Sleeper + ESPN 60%, FantasyPros 40%) | +0.14 | +0.33 | +0.02 |

Tier averages, all four weeks (± = 2 standard errors; player-games in brackets):

| | DL | LB | DB |
|---|---|---|---|
| Old table (Sleeper only, z 1.0) | 8.4, 10.1, 7.2 | 18.8, 14.6, 15.0, 15.9 | 9.4, 10.5, 11.1 |
| Current blend, z 1.0 | 9.9, 8.1, 11.4 (8) | 16.6, 12.7 | 12.2, 11.4 |
| Current blend, z 0.75 (now live) | 10.8±2.9, 8.3±2.1, 8.2±2.5, 11.0 (1) | 17.2±2.7, 14.3±1.6, 8.0±2.4 | 12.2±2.0, 11.8±1.7, 14.7±1.3 (3) |

## Why z = 0.75
Tiers only group the μ order; z can't change the order itself. So "higher tiers score more" always
improves as tiers get finer, and isn't the right target. The useful z is the widest one that leaves no
real order inside a tier ("inside r" in `sadlidp tune`):

| z | DL tiers/wk, inside r | LB tiers/wk, inside r | DB tiers/wk, inside r |
|---|---|---|---|
| 0.5 | 4.8, +0.08 | 3.5, +0.26 | 2.8, +0.15 |
| **0.75** | 3.2, −0.01 | 2.8, +0.11 | 2.2, +0.07 |
| 1.0 | 2.8, +0.19 | 2.0, +0.21 | 1.5, +0.01 |
| 1.5 | 2.0, +0.24 | 1.0, +0.33 | 1.2, +0.03 |

At 1.0, DL and LB tiers lumped together players our numbers could tell apart; at 0.75 they mostly don't.
DB has no measurable order among waiver players at any z, so its tiers are honest either way. This is a
lean, not proof: every value is ±0.20.

## What didn't move, and why
- **Projection:rank split.** 0.4–0.7 all land within 0.05 of each other; 0.8 helps DB (+0.10) and hurts
  LB (+0.22). Nothing beats 0.6 by more than the noise.
- **Source weights.** Dropping Sleeper helps DB (+0.15) and hurts DL and LB. Weights fitted per held-out
  week swing from 0 to 0.6 for the same source. Per-source accuracy weights are PR 6, and every week-1–4
  row is a backfill, which PR 6 may use only after each source passes its archive-integrity check.
- **σ floors, ν0, s0.** They only reshape tier widths, which z already covers; four weeks can't separate them.

## When to look again
Re-run `sadlidp tune` every few weeks. The margins shrink with the square root of the player-games, so
by week 12 they're about ±0.12 and real differences of 0.15 or more start to show.
