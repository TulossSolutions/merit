from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.text import slugify

class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: abstract = True

class Position(models.TextChoices):
    GK="GK","Goalkeeper"; DEF="DEF","Defender"; MID="MID","Midfielder"; FWD="FWD","Attacker"; UNKNOWN="UNKNOWN","Unknown"

class Season(Timestamped):
    name=models.CharField(max_length=20); slug=models.SlugField(unique=True); starts_on=models.DateField(); ends_on=models.DateField(); is_current=models.BooleanField(default=False); is_published=models.BooleanField(default=False)
    class Meta: ordering=["-starts_on"]
    def progress(self, as_of_date):
        span=(self.ends_on-self.starts_on).days
        if span <= 0: return Decimal("1")
        return max(Decimal("0"), min(Decimal("1"), Decimal((as_of_date-self.starts_on).days)/Decimal(span)))
    def eligibility_minutes(self, as_of_date): return max(180, round(1800*self.progress(as_of_date)))
    def availability_target_minutes(self, as_of_date): return max(270, round(2700*self.progress(as_of_date)))
    def clean(self):
        if self.is_current and Season.objects.filter(is_current=True).exclude(pk=self.pk).exists(): raise ValidationError("Only one season may be current.")
    def __str__(self): return self.name

class Competition(Timestamped):
    class Type(models.TextChoices): DOMESTIC_LEAGUE="DOMESTIC_LEAGUE","Domestic league"; UCL="UCL","UEFA Champions League"; CUP="CUP","Other tournament"; UNKNOWN="UNKNOWN","Unclassified"
    class Participants(models.TextChoices): CLUB="CLUB","Clubs"; NATIONAL="NATIONAL","National teams"; UNKNOWN="UNKNOWN","Unclassified"
    class Format(models.TextChoices): LEAGUE="LEAGUE","League"; CUP="CUP","Cup"; UNKNOWN="UNKNOWN","Unclassified"
    class Scope(models.TextChoices): DOMESTIC="DOMESTIC","Domestic"; CONTINENTAL="CONTINENTAL","Continental"; GLOBAL="GLOBAL","Global"; UNKNOWN="UNKNOWN","Unclassified"
    participant_type=models.CharField(max_length=10,choices=Participants.choices,default=Participants.UNKNOWN)
    format=models.CharField(max_length=10,choices=Format.choices,default=Format.UNKNOWN)
    scope=models.CharField(max_length=15,choices=Scope.choices,default=Scope.UNKNOWN)
    provider=models.CharField(max_length=30); provider_id=models.CharField(max_length=100); name=models.CharField(max_length=150); slug=models.SlugField(); country_code=models.CharField(max_length=3,blank=True,null=True); competition_type=models.CharField(max_length=30,choices=Type.choices); is_tracked=models.BooleanField(default=False); base_importance=models.DecimalField(max_digits=4,decimal_places=2,default=Decimal("1.00"))
    class Meta: constraints=[models.UniqueConstraint(fields=["provider","provider_id"],name="uniq_comp_provider_id")]
    def __str__(self): return self.name

class CompetitionSeason(Timestamped):
    edition_name=models.CharField(max_length=100,blank=True)
    starts_on=models.DateField(blank=True,null=True)
    ends_on=models.DateField(blank=True,null=True)
    context_policy=models.ForeignKey("CampaignPolicy",on_delete=models.PROTECT,blank=True,null=True)
    created_at=models.DateTimeField(auto_now_add=True,null=True); updated_at=models.DateTimeField(auto_now=True,null=True)
    competition=models.ForeignKey(Competition,on_delete=models.CASCADE); season=models.ForeignKey(Season,on_delete=models.CASCADE); provider_season_id=models.CharField(max_length=100,blank=True,null=True); is_active=models.BooleanField(default=True); league_strength_rating=models.DecimalField(max_digits=8,decimal_places=3,blank=True,null=True); league_strength_multiplier=models.DecimalField(max_digits=6,decimal_places=4,default=Decimal("1")); last_strength_calculated_at=models.DateTimeField(blank=True,null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["competition","season"],name="uniq_comp_season"),
                     models.UniqueConstraint(fields=["competition","provider_season_id"],condition=models.Q(provider_season_id__isnull=False)&~models.Q(provider_season_id=""),name="uniq_provider_competition_edition")]

    def clean(self):
        if bool(self.starts_on)!=bool(self.ends_on): raise ValidationError("Edition dates must be supplied together.")
        if self.starts_on and self.ends_on<self.starts_on: raise ValidationError("Edition end precedes its start.")
        if self.starts_on and self.ends_on and (self.ends_on-self.starts_on).days<=120 and self.starts_on.month in (6,7,8) and self.competition.participant_type==Competition.Participants.NATIONAL:
            from .award_periods import summer_award_period
            if summer_award_period(self.starts_on,self.ends_on).pk!=self.season_id:
                raise ValidationError("Summer tournaments belong to the season just ended.")
        if self.provider_season_id and CompetitionSeason.objects.filter(competition=self.competition,provider_season_id=self.provider_season_id).exclude(pk=self.pk).exists():
            raise ValidationError("A provider edition can belong to only one award period.")

