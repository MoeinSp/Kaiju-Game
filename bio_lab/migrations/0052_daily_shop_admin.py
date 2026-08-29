from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0051_builder_slots_multi_upgrade"),
    ]

    operations = [
        migrations.AddField(
            model_name="botconfig",
            name="energy_refill_diamonds",
            field=models.IntegerField(default=25),
        ),
        migrations.CreateModel(
            name="DailyShopOffer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=32, unique=True)),
                ("cost", models.IntegerField(default=0)),
                ("currency", models.CharField(default="coins", max_length=12)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
