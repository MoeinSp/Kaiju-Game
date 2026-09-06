from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0065_treasury_avg_and_join_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="alliance",
            name="raid_level",
            field=models.IntegerField(default=1),
        ),
        migrations.AddField(
            model_name="raidboss",
            name="alliance",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name="raid_bosses", to="bio_lab.alliance",
            ),
        ),
        migrations.AlterField(
            model_name="raidboss",
            name="group",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="raid_bosses", to="bio_lab.group",
            ),
        ),
    ]
