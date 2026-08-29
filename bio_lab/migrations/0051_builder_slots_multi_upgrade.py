from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0050_building_banked_pending"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="builder_slots",
            field=models.IntegerField(default=1),
        ),
        migrations.AlterField(
            model_name="buildingupgrade",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="building_upgrades",
                to="bio_lab.user",
            ),
        ),
    ]
