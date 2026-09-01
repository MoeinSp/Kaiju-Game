from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0057_scale_lab_xp"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyResourceGain",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day", models.CharField(max_length=10)),
                ("source", models.CharField(max_length=32)),
                ("coins", models.BigIntegerField(default=0)),
                ("dna", models.BigIntegerField(default=0)),
                ("diamonds", models.BigIntegerField(default=0)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="bio_lab.user")),
            ],
        ),
        migrations.AddIndex(
            model_name="dailyresourcegain",
            index=models.Index(fields=["user", "day"], name="bio_lab_dai_user_id_day_idx"),
        ),
        migrations.AddConstraint(
            model_name="dailyresourcegain",
            constraint=models.UniqueConstraint(fields=["user", "day", "source"], name="uq_daily_resource_gain"),
        ),
    ]
