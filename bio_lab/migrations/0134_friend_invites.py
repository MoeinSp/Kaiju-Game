from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0133_friends"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="friend_invites",
            field=models.BooleanField(default=True),
        ),
    ]
