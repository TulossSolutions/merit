# Technical Specification — Objective Football Player Rankings MVP

**Document status:** Build specification  
**Target:** Codex implementation  
**MVP stack:** Django 6.0.x, PostgreSQL, Redis, HTMX 4.0, Tailwind CSS 4.x  
**MVP data strategy:** API-Football Free + PostgreSQL + our own Elo/scoring + StatsBomb Open Data for backtesting  
**Primary runtime provider:** API-Football v3 behind a provider abstraction  
**Python:** 3.12+  
**Architecture:** server-rendered Django application with HTMX partial navigation; no SPA and no separate frontend API required for the MVP.

## Implementation review — 2026-09-09

Status is evidence-based: ✅ means implemented and locally verified; ❌ means incomplete, unverified, or deliberately deferred. Partial work remains ❌. Production-only work is deferred per the current request. Provider-related status was re-audited after API-Football replaced Sportmonks as the MVP primary provider; the existing Sportmonks implementation does not satisfy API-Football acceptance items.

| § | Feature | MVP |
|---:|---|:---:|
| 1 | Four public position rankings with explainable scores | ✅ |
| 2 | Complete MVP scope, including six real configurable competitions | ✅ |
| 3 | All technical principles, including API-Football raw-payload ingestion | ✅ |
| 4 | Complete target stack verified with PostgreSQL/Redis and HTMX 4 | ✅ |
| 5 | Repository layout | ✅ |
| 6 | Five-app responsibility split, including provider/importer ownership | ✅ |
| 7 | API-Football environment configuration | ✅ |
| 8 | Complete core data model contract | ✅ |
| 9 | Team Elo snapshot model | ✅ |
| 10 | Formula, score, snapshot, and ranking models | ✅ |
| 11 | Typed provider abstraction | ✅ |
| 12 | Working API-Football v3 primary adapter | ✅ |
| 12a | Isolated StatsBomb Open Data backtesting importer | ✅ |
| 13 | Fully usable provider-backed mock implementation | ✅ |
| 14 | Position normalization | ✅ |
| 15 | Complete API-Football fixture ingestion workflow | ❌ |
| 16 | All management commands perform their documented work | ❌ |
| 17 | 03:00 daily ingestion and Monday 06:00 publication scheduling | ❌ (production deferred) |
| 18 | Deterministic Elo algorithm and rebuild | ✅ |
| 19 | League-strength multiplier | ✅ |
| 20 | Competition/stage factors | ✅ |
| 21 | Pre-match opponent factor | ✅ |
| 22 | Persisted total context factor | ✅ |
| 23 | Count, rate, negative, and clean-sheet aggregation | ✅ |
| 24 | Position cohort and scaled eligibility | ✅ |
| 25 | 15% metric coverage gating in formulas v1.1/v1.2 (v1.0 retained) | ✅ |
| 26 | Deterministic tied percentile scoring | ✅ |
| 27 | Exact V1 scoring formula | ✅ |
| 28 | Missing-metric weight renormalization | ✅ |
| 29 | Performance score | ✅ |
| 30 | Availability score | ✅ |
| 31 | Final 92/8 score | ✅ |
| 32 | Deterministic order and movement | ✅ |
| 33 | Immutable weekly publication semantics | ✅ |
| 34 | Complete data-quality validation catalogue | ✅ |
| 35 | Specified Redis cache design and invalidation | ✅ |
| 36 | Public URL design | ✅ |
| 37 | Complete home-page content, including full-ranking links | ✅ |
| 38 | Complete Top 100 ranking table and headline statistics | ✅ |
| 39 | Complete player detail and context explanation | ✅ |
| 40 | Searchable HTMX player comparison controls | ✅ |
| 41 | Dynamic public methodology | ✅ |
| 42 | Formula changelog for immutable v1.0/v1.1 and active v1.2 | ✅ |
| 43 | Progressive HTMX navigation and fragments | ✅ |
| 44 | Local Tailwind implementation | ✅ |
| 45 | Performance targets and query benchmarks | ✅ |
| 46 | Complete accessibility verification | ✅ |
| 47 | SEO basics, robots, and sitemap | ✅ |
| 48 | Required Django Admin screens and read-only results | ✅ |
| 49 | Required operational logging, including API quota state | ✅ |
| 50 | Complete API-Football response/error handling | ✅ |
| 51 | Production security | ❌ (production deferred) |
| 52 | Complete scoring-formula validation, including `major.minor` version | ✅ |
| 53 | Deterministic calculation inputs | ✅ |
| 54 | Explicit service/query layer | ✅ |
| 55 | Full locked publication workflow with fatal prechecks | ✅ |
| 56 | Historical team persisted on ranking entry | ✅ |
| 57 | Role-specific headline ranking statistics | ✅ |
| 58 | Stable score-explanation payload | ✅ |
| 59 | Complete required test matrix, including API-Football | ✅ |
| 60 | End-to-end mock pipeline test | ✅ |
| 61 | Developer setup documentation for the selected provider | ✅ |
| 62 | Modern Python project metadata | ✅ |
| 63 | Local Tailwind build | ✅ |
| 64 | Production static-file serving | ❌ (production deferred) |
| 65 | Database-backed health endpoint | ✅ |
| 66 | Production cron concurrency verification | ❌ (production deferred) |
| 67 | Initial backfill strategy documentation | ✅ |
| 68 | Working API-Football raw-payload fixture replay | ✅ |
| 69 | Immutable formula lifecycle with configured v1.2 selection | ✅ |
| 70 | Dedicated methodology-integrity test | ✅ |
| 71 | Complete reusable component inventory | ✅ |
| 72 | Empty, eligibility, and missing-value states | ✅ |
| 73 | Explicit UTC date/time handling | ✅ |
| 74 | Required database indexes | ✅ |
| 75 | Stable collision-safe player slugs | ✅ |
| 76 | Season archive with season-specific ranking navigation | ✅ |
| 77 | Specified current/archive HTTP caching policy | ❌ |
| 78 | Server-side player search endpoint | ✅ |
| 79 | No unnecessary public REST API | ✅ |
| 80 | Production deployment artifacts | ❌ (production deferred) |
| 84 | Required phased build outcome | ❌ |
| 85 | Complete Definition of Done | ❌ |
| 86 | MVP implementation guardrails | ✅ |
| 87 | Future extensions excluded | ✅ |
| 88 | Product statement preserved | ✅ |

---

## 0. Instructions to Codex

Build the MVP described in this document end-to-end.

Do not redesign the product, invent extra features, or replace the specified architecture with React, Next.js, Vue, a client-side SPA, or a microservice architecture.

When a provider-specific field or external ID cannot safely be hardcoded, implement the abstraction/configuration required to supply it later. Never invent API-Football league, season, team, player, fixture, or stage IDs.

The application must be usable with a `MockFootballProvider` without an API key. The real `ApiFootballProvider` is the MVP primary adapter and must be activated through environment configuration. `SportmonksProvider` is a future extension only. `StatsBombImporter` is an offline research/backtesting input and must never feed public rankings directly.

All ranking calculations must be deterministic and repeatable. A ranking must be reproducible from persisted source data plus a formula version. No LLM may participate in scoring.

Prioritize correctness, auditability, idempotent ingestion, tests, and fast server-rendered UX over feature breadth.

---

# 1. Product goal

Build a public web application that answers four questions throughout a football season:

1. Who is currently the best **attacker**?
2. Who is currently the best **midfielder**?
3. Who is currently the best **defender**?
4. Who is currently the best **goalkeeper**?

The rankings are generated from match and player statistics, not human votes.

The public ranking is updated once per week from all eligible matches ingested up to the weekly cutoff.

Each player receives a score from `0.00` to `100.00` within his position cohort. The score must be explainable: users can see the component metrics, percentile scores, context adjustments, minutes, formula version, and ranking movement.

The MVP covers men's football only.

---

# 2. MVP scope

## 2.1 Included competitions

The initial product must support configuration for:

- English Premier League
- Spanish La Liga
- Italian Serie A
- German Bundesliga
- French Ligue 1
- UEFA Champions League

Do **not** hardcode provider IDs. Store provider IDs in the database and allow tracked competitions to be enabled/disabled from Django Admin.

The architecture must support additional leagues later without schema changes.

## 2.2 Included ranking categories

Use exactly these public ranking cohorts in the MVP:

- `GK` — Goalkeepers
- `DEF` — Defenders
- `MID` — Midfielders
- `FWD` — Attackers

Detailed positions may be stored when supplied by the provider but do not create separate public rankings for CB, LB, DM, RW, CF, etc. in the MVP.

## 2.3 Included pages

- Home / ranking overview
- Full ranking by position
- Player detail
- Player comparison
- Methodology
- Formula changelog
- Season archive shell, even if only the current season has data
- Basic health endpoint
- Django Admin

## 2.4 Explicitly out of scope

Do not build these in MVP:

