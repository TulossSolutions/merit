from decimal import Decimal
from django.db.models import Count,F,Q
from apps.football.models import Fixture,Player,PlayerFixture,PlayerFixtureMetric
from apps.scoring.models import PlayerSeasonScore
from apps.football.award_periods import fixtures_for_award_period

def check_quality(season,as_of=None,*,allow_unavailable=False):
    issues=[]
    def add(level,code,count):
        if count: issues.append((level,code,count))
    fixtures=fixtures_for_award_period(season)
    if as_of is not None: fixtures=fixtures.filter(starts_at__lte=as_of)
    missing=fixtures.filter(status=Fixture.Status.FINISHED,stats_ingested_at__isnull=True)
    if allow_unavailable:
        add("WARNING","provider_player_stats_unavailable",missing.filter(player_data_unavailable_at__isnull=False).count())
        missing=missing.filter(player_data_unavailable_at__isnull=True)
    add("ERROR","completed_fixture_without_player_stats",missing.count())
    scoring_fixtures=fixtures.exclude(stats_ingested_at__isnull=True,player_data_unavailable_at__isnull=False) if allow_unavailable else fixtures
    add("ERROR","player_minutes_over_130",PlayerFixture.objects.filter(fixture__in=scoring_fixtures,minutes__gt=130).count())
    add("WARNING","active_player_unknown_position",Player.objects.filter(active=True,primary_position="UNKNOWN").count())
    add("ERROR","finished_fixture_missing_elo",fixtures.filter(status=Fixture.Status.FINISHED).annotate(n=Count("teamelosnapshot")).exclude(n=2).count())
    add("ERROR","missing_opponent",PlayerFixture.objects.filter(fixture__in=scoring_fixtures,opponent__isnull=True).count())
    add("ERROR","same_team_and_opponent",PlayerFixture.objects.filter(fixture__in=scoring_fixtures,team=F("opponent")).count())
    add("ERROR","player_assigned_outside_fixture_teams",PlayerFixture.objects.filter(fixture__in=scoring_fixtures).exclude(Q(team=F("fixture__home_team"))|Q(team=F("fixture__away_team"))).count())
    add("ERROR","negative_count_statistics",PlayerFixtureMetric.objects.filter(player_fixture__fixture__in=scoring_fixtures,value__lt=0).count())
    add("ERROR","numerator_greater_than_denominator",PlayerFixtureMetric.objects.filter(player_fixture__fixture__in=scoring_fixtures,numerator__gt=F("denominator")).count())
    for score in PlayerSeasonScore.objects.filter(season=season):
        total=sum((Decimal(str(v["effective_weight"])) for v in score.metric_breakdown.values() if v.get("active")),Decimal("0"))
        if score.metric_breakdown and abs(total-1)>Decimal("0.00001"): issues.append(("ERROR","effective_weights_not_one",score.pk))
        disabled=sum(1 for value in score.coverage_breakdown.values() if not value.get("active"))
        if disabled: issues.append(("WARNING","metrics_below_coverage_threshold",disabled))
    return issues
