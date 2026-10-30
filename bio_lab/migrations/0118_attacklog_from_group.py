import datetime

from django.db import migrations, models
from django.utils import timezone


def mark_open_group_attacks(apps, schema_editor):
    """Group «اتک»s already logged and still open for revenge (the last few days) get the flag
    too: each was written together with a DuelLog for the same pair at the same moment."""
    AttackLog = apps.get_model("bio_lab", "AttackLog")
    DuelLog = apps.get_model("bio_lab", "DuelLog")
    since = timezone.now() - datetime.timedelta(days=4)
    window = datetime.timedelta(seconds=10)
    ids = []
    for d in DuelLog.objects.filter(created_at__gte=since).values("challenger_id", "opponent_id", "created_at"):
        ids += list(AttackLog.objects.filter(
            attacker_id=d["challenger_id"], defender_id=d["opponent_id"], revenge_taken=False,
            created_at__gte=d["created_at"] - window, created_at__lte=d["created_at"] + window,
        ).values_list("id", flat=True))
    if ids:
        AttackLog.objects.filter(id__in=ids).update(from_group=True)


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0117_dungeon_shop_daily"),
    ]

    operations = [
        migrations.AddField(
            model_name="attacklog",
            name="from_group",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_open_group_attacks, migrations.RunPython.noop),
    ]
