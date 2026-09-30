from django.db import migrations


def classify_existing(apps,schema_editor):
    competition=apps.get_model("football","Competition")
    # The old API adapter labelled every cup UCL. Correct unused reference entries only;
    # retain context semantics for any competition with published historical appearances.
    competition.objects.filter(provider="api_football",competition_type="UCL",is_tracked=False).exclude(provider_id="2").update(competition_type="CUP")
    competition.objects.filter(competition_type="DOMESTIC_LEAGUE").update(format="LEAGUE")
    competition.objects.filter(competition_type="UCL").update(format="CUP",scope="CONTINENTAL")
    # Only known MVP/mock competitions can safely be classified as clubs.
    competition.objects.filter(is_tracked=True,competition_type__in=["DOMESTIC_LEAGUE","UCL"]).update(participant_type="CLUB")
    competition.objects.filter(is_tracked=True,competition_type="DOMESTIC_LEAGUE").update(scope="DOMESTIC")


class Migration(migrations.Migration):
    dependencies=[("football","0004_competitionseason_uniq_provider_competition_edition")]
    operations=[migrations.RunPython(classify_existing,migrations.RunPython.noop)]
