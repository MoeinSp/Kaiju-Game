from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0054_daily_shop_purchase"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyShopItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=40, unique=True)),
                ("emoji", models.CharField(default="🎁", max_length=8)),
                ("title", models.CharField(max_length=64)),
                ("contents_json", models.TextField(default="[]")),
                ("cost", models.IntegerField(default=0)),
                ("currency", models.CharField(default="coins", max_length=12)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
