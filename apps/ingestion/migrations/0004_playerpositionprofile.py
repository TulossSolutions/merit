from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("football", "0006_fixture_award_season_and_more"),
        ("ingestion", "0003_statsbombbacktestpayload"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlayerPositionProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.CharField(choices=[("GK", "Goalkeeper"), ("DEF", "Defender"), ("MID", "Midfielder"), ("FWD", "Attacker"), ("UNKNOWN", "Unknown")], default="UNKNOWN", max_length=10)),
                ("provider_position", models.CharField(blank=True, max_length=100)),
                ("checked_at", models.DateTimeField()),
                ("reason", models.CharField(blank=True, max_length=100)),
                ("player", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="position_profile", to="football.player")),
                ("source_payload", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="ingestion.rawproviderpayload")),
            ],
        ),
    ]
