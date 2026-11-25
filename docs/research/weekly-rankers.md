# Weekly IDP, K and DEF rankers: a sweep for new sources (2026-11-25)

*For the owner to decide which, if any, to add. Nothing here is wired in.*

## What was searched, and how
The goal: individual analysts who publish their own **weekly** IDP, kicker or team-defense rankings, ideally
people who post on Reddit and also host the list on their own site, so the tool could read the site each week.
Names came from web-search snippets (Reddit threads included), FantasyPros' accuracy recaps (2024 and 2025
in-season, 2025 draft) and FantasyPros' week-12 2026 expert pools. Each candidate was then opened logged out
with a plain GET, robots.txt first. Nothing was logged into, no challenge was solved, and no Reddit page was read
by a script. Sources already used or already skipped (SPEC D21) were not re-checked.

**Two limits shaped the result.** This session's network allowlist blocked nearly every analyst's own site
(Substack, personal domains); fantasypros.com, the other sites already used, and GitHub were reachable. And the
web-search budget ran out before Reddit presence could be checked for anyone. So most facts about own sites are
marked *unverified*.

**Entry rules, fixed before checking.** A source must be:
1. free, with no login and no bot challenge, and allowed by robots.txt;
2. a current-week page with a week label;
3. machine-readable (HTML table, JSON, CSV, published sheet; not images, PDFs or prose);
4. deep enough: at least 24 for K or DEF, at least 30 per IDP position;
5. posted before Sunday's games;
6. at least 2 seasons active;
7. independent of FantasyPros (its own model or ranks, not a re-tiering of FantasyPros' consensus).

## Bottom line
- **Six rankers meet every rule, but only through their FantasyPros expert pages.** Each expert's own list is
  served free on fantasypros.com (`/nfl/rankings/<expert>.php?type=weekly&position=DST|K|LB…`, with a week
  archive through `week=N&year=YYYY`). Five cover K or DEF; one covers IDP.
- **All six already feed FantasyPros' consensus**, which the tool already reads. Adding one would weight that
  expert by his own measured accuracy instead of FantasyPros' equal share; it adds no new voice.
- **No analyst's own site could be verified.** The best leads for genuinely new signal (not in FantasyPros'
  consensus) are Mike Woellert and The IDP Show (IDP), Fantasy Six Pack (K, IDP) and Ted Chmyz (DEF). Their
  hosts must be checked from a runner or an allowed network first.
- **Several K/DEF models say they are close to sorting by the betting market's implied team total** (Parlae, Edge
  Halla, FantasyOmatic's own test, a GitHub kicker model). Those would add little over a baseline the tool could
  compute itself; for kickers the game-lines idea is already in the ledger (no verdict, not used).

## Shortlist: meets the rules (through FantasyPros expert pages)
Shared facts: HTML table served logged out (HTTP 200); robots.txt allows `/nfl/rankings/` (crawl-delay 5);
each page carries a week label ("DST Rankings - Week 12 - Nov 25, 2026"); ranks only (standard scoring). Each
expert is one input to FantasyPros' consensus (this week: 53 experts for K, 60 for DST, about 10 for IDP).
FantasyPros' terms of use were not read. "Acc." is FantasyPros' in-season accuracy rank, 2026 / 2025.

| Name | Positions | Posts | Archive | Depth | Own ranks? | Acc. |
|---|---|---|---|---|---|---|
| Kyle Krajewski (First Seed Sports) | DST, K | Wed 11/25 11:29 AM ET | unverified | 32 / 32 | yes, often far from consensus | DST 8 / 3, K 5 / 79 |
| Justin Jaksa (ProFootballIntel) | K, DST | Wed 8:56 AM ET; some weeks revised Sunday | yes (2024–26 seen) | 32 / 32 | yes | K 9 / 5, DST 21 / 17 |
| Kev Wheeler (Wheel Route FF) | DST, K | Tue 5:26 PM ET; some Sunday revisions | yes (2024–26 seen) | 32 / 32 | DST yes; K matches consensus | DST 6 / 42, K 63 / 57 |
| Mick Ciallela (Fantrax) | DST, K | Wed 6:43 AM ET | unverified | 32 / 32 | yes (a mid-size site) | DST 16 / 2, K 54 / 3 |
| Nate Polvogt (Optimus Fantasy Football) | K | Tue 7:04 PM ET | unverified | 28 | yes | K 4 / 61 |
| Aaron St Denis (Fantasy Sports Advice Network) | DL, LB, DB | Wed | unverified | 50 each | close to consensus (35 of 50 DB rows equal) | DL 4 / 10, LB 3 / 2, DB 5 / 4 |

