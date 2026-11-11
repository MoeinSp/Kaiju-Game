from django.db import migrations, models
from django.db.models import Count

# keep in step with game/referral.TIERS (only the thresholds matter here)
_TIER_NEEDS = [1, 3, 5, 10, 25, 50]


def seed_tiers(apps, schema_editor):
    """Inviters from before the tier system start at the tier their old valid invites already
    reach — no lump payout for the past, but those invites still count toward the next tier."""
    User = apps.get_model("bio_lab", "User")
    rows = (User.objects.filter(referred_by__isnull=False, referral_bonus_paid=True)
            .values("referred_by").annotate(n=Count("id")))
    for r in rows:
        tier = sum(1 for need in _TIER_NEEDS if r["n"] >= need)
        if tier:
            User.objects.filter(id=r["referred_by"]).update(referral_tier=tier)


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0123_serum_parallel"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user", name="referred_by",
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(model_name="user", name="referred_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name="user", name="referral_paid_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(model_name="user", name="active_days", field=models.IntegerField(default=0)),
        migrations.AddField(
            model_name="user", name="last_active_day", field=models.CharField(blank=True, default="", max_length=10),
        ),
        migrations.AddField(model_name="user", name="referral_tier", field=models.IntegerField(default=0)),
        migrations.CreateModel(
            name="ReferralWeekPrize",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week", models.CharField(max_length=10, unique=True)),
                ("winners", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.RunPython(seed_tiers, migrations.RunPython.noop),
    ]
