# Campaign contribution and tournament integration

Implemented: tournament classification, independent edition dates and award-period assignment,
verified winners, immutable contribution policies/evidence, optional achievement scoring, and tests.
Activation is separate from implementation: the user authorized initial calibration values and summer
tournaments belong to the season just ended. The archive command activates formula v1.3 only after
complete campaign data and verified outcomes produce a new public snapshot. Existing snapshots are not rewritten.

## Initial calibration

- Achievements carry 5% of the final score; the existing performance/availability blend carries 95%.
- The achievement component reaches 100 at 10 earned points and is capped there.
- Each of the five tracked domestic league titles is worth 3 points before participation scaling.
- Champions League, World Cup and major continental national-team titles are worth 5 points.
- Domestic cups, secondary continental competitions and other tournaments need their own explicit
  title values before inclusion; no unconfigured tournament receives automatic credit.
- Contribution stage weights are 1.0 for league/group phase, 1.10 for UCL knockout playoffs, 1.15 for round of 16, 1.30 for quarterfinal,
  1.50 for semifinal and 1.75 for final. Domestic league matches all use 1.0.

These are transparent initial modelling choices, not empirically proven optimal weights. They bound
the team-achievement influence and reward participation without counting goals or other metrics twice.
Policy JSON files live in `campaign_policies/`. The national-major policy supplies independent performance
context factors of 1.0, 1.05, 1.08, 1.10 and 1.15. The domestic/UCL title policies intentionally do not
replace historical performance context: attach them to the winning campaign, not as edition context.
Use a separately versioned policy with explicit performance factors when changing existing context rules.

## Canonical calculation

```
participation_m = min(1, player_minutes_m / available_minutes_m)
campaign_contribution = sum(participation_m * stage_weight_m) / sum(stage_weight_m)
```

The denominator includes every match played by the winning team in that edition, including matches the
player missed or played for another team. An unused registered player receives zero. Goals, assists,
saves and other performance statistics do not enter this calculation. Extra-time duration is 120 minutes,
normal completed duration is 90 minutes; stoppage time is not an extra regulation period. For a penalty
shootout fixture, status alone does not establish whether 90 or 120 minutes preceded it: duration must
be verified separately. Unknown durations or stages yield an unavailable result, never guessed credit.

## Six-step implementation

1. `Competition` has format, participant type and scope independently of its legacy MVP type. Cups are
   no longer universally classified as Champions League. Reference sync preserves operator classification.
2. `CompetitionSeason` represents a provider edition mapped to exactly one Merit award period (`season`).
   Its edition name and start/end dates can differ from the award period, including summer tournaments.
   A database constraint prevents assigning the same provider edition twice. Summer national-team
   assignment uses `summer_award_period`; Admin validation rejects assigning June–August editions to
   the next season. Dates and award-period membership remain explicit and reviewable.
3. `WinningCampaign` records the winner, award date, verification timestamp/evidence, expected match
   count and completeness. Use verified competition outcomes, not the winner of an arbitrary fixture.
   Matches already capture players' team membership; national and club appearances reuse provider player IDs.
4. `CampaignPolicy` is immutable, versioned and checksummed. Configure exact provider-stage aliases,
   contribution stage weights, optional independent performance-stage factors, and optional title points.
   There is no substring matching (`semi-final` does not match `final`) or silent fallback in this engine.
   Assign a context policy to each new tournament before Elo/scoring. National Elo uses a separate rating
   pool and does not influence club league-strength medians. Neutral fixtures omit home advantage.
   Existing unconfigured MVP competitions keep the legacy context calculation for compatibility.
