from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0140_globalsale"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="ancient_kaiju_claimed_on",
            field=models.DateField(blank=True, null=True),
        ),
    ]
