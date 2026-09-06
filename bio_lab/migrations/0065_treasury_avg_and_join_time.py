from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0064_seasonstate_last_treasury_day"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="alliance_joined_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alliance",
            name="treasury_avg_sum",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="alliance",
            name="treasury_avg_count",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="alliance",
            name="treasury_avg_day",
            field=models.CharField(blank=True, default="", max_length=10),
        ),
    ]
