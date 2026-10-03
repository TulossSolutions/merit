from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.core.alerts import incidents, notify_job
from apps.ingestion.models import ProviderSyncState, RawProviderPayload

pytestmark = pytest.mark.django_db


@pytest.fixture
def failed_archive():
    return ProviderSyncState.objects.create(provider="api_football", sync_key="archive-backfill",
        last_success_at=timezone.now()-timedelta(days=1), last_attempt_at=timezone.now(),
        last_error="Duplicate canonical player in fixture bundle", metadata={"status": "failed"})


@override_settings(MERIT_PRODUCTION_ALERTS=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_failure_email_plain_language_recipient_dedup_and_new_failure_cycle(failed_archive):
    assert notify_job("archive-backfill") == 1
    assert mail.outbox[0].to == ["hello@tuloss.com"]
    assert "Duplicate canonical player" in mail.outbox[0].body
    assert "New data or ranking history may be delayed" in mail.outbox[0].body
    assert notify_job("archive-backfill") == 0
    failed_archive.last_success_at = timezone.now()
    failed_archive.save()
    assert notify_job("archive-backfill") == 1


@override_settings(MERIT_PRODUCTION_ALERTS=False)
def test_disabled_alerts_do_not_send_or_write(failed_archive):
    with patch("apps.core.alerts.send_mail") as sender:
        assert notify_job("archive-backfill") == 0
        sender.assert_not_called()
    assert not ProviderSyncState.objects.filter(provider="merit_alerts").exists()


@override_settings(MERIT_PRODUCTION_ALERTS=True)
def test_failed_transport_is_retryable_and_secrets_redacted(failed_archive):
    with override_settings(API_FOOTBALL_KEY="very-secret"):
        failed_archive.last_error = "Failed very-secret"
        failed_archive.save()
        with patch("apps.core.alerts.send_mail", side_effect=RuntimeError("very-secret")):
            with pytest.raises(RuntimeError):
                notify_job("archive-backfill")
        receipt = ProviderSyncState.objects.get(provider="merit_alerts")
        assert not receipt.last_success_at
        assert receipt.last_error == "[redacted]"
        with patch("apps.core.alerts.send_mail", return_value=1) as sender:
            assert notify_job("archive-backfill") == 1
            assert "very-secret" not in sender.call_args.args[1]


def test_systemd_timeout_and_rejected_publication():
    assert incidents("weekly-ranking-replay", "timeout")[0][2] == "timeout"
    ProviderSyncState.objects.create(provider="api_football", sync_key="weekly-ranking-replay",
        metadata={"periods": {"2025-26": {"weeks": [
            {"cutoff": "2026-06-01", "status": "blocked", "reason": "Missing trophy evidence"},
            {"cutoff": "2026-05-25", "status": "published"}]}}})
    found = incidents("weekly-ranking-replay")
    assert len(found) == 1 and found[0][0] == "week:2025-26:2026-06-01"
    assert found[0][2] == "Missing trophy evidence"


def test_quarantined_fixture_alert_includes_identity_issue_and_scoring_impact(failed_archive):
    failed_archive.last_error = ""
    failed_archive.metadata = {"status": "coverage_gaps", "periods": {
        "2024-25": {"publication": "blocked_by_data_quality", "reason": "Invalid weights"}}}
    failed_archive.save()
    RawProviderPayload.objects.create(provider="api_football", resource_type="fixture", provider_resource_id="1528916",
        request_path="fixtures", http_status=200, payload_sha256="test", payload={
            "fixture": {"teams": {"home": {"name": "Azerbaijan"}, "away": {"name": "Liechtenstein"}}},
            "merit_identity_review": {"players": [{"name": "Unknown"}]}})
    found = incidents("archive-backfill")
    assert len(found) == 2
    assert found[0][2] == "Invalid weights"
    assert found[1][0] == "fixture:1528916"
    assert "Azerbaijan vs Liechtenstein" in found[1][1]
    assert "player ID 0" in found[1][2]
    assert "entire match is omitted" in found[1][3]
