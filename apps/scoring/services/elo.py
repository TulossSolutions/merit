from decimal import Decimal
import logging
from statistics import median
from django.db import transaction
from django.utils import timezone
from apps.football.models import Competition, Fixture, TeamCompetitionSeason, TeamEloSnapshot
from .context import league_factor, match_context, stage_factor
from .campaigns import performance_context_weight
from apps.football.award_periods import fixtures_for_award_period

INITIAL=Decimal("1500"); K=Decimal("20"); HOME_ADVANTAGE=Decimal("60")
logger=logging.getLogger(__name__)
def expected(a,b,home=False):
    adjusted=a+(HOME_ADVANTAGE if home else 0)
    return Decimal("1")/(Decimal("1")+(Decimal("10")**((b-adjusted)/Decimal("400"))))

@transaction.atomic
def rebuild_elo(season):
    logger.info("elo_rebuild_start season=%s",season.slug)
    fixtures=list(fixtures_for_award_period(season).filter(status=Fixture.Status.FINISHED).order_by("starts_at","provider_id").select_related("home_team","away_team","competition_season__competition","competition_season__context_policy"))
    TeamEloSnapshot.objects.filter(fixture__in=fixtures).delete(); ratings={}
    for fixture in fixtures:
        pool="national" if fixture.competition_season.competition.participant_type==Competition.Participants.NATIONAL else "club"
        home=ratings.get((pool,fixture.home_team_id),INITIAL); away=ratings.get((pool,fixture.away_team_id),INITIAL); eh=expected(home,away,not fixture.neutral_venue); ea=Decimal("1")-eh
        if fixture.home_score>fixture.away_score: ah,aa=Decimal("1"),Decimal("0")
        elif fixture.home_score<fixture.away_score: ah,aa=Decimal("0"),Decimal("1")
        else: ah=aa=Decimal("0.5")
        nh=home+K*(ah-eh); na=away+K*(aa-ea)
        TeamEloSnapshot.objects.bulk_create([TeamEloSnapshot(fixture=fixture,team=fixture.home_team,rating_before=home,rating_after=nh,expected_result=eh,actual_result=ah,is_home=True),TeamEloSnapshot(fixture=fixture,team=fixture.away_team,rating_before=away,rating_after=na,expected_result=ea,actual_result=aa,is_home=False)])
        ratings[(pool,fixture.home_team_id)]=nh; ratings[(pool,fixture.away_team_id)]=na
    domestic=[]
    for cs in season.competitionseason_set.filter(is_active=True,competition__competition_type=Competition.Type.DOMESTIC_LEAGUE).exclude(competition__participant_type=Competition.Participants.NATIONAL):
        vals=[ratings.get(("club",x.team_id),INITIAL) for x in TeamCompetitionSeason.objects.filter(competition_season=cs,active=True)]
        if vals: domestic.extend(vals); cs.league_strength_rating=median(vals); cs.save(update_fields=["league_strength_rating"])
    global_med=median(domestic) if domestic else INITIAL
    for cs in season.competitionseason_set.filter(is_active=True):
        factor=Decimal("1") if cs.competition.competition_type!=Competition.Type.DOMESTIC_LEAGUE or cs.competition.participant_type==Competition.Participants.NATIONAL else league_factor(cs.league_strength_rating or INITIAL,global_med)
        cs.league_strength_multiplier=factor; cs.last_strength_calculated_at=timezone.now(); cs.save(update_fields=["league_strength_multiplier","last_strength_calculated_at"])
    for fixture in fixtures:
        snapshots={s.team_id:s for s in fixture.teamelosnapshot_set.all()}; comp=fixture.competition_season.competition
        competition=stage_factor(fixture.stage_name) if comp.competition_type==Competition.Type.UCL else Decimal("1")
        league=Decimal("1") if comp.competition_type==Competition.Type.UCL else fixture.competition_season.league_strength_multiplier
        if fixture.competition_season.context_policy_id:
            competition=performance_context_weight(fixture)
        elif comp.competition_type not in (Competition.Type.UCL,Competition.Type.DOMESTIC_LEAGUE) or comp.participant_type==Competition.Participants.NATIONAL:
            raise ValueError("New tournaments require an explicit versioned context policy before Elo/scoring.")
        for pf in fixture.playerfixture_set.all():
            opp=snapshots.get(pf.opponent_id)
            if opp:
                pf.opponent_elo_before=opp.rating_before; pf.competition_factor=competition; pf.league_factor=league; pf.context_factor=match_context(opp.rating_before,league,competition); pf.save(update_fields=["opponent_elo_before","competition_factor","league_factor","context_factor","updated_at"])
    logger.info("elo_rebuild_complete season=%s fixtures=%s",season.slug,len(fixtures)); return len(fixtures)