Notes: Krajewski (DST) and Jaksa (K) hold up across both seasons; Wheeler (DST) and Polvogt (K) swing a lot
between seasons. Jaksa and Wheeler sometimes revise on Sunday, so an adapter would keep the version read before
the first kickoff, as the tool already does for every source.

## Close: fails one rule (2 seasons)
| Name | Positions | Fails | Note |
|---|---|---|---|
| Jeff Boggis (Fantasy Football Empire) | DL, LB, DB, K, DST | 2 seasons not confirmed | own ranks; full depth on FantasyPros |
| Kelly Smelser (Punch Drunk Wonderland) | DL, LB, DB, K | no 2025 rank | own ranks (LB #4 vs consensus 23) |
| Bradley Stalder (BS Fantasy Football) | DL, IDP, K, DST | new in 2026 | clearly own ranks; recheck in 2027 |

## Out
- Wolf of Roto Street (FantasyPros page): mechanically fine, but its week-12 list dated from week 11 and its
  accuracy is poor (DST 87 / 63).
- Scott Bogman: FantasyPros staff, not independent.
- tdub29/Kicker-Analysis (GitHub CSV): one live season, no week-11 or 12 board; its README claims no advantage.
- Dynasty Nerds (Jon Glosser): weekly IDP piece is prose start/sit; ranked boards are dynasty.
- Beast Dome (Muntradamus): weekly posts password-protected.
- The IDP Army: audio, Patreon early access, no rankings page.

## Leads that could not be checked (host blocked here)
- **IDP, not in any FantasyPros pool (new signal):** Mike Woellert (`mikewoellert.substack.com`; earlier posts
  may be paid), The IDP Show (`theidpshow.com`).
- **IDP, already in FantasyPros' consensus:** Leo McNeeley / MasterIDP (`rpofootball.com`; FantasyPros' #1
  weekly IDP ranker in 2025; a snippet says membership), Ryan Sitzmann / The IDP Guru (`idpguru.com`; 2026's LB
  and DB #1 so far; hints of paid in-season content).
- **K and DEF:** Fantasy Six Pack (`fantasysixpack.net`; Joe Bond was 2024's #1 kicker ranker), Ted Chmyz
  (`fantasyfootballblueprint.com`; 2025's #1 DST ranker, "Fantasy Football Bots"), Ben Wasley
  (`ffdfantasyfootball.com` / `hashtagfootball.com`), Dynasty Math, Statcracker, Parlae, FantasyOmatic.

## Next step (proposed)
1. **Check the blocked hosts from a runner**, structure only (status, robots.txt, whether a free week-labelled
   table exists), with the existing `probe.yml` pattern: Woellert, The IDP Show, Fantasy Six Pack, Chmyz first.
2. **Owner decision:** whether a FantasyPros expert page counts as "their own site". If yes, the DST lists of
   Krajewski and Jaksa are the strongest candidates.

## How an approved source would join
Each approved source becomes its own adapter, reading only that person's page, never Reddit. It starts at a prior
weight of 1.0, like RotoBaller and FantasyData, and its weight then follows its measured accuracy ("as we have
been doing"). Past weeks are backfilled where an archive exists and stay untrusted until checked. One protocol
constraint: IDP and K are live mid-season on SADL, so a new IDP or K source is stored and graded but not blended
until the Dec 15 checkpoint (`docs/eval-protocol.md`, adoption rule 4). A new DEF source may join at once, since
DEF itself is new. With many collinear rank sources the ratio rule can't tell signal from noise (the 2026-11-17
review), so an N-source least-squares share with heavy shrinkage and placebo checks is to be registered for a
season-end test, for 2027 only.
