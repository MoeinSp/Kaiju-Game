from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0059_alter_dailyresourcegain_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="egg",
            name="fallback_rarity",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
