from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0055_daily_shop_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="botconfig",
            name="buy_url",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="botconfig",
            name="buy_title",
            field=models.CharField(blank=True, default="", max_length=48),
        ),
    ]
