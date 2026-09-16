from django.db import migrations


# Frozen snapshot of the canonical curve at the time of this migration (kept inline so
# the migration never drifts if the live constants change later). Base stats are keyed
# ONLY on rarity + level, so every creature of the same rarity and level ends up with
# identical base stats — no more lineage/fusion-based divergence between "maxed"
# creatures (e.g. سیمرغ 1744 vs کرکس‌دریا 1090 base HP for the same mythic 5★).
STARTER = {"base_hp": 50, "base_atk": 10, "base_def": 10, "base_spd": 10}
LEVEL_UP = {"base_hp": 10, "base_atk": 2, "base_def": 2, "base_spd": 1}
RARITY_MULT = {"common": 1.0, "rare": 1.15, "epic": 1.35, "legendary": 1.6, "mythic": 2.0}


def _canonical(rarity, level):
    mult = RARITY_MULT.get(rarity, 1.0)
    lvl = max(1, int(level or 1))
    return {stat: round(STARTER[stat] * mult) + (lvl - 1) * LEVEL_UP[stat] for stat in STARTER}


def normalize(apps, schema_editor):
    Creature = apps.get_model("bio_lab", "Creature")
    batch = []
    for c in Creature.objects.all().iterator():
        canon = _canonical(c.rarity, c.level)
        if (c.base_hp, c.base_atk, c.base_def, c.base_spd) == (
            canon["base_hp"], canon["base_atk"], canon["base_def"], canon["base_spd"]
        ):
            continue
        c.base_hp = canon["base_hp"]
        c.base_atk = canon["base_atk"]
        c.base_def = canon["base_def"]
        c.base_spd = canon["base_spd"]
        batch.append(c)
        if len(batch) >= 500:
            Creature.objects.bulk_update(batch, ["base_hp", "base_atk", "base_def", "base_spd"])
            batch = []
    if batch:
        Creature.objects.bulk_update(batch, ["base_hp", "base_atk", "base_def", "base_spd"])


def noop(apps, schema_editor):
    # irreversible: the pre-normalization (lineage-inflated) base stats aren't recoverable
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("bio_lab", "0078_purchasepack"),
    ]

    operations = [
        migrations.RunPython(normalize, noop),
    ]
