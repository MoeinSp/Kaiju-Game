from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0060_egg_fallback_rarity"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="receipt_blocked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="botconfig",
            name="buy_price_per_gold",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="botconfig",
            name="buy_price_per_dna",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="botconfig",
            name="buy_price_per_diamond",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="botconfig",
            name="buy_card_number",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="botconfig",
            name="buy_card_holder",
            field=models.CharField(blank=True, default="", max_length=96),
        ),
        migrations.CreateModel(
            name="PurchaseRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("coins", models.BigIntegerField(default=0)),
                ("dna", models.BigIntegerField(default=0)),
                ("diamonds", models.BigIntegerField(default=0)),
                ("price_toman", models.BigIntegerField(default=0)),
                ("status", models.CharField(default="awaiting_receipt", max_length=20)),
                ("receipt_file_id", models.CharField(blank=True, default="", max_length=256)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="purchases", to="bio_lab.user")),
            ],
        ),
        migrations.AddIndex(
            model_name="purchaserequest",
            index=models.Index(fields=["user", "status"], name="bio_lab_pur_user_id_status_idx"),
        ),
    ]
