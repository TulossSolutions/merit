"""Plain-language production job alerts, deduplicated after successful mail submission."""
import hashlib
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.core.services import application_lock
from apps.ingestion.models import ProviderSyncState, RawProviderPayload

logger = logging.getLogger(__name__)
JOBS = {"archive-backfill": "Archive import", "weekly-ranking-replay": "Weekly ranking replay"}


def safe_reason(reason):
    reason = str(reason or "No additional reason was recorded.")
    for key in ("API_FOOTBALL_KEY", "EMAIL_HOST_PASSWORD", "SECRET_KEY", "DATABASE_URL"):
        secret = getattr(settings, key, "")
        if secret:
            reason = reason.replace(secret, "[redacted]")
    return reason[:2000]


def incidents(job, service_result="success"):
    state = ProviderSyncState.objects.filter(provider="api_football", sync_key=job).first()
    name = JOBS[job]
    found = []
    if service_result not in ("", "success") or (state and (state.last_error or state.metadata.get("status") == "failed")):
        cycle = state.last_success_at.isoformat() if state and state.last_success_at else "initial"
        found.append((f"job_failed:{cycle}", f"{name} failed", safe_reason((state.last_error if state else "") or service_result),
            "The scheduled job stopped. New data or ranking history may be delayed until the issue is resolved."))
    if not state:
        return found
    for slug, period in state.metadata.get("periods", {}).items():
        if job == "archive-backfill" and period.get("publication") in (
                "blocked_by_data_quality", "waiting_for_verified_trophies", "waiting_for_verified_player_profiles"):
            found.append((f"publication:{slug}", f"Ranking publication rejected for {slug}", safe_reason(period.get("reason", period["publication"])),
                "New rankings for this season were withheld because required evidence or validation is missing. Existing published rankings remain available."))
        if job == "weekly-ranking-replay":
            for week in period.get("weeks", []):
                if week.get("status") == "blocked":
                    found.append((f"week:{slug}:{week['cutoff']}", f"Weekly ranking rejected for {slug} at {week['cutoff']}", safe_reason(week.get("reason")),
                        "This historical week could not be published. Other verifiable weeks can continue; this week needs evidence review."))
    if job == "archive-backfill" and state.last_attempt_at:
        sources = RawProviderPayload.objects.filter(provider="api_football", resource_type="fixture",
            received_at__gte=state.last_attempt_at, payload__has_key="merit_identity_review")
        for source in sources:
            review = source.payload["merit_identity_review"]
            fixture = source.payload.get("fixture", {})
            teams = fixture.get("teams", {})
            home = (teams.get("home") or {}).get("name", "Home")
            away = (teams.get("away") or {}).get("name", "Away")
            found.append((f"fixture:{source.provider_resource_id}", f"Player statistics rejected: {home} vs {away}",
                f"Fixture {source.provider_resource_id}: the provider assigned player ID 0 to {len(review['players'])} players who played. Their identities cannot be verified.",
                "The entire match is omitted from player scoring and shown as an incomplete-coverage gap. Other matches continue importing. No player identities or statistics were invented."))
    return found


def notify_job(job, service_result="success"):
    if not settings.MERIT_PRODUCTION_ALERTS:
        return 0
    with application_lock("production_email_alerts") as acquired:
        if not acquired:
            return 0
        sent = 0
        for key, title, reason, impact in incidents(job, service_result):
            signature = hashlib.sha256(f"{job}:{key}:{reason}".encode()).hexdigest()
            receipt, _ = ProviderSyncState.objects.get_or_create(provider="merit_alerts", sync_key=signature)
            if receipt.last_success_at:
                continue
            body = f"Merit production alert\n\nIssue: {title}\n\nWhat happened: {reason}\n\nWhat this means: {impact}\n\nSite: https://x.tuloss.com/merit/\nJob: {JOBS[job]}\n"
            receipt.last_attempt_at = timezone.now()
            try:
                accepted = send_mail(f"[Merit] {title}", body, settings.DEFAULT_FROM_EMAIL, [settings.MERIT_ALERT_EMAIL])
                if accepted != 1:
                    raise RuntimeError("Mail transport did not accept the alert")
            except Exception as exc:
                receipt.last_error = safe_reason(exc)
                receipt.metadata = {"job": job, "issue": key, "delivery": "failed"}
                receipt.save()
                logger.exception("production_alert_delivery_failed job=%s issue=%s", job, key)
                raise
            receipt.last_success_at = timezone.now()
            receipt.last_error = ""
            receipt.metadata = {"job": job, "issue": key, "delivery": "accepted_by_mail_transport"}
            receipt.save()
            sent += 1
        return sent
