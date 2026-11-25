from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0135_user_mugen_tower_cleared_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="dungeonprofile",
            name="keys_extra",
            field=models.IntegerField(default=0),
        ),
    ]