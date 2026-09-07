from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0067_purchase_channel"),
    ]

    operations = [
        migrations.AddField(
            model_name="group",
            name="next_vein_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="group",
            name="next_capsule_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
