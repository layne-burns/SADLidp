# Phase 1 probe

A one-off spike (SPEC.md §8, Phase 1) that checks every candidate data source and the Sleeper facts the spec depends on before the real tool is built. It is stdlib-only Python.

## What it answers

| Question | Section of `report.md` |
|---|---|
| Which position Sleeper counts toward SADL's max-2 limit (D14), and whether IR/taxi players count | 2 |
| Waiver type, day, hour and observed processing times | 1, 3 |
| Week rollover: Sleeper `state` vs the nflverse schedule | 4 |
| Sleeper injury/status codes for IDPs | 5 |
| Whether our scoring formula reproduces Sleeper's own points, and how bonus keys behave | 6 |
| Shape of Sleeper's IDP projections, and whether past weeks can be retrieved (backfill) | 7 |
| Schedule columns and team codes; ID crosswalk coverage for IDPs | 8 |
| Each candidate source: reachable from GitHub's runners? parseable tables? weekly? | 9 |
| FantasyPros public vs logged-in pages (structure only, never values) | 9 |
| Can a script log in to FantasyPros from GitHub's runners? (Answered: no, Cloudflare Turnstile. D9 now uses the free rankings only.) | 9 |
| The owner's ESPN league: public access, IDP slots, projection coverage, and which ESPN stat ids match which Sleeper stats | 9 |

## Where the results go

Every run commits its output to the **`probe-fixtures`** branch: `latest/` holds the most recent run and `runs/<id>/` keeps history. The same `report.md` is shown as the run's job summary in the Actions tab.

- `report.md` / `report.json`: findings.
- `samples/`: public data only (SADL league and rosters, a ground-truth week, trimmed projections, schedule, and table excerpts from public pages). These become test fixtures in Phase 2.
- `encrypted/*.age`: raw FantasyPros pages, only when `FP_DATA_KEY` is set. Only you can decrypt them.

Nothing from FantasyPros is ever written in plain text. The leak guard (`python -m probe.leakcheck`) aborts the run before any commit if a cookie value, password, email, API key, credential header or private plaintext appears.

## Optional secrets

None are needed any more. FantasyPros is read logged out (SPEC D9) and the ESPN league is public. The probe still accepts these if set, as Phase 1 evidence; it reports "not configured" without them.

| Secret | What it is | How to get it |
|---|---|---|
| `FP_EMAIL` | Your FantasyPros login email | Used only to test whether a script can log in. |
| `FP_PASSWORD` | Your FantasyPros password | Use a password unique to FantasyPros. It is sent only to fantasypros.com over https and never written anywhere. |
| `FP_COOKIE` | Your FantasyPros login session (fallback) | Log in at fantasypros.com in a desktop browser. Open DevTools → Network, reload the page, click the first request, and under **Request Headers** copy the whole value of `cookie` (everything after `cookie:`). Sessions expire, so you will need to refresh it occasionally. |
| `FP_DATA_KEY` | The age key that encrypts FP data | Run `age-keygen -o fp_key.txt` on your computer and paste the **entire file** as the secret. Keep `fp_key.txt` safe; you decrypt with `age -d -i fp_key.txt file.age`. |
| `ODDS_API_KEY` | The Odds API key (betting props) | Free sign-up at the-odds-api.com. One probe run uses about 3 credits. |

## Re-running

The first run comes from a push to a `claude/**` branch that changes anything under `probe/`, `tests/probe/` or this workflow. To re-run after adding secrets, open **Actions → probe**, select the latest run, and click **Re-run all jobs**; re-runs pick up newly added secrets. Once `probe.yml` is on `main`, **Run workflow** works too.

## Local run

```
python -m probe.probe --out out        # needs network access to the sources
python -m pytest tests/probe -q        # offline tests
```