- coach ranking
- women's football
- fan/journalist voting
- comments
- social network features
- user accounts or authentication for public users
- subscriptions/payments
- native mobile app
- live match center
- betting/odds
- fantasy football
- AI-generated ranking decisions
- automated news articles
- advanced tactical maps
- expected goals model built in-house
- multiple runtime data providers active simultaneously (the isolated StatsBomb backtesting importer is not a runtime provider)
- domestic cups
- international national-team tournaments

Keep extension points where sensible, but do not implement these features.

---

# 3. Technical principles

1. **Server-rendered first.** Django renders complete HTML for normal requests.
2. **HTMX enhancement.** HTMX requests return fragments for tabs, filters, comparisons, pagination, and navigation where useful.
3. **No ranking calculation during a public page request.** Rankings are precomputed and persisted.
4. **Redis cache is a performance layer, never the source of truth.**
5. **PostgreSQL is the canonical application database.**
6. **Raw provider payloads are retained.**
7. **Imports are idempotent.** Re-running a sync must update/deduplicate rather than duplicate records.
8. **Provider abstraction.** Business logic never imports API-Football-specific response structures directly.
9. **Formula versioning.** Every computed score points to an immutable scoring formula version.
10. **Public methodology.** The calculation implemented in code must match what the methodology page describes.
11. **No silent manual ranking edits.** Admin can manage metadata and re-run jobs but cannot directly assign a player's final score or rank.

---

# 4. Target technology stack

## Backend

- Python 3.12+
- Django 6.0.x
- PostgreSQL 16+
- Psycopg 3
- Redis 7+
- `django-redis`
- `httpx` for provider HTTP calls

## Frontend

- Django templates
- HTMX 4.0.x, served locally from static files
- Tailwind CSS 4.x, compiled locally
- small vanilla JavaScript only when HTMX/CSS are insufficient
- no React
- no Vue
- no Alpine unless implementation genuinely requires it; prefer zero additional framework for MVP

## Quality/tooling

- `pytest`
- `pytest-django`
- `pytest-cov`
- `ruff`
- `factory-boy` or lightweight local factories for tests
- `freezegun` if useful for snapshot/date tests

## Production server

- Gunicorn
- Nginx reverse proxy
- Redis
- PostgreSQL

The application should also run locally with Django's development server.

---

# 5. Repository layout

Use approximately this structure:

```text
project/
├── manage.py
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── package.json
├── config/
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── dev.py
│   │   └── prod.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── core/
│   ├── football/
│   ├── ingestion/
│   ├── scoring/
│   └── rankings/
├── scoring_formulas/
│   └── v1.json
├── templates/
│   ├── base.html
│   ├── core/
│   ├── rankings/
│   └── players/
├── static/
│   ├── js/
│   │   └── htmx.min.js
│   └── dist/
│       └── app.css
├── static_src/
│   └── app.css
├── tests/
└── deployment/
    ├── nginx.example.conf
    ├── gunicorn.service.example
    └── cron.example
```

Do not create needless application boundaries. The five apps above are sufficient.

---

# 6. Django app responsibilities

## `core`

Responsibilities:

- home page
- methodology/changelog static views
- common template tags/utilities
- health endpoint
- shared enums/utilities

## `football`

Responsibilities:

- competition, season, team, player, fixture data models
- normalized player fixture statistics
- team Elo history
- football-domain query services

## `ingestion`

Responsibilities:

- provider protocol/base class
- API-Football adapter (MVP primary)
- StatsBomb Open Data importer for isolated research/backtesting
- mock provider
- provider API client
- raw payload persistence
- normalization
- sync state
- management commands

## `scoring`

Responsibilities:

- scoring formula loading and validation
- metric aggregation
- contextual weighting
- Elo calculation
- percentile calculation
- eligibility logic
- season score generation
- formula/version models

## `rankings`

Responsibilities:

- weekly snapshots
- ranking entries
- ranking/player/compare views
- HTMX fragments
- caching

---

# 7. Environment configuration

Create `.env.example` with at least:

```dotenv
DJANGO_SETTINGS_MODULE=config.settings.dev
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/football_rankings
REDIS_URL=redis://127.0.0.1:6379/0

API_FOOTBALL_KEY=
FOOTBALL_PROVIDER=api_football
FORMULA_VERSION=1.2
API_FOOTBALL_BASE_URL=https://v3.football.api-sports.io

DEFAULT_SEASON_SLUG=2026-27
PUBLIC_RANKING_WEEKDAY=0
PUBLIC_RANKING_HOUR_UTC=6
```

`FOOTBALL_PROVIDER` values:

- `mock`
- `api_football`

`api_football` is the MVP production-data value. `sportmonks` is reserved for a future adapter and is not an accepted MVP runtime value.

Never expose `API_FOOTBALL_KEY` in templates, JavaScript, logs, or public endpoints.

---

# 8. Core database model

Use Django `BigAutoField` primary keys unless an external natural key is explicitly needed.

All mutable operational models should have `created_at` and `updated_at` timestamps unless pointless.

## 8.1 `Season`

Fields:

```text
id
name                  # "2026/27"
slug                  # "2026-27"
starts_on
ends_on
is_current
is_published
created_at
updated_at
```

Constraints:

- unique `slug`
- only one current season should normally exist; validate in admin/service layer

Methods/properties:

- `progress(as_of_date) -> Decimal` clamped `0..1`
- `eligibility_minutes(as_of_date)`
- `availability_target_minutes(as_of_date)`

Formula:

```text
season_progress = clamp((as_of_date - starts_on) / (ends_on - starts_on), 0, 1)
eligibility_minutes = max(180, round(1800 * season_progress))
availability_target_minutes = max(270, round(2700 * season_progress))
```

At final season date this yields:

- eligibility threshold: 1800 minutes
- availability target: 2700 minutes

## 8.2 `Competition`

Fields:

```text
id
provider
provider_id
name
slug
country_code nullable
competition_type       # DOMESTIC_LEAGUE / UCL
is_tracked
base_importance        # Decimal, default 1.00
created_at
updated_at
```

Unique constraint:

```text
(provider, provider_id)
```

Do not encode provider IDs in application code.

Default `base_importance`:

- domestic league: `1.00`
- UEFA Champions League: `1.05`

Stage-specific importance can override the UCL base value.

## 8.3 `CompetitionSeason`

Fields:

```text
id
competition FK
season FK
provider_season_id nullable
is_active
league_strength_rating nullable
league_strength_multiplier default 1.00
last_strength_calculated_at nullable
```

Unique:

```text
(competition, season)
```

## 8.4 `Team`

Fields:

```text
id
provider
provider_id
name
slug
short_name nullable
country_code nullable
logo_url nullable
active
created_at
updated_at
```

Unique `(provider, provider_id)`.

## 8.5 `TeamCompetitionSeason`

Fields:

```text
id
team FK
competition_season FK
active
```

Unique `(team, competition_season)`.

## 8.6 `Player`

Fields:

```text
id
provider
provider_id
name
slug
first_name nullable
last_name nullable
common_name nullable
birth_date nullable
nationality_code nullable
image_url nullable
height_cm nullable
preferred_foot nullable
primary_position        # GK / DEF / MID / FWD / UNKNOWN
detailed_position nullable
active
created_at
updated_at
```

Unique `(provider, provider_id)`.

Indexes:

- `slug`
- `primary_position`
- `name`

## 8.7 `PlayerTeamSeason`

Fields:

```text
id
player FK
team FK
season FK
competition_season FK nullable
shirt_number nullable
provider_position_id nullable
provider_detailed_position_id nullable
started_on nullable
ended_on nullable
```

This preserves transfers rather than overwriting a player's team.

## 8.8 `Fixture`

Fields:

```text
id
provider
provider_id
competition_season FK
home_team FK
away_team FK
starts_at
status                  # SCHEDULED / LIVE / FINISHED / POSTPONED / CANCELLED
stage_name nullable
round_name nullable
home_score nullable
away_score nullable
winner_team FK nullable
last_provider_update nullable
stats_ingested_at nullable
created_at
updated_at
```

Unique `(provider, provider_id)`.

Indexes:

- `(competition_season, starts_at)`
- `status`
- `starts_at`

## 8.9 `PlayerFixture`

One row per player participation in a fixture.

Fields:

```text
id
fixture FK
player FK
team FK
opponent FK
position             # normalized GK/DEF/MID/FWD
provider_position_id nullable
started boolean
minutes integer
jersey_number nullable
context_factor Decimal nullable
opponent_elo_before Decimal nullable
competition_factor Decimal nullable
league_factor Decimal nullable
created_at
updated_at
```

Unique `(fixture, player)`.

Validation:

```text
0 <= minutes <= 130
```

Allow extra time.

## 8.10 `PlayerFixtureMetric`

Use a generic normalized metric table rather than one DB column per provider statistic.

Fields:

```text
id
player_fixture FK
metric_key
value Decimal nullable
numerator Decimal nullable
denominator Decimal nullable
is_available boolean default True
source_type_id nullable
created_at
updated_at
```

Unique:

```text
(player_fixture, metric_key)
```

Examples of `metric_key`:

