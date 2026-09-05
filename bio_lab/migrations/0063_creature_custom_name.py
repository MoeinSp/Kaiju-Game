from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0062_botconfig_buy_min"),
    ]

    operations = [
        migrations.AddField(
            model_name="creature",
            name="custom_name",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
        migrations.AddField(
            model_name="creature",
            name="name_changes",
            field=models.IntegerField(default=0),
        ),
    ]
