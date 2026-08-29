from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0052_daily_shop_admin"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyShopDay",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slot", models.IntegerField(unique=True)),
                ("offers_json", models.TextField(default="[]")),
                ("configured", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