```text
goals
assists
shots
shots_on_target
key_passes
passes
accurate_passes
tackles
tackles_won
interceptions
clearances
recoveries
duels
duels_won
aerial_duels
aerial_duels_won
dribble_attempts
successful_dribbles
goals_conceded
saves
penalties_saved
long_passes
accurate_long_passes
errors_leading_to_goal
```

Do not assume every provider exposes every metric.

## 8.11 `RawProviderPayload`

Fields:

```text
id
provider
resource_type
provider_resource_id
request_path
payload JSONField
payload_sha256
received_at
http_status
```

Indexes:

- `(provider, resource_type, provider_resource_id)`
- `received_at`

Purpose:

- audit
- debugging
- replay/renormalization

Do not delete old payloads automatically in MVP.

## 8.12 `ProviderSyncState`

Fields:

```text
id
provider
sync_key
cursor nullable
last_success_at nullable
last_attempt_at nullable
last_error nullable
metadata JSONField default {}
```

Unique `(provider, sync_key)`.

---

# 9. Elo models

## `TeamEloSnapshot`

Fields:

```text
id
fixture FK
team FK
rating_before Decimal
rating_after Decimal
expected_result Decimal
actual_result Decimal
is_home boolean
created_at
```

Unique `(fixture, team)`.

Indexes:

- `(team, fixture)`

Elo snapshots must be reproducible chronologically from fixtures.

---

# 10. Scoring models

## 10.1 `ScoringFormula`

Fields:

```text
id
version             # "1.0"
name
config JSONField
checksum_sha256
is_active
created_at
activated_at nullable
notes text
```

Rules:

- formula versions are immutable after any ranking snapshot references them
- there can be only one active formula for new calculations
- never update an old formula config in place

The canonical file for v1 is:

```text
/scoring_formulas/v1.json
```

A management command must import/validate it into `ScoringFormula`.

## 10.2 `PlayerSeasonScore`

One current calculated season score per player/formula.

Fields:

```text
id
season FK
player FK
formula FK
position
as_of
eligible boolean
minutes
appearances
performance_score Decimal nullable
availability_score Decimal nullable
final_score Decimal nullable
metric_breakdown JSONField default {}
coverage_breakdown JSONField default {}
context_summary JSONField default {}
calculated_at
```

Unique:

```text
(season, player, formula, as_of)
```

For current display, query latest `as_of` or persist a pointer via snapshot.

## 10.3 `RankingSnapshot`

Fields:

```text
id
season FK
formula FK
published_at
cutoff_at
is_public
created_at
```

Unique `(season, formula, cutoff_at)`.

## 10.4 `RankingEntry`

Fields:

```text
id
snapshot FK
player FK
position
rank
score
previous_rank nullable
movement integer nullable
minutes
metric_breakdown JSONField default {}
```

Unique:

```text
(snapshot, position, rank)
(snapshot, player)
```

Indexes:

- `(snapshot, position, rank)`
- `(player, snapshot)`

---

# 11. Provider abstraction

Create a typed provider interface/protocol independent from Django models.

Example shape:

```python
class FootballProvider(Protocol):
    def list_competitions(self) -> list[ProviderCompetition]: ...
    def list_seasons(self, competition_id: str) -> list[ProviderSeason]: ...
    def list_teams(self, provider_season_id: str) -> list[ProviderTeam]: ...
    def list_players(self, team_id: str, provider_season_id: str) -> list[ProviderPlayer]: ...
    def list_fixtures(self, provider_season_id: str, start: date, end: date) -> list[ProviderFixture]: ...
    def get_fixture_details(self, fixture_id: str) -> ProviderFixtureBundle: ...
```

`ProviderFixtureBundle` should normalize:

- fixture metadata
- participants
- score/status
- lineups
- minutes
- player positions
- normalized metric values

Business code must consume normalized DTOs, not provider JSON dictionaries.

---

# 12. API-Football implementation

Use this provider boundary:

```text
FootballDataProvider
         │
         ├── ApiFootballProvider     ← PRIMARY
         │
         ├── SportmonksProvider      ← future
         │
         └── StatsBombImporter       ← research/backtesting
```

`FootballDataProvider` is the runtime provider protocol described in §11. Implement `ApiFootballProvider` for API-Football v3 as the sole real MVP runtime provider. Keep `SportmonksProvider` as a named future extension only; it is not required or selectable in MVP v1.

Required behavior:

- authentication via the `x-apisports-key` request header sourced from `API_FOOTBALL_KEY`
- base URL from `API_FOOTBALL_BASE_URL`
- request timeout: 20 seconds
- retry 429 and transient 5xx responses
- exponential backoff with jitter
- maximum 4 total attempts
- honor `Retry-After` when supplied
- structured logging without token leakage
- preserve every successful resource payload in `RawProviderPayload` before normalization
- inspect API response `errors`, `results`, and `paging` rather than treating every HTTP 200 response as usable data
- record rate-limit response headers when present and stop cleanly before exhausting the Free-plan daily allowance; resume from `ProviderSyncState` on the next run
- never use API-Football's provider rating as an input to our Elo or scoring formula

For completed fixture ingestion, retrieve completed fixtures from `/fixtures` since the last successful sync cursor. For each unseen completed fixture, call `/fixtures/players?fixture={fixture_id}` once to obtain the participating players and their match statistics.

Do not hardcode provider IDs from guesses. Competition and season IDs remain database configuration.

## Named-field mapping

Implement a provider mapping structure such as:

```python
API_FOOTBALL_METRIC_MAP = {
    "goals.total": "goals",
    "goals.assists": "assists",
    "shots.total": "shots",
    "shots.on": "shots_on_target",
    "passes.total": "passes",
    "passes.key": "key_passes",
    "tackles.total": "tackles",
    "tackles.interceptions": "interceptions",
    "duels.total": "duels",
    "duels.won": "duels_won",
    "dribbles.attempts": "dribble_attempts",
    "dribbles.success": "successful_dribbles",
    "goals.conceded": "goals_conceded",
    "goals.saves": "saves",
    "penalty.saved": "penalties_saved",
}
```

This is illustrative, not permission to fabricate unsupported values. Verify the response schema in adapter tests and explicitly map only fields used by the active formula. An unrecognized field must be ignored without crashing ingestion. `source_type_id` remains nullable because API-Football uses named nested fields rather than statistic type IDs.

## Missing values

Distinguish:

- explicit numerical zero
- absent/unavailable statistic

Do not globally convert every absent provider detail into zero.

The adapter may emit zero only when API-Football explicitly returns a numerical zero. `null`, absent fields, unavailable competition coverage, and non-numeric values must remain unavailable.

Otherwise emit `is_available=False`.

## StatsBomb Open Data backtesting

Implement `StatsBombImporter` as an offline research/backtesting path, not as a `FOOTBALL_PROVIDER` runtime option. It reads locally available StatsBomb Open Data JSON for competitions, matches, lineups, and events and normalizes only the fields needed for reproducible formula evaluation.

Backtesting data must remain isolated from operational API-Football rows and public ranking snapshots. Store explicit source/provenance metadata, preserve StatsBomb attribution requirements in exported research, and never silently combine StatsBomb and API-Football measurements in one cohort. A backtest may compare formula outputs, coverage, and ranking stability; it must not publish rankings.

Pipeline terminology in this document maps to the canonical MVP models as follows:

- `PlayerMatchStats` → `PlayerFixture` plus `PlayerFixtureMetric`
- `PlayerMatchScore` → deterministic per-fixture scoring output used as an aggregation input; it is not a public ranking
- `SeasonScore` → `PlayerSeasonScore`
- `WeeklyRankingSnapshot` → `RankingSnapshot`

---

# 13. Mock provider

Implement `MockFootballProvider` and deterministic fixture data so the application can be fully developed and tested without an API-Football key.

Seed at minimum:

- 1 season
- 2 competitions
- 8 teams
- 40+ players distributed across four positions
- 20+ completed fixtures
- player stats covering every v1 metric

Mock data must be deterministic using a fixed seed.

Provide:

```bash
python manage.py seed_demo_data
```

After seeding and recomputing, every public page must work.

---

# 14. Position normalization

Create a mapping service that converts provider position information into:

```text
GK
DEF
MID
FWD
UNKNOWN
```

Priority:

1. provider's explicit broad position
2. verified detailed-position mapping
3. existing player's primary position
4. `UNKNOWN`

Players with `UNKNOWN` are ingested but excluded from rankings.

Do not guess based on shirt number or player name.

---

# 15. Fixture ingestion workflow

At 03:00 UTC daily, for each tracked competition season:

1. read the last successful API-Football sync cursor
2. retrieve completed fixtures since that cursor from `/fixtures`
3. upsert fixture metadata and skip player-stat calls for fixtures whose latest provider payload is already processed
4. for each unseen completed fixture, retrieve `/fixtures/players?fixture={fixture_id}`
5. save each response as `RawProviderPayload` before normalization
6. upsert teams and players if needed
7. normalize into `PlayerFixture` and `PlayerFixtureMetric` (`PlayerMatchStats`)
8. update Elo chronologically using the completed fixture result
9. calculate the deterministic `PlayerMatchScore` intermediate
10. mark `stats_ingested_at`, advance the cursor only after successful fixture commits, and log data-quality warnings

