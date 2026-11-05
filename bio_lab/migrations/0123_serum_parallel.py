from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0122_required_bot_api"),
    ]

    operations = [
        migrations.AddField(
            model_name="serumlab",
            name="starts",
            field=models.JSONField(default=list),
        ),
    ]
