"""Coverage evidence frozen with each new snapshot, never inferred for historical ones."""
from django.db.models import Count
from apps.football.award_periods import fixtures_for_award_period


def snapshot_coverage(season,cutoff,allow_unavailable=False):
    fixtures=fixtures_for_award_period(season).filter(status="FINISHED",starts_at__lte=cutoff)
    missing=fixtures.filter(stats_ingested_at__isnull=True)
    expected=fixtures.count(); covered=fixtures.filter(stats_ingested_at__isnull=False).count()
    unavailable=missing.filter(player_data_unavailable_at__isnull=False).count()
    groups=missing.values("competition_season__competition__name","stage_name").annotate(count=Count("pk")).order_by("competition_season__competition__name","stage_name")
    return {"policy":"covered_matches" if allow_unavailable else "complete_player_data",
        "expected":expected,"covered":covered,"unavailable":unavailable,"pending":expected-covered-unavailable,
        "percent":round(100*covered/expected,1) if expected else 0,"incomplete":covered<expected,
        "gaps":[{"competition":row["competition_season__competition__name"],"stage":row["stage_name"] or "Unspecified stage","fixtures":row["count"]} for row in groups]}
