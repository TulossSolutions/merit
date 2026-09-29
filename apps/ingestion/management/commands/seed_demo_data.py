from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from apps.football.models import Competition,CompetitionSeason,Fixture,Player,PlayerFixture,PlayerFixtureMetric,PlayerTeamSeason,Position,Season,Team,TeamCompetitionSeason

METRICS=("goals","assists","shots","shots_on_target","key_passes","passes","accurate_passes","tackles_won","interceptions","clearances","recoveries","duels","aerial_duels","successful_dribbles","goals_conceded","saves","penalties_saved","long_passes","accurate_long_passes","errors_leading_to_goal","tackles")
class Command(BaseCommand):
    @transaction.atomic
    def handle(self,*args,**opts):
        season,_=Season.objects.update_or_create(slug="2026-27",defaults={"name":"2026/27","starts_on":date(2026,8,1),"ends_on":date(2027,5,31),"is_current":True,"is_published":True})
        competitions=[]
        for cid,name,kind in [("demo-domestic","Demo Premier League",Competition.Type.DOMESTIC_LEAGUE),("demo-ucl","Demo Champions League",Competition.Type.UCL)]:
            comp,_=Competition.objects.update_or_create(provider="mock",provider_id=cid,defaults={"name":name,"slug":slugify(name),"competition_type":kind,"is_tracked":True,"base_importance":Decimal("1.05") if kind==Competition.Type.UCL else Decimal("1")})
            cs,_=CompetitionSeason.objects.update_or_create(competition=comp,season=season,defaults={"provider_season_id":f"{cid}-2026","is_active":True}); competitions.append(cs)
        teams=[]
        for i in range(8):
            team,_=Team.objects.update_or_create(provider="mock",provider_id=f"team-{i+1}",defaults={"name":f"Demo City {i+1}","slug":f"demo-city-{i+1}"}); teams.append(team)
            for cs in competitions: TeamCompetitionSeason.objects.get_or_create(team=team,competition_season=cs)
        players={}
        pattern=[Position.GK,Position.DEF,Position.DEF,Position.MID,Position.FWD,Position.FWD]
        for ti,team in enumerate(teams):
            players[team.id]=[]
            for pi,position in enumerate(pattern):
                number=ti*len(pattern)+pi+1; player,_=Player.objects.update_or_create(provider="mock",provider_id=f"player-{number}",defaults={"name":f"Demo Player {number}","slug":f"demo-player-{number}","primary_position":position,"nationality_code":"FRA"})
                PlayerTeamSeason.objects.get_or_create(player=player,team=team,season=season,competition_season=competitions[0]); players[team.id].append(player)
        pairs=[]
        for rnd in range(6):
            for i in range(4): pairs.append((teams[(i+rnd)%8],teams[(7-i+rnd)%8]))
        for idx,(home,away) in enumerate(pairs):
            fixture,_=Fixture.objects.update_or_create(provider="mock",provider_id=f"fixture-{idx+1}",defaults={"competition_season":competitions[1] if idx%6==0 else competitions[0],"home_team":home,"away_team":away,"starts_at":datetime(2026,8,2,tzinfo=timezone.utc)+timedelta(days=idx*2),"status":Fixture.Status.FINISHED,"home_score":idx%4,"away_score":(idx+1)%3,"stage_name":"League phase" if idx%6==0 else None,"stats_ingested_at":datetime(2026,9,30,tzinfo=timezone.utc)})
            for team,opponent in ((home,away),(away,home)):
                for pi,player in enumerate(players[team.id]):
                    pf,_=PlayerFixture.objects.update_or_create(fixture=fixture,player=player,defaults={"team":team,"opponent":opponent,"position":player.primary_position,"started":True,"minutes":90})
                    quality=(player.id%7)+1
                    for mi,key in enumerate(METRICS):
                        value=Decimal((quality+idx+mi)%5)
                        numerator=denominator=None
                        if key=="passes": denominator=Decimal(35+quality); numerator=Decimal(27+quality)
                        elif key=="duels": denominator=Decimal(8+quality); numerator=Decimal(4+quality)
                        elif key=="aerial_duels": denominator=Decimal(5+quality); numerator=Decimal(2+quality)
                        elif key=="saves": denominator=Decimal(4+quality); numerator=Decimal(2+quality); value=numerator
                        elif key=="long_passes": denominator=Decimal(10+quality); numerator=Decimal(5+quality)
                        elif key=="shots": denominator=Decimal(4+quality); numerator=Decimal((quality+idx)%3); value=denominator
                        PlayerFixtureMetric.objects.update_or_create(player_fixture=pf,metric_key=key,defaults={"value":value,"numerator":numerator,"denominator":denominator,"is_available":True})
        self.stdout.write(self.style.SUCCESS(f"Seeded 1 season, 2 competitions, 8 teams, 48 players and {len(pairs)} fixtures"))
