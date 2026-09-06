from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0063_creature_custom_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="seasonstate",
            name="last_treasury_day",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
    ]
