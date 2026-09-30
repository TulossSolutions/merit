from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

@dataclass(frozen=True)
class ProviderCompetition: id:str; name:str; country_code:str|None=None; kind:str="DOMESTIC_LEAGUE"
@dataclass(frozen=True)
class ProviderSeason: id:str; name:str; starts_on:date; ends_on:date
@dataclass(frozen=True)
class ProviderTeam: id:str; name:str; short_name:str|None=None; country_code:str|None=None; logo_url:str|None=None
@dataclass(frozen=True)
class ProviderPlayer:
    id:str; name:str; position:str|None=None; detailed_position:str|None=None; nationality_code:str|None=None
    first_name:str|None=None; last_name:str|None=None; common_name:str|None=None; birth_date:date|None=None
    image_url:str|None=None; height_cm:int|None=None
@dataclass(frozen=True)
class ProviderMetric: key:str; value:Decimal|None=None; numerator:Decimal|None=None; denominator:Decimal|None=None; available:bool=True; source_type_id:str|None=None
@dataclass(frozen=True)
class ProviderParticipation: player:ProviderPlayer; team_id:str; opponent_id:str; position:str|None; started:bool; minutes:int; metrics:tuple[ProviderMetric,...]=field(default_factory=tuple)
@dataclass(frozen=True)
class ProviderFixture: id:str; season_id:str; home_team:ProviderTeam; away_team:ProviderTeam; starts_at:datetime; status:str; home_score:int|None=None; away_score:int|None=None; stage_name:str|None=None; round_name:str|None=None; raw_payload:dict=field(default_factory=dict,compare=False); available_minutes:int|None=None
@dataclass(frozen=True)
class ProviderFixtureBundle: fixture:ProviderFixture; participations:tuple[ProviderParticipation,...]; raw_payload:dict=field(default_factory=dict)

class FootballProvider(Protocol):
    def list_competitions(self)->list[ProviderCompetition]: ...
    def list_seasons(self,competition_id:str)->list[ProviderSeason]: ...
    def list_teams(self,provider_season_id:str)->list[ProviderTeam]: ...
    def list_players(self,team_id:str,provider_season_id:str)->list[ProviderPlayer]: ...
    def list_fixtures(self,provider_season_id:str,start:date,end:date)->list[ProviderFixture]: ...
    def get_fixture_details(self,fixture:ProviderFixture|str)->ProviderFixtureBundle: ...

class ProviderRequestLimitReached(RuntimeError): pass