Every operation must be idempotent.

A provider correction received later must update the existing normalized rows.

Use database transactions per fixture. A failure for one fixture must not rollback the entire batch.

---

# 16. Management commands

Implement these commands.

## Bootstrap/config

```bash
python manage.py sync_competitions
python manage.py sync_reference_data --season 2026-27
python manage.py seed_demo_data
python manage.py import_scoring_formula scoring_formulas/v1.json
python manage.py import_statsbomb_open_data --path /path/to/open-data/data
```

## Ingestion

```bash
python manage.py sync_fixtures --season 2026-27 --from 2026-08-01 --to 2026-09-08
python manage.py sync_recent_results --lookback-hours 72
```

## Scoring

```bash
python manage.py rebuild_elo --season 2026-27
python manage.py recompute_scores --season 2026-27 --as-of 2026-09-07
python manage.py publish_rankings --season 2026-27 --cutoff 2026-09-07T23:59:59Z
```

## Validation

```bash
python manage.py check_data_quality --season 2026-27
```

Commands must exit non-zero on fatal failures and emit human-readable summaries.

---

# 17. Scheduling

Do not run a scheduler thread from Django `AppConfig.ready()`.

The application exposes management commands and production schedules them externally with cron or systemd timers.

Required MVP schedule:

```cron
# Daily incremental API-Football ingestion at 03:00 UTC.
0 3 * * * /path/to/venv/bin/python /path/to/app/manage.py sync_recent_results --since-last-success

# Aggregate, calculate, freeze the weekly snapshot, and invalidate Redis each Monday at 06:00 UTC.
0 6 * * 1 /path/to/venv/bin/python /path/to/app/manage.py publish_rankings --season current --aggregate --invalidate-cache
```

The jobs implement these ordered pipelines:

```text
03:00 UTC daily
    │
    ▼
retrieve completed fixtures since last successful sync
    │
    ▼
for each unseen fixture ──► /fixtures/players
    │
    ▼
save RawProviderPayload
    │
    ▼
normalize ──► PlayerMatchStats
    │
    ▼
update Elo
    │
    ▼
calculate PlayerMatchScore

Monday 06:00 UTC
    │
    ▼
aggregate season statistics
    │
    ▼
calculate percentiles by position
    │
    ▼
calculate SeasonScore
    │
    ▼
freeze WeeklyRankingSnapshot
    │
    ▼
invalidate Redis
```

`current` must resolve to `Season.is_current=True`.

Document timezone assumptions clearly. Store datetimes in UTC.

---

# 18. Elo algorithm

Use Elo only to model team/opponent strength. Elo never directly determines a player's rank.

## Initial rating

```text
1500
```

To establish useful cross-league ratings, support backfilling at least one prior season before the public target season when data is available.

## Parameters

```text
K = 20
HOME_ADVANTAGE = 60 Elo points
```

## Expected result

For team A:

```text
adjusted_A = rating_A + 60 if home else rating_A
adjusted_B = rating_B
expected_A = 1 / (1 + 10 ** ((adjusted_B - adjusted_A) / 400))
```

Actual result:

```text
win  = 1.0
draw = 0.5
loss = 0.0
```

Update:

```text
new_rating_A = rating_A + K * (actual_A - expected_A)
new_rating_B = rating_B + K * (actual_B - expected_B)
```

No goal-margin multiplier in v1.

Process fixtures chronologically and use the rating immediately **before** a fixture as opponent context for player statistics in that fixture.

Persist both teams' before/after values in `TeamEloSnapshot`.

## Rebuild behavior

`rebuild_elo` must be deterministic and safe to run repeatedly.

Delete/rebuild snapshots for the requested reconstruction range inside controlled transactions rather than accumulating duplicates.

---

# 19. League-strength multiplier

For each active domestic league at calculation time:

1. collect current Elo ratings for active teams
2. calculate the league median Elo
3. calculate global median Elo across all active tracked domestic-league teams
4. derive:

```text
league_factor = 1 + ((league_median_elo - global_median_elo) / 4000)
league_factor = clamp(league_factor, 0.95, 1.05)
```

Examples:

- league 200 Elo points above global median -> `1.05`
- equal to global median -> `1.00`
- league 200 points below global median -> `0.95`

Store the resulting multiplier on `CompetitionSeason` during ranking calculation.

For Champions League fixtures, use `league_factor = 1.00`, because opponent Elo already carries cross-league strength and the competition has its own importance factor.

---

# 20. Competition/stage factor

Use these default v1 factors:

## Domestic league

```text
1.00
```

Then multiply by `league_factor`.

## UEFA Champions League

When stage can be reliably normalized:

```text
league/group phase     1.05
round of 16            1.06
quarter-final          1.07
semi-final             1.08
final                   1.10
```

When stage is unknown:

```text
1.05
```

Keep stage mapping isolated in a service and covered by tests.

---

# 21. Opponent factor

For every `PlayerFixture`, obtain the opponent's Elo immediately before the match.

Calculate:

```text
opponent_factor = 1 + ((opponent_elo_before - 1500) / 4000)
opponent_factor = clamp(opponent_factor, 0.90, 1.10)
```

Examples:

```text
1100 Elo -> 0.90
1300 Elo -> 0.95
1500 Elo -> 1.00
1700 Elo -> 1.05
1900 Elo -> 1.10
```

---

# 22. Total match context factor

For a domestic-league fixture:

```text
context_factor = opponent_factor * league_factor * 1.00
```

For UCL:

```text
context_factor = opponent_factor * 1.00 * stage_factor
```

Clamp final value:

```text
0.85 <= context_factor <= 1.20
```

Persist the factor and its components on `PlayerFixture` so public explanations do not need to reconstruct historical context from changing present-day ratings.

---

# 23. Metric aggregation rules

Metrics have one of these aggregation types:

- `COUNT_PER90`
- `RATE`
- `NEGATIVE_COUNT_PER90`
- `DERIVED_RATE`

## Positive count metric

For metrics such as goals, assists, saves, interceptions:

```text
context_adjusted_count = sum(raw_match_value * context_factor)
metric_value = context_adjusted_count / total_minutes * 90
```

Only apply context adjustment to positive count metrics configured with `context_adjust=true`.

## Negative count metric

For metrics such as `errors_leading_to_goal`:

```text
metric_value = sum(raw_value) / total_minutes * 90
```

Do not reduce a mistake penalty merely because the opponent is strong.

## Rate metric

For a rate with numerator and denominator:

```text
metric_value = sum(numerator) / sum(denominator)
```

Examples:

- pass accuracy
- duel win rate
- aerial duel win rate
- save percentage
- long-pass accuracy

Do not average individual match percentages.

If denominator is zero, metric is unavailable for that player.

## Derived clean-sheet rate

For goalkeeper clean-sheet rate:

Count only fixtures where:

- player position is GK
- player played at least 60 minutes

```text
clean_sheet = 1 when player's team conceded zero goals in fixture else 0
clean_sheet_rate = clean_sheets / eligible_gk_appearances
```

---

# 24. Cohort and eligibility

All normalization occurs **within position cohort**.

Never directly compare an attacker's raw metric percentile with a goalkeeper cohort.

For a ranking as of date `D`:

```text
required_minutes = Season.eligibility_minutes(D)
```

A player is rank-eligible only if:

- normalized position is not `UNKNOWN`
- total minutes in tracked eligible competitions >= `required_minutes`
- player has sufficient metric coverage to calculate a score

Ineligible players may have a player page but must not appear in the public numbered ranking.

Display a clear label such as:

```text
Not yet eligible — 284 / 340 required minutes
```

---

# 25. Metric coverage

Do not treat missing provider data as poor performance.

For each metric and cohort at a scoring cutoff:

```text
coverage = players_with_available_metric / cohort_players_with_min_population_minutes
```

Use a minimum population threshold of 180 minutes for coverage analysis.

A v1.1 or v1.2 metric becomes active for that calculation only when:

```text
coverage >= 0.15
```

If a metric is below 15% coverage, disable that metric for the entire cohort/calculation and renormalize remaining weights proportionally. Published v1.0 snapshots retain their original 85% gate.

Record active/disabled metrics in `coverage_breakdown`.

This behavior must be visible on the methodology/debug admin views and covered by tests.

---

# 26. Percentile scoring

For every active metric within a position cohort:

1. compute the aggregated season metric value
2. rank eligible cohort players by that metric
3. convert rank to a percentile in `0..100`

Use deterministic average ranking for tied raw values.

For a positive metric:

```text
metric_score = percentile(metric_value)
```

For a negative metric:

```text
metric_score = 100 - percentile(metric_value)
```

A lower error rate is therefore better.

Use full precision internally. Round only for display.

No z-score is required in v1.

---

# 27. V1 scoring formula

