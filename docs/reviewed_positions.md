# Reviewed positions and the Mbeumo identity correction

## Approved scope

Formula v1.5 adds an audited fallback to v1.4's profile-based award categories.
The reviewed source is `docs/unknown_profile_positions.md`: 68 nonzero provider
IDs with consistent owner-supplied categories, repeated across 96 season rows.
The invalid provider ID `0` is deliberately excluded and needs a separate
fixture-level identity investigation.

Verified API-Football `/players/profiles.position` always takes priority.
When that field is unavailable, the latest explicit owner review supplies the
category for all imported seasons. Each review records its source-file SHA-256,
reviewer and timestamp separately from retained API profile evidence. Catalog
refreshes preserve these reviews; a newly available API position supersedes
the fallback. Published entries freeze the resolved category and provenance.

The explicit alias `90588 -> 20589` consolidates Bryan Mbeumo's four 2017/18
appearances (60 minutes) under his canonical identity. Participation IDs and
their metrics remain intact; original raw payloads and the old player record
are retained. Future imports and replays resolve the old ID to the canonical
player. The old detail URL permanently redirects, preserving the season query.
Duplicate canonical participants, overlapping appearances, alias chains, and
unexpected pre-existing score/campaign records block the import for review
instead of being silently merged.

## Application and publication

After backing up production and importing the immutable v1.5 formula:

```console
python manage.py migrate --noinput
python manage.py import_scoring_formula scoring_formulas/v1_5.json
python manage.py import_reviewed_positions docs/unknown_profile_positions.md --alias 90588:20589
python manage.py publish_reviewed_rankings
```

Set `FORMULA_VERSION=1.5` for activation. These correction commands use no API
requests. They share the backfill advisory lock and must run at a saved
checkpoint, not concurrently with fixture backfill. Import is transactional
and idempotent for the same source hash and alias.

Publication recalculates each already-public season at its latest published
cutoff, reusing local metrics, Elo and verified trophy evidence, then adds a
new v1.5 snapshot. Existing scores from older formulas and all published
snapshots are preserved. Previously unpublished seasons are not published by
this command. Existing data-quality and trophy completeness checks still
apply; an error is reported rather than bypassed. Rerunning skips corrections
already published at the same cutoff.

The daily archive scheduler uses v1.5 for subsequent snapshots. Metric weights,
15% coverage threshold, Elo, trophy rules and eligibility are unchanged.

## Verification

`tests/test_profile_positions.py` covers source parsing, immutable reviews,
API precedence, refresh persistence, invalid ID exclusion, canonical replay,
negative identity merges, redirects, future reference imports, zero-call
publication and snapshot preservation. The v1.5 contract test compares every
scoring rule with v1.4, excluding only version, name, notes and position policy.
