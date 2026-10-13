# Sleeper archive-integrity evidence (week 5, MNF BUF @ LAR, kickoff 2026-10-13 00:15 UTC)

Evidence only; this branch is never merged.

| File | Taken (UTC) | Sleeper max `last_modified` | Rows (BUF/LAR, real) |
|---|---|---|---|
| `mnf_w5_snapshot_0912Z.json` | 2026-10-12 09:12 | 2026-10-12 09:10:31 | 46 |
| `mnf_w5_snapshot_0006Z.json` | 2026-10-13 00:06 (9 min before kickoff) | 2026-10-12 23:57:09 | 42 (sha256 of the stored blob: edd362ab…) |
| `mnf_w5_postgame_0931Z.json` | 2026-10-13 09:31 (after the game) | 2026-10-13 04:00:12 | 42 |

Results:
- Pre-kickoff (00:06Z) vs post-game (09:31Z): **42 of 42 rows identical** (stats, ignoring pts_/adp_ keys), although
  Sleeper restamped the week at 04:00Z. Sleeper's projections don't change after kickoff.
- 09:12Z vs 00:06Z: 37 of 46 rows changed and 4 players lost their projection (pre-game inactives). Sleeper does
  update projections during game day, up to kickoff (batches seen at 09:10Z and 23:57Z).
- main's week-5 file vs post-game: 587 of 628 identical, including all 581 late rows (captured after their games);
  the 41 differences are all BUF/LAR rows captured at ~15:00Z, before the 23:57Z pre-game update.
