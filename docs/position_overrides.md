# Approved award-position override

Approved by the project owner on 1 October 2026: change Michael Olise to
Attacker for all imported seasons. After the unpublished comparison, the
owner requested "Ok. Update his position" and selected option B: apply the
persistent override and publish new corrected rankings immediately while
preserving existing snapshots.

| Provider | Player ID | Player | Award position |
| --- | --- | --- | --- |
| api_football | 19617 | Michael Olise | Attacker |

Formula v1.6 records this exception explicitly, together with the reviewer,
this document's path and its SHA-256 (UTF-8 text with LF line endings). Only
this player overrides a verified API profile. Other players retain v1.5's
profile-first, reviewed-fallback policy. Metrics, weights, coverage,
eligibility, Elo and achievement rules are unchanged.

The retained API profile still says Midfielder; it is not edited or presented
as proof of the reviewed Attacker category. Profile refreshes and reference
imports preserve the active override. New scores and snapshots freeze both
the manual approval and the original profile provenance.

The publication command recalculates affected, already-published seasons at
their latest public cutoff, including seasons where Olise is below the
eligibility threshold but still contributes to cohort percentiles. It adds
new v1.6 snapshots without replacing history, fetching API data or rebuilding
Elo. Data-quality and trophy verification remain mandatory. The command is
transactional, idempotent and shares the backfill advisory lock.

Subsequent archive publications use v1.6. The already-approved weekly replay
remains pinned to its saved v1.5 series; its progress and existing snapshots
are not reset or silently recalculated under different rules.
