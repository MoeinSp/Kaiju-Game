from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0131_bank_interest"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="bank_interest_notice",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="bank_interest_notice_days",
            field=models.IntegerField(default=0),
        ),
    ]
