"""FULL GAME WIPE: reset every player to a brand-new account, keeping only their
identity + lab name, and restart the creature/equipment/building id sequences at 1.

This is the destructive "official v1 relaunch" reset. It:
  1. snapshots each user's identity (id, username, first_name, lab_name, is_banned);
  2. deletes ALL game state — every user (cascading creatures, equipment, buildings,
     jobs, eggs, cards, logs, memberships, season results…), plus alliances, wars,
     raid/group bosses, drops, interactive battles and season state;
  3. restarts the creature/equipment/building id sequences at 1 (Postgres);
  4. recreates each user with their kept identity and runs the fresh-player bootstrap
     (starter creature, buildings, starting speed-up cards).

Operator config (groups, themes, button/emoji overrides, required channels, shop
items, bot config) is deliberately left untouched.

    docker compose exec web python manage.py reset_all_players --dry-run
    docker compose exec web python manage.py reset_all_players --yes
"""

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from bio_lab.models import (
    Alliance,
    AllianceWarState,
    Creature,
    Equipment,
    Building,
    GroupDrop,
    GroupEventLog,
    InteractiveBattle,
    RaidBoss,
    SeasonState,
    User,
)


class Command(BaseCommand):
    help = "Wipe all player progress to fresh accounts (keeps lab name); restart creature ids at 1."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="actually perform the wipe")
        parser.add_argument("--dry-run", action="store_true", help="report only")

    def _reset_sequences(self):
        """Restart auto-increment id sequences so new creatures start at #1 again."""
        if connection.vendor != "postgresql":
            self.stdout.write("  (non-Postgres: skipping sequence reset)")
            return
        with connection.cursor() as cur:
            for model in (Creature, Equipment, Building):
                seq = f"{model._meta.db_table}_id_seq"
                cur.execute(f'ALTER SEQUENCE IF EXISTS "{seq}" RESTART WITH 1;')
                self.stdout.write(f"  sequence {seq} → 1")

    def handle(self, *args, **opts):
        from game import constants
        from game.buildings import get_or_create_buildings, grant_speedup_card
        from game.creature import create_starter_creature

        identities = [
            {"id": u.id, "username": u.username, "first_name": u.first_name,
             "lab_name": u.lab_name, "is_banned": u.is_banned}
            for u in User.objects.all().iterator()
        ]
        self.stdout.write(f"players to reset: {len(identities)}")
        if opts["dry_run"] or not opts["yes"]:
            self.stdout.write(self.style.WARNING(
                "[dry-run] no changes made. Re-run with --yes to actually wipe."))
            return

        with transaction.atomic():
            # non-user-owned game state first (not cleared by cascading user deletes)
            for model in (AllianceWarState, RaidBoss, GroupDrop, GroupEventLog,
                          InteractiveBattle, SeasonState, Alliance):
                n = model.objects.all().delete()[0]
                self.stdout.write(f"  cleared {model.__name__}: {n}")
            # every user + all their cascaded game rows
            n = User.objects.all().delete()[0]
            self.stdout.write(f"  deleted user-owned rows: {n}")
            self._reset_sequences()

            # recreate fresh accounts in id order and bootstrap each
            for idn in identities:
                fresh = User.objects.create(**idn)
                create_starter_creature(fresh)
                for minutes, count in constants.STARTING_SPEEDUP_CARDS.items():
                    grant_speedup_card(fresh, minutes, count=count)
                get_or_create_buildings(fresh)

        self.stdout.write(self.style.SUCCESS(
            f"reset complete — {len(identities)} fresh accounts, creature ids restart at 1."))