5. `scoring_formulas/v1_3.json` configures opt-in achievements. The formula pins competition
   policy versions, a points cap and final-score weight. Title points multiply campaign contribution;
   earned title points are summed, capped, and converted to a 0–100 achievement score. The result is:

   ```
   base = performance_score * 0.92 + availability_score * 0.08
   final = base * (1 - achievement_weight) + achievement_score * achievement_weight
   ```

   Performance, availability and achievement remain separate persisted components. Public snapshot
   context includes campaign credit, earned points, input checksums and policy versions. Player pages
   explain the component only when enabled. Titles enter only on/after `awarded_at`. Unverified outcomes,
   missing completed-edition outcomes, mismatched policies or incomplete winning-campaign evidence block
   enabled scoring rather than silently become zero. Mid-campaign absence of a title is expected.
6. Tests cover full/partial/no participation, extra time, transfers, exact stages, unknown durations,
   incomplete campaigns, future titles, immutable/idempotent results, summer mapping, national context,
   enabled scoring/publication/player rendering, retained-payload duration repair, and reproduction of
   v1.2 scores with the achievement component disabled.

## Operator workflow

1. Apply the additive football/scoring migrations. Historical formulas and snapshots are not modified.
2. Configure competition format/participants/scope and edition name/dates in Admin. Assign its award period.
3. Import a policy JSON containing `version` and `config`. Required config keys are `stage_weights` and
   `stage_aliases`. Optional keys are `performance_stage_weights` and `title_points`. Alias keys must be
   stripped, lowercase exact provider stage names, mapped to configured stages. Weights must be positive.
   Set these only from approved rules. National competitions must have an explicit context policy.

   ```
   python manage.py import_campaign_policy path/to/approved-policy.json
   ```

4. Ingest fixtures and all player appearances. Set `neutral_venue` based on verified fixture information.
   The provider supplies duration for FT/AET. Retained historical payloads can populate durations with
   zero API calls; preview first. Penalty fixtures need explicit elapsed-time evidence of 90 or 120 minutes;
   penalty status alone and unavailable payloads remain unresolved.

   ```
   python manage.py populate_fixture_durations --season 2024-25
   python manage.py populate_fixture_durations --season 2024-25 --apply
   ```

5. Create a `WinningCampaign` in Admin after verifying the outcome. Verify the expected match count
   against the provider's complete winning-team campaign, including qualifiers if part of this edition.
   Mark complete only after verifying all fixture/player payloads. Registered unused players require an
   edition-specific `PlayerTeamSeason` membership to distinguish zero participation from a nonmember.

   ```
   python manage.py calculate_campaign_contribution --campaign 1 --player player-slug --as-of 2025-06-30T23:59:59Z
   ```

6. Once verified complete campaigns exist for the configured competitions, activate v1.3.
   `policy_versions` keys use `provider:competition_provider_id`. Its configuration covers the six club
   competitions and seven national competitions listed below. Adding another tournament requires its edition, context policy, campaign and a new
   immutable formula version pinning its policy. Import the formula through the
   existing formula command with the matching `FORMULA_VERSION`, rebuild context, recompute and publish
   a new snapshot. Never force-replace previously published snapshots for this change.

Repeated contribution calculations reuse the same record for the same inputs; corrected inputs create
new records. Breakdowns preserve the policy, durations, minutes and stage weights used. Verification
can happen after the historical award date; backtesting excludes future titles by the event date, not
the date an operator entered the evidence. Serving player pages reads persisted snapshots only.

## Pro archive rollout

`backfill_archive` implements the approved priority order: finish 2024/25 (including July UCL qualifiers),
catch up the current award period, load 2025/26, then work backwards to 2015/16. It uses up to 20 fixture
IDs per API request, with the same player/metric normalizer as single-fixture ingestion. Full manifests
and raw batch fixture responses are retained. The catalog selects the six verified club competitions
and seven national-team competitions by name; editions without player-stat coverage are excluded.
The national competitions are World Cup, Euro Championship, Copa America, Africa Cup of Nations,
Asian Cup, CONCACAF Gold Cup and UEFA Nations League. This does not include separate qualifying leagues,
friendlies or domestic cups; qualifiers bundled into a covered tournament edition remain part of its campaign.

```
python manage.py backfill_archive --daily-call-budget 7000 --reserve 500 --request-interval 0.25 --max-runtime 21600
```