The active successor is `/scoring_formulas/v1_2.json`; keep `/scoring_formulas/v1.json` and `/scoring_formulas/v1_1.json` unchanged for historical reproducibility. V1.2 replaces the unavailable API-Football `tackles_won` input with the provider's `tackles.total` value; all weights and the 15% coverage gate remain unchanged. The active formula has this conceptual schema:

```json
{
  "version": "1.2",
  "coverage_threshold": 0.15,
  "performance_weight": 0.92,
  "availability_weight": 0.08,
  "positions": {
    "FWD": {"metrics": []},
    "MID": {"metrics": []},
    "DEF": {"metrics": []},
    "GK": {"metrics": []}
  }
}
```

The exact metric configuration follows.

## 27.1 Attackers (`FWD`)

Base performance weights:

```text
goals_per90                    0.30  positive, count, context adjusted
assists_per90                  0.16  positive, count, context adjusted
shots_on_target_per90          0.12  positive, count, context adjusted
key_passes_per90               0.12  positive, count, context adjusted
successful_dribbles_per90      0.12  positive, count, context adjusted
duel_win_rate                  0.08  positive, rate
goal_conversion_rate           0.10  positive, derived rate
TOTAL                           1.00
```

`goal_conversion_rate`:

```text
goals / shots
```

If shots are unavailable cohort-wide, the metric drops through coverage logic.

## 27.2 Midfielders (`MID`)

```text
key_passes_per90               0.16  positive, count, context adjusted
assists_per90                  0.10  positive, count, context adjusted
accurate_passes_per90          0.12  positive, count, context adjusted
pass_accuracy                  0.08  positive, rate
interceptions_per90            0.14  positive, count, context adjusted
tackles_per90                  0.12  positive, count, context adjusted
duel_win_rate                  0.10  positive, rate
successful_dribbles_per90      0.08  positive, count, context adjusted
goals_per90                    0.10  positive, count, context adjusted
TOTAL                           1.00
```

This is intentionally a blended midfielder model in MVP. Separate DM/CM/AM models are a post-MVP enhancement.

## 27.3 Defenders (`DEF`)

```text
duel_win_rate                  0.18  positive, rate
aerial_duel_win_rate           0.14  positive, rate
interceptions_per90            0.16  positive, count, context adjusted
tackles_per90                  0.16  positive, count, context adjusted
clearances_per90               0.10  positive, count, context adjusted
recoveries_per90               0.10  positive, count, context adjusted
accurate_passes_per90          0.08  positive, count, context adjusted
pass_accuracy                  0.04  positive, rate
errors_leading_to_goal_per90   0.04  negative, count, no context adjustment
TOTAL                           1.00
```

The model deliberately avoids assigning a large clean-sheet weight to defenders because it is highly team-dependent.

## 27.4 Goalkeepers (`GK`)

```text
save_percentage                0.26  positive, rate
saves_per90                    0.20  positive, count, context adjusted
clean_sheet_rate               0.15  positive, derived rate
goals_conceded_per90           0.14  negative, count, no context adjustment
penalties_saved_per90          0.10  positive, count, context adjusted
long_pass_accuracy             0.07  positive, rate
accurate_long_passes_per90     0.04  positive, count, context adjusted
errors_leading_to_goal_per90   0.04  negative, count, no context adjustment
TOTAL                           1.00
```

If a future provider reliably exposes post-shot xG / goals prevented, add it only in a new formula version; do not silently alter v1.

---

# 28. Missing metric weight renormalization

If one or more configured metrics are disabled by coverage or unavailable globally, use:

```text
active_weight_i = base_weight_i / sum(base_weight of all active metrics)
```

Do not assign zero points for a provider-wide missing metric.

Store both base and effective weights in the player's breakdown.

---

# 29. Performance score

For a player:

```text
performance_score = sum(metric_percentile_score_i * active_weight_i)
```

Range:

```text
0..100
```

---

# 30. Availability score

For ranking cutoff `D`:

```text
target_minutes = Season.availability_target_minutes(D)
availability_score = min(100, player_minutes / target_minutes * 100)
```

This rewards sustained participation but intentionally has a low total weight.

---

# 31. Final score

```text
final_score = (performance_score * 0.92) + (availability_score * 0.08)
```

Persist at least four decimal places.

Public display:

```text
92.41
```

Do not introduce arbitrary bonuses for celebrity, awards, team trophies, transfer value, salary, nationality, popularity, or media recognition.

---

# 32. Ranking order and ties

Sort eligible players by:

1. `final_score` descending using unrounded precision
2. `performance_score` descending
3. `minutes` descending
4. `player.id` ascending for deterministic final ordering

Publicly display sequential ranks.

`previous_rank` comes from the immediately preceding public `RankingSnapshot` for the same season and position.

```text
movement = previous_rank - current_rank
```

Examples:

- previous 5, current 2 -> `+3`
- previous 1, current 3 -> `-2`
- no previous entry -> `null`, display `NEW`

---

# 33. Weekly publication semantics

A weekly public snapshot is immutable.

`cutoff_at` determines what data belongs in it.

Only include fixtures where:

```text
fixture.status == FINISHED
fixture.starts_at <= cutoff_at
stats successfully ingested
```

A later provider correction does not mutate an already published snapshot automatically.

Corrections affect the next snapshot. Admin may explicitly rebuild a historical snapshot only through a deliberate management command with a `--force` flag; log that action.

---

# 34. Data-quality validation

Implement checks for at least:

- completed fixture without player stats
- duplicate provider fixture ID
- player fixture with minutes > 130
- active player with unknown position
- score with weights not summing to 1 after normalization
- scoring metric coverage below threshold
- fixture with no Elo snapshot when scoring
- player assigned to both fixture teams
- missing opponent
- negative count statistics
- pass/duel/save numerator greater than denominator

`check_data_quality` prints:

```text
ERROR
WARNING
INFO
```

Fatal calculation errors block publication.

Coverage warnings do not block publication because affected metrics are disabled and reweighted.

---

# 35. Cache design

Use Django cache backed by Redis.

Suggested keys:

```text
ranking:{season_slug}:{snapshot_id}:{position}:page:{page}
ranking-home:{season_slug}:{snapshot_id}
player:{player_id}:{snapshot_id}
player-history:{player_id}:{season_id}
compare:{snapshot_id}:{player_a_id}:{player_b_id}
```

Default TTL:

```text
24 hours
```

Invalidate relevant keys after publication.

Because snapshots are immutable, snapshot-specific caches may safely have longer TTLs.

Never store the only copy of a score in Redis.

---

# 36. Public URL design

Use stable readable URLs.

```text
/                                      home
/rankings/                              redirect/default FWD or overview
/rankings/attackers/
/rankings/midfielders/
/rankings/defenders/
/rankings/goalkeepers/
/players/<slug>/
/compare/?a=<player-slug>&b=<player-slug>
/methodology/
/methodology/changelog/
/seasons/<season-slug>/
/healthz/
/admin/
```

Use canonical URLs and proper `<title>` / meta descriptions.

---

# 37. Home page

Purpose: answer the four ranking questions immediately.

Content order:

1. compact header/logo/navigation
2. current season + data cutoff
3. four ranking blocks
4. each block shows top 5 players
5. link to full Top 100
6. short methodology statement

Example information hierarchy:

```text
2026/27 rankings
Updated 7 Sep 2026

ATTACKERS
#1 Player Name       92.41  ↑2
#2 Player Name       91.88   —
...

MIDFIELDERS
...
```

Do not use heavy hero imagery or autoplay media.

---

# 38. Ranking page

Example:

```text
/rankings/attackers/
```

Requirements:

- position title
- season selector
- latest public cutoff date
- Top 100 table
- columns:
  - rank
  - movement
  - player
  - team
  - minutes
  - 3–5 role-specific headline stats
  - score
- pagination: 25 rows/page
- links to player pages

Position tabs:

```text
Attackers | Midfielders | Defenders | Goalkeepers
```

HTMX behavior:

- clicking another position uses HTMX
- target the main ranking region only
- push browser URL
- preserve browser back/forward navigation
- normal non-HTMX requests return the full page

Do not require JavaScript for basic navigation; links must have valid `href` fallbacks.

---

# 39. Player detail page

Route:

```text
/players/<slug>/
```

Header:

- image when available
- name
- current team
- nationality
- broad position
- current rank in position
- current score
- movement
- minutes
- appearances

Score breakdown:

For each active metric display:

- human name
- aggregated raw value
- percentile score
- base weight
- effective weight

Display context summary:

- average opponent Elo
- average context factor
- domestic/UCL minutes split

Display current formula version.

Display eligibility message when not eligible.

Ranking history:

Show recent weekly snapshot history as a lightweight list or inline SVG. Do not add a heavy charting dependency solely for this.

Also show a link/button to compare the player.

---

# 40. Player comparison page

Route:

```text
/compare/?a=<slug>&b=<slug>
```

Requirements:

- two searchable/selectable players
- restrict default comparison to same broad position
- if positions differ, allow viewing but clearly state that overall position scores are normalized within different cohorts and therefore are not directly comparable
- show:
  - current rank
  - final score
  - minutes
  - metric values
  - percentile values
  - effective weights
  - context summary

