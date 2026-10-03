from datetime import timedelta
from unittest.mock import patch

import test_player_presentations
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.football.models import Player, Position, Season
from apps.ingestion.models import ProviderSyncState
from apps.rankings.models import RankingEntry, RankingSnapshot
from apps.rankings.services.queries import present_category_notices, present_movement
from apps.scoring.models import ScoringFormula

changed_player = test_player_presentations.changed_player


def advance(entry, *, cutoff=None, published=None, position=Position.FWD, formula=None, public=True):
    snapshot = RankingSnapshot.objects.create(season=entry.snapshot.season, formula=formula or entry.snapshot.formula,
        cutoff_at=cutoff or entry.snapshot.cutoff_at+timedelta(days=1),
        published_at=published or entry.snapshot.published_at+timedelta(days=1), is_public=public)
    return RankingEntry.objects.create(snapshot=snapshot, player=entry.player, position=position, rank=1,
        score=entry.score, minutes=entry.minutes, context_summary=entry.context_summary)


def shown(entry):
    present_movement([entry], entry.snapshot)
    return present_category_notices([entry], entry.snapshot)[0]


def test_notice_lasts_exactly_three_forward_updates_on_both_pages_and_movement_stays_real(changed_player):
    player, first = changed_player
    client = Client()
    entries = [first]
    for _ in range(3):
        entries.append(advance(entries[-1]))
    # Check historical selections directly, and the current public page at each step.
    for index, entry in enumerate(entries, 1):
        row = shown(entry)
        assert row.category_change_notice == (index <= 3)
        assert row.category_change_update == (index if index <= 3 else None)
        if index == 1:
            assert row.movement_state == "new_category"
        else:
            assert row.movement_state == "existing" and row.display_movement == 0
    # Temporarily make only each cutoff public; later-published rows must not leak into it.
    for index, entry in enumerate(entries, 1):
        RankingSnapshot.objects.filter(pk__in=[item.snapshot_id for item in entries]).update(
            is_public=False)
        RankingSnapshot.objects.filter(pk__in=[item.snapshot_id for item in entries[:index]]).update(is_public=True)
        cache.clear()
        with patch("apps.ingestion.providers.api_football.ApiFootballProvider._request", side_effect=AssertionError("No API")):
            home = client.get("/")
            detail = client.get(f"/players/{player.slug}/")
        assert (b'class="category-info"' in home.content) == (index <= 3)
        assert (b'class="category-info"' in detail.content) == (index <= 3)
        assert home.content.count(b'class="category-info"') == (1 if index <= 3 else 0)
        assert detail.content.count(b'class="category-info"') == (1 if index <= 3 else 0)
        assert (b"NEW category" in detail.content) == (index == 1)
        if index in (2, 3):
            assert b'aria-label="No change"' in detail.content
            eyebrow = detail.content.split(b'<p class="eyebrow">', 1)[1].split(b"</p>", 1)[0]
            assert b'class="category-info"' in eyebrow


def test_failed_sync_private_publication_and_backdated_replay_do_not_consume_window(changed_player):
    _, first = changed_player
    ProviderSyncState.objects.create(provider="api_football", sync_key="archive-backfill",
        metadata={"status": "failed"}, last_error="No publication")
    advance(first, public=False)
    advance(first, cutoff=first.snapshot.cutoff_at-timedelta(days=3),
        published=first.snapshot.published_at+timedelta(hours=1), position=Position.MID)
    second = advance(first, cutoff=first.snapshot.cutoff_at+timedelta(days=2),
        published=first.snapshot.published_at+timedelta(days=2))
    assert shown(second).category_change_update == 2
    assert shown(second).display_movement == 0


def test_same_cutoff_republication_does_not_age_notice_and_change_starts_new_window(changed_player):
    _, first = changed_player
    revision_formula = ScoringFormula.objects.create(version="revision", config={}, checksum_sha256="test", is_active=True)
    revised = advance(first, cutoff=first.snapshot.cutoff_at, published=first.snapshot.published_at+timedelta(hours=1),
        formula=revision_formula)
    assert shown(revised).category_change_update == 1
    second = advance(revised, cutoff=first.snapshot.cutoff_at+timedelta(days=1))
    assert shown(second).category_change_update == 2
    changed = advance(second, cutoff=second.snapshot.cutoff_at, published=second.snapshot.published_at+timedelta(hours=1),
        formula=first.snapshot.formula, position=Position.MID)
    # Activate this correction for the same-date selection, as production does.
    ScoringFormula.objects.filter(pk=revision_formula.pk).update(is_active=False)
    ScoringFormula.objects.filter(pk=first.snapshot.formula_id).update(is_active=True)
    assert shown(changed).category_change_update == 1