The command verifies the live subscription, respects the daily remaining quota and reserve, applies the
Free-plan pace if the subscription expires, and checkpoints after each batch in `ProviderSyncState`
under `archive-backfill`. Preparation can be tested without player imports/publication using `--prepare-only`.
The systemd service/timer in `deployment/` runs at 03:00 UTC and allows eight hours for the six-hour job.
There is one shared backfill lock; the old single-season timer is replaced, not duplicated.

National fixture performance is assigned by actual played date through `Fixture.award_season`;
June/July belong to the season just ended. National edition/title assignment uses the actual title final
when available. This separates multi-year campaigns from performance award periods. Centralized
national tournaments and Nations League final-four games use neutral context; other Nations League
rounds retain nominal home advantage. Club context policies are not replaced by this release.

Archive policy v2.0 prepares exact aliases from the observed, retained provider stage catalog, using
the published stage categories/weights. Unknown categories or newly introduced aliases require an
explicit new policy version, never mutation of an existing policy. Formula v1.3 pins all 13 competitions.
Nonannual national tournaments earn no title in periods in which there is no edition/title award.

Domestic winners are verified from retained final standings after the full double round robin; title
dates use the closing regular-season round, not subsequent relegation playoffs. Curtailed/exceptional
seasons require separate verified outcome evidence. Cup winners require an explicit winning team in
a completed title final, never an arbitrary knockout match. Shootout duration can be resolved from
explicit elapsed-time evidence (90/120); unknown duration remains unavailable.

Empty/one-sided player payloads are retained but are not marked successfully ingested. Gaps are
checkpointed and retried after seven days. P1 option A (approved after the first production run) allows
rankings from covered matches once every completed fixture has been attempted. Known unavailable
player payloads are omitted from performance/availability scoring, never replaced with zeroes.
Unprocessed fixtures still block publication. Snapshot coverage counts and missing competition/stage
groups are persisted and displayed on ranking, homepage, player, comparison and season pages.
A period with gaps is not labelled fully imported. Publication remains gated on campaign completeness, required
outcomes and the existing data-quality checks. Other periods continue even if one is blocked.
Campaigns spanning award periods are revalidated after older appearances arrive. v1.3 activates only
after a verified new snapshot succeeds. The current-season default switches only after its snapshot exists.
Raw archives and publication are distinct: importing data never rewrites published ranking entries.
The archive alone opts into covered-match publication; other publication callers remain strict by default.
Unknown outcomes, incomplete winning-team campaigns and other data-integrity errors still block publication.

Provider documentation: [batch fixtures](https://www.api-football.com/news/post/how-to-get-all-fixtures-data-from-one-league),
[quota and coverage](https://www.api-football.com/news/post/how-to-optimize-api-sports-calls-and-quota-usage).

## Remaining limitations

P2 consistency repair (approved separately) preserves provider goal and shot counts but makes impossible
goal/shot conversion ratios unavailable instead of scoring them. Existing contradictions can be previewed
with `python manage.py refresh_shot_conversion --season 2026-27`; add `--apply` to re-fetch at most
60 affected fixtures in three batches, subject to the remaining quota/reserve. Only affected shot records
are repaired: verified fresh shot totals can restore a ratio against the unchanged local goal count;
otherwise ratio inputs become unavailable. Fresh responses and before/after repair evidence are retained.
Goal counts, formula definitions, published snapshots and other player metrics are not rewritten.

- Initial title values, cap and final weight are configured under the user's best-judgment authorization;
  they still need empirical backtesting. Publication and activation require the checks described above.
- Summer national tournaments must be assigned to the season just ended, using verified edition dates.
- Exceptional championship outcomes and unknown penalty-match durations require verified operator
  evidence. Missing provider player coverage is recorded as a gap, not fabricated or counted as complete.
- This reuses `CompetitionSeason` as the edition/award-period bridge. Adding a tournament follows the
  same models and services; it does not require tournament-specific contribution algorithms.
