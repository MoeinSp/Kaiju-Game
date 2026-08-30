from django.db import migrations


def scale_down(apps, schema_editor):
    """Scale every player's stored lab_xp by 0.7 to match the 30%-cheaper lab-XP curve
    (LAB_XP_COEFFICIENT 18 → 12.6). Because both the curve and the stored XP move by the
    same factor, level_for_xp(lab_xp) is unchanged for everyone — nobody's lab level
    changes, only the raw XP number drops and future levels arrive sooner."""
    User = apps.get_model("bio_lab", "User")
    for uid, xp in User.objects.exclude(lab_xp=0).values_list("id", "lab_xp"):
        User.objects.filter(id=uid).update(lab_xp=round((xp or 0) * 0.7))


def scale_up(apps, schema_editor):
    User = apps.get_model("bio_lab", "User")
    for uid, xp in User.objects.exclude(lab_xp=0).values_list("id", "lab_xp"):
        User.objects.filter(id=uid).update(lab_xp=round((xp or 0) / 0.7))


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0056_botconfig_buy_link"),
    ]

    operations = [
        migrations.RunPython(scale_down, scale_up),
    ]
