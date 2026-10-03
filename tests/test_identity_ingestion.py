from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from django.core.exceptions import ValidationError

from apps.football.models import Competition, CompetitionSeason, Player, PlayerFixture, PlayerFixtureMetric, Season
from apps.ingestion.models import RawProviderPayload
from apps.ingestion.providers.base import ProviderFixture, ProviderFixtureBundle, ProviderParticipation, ProviderPlayer, ProviderTeam
from apps.ingestion.services.sync import ingest_fixture_bundle

pytestmark = pytest.mark.django_db


@pytest.fixture
def bundle_and_edition():
    season = Season.objects.create(name="2026/27", slug="2026-27", starts_on=date(2026, 8, 1), ends_on=date(2027, 7, 31))
    competition = Competition.objects.create(provider="api_football", provider_id="1", name="Test league", slug="test",
        competition_type="DOMESTIC_LEAGUE", is_tracked=True)
    edition = CompetitionSeason.objects.create(competition=competition, season=season)
    fixture = ProviderFixture("1", "1:2026", ProviderTeam("10", "Home"), ProviderTeam("20", "Away"),
        datetime(2026, 10, 1, tzinfo=timezone.utc), "FINISHED", 0, 0)
    rows = tuple(ProviderParticipation(ProviderPlayer(str(team), f"Player {team}"), str(team), str(30-team),
        "FWD", True, 90) for team in (10, 20))
    return ProviderFixtureBundle(fixture, rows, {"original_evidence": True}), edition


def unidentified(minutes, name="Unknown"):
    return ProviderParticipation(ProviderPlayer("0", name), "10", "20", "FWD", False, minutes)


def test_zero_minute_id_zero_rows_are_discarded_without_creating_a_player(bundle_and_edition):
    bundle, edition = bundle_and_edition
    bundle = replace(bundle, participations=bundle.participations + (unidentified(0), unidentified(0, "Other")))
    fixture = ingest_fixture_bundle(bundle, edition, "api_football")
    assert fixture.stats_ingested_at and fixture.player_data_unavailable_at is None
    assert fixture.playerfixture_set.count() == 2
    assert not Player.objects.filter(provider_id="0").exists()
    assert RawProviderPayload.objects.get().payload == bundle.raw_payload


def test_playing_id_zero_quarantines_whole_fixture_and_retains_evidence(bundle_and_edition):
    bundle, edition = bundle_and_edition
    bundle = replace(bundle, participations=bundle.participations + (unidentified(15), unidentified(0, "Unused")))
    fixture = ingest_fixture_bundle(bundle, edition, "api_football")
    assert fixture.stats_ingested_at is None and fixture.player_data_unavailable_at
    assert not Player.objects.exists()
    assert not PlayerFixture.objects.exists()
    assert not PlayerFixtureMetric.objects.exists()
    payload = RawProviderPayload.objects.get().payload
    assert payload["original_evidence"] is True
    assert payload["merit_identity_review"]["players"] == [{"name": "Unknown", "team_id": "10", "minutes": 15}]
    # The next usable fixture continues; quarantining does not abort a batch.
    next_fixture = ingest_fixture_bundle(replace(bundle, fixture=replace(bundle.fixture, id="2"),
        participations=bundle.participations[:2]), edition, "api_football")
    assert next_fixture.stats_ingested_at
    assert PlayerFixture.objects.count() == 2


def test_duplicate_nonzero_identity_still_fails_atomically(bundle_and_edition):
    bundle, edition = bundle_and_edition
    with pytest.raises(ValidationError, match="Duplicate canonical"):
        ingest_fixture_bundle(replace(bundle, participations=bundle.participations + (bundle.participations[0],)),
            edition, "api_football")
    assert not RawProviderPayload.objects.exists()
    assert not Player.objects.exists()


def test_quarantine_removes_existing_fixture_from_score_population(bundle_and_edition):
    bundle, edition = bundle_and_edition
    fixture = ingest_fixture_bundle(bundle, edition, "api_football")
    assert fixture.stats_ingested_at
    fixture = ingest_fixture_bundle(replace(bundle, participations=bundle.participations + (unidentified(15),)),
        edition, "api_football")
    assert fixture.stats_ingested_at is None
    assert not PlayerFixture.objects.filter(fixture__stats_ingested_at__isnull=False).exists()