def test_same_cutoff_category_correction_survives_later_backdated_replay(changed_player):
    player, first = changed_player
    legacy = ScoringFormula.objects.create(version="legacy", config={}, checksum_sha256="legacy")
    prior = RankingSnapshot.objects.filter(season=first.snapshot.season).exclude(pk=first.snapshot_id).first()
    RankingSnapshot.objects.filter(pk=prior.pk).update(formula=legacy, cutoff_at=first.snapshot.cutoff_at)
    # This mirrors the production correction: MID -> FWD at one cutoff, then
    # earlier MID replay weeks published later, followed by a forward FWD update.
    for day in (21, 14, 7):
        advance(first, cutoff=first.snapshot.cutoff_at-timedelta(days=day),
            published=first.snapshot.published_at+timedelta(hours=day), formula=legacy, position=Position.MID)
    second = advance(first, published=first.snapshot.published_at+timedelta(days=2))
    assert shown(first).category_change_update == 1
    assert shown(second).category_change_update == 2 and second.movement_state == "existing"
    assert Client().get(f"/players/{player.slug}/").content.count(b'class="category-info"') == 1


def test_same_cutoff_inactive_formula_replay_does_not_reset_notice(changed_player):
    _, first = changed_player
    ScoringFormula.objects.filter(pk=first.snapshot.formula_id).update(is_active=True)
    legacy = ScoringFormula.objects.create(version="inactive", config={}, checksum_sha256="inactive")
    advance(first, cutoff=first.snapshot.cutoff_at, published=first.snapshot.published_at+timedelta(hours=1),
        formula=legacy, position=Position.MID)
    second = advance(first)
    assert shown(second).category_change_update == 2


def test_backdated_correction_cannot_rewrite_notice_on_an_already_published_page(changed_player):
    _, first = changed_player
    second = advance(first)
    frozen = list(RankingEntry.objects.filter(snapshot=second.snapshot).values())
    later_formula = ScoringFormula.objects.create(version="later", config={}, checksum_sha256="later", is_active=True)
    advance(first, cutoff=first.snapshot.cutoff_at, published=second.snapshot.published_at+timedelta(days=1),
        position=Position.MID, formula=later_formula)
    with CaptureQueriesContext(connection) as queries:
        assert shown(second).category_change_update == 2
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)
    assert list(RankingEntry.objects.filter(snapshot=second.snapshot).values()) == frozen


def test_category_change_window_is_season_local_and_no_history_is_not_a_change(changed_player):
    player, first = changed_player
    season = Season.objects.create(name="2025/26", slug="2025-26", starts_on=first.snapshot.season.starts_on-timedelta(days=365),
        ends_on=first.snapshot.season.ends_on-timedelta(days=365))
    other = RankingSnapshot.objects.create(season=season, formula=first.snapshot.formula,
        cutoff_at=first.snapshot.cutoff_at-timedelta(days=1), published_at=first.snapshot.published_at-timedelta(days=1),
        is_public=True)
    row = RankingEntry.objects.create(snapshot=other, player=player, position=Position.DEF, rank=1, score=80, minutes=484)
    assert not shown(row).category_change_notice
    assert shown(first).category_change_update == 1


def test_notice_queries_are_bulk_and_do_not_scale_with_number_of_players(changed_player):
    player, first = changed_player
    other = Player.objects.create(provider="mock", provider_id="other", name="Other")
    row = RankingEntry.objects.create(snapshot=first.snapshot, player=other, position=Position.DEF, rank=1, score=80, minutes=484)
    with CaptureQueriesContext(connection) as one:
        present_category_notices([first], first.snapshot)
    with CaptureQueriesContext(connection) as two:
        present_category_notices([first, row], first.snapshot)
    assert len(one) == len(two) == 2
    assert not row.category_change_notice
