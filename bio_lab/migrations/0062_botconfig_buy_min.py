from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0061_purchase_flow"),
    ]

    operations = [
        migrations.AddField(
            model_name="botconfig",
            name="buy_min_toman",
            field=models.BigIntegerField(default=0),
        ),
    ]
