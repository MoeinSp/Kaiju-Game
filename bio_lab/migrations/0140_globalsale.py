from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0139_user_last_arena_active_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="GlobalSale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("percent", models.PositiveSmallIntegerField()),
                ("reason", models.CharField(blank=True, default="", max_length=64)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(db_index=True)),
                ("broadcast", models.BooleanField(default=False)),
                ("cancelled", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
