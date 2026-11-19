from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0130_alliance_flag"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="bank_interest_on",
            field=models.DateField(blank=True, null=True),
        ),
    ]
