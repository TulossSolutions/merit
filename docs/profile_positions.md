# Profile-based award categories (formula v1.4)

Approved scope: API-Football `/players/profiles.position` determines each tracked
player's award category for **all imported seasons**. Tactical positions on
`PlayerFixture` remain untouched. Formula v1.4 aggregates all covered appearances
once per player, in their verified profile category. Today’s profile is not proof
of a player's historical tactical role.

## Collection

The archive job scans `/players/profiles?page=N`, not `/players` season statistics
and not `/fixtures/players` tactical positions. The live endpoint was checked on
30 September 2026: up to 250 profiles per page, 2,739 pages at that time. The page
count is read from each response, not hard-coded. The documentation renderer was
inaccessible during verification; response shape and paging were checked live.

- [Official profile endpoint documentation](https://www.api-football.com/documentation-v3#tag/Players/operation/get-players-profiles)
- [Official quota optimization guide](https://www.api-football.com/news/post/how-to-optimize-api-sports-calls-and-quota-usage)

Each page is saved as `RawProviderPayload` (`player_profile_page`), with the
request path, receipt time and SHA-256. A transactional checkpoint in
`ProviderSyncState` (`player-profile-catalogue`) advances only after saving the
page. Budget/time-limit interruptions resume at the next page; incomplete scans
never establish that a player is absent.

Completed catalogs are reused for seven days. Newly imported tracked players are
resolved from the cached catalog without extra calls. Only tracked players with
appearances get normalized `PlayerPositionProfile` rows; unrelated catalog
players do not become app players. Raw catalog pages remain research/audit
evidence. Scoring reads provenance hashes without loading entire raw pages.

The scan shares the existing archive budget of at most 7,000 calls per run and a
500-call daily reserve on Pro. Calls are paced at 0.25 seconds or slower and obey
remaining-quota response headers. Public page views never call this endpoint.
No API keys are sent to browsers.

## Publication and preservation

A complete scan resolves Goalkeeper, Defender, Midfielder and Attacker to GK,
DEF, MID and FWD. Missing or unsupported positions are UNKNOWN, retained with a
reason and excluded from category scoring rather than guessed from a match.
Snapshot coverage reports how many players lack profile positions.

Known categories require retained payload evidence. New v1.4 scores and ranking
entries freeze the resolved category, source, profile receipt time and payload
hash. Existing formula configurations and all published snapshots stay intact.
The next archive run publishes new v1.4 snapshots; it does not replace v1.3
snapshots at the same dates. Metric weights, the 15% coverage threshold,
eligibility, Elo and achievement policies are unchanged.

Deployment requires the additive ingestion migration, formula v1.4 activation,
and a checkpointed restart of the archive service. Its daily timer, call budget
and fixture ingestion checkpoints remain unchanged.