class Team(Timestamped):
    provider=models.CharField(max_length=30); provider_id=models.CharField(max_length=100); name=models.CharField(max_length=150); slug=models.SlugField(); short_name=models.CharField(max_length=50,blank=True,null=True); country_code=models.CharField(max_length=3,blank=True,null=True); logo_url=models.URLField(blank=True,null=True); active=models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["provider","provider_id"],name="uniq_team_provider_id")]
    def __str__(self): return self.name

class TeamCompetitionSeason(Timestamped):
    created_at=models.DateTimeField(auto_now_add=True,null=True); updated_at=models.DateTimeField(auto_now=True,null=True)
    team=models.ForeignKey(Team,on_delete=models.CASCADE); competition_season=models.ForeignKey(CompetitionSeason,on_delete=models.CASCADE); active=models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["team","competition_season"],name="uniq_team_comp_season")]

class Player(Timestamped):
    provider=models.CharField(max_length=30); provider_id=models.CharField(max_length=100); name=models.CharField(max_length=150,db_index=True); slug=models.SlugField(unique=True); first_name=models.CharField(max_length=80,blank=True,null=True); last_name=models.CharField(max_length=80,blank=True,null=True); common_name=models.CharField(max_length=150,blank=True,null=True); birth_date=models.DateField(blank=True,null=True); nationality_code=models.CharField(max_length=3,blank=True,null=True); image_url=models.URLField(blank=True,null=True); height_cm=models.PositiveSmallIntegerField(blank=True,null=True); preferred_foot=models.CharField(max_length=20,blank=True,null=True); primary_position=models.CharField(max_length=10,choices=Position.choices,default=Position.UNKNOWN,db_index=True); detailed_position=models.CharField(max_length=60,blank=True,null=True); active=models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["provider","provider_id"],name="uniq_player_provider_id")]
    def save(self,*args,**kwargs):
        if not self.slug:
            base=slugify(self.name) or "player"; self.slug=base
            if Player.objects.filter(slug=base).exclude(pk=self.pk).exists(): self.slug=f"{base}-{self.provider_id[-8:].lower()}"
        super().save(*args,**kwargs)
    def __str__(self): return self.name

class PlayerTeamSeason(Timestamped):
    created_at=models.DateTimeField(auto_now_add=True,null=True); updated_at=models.DateTimeField(auto_now=True,null=True)
    player=models.ForeignKey(Player,on_delete=models.CASCADE); team=models.ForeignKey(Team,on_delete=models.CASCADE); season=models.ForeignKey(Season,on_delete=models.CASCADE); competition_season=models.ForeignKey(CompetitionSeason,on_delete=models.SET_NULL,blank=True,null=True); shirt_number=models.PositiveSmallIntegerField(blank=True,null=True); provider_position_id=models.CharField(max_length=100,blank=True,null=True); provider_detailed_position_id=models.CharField(max_length=100,blank=True,null=True); started_on=models.DateField(blank=True,null=True); ended_on=models.DateField(blank=True,null=True)

class Fixture(Timestamped):
    award_season=models.ForeignKey(Season,on_delete=models.PROTECT,blank=True,null=True,related_name="awarded_fixtures")
    player_data_unavailable_at=models.DateTimeField(blank=True,null=True)
    available_minutes=models.PositiveSmallIntegerField(blank=True,null=True)
    neutral_venue=models.BooleanField(default=False)
    class Status(models.TextChoices): SCHEDULED="SCHEDULED","Scheduled"; LIVE="LIVE","Live"; FINISHED="FINISHED","Finished"; POSTPONED="POSTPONED","Postponed"; CANCELLED="CANCELLED","Cancelled"
    provider=models.CharField(max_length=30); provider_id=models.CharField(max_length=100); competition_season=models.ForeignKey(CompetitionSeason,on_delete=models.CASCADE); home_team=models.ForeignKey(Team,on_delete=models.PROTECT,related_name="home_fixtures"); away_team=models.ForeignKey(Team,on_delete=models.PROTECT,related_name="away_fixtures"); starts_at=models.DateTimeField(); status=models.CharField(max_length=20,choices=Status.choices); stage_name=models.CharField(max_length=100,blank=True,null=True); round_name=models.CharField(max_length=100,blank=True,null=True); home_score=models.SmallIntegerField(blank=True,null=True); away_score=models.SmallIntegerField(blank=True,null=True); winner_team=models.ForeignKey(Team,on_delete=models.SET_NULL,blank=True,null=True,related_name="won_fixtures"); last_provider_update=models.DateTimeField(blank=True,null=True); stats_ingested_at=models.DateTimeField(blank=True,null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["provider","provider_id"],name="uniq_fixture_provider_id")]
        indexes=[models.Index(fields=["competition_season","starts_at"]),models.Index(fields=["status","starts_at"])]

