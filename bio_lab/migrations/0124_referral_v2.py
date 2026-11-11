from django.db import migrations, models


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
        migrations.AddField(model_name="user", name="referral_step", field=models.IntegerField(default=0)),
        migrations.CreateModel(
            name="ReferralWeekPrize",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week", models.CharField(max_length=10, unique=True)),
                ("winners", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
