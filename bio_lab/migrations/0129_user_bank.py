from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0128_group_base_lock"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="bank_gold",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="unearned_coins",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="bank_out_amount",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="bank_out_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
