from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0129_user_bank"),
    ]

    operations = [
        migrations.AddField(
            model_name="alliance",
            name="flag",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
