from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0053_daily_shop_day"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyShopPurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=32)),
                ("day", models.CharField(max_length=10)),
                ("count", models.IntegerField(default=0)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="bio_lab.user")),
            ],
        ),
        migrations.AddConstraint(
            model_name="dailyshoppurchase",
            constraint=models.UniqueConstraint(fields=("user", "key", "day"), name="uq_daily_shop_purchase"),
        ),
    ]