class PlayerFixture(Timestamped):
    fixture=models.ForeignKey(Fixture,on_delete=models.CASCADE); player=models.ForeignKey(Player,on_delete=models.CASCADE); team=models.ForeignKey(Team,on_delete=models.PROTECT,related_name="player_fixtures"); opponent=models.ForeignKey(Team,on_delete=models.PROTECT,related_name="opponent_player_fixtures"); position=models.CharField(max_length=10,choices=Position.choices); provider_position_id=models.CharField(max_length=100,blank=True,null=True); started=models.BooleanField(default=False); minutes=models.PositiveSmallIntegerField(validators=[MaxValueValidator(130)]); jersey_number=models.PositiveSmallIntegerField(blank=True,null=True); context_factor=models.DecimalField(max_digits=7,decimal_places=5,blank=True,null=True); opponent_elo_before=models.DecimalField(max_digits=8,decimal_places=3,blank=True,null=True); competition_factor=models.DecimalField(max_digits=6,decimal_places=4,blank=True,null=True); league_factor=models.DecimalField(max_digits=6,decimal_places=4,blank=True,null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["fixture","player"],name="uniq_fixture_player")]
        indexes=[models.Index(fields=["player","fixture"])]
    def clean(self):
        if self.team_id == self.opponent_id: raise ValidationError("Team and opponent must differ.")

class PlayerFixtureMetric(Timestamped):
    player_fixture=models.ForeignKey(PlayerFixture,on_delete=models.CASCADE,related_name="metrics"); metric_key=models.CharField(max_length=80); value=models.DecimalField(max_digits=14,decimal_places=6,blank=True,null=True); numerator=models.DecimalField(max_digits=14,decimal_places=6,blank=True,null=True); denominator=models.DecimalField(max_digits=14,decimal_places=6,blank=True,null=True); is_available=models.BooleanField(default=True); source_type_id=models.CharField(max_length=100,blank=True,null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["player_fixture","metric_key"],name="uniq_player_fixture_metric")]
        indexes=[models.Index(fields=["player_fixture","metric_key"])]

class TeamEloSnapshot(models.Model):
    fixture=models.ForeignKey(Fixture,on_delete=models.CASCADE); team=models.ForeignKey(Team,on_delete=models.CASCADE); rating_before=models.DecimalField(max_digits=8,decimal_places=3); rating_after=models.DecimalField(max_digits=8,decimal_places=3); expected_result=models.DecimalField(max_digits=8,decimal_places=6); actual_result=models.DecimalField(max_digits=3,decimal_places=2); is_home=models.BooleanField(); created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["fixture","team"],name="uniq_fixture_team_elo")]
        indexes=[models.Index(fields=["team","fixture"])]

class CampaignPolicy(models.Model):
    version=models.CharField(max_length=30,unique=True)
    config=models.JSONField()
    checksum_sha256=models.CharField(max_length=64,editable=False)
    created_at=models.DateTimeField(auto_now_add=True)

    def save(self,*args,**kwargs):
        from apps.scoring.services.campaigns import validate_campaign_policy
        checksum=validate_campaign_policy(self.config)
        if self.pk:
            old=CampaignPolicy.objects.get(pk=self.pk)
            if old.config!=self.config or old.version!=self.version:
                raise ValidationError("Campaign policies are immutable; create a new version.")
        self.checksum_sha256=checksum
        super().save(*args,**kwargs)

    def __str__(self): return f"Campaign policy {self.version}"

class WinningCampaign(Timestamped):
    edition=models.OneToOneField(CompetitionSeason,on_delete=models.PROTECT,related_name="winning_campaign")
    winner=models.ForeignKey(Team,on_delete=models.PROTECT)
    policy=models.ForeignKey(CampaignPolicy,on_delete=models.PROTECT)
    awarded_at=models.DateTimeField()
    verified_at=models.DateTimeField(blank=True,null=True)
    evidence=models.TextField(blank=True)
    expected_matches=models.PositiveSmallIntegerField()
    is_complete=models.BooleanField(default=False)

    def clean(self):
        if self.expected_matches<1: raise ValidationError("A campaign must include at least one match.")
        if self.verified_at and not self.evidence.strip(): raise ValidationError("Verified winners require evidence.")

    def __str__(self): return f"{self.edition.edition_name or self.edition.competition.name}: {self.winner.name}"

class PlayerCampaignContribution(models.Model):
    campaign=models.ForeignKey(WinningCampaign,on_delete=models.PROTECT,related_name="contributions")
    player=models.ForeignKey(Player,on_delete=models.PROTECT,related_name="campaign_contributions")
    as_of=models.DateTimeField()
    contribution=models.DecimalField(max_digits=12,decimal_places=10,blank=True,null=True)
    reason=models.CharField(max_length=100,blank=True)
    breakdown=models.JSONField()
    input_sha256=models.CharField(max_length=64)
    calculated_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["campaign","player","as_of","input_sha256"],name="uniq_campaign_contribution_input")]

    def save(self,*args,**kwargs):
        if self.pk: raise ValidationError("Contribution records are immutable; recalculate to create a new record.")
        super().save(*args,**kwargs)
