from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0066_alliance_raids"),
    ]

    operations = [
        migrations.AddField(
            model_name="botconfig",
            name="buy_channel_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="channel_chat_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="channel_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
