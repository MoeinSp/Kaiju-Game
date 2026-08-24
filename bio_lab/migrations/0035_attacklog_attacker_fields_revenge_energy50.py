# Generated manually 2026-08-24

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0034_groupdrop_claimed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="attacklog",
            name="attacker_label",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.AddField(
            model_name="attacklog",
            name="attacker_power",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="attacklog",
            name="revenge_taken",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="user",
            name="energy",
            field=models.IntegerField(default=50),
        ),
    ]
