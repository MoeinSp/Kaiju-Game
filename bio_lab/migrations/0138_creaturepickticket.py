from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0137_shopitem_paid_offer_cadence"),
    ]

    operations = [
        migrations.CreateModel(
            name="CreaturePickTicket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rarity", models.CharField(default="mythic", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "result_creature",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="bio_lab.creature",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="creature_pick_tickets",
                        to="bio_lab.user",
                    ),
                ),
            ],
        ),
    ]