HTMX:

- player selection refreshes comparison region without full reload
- push current query string to URL

Do not generate subjective prose such as “Player A is definitively better because...”. Keep comparison numerical in MVP.

---

# 41. Methodology page

Route:

```text
/methodology/
```

This page is product-critical.

Explain in plain language:

1. positions are ranked separately
2. stats come from an external football data provider
3. counting stats are normalized per 90
4. positive counting metrics are adjusted for match context
5. opponent strength uses pre-match Elo
6. league strength uses median team Elo
7. Champions League stages have a small importance factor
8. player metrics are converted to percentiles within position
9. missing provider metrics are removed cohort-wide rather than scored as zero
10. availability contributes only 8%
11. eligibility minutes scale through the season
12. ranking formula is versioned
13. public snapshots are weekly

Display exact active metric weights from the current `ScoringFormula` rather than duplicating weights manually in a hardcoded template.

---

# 42. Formula changelog

Route:

```text
/methodology/changelog/
```

List formula versions:

```text
v1.0
Activated: ...
Notes: Initial public model.
Checksum: ...

v1.1
Activated: ...
Notes: Coverage threshold 15%; other scoring weights unchanged.
Checksum: ...

v1.2
Activated: ...
Notes: API-Football tackles.total replaces unavailable tackles won; weights unchanged.
Checksum: ...
```

Future versions must be visible here.

---

# 43. HTMX behavior

Serve HTMX locally from:

```text
/static/js/htmx.min.js
```

Do not load from CDN.

Views that support fragments must detect HTMX requests and render a fragment; otherwise render the full page.

Recommended pattern:

```python
if request.headers.get("HX-Request") == "true":
    return render(request, "rankings/partials/ranking_content.html", context)
return render(request, "rankings/ranking_page.html", context)
```

Progressively enhance all HTMX links with real `href` values.

Use `hx-push-url="true"` for ranking tabs and comparison state.

Use `aria-live="polite"` on regions that are replaced.

Add a subtle loading state via `htmx-request` CSS. Avoid global spinners that block the whole page.

---

# 44. Tailwind implementation

Use Tailwind CSS 4.x compiled locally.

`static_src/app.css` should start with the appropriate Tailwind v4 import.

Add npm scripts:

```json
{
  "scripts": {
    "css:build": "...",
    "css:watch": "..."
  }
}
```

Do not use Tailwind CDN in production.

Design requirements:

- responsive/mobile-first
- light theme first
- high information density without looking like an admin dashboard
- readable tables
- score visually prominent
- position tabs easy to tap
- accessible focus states
- no unnecessary animations
- no large JS UI library

Use semantic HTML.

---

# 45. Performance requirements

The application should feel instant on warm/cached public pages.

Targets for production-like local benchmark:

- cached ranking view application processing p95 < 100 ms excluding external network latency
- no provider API call on a public page request
- no scoring calculation on a public page request
- no N+1 queries on ranking pages
- target <= 15 DB queries for a cold ranking page before fragment cache
- target <= 5 DB queries for a cached ranking page
- CSS locally cached with fingerprinted static filename in production
- HTMX locally cached

Use:

- `select_related`
- `prefetch_related`
- annotated/queryset services when useful
- Redis fragment/data caching

Add Django Debug Toolbar only in development if desired, never production.

---

# 46. Accessibility

Minimum:

- semantic headings
- keyboard navigable links/controls
- visible focus ring
- sufficient contrast
- table headers with scope
- icons never carry meaning without text/accessible label
- ranking movement represented by text/symbol plus accessible label
- HTMX replacement region uses appropriate live-region behavior
- player images have alt text or decorative empty alt as appropriate

---

# 47. SEO

Because pages are server-rendered, all meaningful content must exist in initial HTML.

Implement:

- page-specific titles
- meta descriptions
- canonical URL
- Open Graph basics
- robots.txt
- sitemap for ranking, player, methodology, season pages

Do not make rendering dependent on client JavaScript.

---

# 48. Django Admin

Register useful admin screens for:

- seasons
- competitions
- competition seasons
- teams
- players
- fixtures
- scoring formulas
- ranking snapshots
- provider sync states

Admin features:

- filters for provider/season/status/position
- search player/team/fixture
- mark competition `is_tracked`
- inspect formula JSON/checksum read-only once referenced
- inspect ranking snapshot read-only
- inspect raw provider payload read-only

Do not provide editable final rank/score fields on published snapshots.

---

# 49. Logging

Use Python/Django structured-enough logs with clear event names.

Log:

- provider request failures
- rate limiting
- ingestion start/end summary
- per-fixture ingestion failure
- data-quality validation summary
- Elo rebuild summary
- score calculation summary
- ranking publication summary
- cache invalidation errors

Never log API tokens or full authorization query strings.

For URLs containing token query parameters, sanitize before logging.

---

# 50. Error handling

Provider outage must not break public pages.

If sync fails:

- preserve last published ranking
- log failure
- command exits non-zero where appropriate
- do not publish a partially invalid snapshot

Public site should display the latest successful cutoff.

404 unknown player/season gracefully.

500 page should be minimal and not expose debug data.

---

# 51. Security

Production settings:

```text
DEBUG=False
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_PROXY_SSL_HEADER configured behind Nginx
SECURE_HSTS_SECONDS configured after HTTPS is confirmed
SECURE_CONTENT_TYPE_NOSNIFF=True
X_FRAME_OPTIONS="DENY"
```

Public MVP has no forms that modify user data except admin.

Provider secrets are server-only.

Use Django Admin authentication and strong production secret key.

Static JS/CSS should be local, allowing a strict CSP later.

---

# 52. Scoring formula validation

When importing a scoring JSON file, validate:

- `major.minor` version present
- four required positions present
- each position weights sum to 1.0 within tolerance `1e-9`
- every metric key is known
- aggregation type is valid
- direction is valid
- no negative base weights
- `performance_weight + availability_weight == 1.0`
- coverage threshold between 0 and 1

Calculate SHA-256 checksum of canonicalized JSON and persist it.

If the same version exists with a different checksum, fail loudly.

---

# 53. Determinism requirements

Given identical:

- normalized fixture data
- season dates
- Elo configuration
- scoring formula
- cutoff timestamp

`rebuild_elo`, `recompute_scores`, and `publish_rankings` must generate identical results.

Do not use random values, current wall-clock time inside scoring, or unordered queryset iteration.

Any `now()` needed operationally must not influence score math except through an explicit `as_of/cutoff` argument.

---

# 54. Query/service layer

Avoid placing complex ranking logic in views.

Use explicit services, for example:

```text
football.services.fixtures
football.services.players
ingestion.services.sync
scoring.services.elo
scoring.services.aggregation
scoring.services.percentiles
scoring.services.calculate
rankings.services.publish
rankings.services.queries
```

Views should mostly:

1. parse request
2. call query/service
3. render template

---

# 55. Ranking publication transaction

`publish_rankings` flow:

1. acquire a PostgreSQL advisory lock or other process-level publication lock
2. verify formula
3. run data-quality fatal checks
4. ensure latest scores exist for exact cutoff
5. start transaction
6. create immutable `RankingSnapshot`
7. create ordered `RankingEntry` rows for GK/DEF/MID/FWD
8. compute previous-rank movement
9. mark snapshot public
10. commit
11. invalidate caches
12. log summary

If any step before commit fails, no partial public snapshot may remain.

---

# 56. Player team shown in ranking

A player's displayed team for the snapshot should be the team from his latest `PlayerFixture` on or before the cutoff.

Do not simply show a mutable `Player.current_team` field because transfers would rewrite historical snapshots.

For snapshot immutability, either:

- persist `team` on `RankingEntry`, or
- persist enough snapshot metadata to resolve historical team deterministically.

Preferred MVP: add nullable `team FK` to `RankingEntry` and populate at publication time.

---

# 57. Headline ranking stats

For each ranking table, display these when active/available:

## FWD

- goals / 90
- assists / 90
- shots on target / 90

## MID

- key passes / 90
- interceptions / 90
- pass accuracy

## DEF

- duel win rate
- interceptions / 90
- tackles won / 90

## GK

- save percentage
- saves / 90
- clean-sheet rate

These displayed values must come from the same aggregation output used by the score, not separately recalculated template logic.

---

# 58. Public score explanation payload

`metric_breakdown` should have a stable schema similar to:

```json
{
  "goals_per90": {
    "label": "Goals / 90",
    "raw_value": 0.81,
    "percentile": 96.4,
    "base_weight": 0.30,
    "effective_weight": 0.3191,
    "active": true,
    "direction": "positive"
  }
}
```

`context_summary` example:

```json
{
  "average_opponent_elo": 1582.4,
  "average_context_factor": 1.031,
  "domestic_minutes": 1024,
  "ucl_minutes": 341
}
```

This lets templates explain a score without rerunning the scoring engine.

---

# 59. Tests — required minimum

Use pytest.

## Model tests

Test:

- uniqueness constraints
- season progress/minute thresholds
- immutable formula rule
- fixture minute validation

## Provider tests

Using recorded/sample JSON fixtures that contain no secrets:

- fixture normalization
- position normalization
- known metric mapping
- unknown metric ignored
- missing vs zero behavior
- provider retries 429
- provider retries 5xx
- no retry on ordinary 4xx except 429
- API key sanitized from logs
- API response `errors` handled on HTTP 200
- paging metadata handled when an endpoint is paginated
- Free-plan rate-limit headers recorded and quota exhaustion resumes safely
- provider-supplied player rating excluded from scoring inputs

## Ingestion tests

- same fixture synced twice creates no duplicates
- corrected provider metric updates existing row
- one fixture transaction failure does not corrupt prior fixture sync

## Elo tests

- home advantage changes expected result
- win raises winner rating
- loss lowers loser rating
- draw behavior
- chronological deterministic rebuild
- pre-match opponent rating persisted

## Scoring tests

- formula weights validate to 1
- stronger opponent raises context-adjusted positive count contribution
- negative metrics are not softened by opponent factor
- rate metrics aggregate numerator/denominator, not average percentages
- per-90 normalization correct
- metric coverage below 15% disables metric cohort-wide in v1.1/v1.2 (85% in archived v1.0)
- disabled weight gets proportionally redistributed
- negative percentile inverted
- availability score correct
- ineligible player excluded
- final score deterministic
- tie ordering deterministic

## Ranking publication tests

- four cohorts created
- rank order correct
- previous movement correct
- NEW behavior correct
- snapshot publication atomic
- public latest snapshot selection correct

## View tests

- home returns 200
- ranking pages return 200
- player page 200/404
- compare page
- methodology
- `HX-Request: true` returns fragment without full layout
- regular request returns full layout
- ranking tab pushes valid target URL markup

## Performance/query tests

Use `django_assert_num_queries` on important views where practical to catch obvious N+1 regressions.

---

# 60. End-to-end test

Provide one integration test or smoke command that:

1. creates demo season
2. seeds mock fixtures
3. rebuilds Elo
4. calculates scores
5. publishes ranking
6. asserts that all four ranking pages have at least one entry
7. asserts player detail exposes metric breakdown

This is the minimum proof that the entire pipeline works.

---

# 61. Developer setup

README must include exact commands similar to:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

cp .env.example .env

createdb football_rankings
python manage.py migrate
python manage.py import_scoring_formula scoring_formulas/v1.json
python manage.py seed_demo_data
python manage.py rebuild_elo --season current
python manage.py recompute_scores --season current
python manage.py publish_rankings --season current

npm install
npm run css:build

