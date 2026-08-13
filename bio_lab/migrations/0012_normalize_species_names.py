"""Normalizes creature species so that a name uniquely identifies a species.

Before this migration a creature's `name` was decorative — old builds used
transliterated English names, then a set of invented Persian ones, and a `name`
could legitimately appear under more than one element. That became a correctness
problem once fusion started keying on `name`: two creatures of genuinely
different species could share a name and fuse together.

This walks every existing creature and, if its name isn't in the current
registry (or is registered under a different element), reassigns it to a species
of its own element. Stats/level/XP/star are all preserved — only the identity
label changes.
"""

import random

from django.db import migrations

# snapshot of game.constants.SPECIES at the time of this migration; deliberately
# inlined so a later edit to the registry can't retroactively change what this
# migration did to existing rows
SPECIES = {
    "سیمرغ": "fire",
    "اژدهاک": "fire",
    "آذرگشسب": "fire",
    "ضحاک": "fire",
    "فرنبغ": "fire",
    "اپم‌نپات": "water",
    "آناهیتا": "water",
    "تیشتر": "water",
    "کرکس دریا": "water",
    "ماهی‌ور": "water",
    "کرکدان": "earth",
    "البرزکوه": "earth",
    "اسپندارمذ": "earth",
    "گاوبرمایه": "earth",
    "سنگ‌دیو": "earth",
    "بهرام": "electric",
    "وایو": "electric",
    "هما": "electric",
    "رخش": "electric",
    "شهباز": "electric",
}

BY_ELEMENT: dict[str, list[str]] = {}
for _name, _element in SPECIES.items():
    BY_ELEMENT.setdefault(_element, []).append(_name)


def normalize(apps, schema_editor):
    Creature = apps.get_model("bio_lab", "Creature")
    rng = random.Random(20260813)  # deterministic, so a re-run maps the same way

    for creature in Creature.objects.all().iterator():
        if SPECIES.get(creature.name) == creature.element:
            continue  # already a valid species for its element
        candidates = BY_ELEMENT.get(creature.element)
        if not candidates:
            continue  # unknown element (shouldn't happen) — leave the row alone
        creature.name = rng.choice(candidates)
        creature.save(update_fields=["name"])


def noop_reverse(apps, schema_editor):
    """Irreversible in practice — the original names aren't recoverable. Declared
    so the migration can still be un-applied without erroring."""


class Migration(migrations.Migration):
    dependencies = [
        ("bio_lab", "0011_alter_building_level_alter_user_coins_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize, noop_reverse),
    ]
