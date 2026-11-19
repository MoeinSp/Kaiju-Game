from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0132_bank_interest_notice"),
    ]

    operations = [
        migrations.CreateModel(
            name="FriendLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(db_index=True, default="pending", max_length=12)),
                ("asked_by_id", models.BigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user_hi", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="friend_hi", to="bio_lab.user")),
                ("user_lo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="friend_lo", to="bio_lab.user")),
            ],
        ),
        migrations.AddConstraint(
            model_name="friendlink",
            constraint=models.UniqueConstraint(fields=("user_lo", "user_hi"), name="uq_friend_pair"),
        ),
        migrations.CreateModel(
            name="FriendMark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(max_length=8)),
                ("day", models.CharField(max_length=10)),
                ("seen", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="friend_marks_out", to="bio_lab.user")),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="friend_marks_in", to="bio_lab.user")),
            ],
        ),
        migrations.AddConstraint(
            model_name="friendmark",
            constraint=models.UniqueConstraint(fields=("kind", "actor", "target", "day"), name="uq_friend_mark"),
        ),
    ]
