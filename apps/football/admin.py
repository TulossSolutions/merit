from django.contrib import admin
from .models import CampaignPolicy,Competition,CompetitionSeason,Fixture,Player,PlayerCampaignContribution,PlayerFixture,PlayerFixtureMetric,PlayerTeamSeason,Season,Team,TeamCompetitionSeason,TeamEloSnapshot,WinningCampaign

@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin): list_display=("name","starts_on","ends_on","is_current","is_published"); list_filter=("is_current","is_published")
@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin): list_display=("name","provider","competition_type","format","participant_type","scope","is_tracked"); list_filter=("provider","competition_type","participant_type","scope","is_tracked"); list_editable=("is_tracked",); search_fields=("name","provider_id")
@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin): list_display=("name","primary_position","provider","active"); list_filter=("provider","primary_position","active"); search_fields=("name","common_name","provider_id")
@admin.register(Team)
class TeamAdmin(admin.ModelAdmin): list_display=("name","provider","active"); list_filter=("provider","active"); search_fields=("name","provider_id")
@admin.register(Fixture)
class FixtureAdmin(admin.ModelAdmin): list_display=("provider_id","home_team","away_team","starts_at","status"); list_filter=("status","competition_season__season"); search_fields=("provider_id","home_team__name","away_team__name")
admin.site.register([CompetitionSeason,TeamCompetitionSeason,PlayerTeamSeason,PlayerFixture,PlayerFixtureMetric,TeamEloSnapshot])

@admin.register(CampaignPolicy)
class CampaignPolicyAdmin(admin.ModelAdmin):
    list_display=("version","checksum_sha256","created_at")
    readonly_fields=("checksum_sha256","created_at")
    def get_readonly_fields(self,request,obj=None):
        return self.readonly_fields+("version","config") if obj else self.readonly_fields

@admin.register(WinningCampaign)
class WinningCampaignAdmin(admin.ModelAdmin):
    list_display=("edition","winner","awarded_at","verified_at","expected_matches","is_complete")

@admin.register(PlayerCampaignContribution)
class PlayerCampaignContributionAdmin(admin.ModelAdmin):
    list_display=("player","campaign","as_of","contribution","reason")
    readonly_fields=tuple(field.name for field in PlayerCampaignContribution._meta.fields)
    def has_add_permission(self,request): return False
    def has_delete_permission(self,request,obj=None): return False
