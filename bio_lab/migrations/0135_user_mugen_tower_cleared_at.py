from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0134_friend_invites"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="mugen_tower_cleared_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]