from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0058_daily_resource_gain"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dailyresourcegain",
            name="id",
            field=models.BigAutoField(
                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
            ),
        ),
    ]
