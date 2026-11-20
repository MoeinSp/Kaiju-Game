"""Giant-slayer serum stays on for every hit of the same giant.

Run: python -m game.worldboss_serum_selftest
Deletes the users and the boss it creates.
"""

from __future__ import annotations

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from bio_lab.models import Creature, SerumLab, User, WorldBoss  # noqa: E402
from game import serums, worldboss  # noqa: E402
from game.creature import GameError  # noqa: E402

UID = 9200000301
OTHER = 9200000302


def _wipe():
    User.objects.filter(id__in=[UID, OTHER]).delete()
    WorldBoss.objects.filter(name="تست غول‌کش").delete()


def _user(uid):
    return User.objects.create(
        id=uid, first_name="تست", lab_name="تست", coins=1000, dna_fragments=50,
        diamonds=10, energy=30, energy_updated_at=timezone.now(),
    )


def _creature(user):
    return Creature.objects.create(
        owner=user, name="سنگ‌دیو", element="earth", rarity="common", is_active=True,
        base_hp=100, base_atk=40, base_def=20, base_spd=20,
    )


def _arm(user):
    SerumLab.objects.create(user=user, stock={}, levels={"slayer": 1}, armed={"slayer": {"lvl": 1, "until": None}})


def _boss():
    now = timezone.now()
    return WorldBoss.objects.create(
        name="تست غول‌کش", element="fire", max_hp=10_000_000, current_hp=10_000_000,
        expires_at=now + timezone.timedelta(minutes=20), status=WorldBoss.ACTIVE,
    )


def main():
    _wipe()
    boss = None
    try:
        user = _user(UID)
        _creature(user)
        _arm(user)
        boss = _boss()
        first = worldboss.hit(user)
        second = worldboss.hit(user)
        assert first["damage"] == second["damage"], (first["damage"], second["damage"])
        bare = round(first["damage"] / 1.15)
        assert first["damage"] > bare
        row = SerumLab.objects.get(user=user)
        assert row.armed["slayer"]["spent"] is True
        assert serums.peek(user, "slayer") > 1
        try:
            serums.toggle(user, "slayer")
            raise AssertionError("spent serum was put back")
        except GameError as exc:
            assert "برنمی‌گرده" in str(exc)
        third = worldboss.hit(user)
        assert third["damage"] == first["damage"]
        row.refresh_from_db()
        assert "slayer" not in (row.armed or {})
        assert int((row.stock or {}).get("slayer", 0)) == 0

        other = _user(OTHER)
        _creature(other)
        _arm(other)
        worldboss.hit(other)
        serums.unarm(other, "slayer")
        row = SerumLab.objects.get(user=other)
        assert "slayer" not in (row.armed or {})
        assert int((row.stock or {}).get("slayer", 0)) == 0
        print("worldboss serum selftest ok", first["damage"])
    finally:
        if boss is not None:
            WorldBoss.objects.filter(id=boss.id).delete()
        _wipe()


if __name__ == "__main__":
    main()
