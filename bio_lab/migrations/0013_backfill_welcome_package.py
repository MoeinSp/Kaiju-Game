"""Tops existing players up to the new welcome package.

The starting grant was raised (200 gold / 0 DNA / 0 diamonds -> 5000 / 100 / 100)
so a new player can actually build, forge, and open a diamond box on day one.
Without this, everyone who joined before the change would be permanently poorer
than someone who signs up a minute later.

Only tops *up* — a player who has already earned more than the new floor keeps
what they have.
"""

from django.db import migrations
from django.db.models import F

STARTING_COINS = 5000
STARTING_DNA = 100
STARTING_DIAMONDS = 100


def backfill(apps, schema_editor):
    User = apps.get_model("bio_lab", "User")
    User.objects.filter(coins__lt=STARTING_COINS).update(coins=STARTING_COINS)
    User.objects.filter(dna_fragments__lt=STARTING_DNA).update(dna_fragments=STARTING_DNA)
    User.objects.filter(diamonds__lt=STARTING_DIAMONDS).update(diamonds=STARTING_DIAMONDS)


def noop_reverse(apps, schema_editor):
    """Not reversible — we can't tell a granted coin from an earned one."""


class Migration(migrations.Migration):
    dependencies = [
        ("bio_lab", "0012_normalize_species_names"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
