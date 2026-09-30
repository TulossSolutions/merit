from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0004_playerpositionprofile")]
    operations = [
        migrations.CreateModel(
            name="ReviewedPlayerPosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.CharField(choices=[("GK", "Goalkeeper"), ("DEF", "Defender"), ("MID", "Midfielder"), ("FWD", "Attacker"), ("UNKNOWN", "Unknown")], max_length=10)),
                ("source_path", models.CharField(max_length=500)),
                ("source_sha256", models.CharField(max_length=64)),
                ("reviewer", models.CharField(max_length=100)),
                ("reviewed_at", models.DateTimeField()),
                ("player", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reviewed_positions", to="football.player")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("player", "source_sha256"), name="uniq_reviewed_position_source")]},
        ),
        migrations.CreateModel(
            name="PlayerIdentityAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_path", models.CharField(max_length=500)),
                ("source_sha256", models.CharField(max_length=64)),
                ("reviewer", models.CharField(max_length=100)),
                ("reviewed_at", models.DateTimeField()),
                ("note", models.TextField()),
                ("alias_player", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="canonical_identity", to="football.player")),
                ("canonical_player", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="provider_aliases", to="football.player")),
            ],
        ),
    ]