python manage.py runserver
```

Adapt commands for actual package configuration.

README must also document how to switch from mock to API-Football and how to run the isolated StatsBomb Open Data backtesting importer.

---

# 62. `pyproject.toml`

Use modern project metadata and dependency groups.

Expected runtime dependencies conceptually:

```text
Django 6.0.x
psycopg[binary]
django-redis
redis
httpx
python-dotenv or django-environ
whitenoise optional but acceptable
Gunicorn
```

Dev:

```text
pytest
pytest-django
pytest-cov
ruff
factory-boy
freezegun
```

Do not add DRF unless a concrete MVP requirement needs a public JSON API. It is not required here.

---

# 63. Tailwind build

Use Node only for static asset compilation, not application runtime.

Output:

```text
static/dist/app.css
```

Production deployment runs CSS build before `collectstatic`.

Keep source scan paths configured so Django templates are detected.

---

# 64. Production static files

Use Django `collectstatic`.

Nginx should serve static assets directly.

Long cache headers are allowed for hashed/versioned assets.

Do not proxy static files through Gunicorn in the recommended production setup.

---

# 65. Health endpoint

`GET /healthz/`

Return JSON:

```json
{"status": "ok"}
```

HTTP 200 when Django can connect to PostgreSQL.

Optionally report Redis as a nonfatal/degraded dependency, but do not expose credentials or infrastructure details.

---

# 66. Production cron concurrency

Commands that should not overlap must take an application lock.

At minimum:

- `rebuild_elo`
- `recompute_scores`
- `publish_rankings`

If lock already held, command exits cleanly with an explanatory log rather than running concurrently.

Use PostgreSQL advisory locks or an equivalent robust DB-based lock.

---

# 67. Data backfill strategy

For the first production import:

1. configure tracked competitions
2. sync competition/season/team/player reference data
3. backfill prior-season completed fixtures if subscription permits
4. rebuild Elo through prior season
5. ingest current-season fixtures
6. continue Elo into current season
7. calculate current scores
8. validate
9. publish first snapshot

If prior-season data is unavailable, start Elo at 1500 and display no special warning publicly, but document in admin/logs that cross-league strength will stabilize as inter-league matches accumulate.

---

# 68. Corrections and replay

Provide a service capable of renormalizing a fixture from the latest saved raw payload without requiring a new provider call.

A management command is recommended:

```bash
python manage.py replay_fixture <provider_fixture_id>
```

This should:

- load latest raw fixture payload
- rerun normalization
- update normalized fixture/player metrics
- not publish a ranking automatically

This makes provider-mapping fixes auditable and testable.

---

# 69. Formula lifecycle

MVP workflow:

1. formula JSON committed to Git
2. import command validates JSON
3. DB gets immutable version/checksum
4. active formula selected explicitly
5. scoring references formula row
6. weekly snapshot references formula row

Never make weight changes in Django Admin for an already activated formula.

A future scoring change requires a new version, e.g. `1.3`.

---

# 70. Methodology integrity test

Add a test ensuring the current methodology page obtains weights/version from the active formula rather than duplicating static values.

This prevents documentation and code from silently diverging.

---

# 71. UI component inventory

Implement reusable Django partials/components for:

- site header
- position tab navigation
- ranking table
- ranking row
- movement badge
- score badge
- player identity
- metric breakdown row
- eligibility notice
- pagination
- empty state
- error state

Do not build a generic design system beyond what is needed.

---

# 72. Empty/loading/error states

Ranking without snapshot:

```text
Rankings are being prepared for this season.
```

Player without eligible score:

```text
Not enough eligible minutes yet.
```

Missing optional metric:

Do not show `0`; show `—` or omit with an explanatory tooltip/title where useful.

HTMX error:

Fall back to normal navigation where possible. Do not leave the page permanently in a loading state.

---

# 73. Date/time handling

- store UTC datetimes in DB
- `USE_TZ=True`
- weekly cutoff explicit UTC
- display dates in a configurable site timezone, default UTC for MVP unless product copy chooses otherwise
- never use local server timezone implicitly in ranking logic

---

# 74. Database indexes

At minimum ensure indexes supporting:

```text
Fixture(provider, provider_id)
Fixture(competition_season, starts_at)
Fixture(status, starts_at)
Player(provider, provider_id)
Player(primary_position)
PlayerFixture(fixture, player)
PlayerFixture(player, fixture)
PlayerFixtureMetric(player_fixture, metric_key)
TeamEloSnapshot(team, fixture)
PlayerSeasonScore(season, formula, as_of, position)
RankingEntry(snapshot, position, rank)
RankingEntry(snapshot, player)
```

Use `EXPLAIN` only if needed; do not over-index blindly.

---

# 75. Player slug behavior

Generate readable unique slugs.

If collision:

```text
kylian-mbappe
kylian-mbappe-<stable-short-id>
```

Do not make slug changes break links when a provider changes a player's display name. Once established, preserve existing slug unless manually corrected.

---

# 76. Snapshot season archive

`/seasons/<slug>/` should display the latest public snapshot from that season and links to four cohort rankings.

Current ranking routes use current season by default.

Architecture must allow past season snapshots to remain immutable and navigable.

---

# 77. HTTP caching

For anonymous public snapshot pages, add conservative cache headers where safe.

Because weekly snapshots are immutable, archive snapshot pages may have longer cache control.

Current ranking URLs can use short browser cache plus Redis server cache.

Do not cache Django Admin publicly.

---

# 78. Search/select players for compare

MVP does not need Elasticsearch.

Implement a simple server-side player search endpoint/view returning an HTML fragment for HTMX:

```text
/players/search/?q=mbap
```

Rules:

- minimum 2 characters
- limit 10 results
- search `name`/`common_name` case-insensitively
- optionally filter by position
- HTML fragment only when called by HTMX
- rate-limiting is optional for MVP

Use PostgreSQL `icontains` initially.

---

# 79. No public REST API requirement

Do not build a parallel REST API just because the data is structured.

Django ORM/services feed Django templates directly.

A public API can be added later without changing the scoring architecture.

---

# 80. Deployment artifacts

Provide examples, not environment-specific secrets:

## `deployment/gunicorn.service.example`

- run Django WSGI application
- bind localhost or Unix socket
- restart on failure
- environment file path placeholder

## `deployment/nginx.example.conf`

- HTTPS reverse proxy placeholder
- static alias
- proxy headers
- gzip/brotli only if available, gzip sufficient

## `deployment/cron.example`

- ingestion schedule
- weekly rebuild/publish schedule

Do not embed a real domain.

---

# 81. Acceptance criteria — functional

The MVP is considered functionally complete when all are true:

- [x] ✅ App boots locally with PostgreSQL and Redis.
- [x] ✅ Demo mode works without an API-Football key.
- [x] ✅ API-Football adapter exists behind provider interface and is verified against fixture/player payloads.
- [x] ✅ StatsBomb Open Data can be imported into an isolated, non-publishing backtesting dataset.
- [x] ✅ Tracked competitions can be managed from Admin.
- [x] ✅ API-Football fixtures and player fixture stats can be ingested idempotently since the last successful sync.
- [x] ✅ API-Football raw provider payloads are retained before normalization.
- [ ] ❌ The 03:00 UTC incremental ingestion pipeline executes in the specified order.
- [ ] ❌ The Monday 06:00 UTC aggregation/publication pipeline freezes a snapshot and invalidates Redis.
- [x] ✅ Players map to GK/DEF/MID/FWD/UNKNOWN.
- [x] ✅ Elo can be rebuilt deterministically.
- [x] ✅ Opponent factor uses pre-match Elo.
- [x] ✅ League factor is derived from league team Elo medians.
- [x] ✅ Competition/stage factor is applied as specified.
- [x] ✅ V1 scoring formula is stored/imported immutably as configured version `1.0`.
- [x] ✅ Player metrics are aggregated per 90/rate correctly.
- [x] ✅ Missing cohort metrics are disabled at <15% coverage in v1.1/v1.2 and weights renormalize; v1.0 snapshots retain their 85% gate.
- [x] ✅ Percentiles are calculated within position cohort.
- [x] ✅ Eligibility minutes scale with season progress.
- [x] ✅ Availability contributes 8% of final score.
- [x] ✅ Weekly snapshots are persisted and immutable.
- [x] ✅ Rank movement compares with prior snapshot.
- [x] ✅ Home shows Top 5 for all four positions.
- [x] ✅ Position ranking pages show Top 100 with pagination.
- [x] ✅ Player detail explains score components.
- [x] ✅ Comparison page compares two players numerically.
- [x] ✅ Methodology renders active formula details.
- [x] ✅ Formula changelog exists.
- [x] ✅ HTMX ranking tabs work with URL history.
- [x] ✅ All HTMX-enhanced links still work without HTMX.
- [x] ✅ Redis caching and post-publication invalidation are implemented and locally verified.
- [x] ✅ Public requests never call API-Football.
- [x] ✅ Public requests never calculate rankings.
- [x] ✅ Admin cannot directly edit published score/rank.
- [x] ✅ Required test suite, including API-Football adapter and ingestion coverage, passes.

---

# 82. Acceptance criteria — UX/performance

- [x] ✅ Mobile layout usable at 360px width.
- [x] ✅ Desktop ranking table is readable without excessive whitespace.
- [x] ✅ Position change via HTMX feels like in-page navigation.
- [x] ✅ Browser back/forward works after HTMX ranking navigation.
- [x] ✅ No full SPA JavaScript bundle.
- [x] ✅ No external CDN required for HTMX or Tailwind.
- [x] ✅ Cached ranking pages do not show visible loading delay under normal local/regional conditions. *(Local warm-cache p95: 0.62 ms, 50 samples.)*
- [x] ✅ No obvious layout shift from client-side rendering.
- [x] ✅ Basic keyboard navigation and focus states work.

---

# 83. Acceptance criteria — data/scoring trust

- [x] ✅ Same inputs + same formula + same cutoff produce identical rankings.
- [x] ✅ Every public score identifies a formula version.
- [x] ✅ Every public score has a metric breakdown.
- [x] ✅ Context factor is inspectable.
- [x] ✅ Provider-wide missing data does not become fake zero performance.
- [x] ✅ Historical public snapshots do not change when a player transfers teams.
- [x] ✅ Historical public snapshots do not change when today's Elo ratings change.
- [x] ✅ Formula edits require a new version.

---

# 84. Build order for Codex

Implement in this order and keep the project runnable after each phase.

## Phase 1 — skeleton

- Django project/settings
- PostgreSQL/Redis config
- Tailwind build
- local HTMX asset
- base template
- health endpoint
- lint/test tooling

## Phase 2 — domain models

- season
- competition
- teams
- players
- fixtures
- normalized metrics
- migrations/admin

## Phase 3 — provider layer

- typed DTOs
- abstract provider
- mock provider
- API-Football client/adapter
- StatsBomb Open Data research/backtesting importer
- raw payload storage
- sync commands

## Phase 4 — Elo

- algorithm
- snapshots
- rebuild command
- league strength
- tests

## Phase 5 — scoring

- v1 JSON
- formula importer/validation
- aggregation
- coverage
- percentiles
- eligibility
- availability
- score persistence
- tests

## Phase 6 — publication

- ranking snapshots
- ranking entries
- movement
- locking/atomicity
- cache invalidation
- tests

## Phase 7 — UI

- home
- ranking pages
- player page
- compare page
- methodology/changelog
- HTMX fragments
- responsive Tailwind styling

## Phase 8 — production readiness

- deployment examples
- cron examples
- logging
- production settings/security
- README
- full smoke test

Do not start UI work by mocking around missing scoring architecture; implement the real pipeline first, then connect templates to it.

---

# 85. Definition of Done

The repository is done when a fresh developer can clone it and, following only the README:

1. create the environment,
2. migrate the database,
3. seed deterministic demo football data,
4. build Elo ratings,
5. calculate scores,
6. publish a weekly snapshot,
7. build Tailwind,
8. start Django,
9. open the site,
10. browse all four rankings,
11. open player score explanations,
12. compare players,
13. see the exact scoring methodology,
14. run the complete test suite successfully.

The same repository must then be switchable to API-Football by setting `API_FOOTBALL_KEY`, `FOOTBALL_PROVIDER=api_football`, and `FORMULA_VERSION=1.2`, then syncing real tracked competitions without changing ranking/business code. Sportmonks remains a future adapter.

---

# 86. Implementation guardrails

Codex must not:

- add a SPA framework
- calculate rankings in templates or views
- hardcode fake API-Football IDs
- make external football API calls from the browser
- expose provider tokens
- treat missing stats as zero without validated semantics
- compare players from different positions using a shared normalization cohort
- allow admin to manually type a final score
- modify a published formula in place
- modify weekly snapshots through ordinary provider sync
- silently swallow ingestion/calculation failures
- use an LLM to generate or change scores

When an external data field required by a metric is unavailable, preserve the specified coverage/reweighting behavior and make that absence visible in calculation metadata rather than inventing data.

---

# 87. Suggested future extensions — DO NOT IMPLEMENT IN MVP

Keep these as notes only:

- DM/CM/AM and CB/FB/WB sub-position scoring
- domestic cup support
- World Cup, Euros, Copa América, AFCON
- women's rankings
- coach rankings
- PSxG/goals-prevented goalkeeper metrics
- xG/xA enrichment
- possession-adjusted defensive stats
- yellow/red card impact on score
- score cards/share images
- public JSON API
- Sportmonks runtime adapter
- webhooks/another runtime data-provider adapter
- ranking model backtesting UI
- formula sandbox
- team pages
- weekly movers editorial page
- historical season backfill

---

# 88. Product statement to preserve during implementation

Tournament/achievement extension (authorized after MVP): see [campaign contribution](campaign_contribution.md).

- MVP extension ✅ Competition format, club/national participants and geographic scope.
- MVP extension ✅ Edition dates and explicit award-period mapping; summer national tournaments count toward the season just ended.
- MVP extension ✅ Verified winning campaigns and normalized context-weighted participation, including missed matches and extra time.
- MVP extension ✅ Immutable policies/evidence, exact stage aliases and separate national-team Elo.
- MVP extension ✅ Opt-in v1.3 achievement scoring: 5% final weight, 10-point cap, league title 3 points, UCL/major national title 5 points.
- MVP extension ✅ Persisted snapshot breakdown and player-page campaign credit.
- MVP extension ✅ Resumable Pro archive command: batches of up to 20 fixtures, live quota/reserve checks, daily 03:00 UTC scheduler, six club and seven national competition types, season priorities and verified-publication gates.
- MVP extension ✅ Public roadmap completion markers and methodology covering national award periods, separate Elo and trophy participation rules.
- MVP extension ✅ P1 option A: covered-match archive rankings, immutable snapshot coverage notices, explicit omission of unavailable player payloads and unchanged winning-campaign completeness checks.
- MVP extension ❌ Production activation and complete verified trophy/campaign data; historical snapshots remain untouched.

The site is not trying to prove that mathematics eliminates every subjective choice in football analysis.

Its value proposition is narrower and stronger:

> The inputs, rules, weights, context adjustments, and results are explicit, consistent, reproducible, and applied equally to every eligible player within the same position.

The implementation should reinforce that principle everywhere: deterministic scoring, immutable formula versions, retained raw data, weekly snapshots, and a public methodology.
