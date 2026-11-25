"""Giant-slayer serum is ONE hit: the boss screen arms it, that hit spends it.

Run: python -m game.worldboss_serum_selftest
Deletes the users and the boss it creates.
"""

from __future__ import annotations

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from bio_lab.models import Building, Creature, SerumLab, User, WorldBoss  # noqa: E402
from game import serums, worldboss  # noqa: E402
from game.creature import GameError  # noqa: E402

UID = 9200000301
OTHER = 9200000302


def _wipe():
    User.objects.filter(id__in=[UID, OTHER]).delete()
    WorldBoss.objects.filter(name="تست غول‌کش").delete()


def _user(uid):
    return User.objects.create(
        id=uid, first_name="تست", lab_name="تست", coins=1000, dna_fragments=5000,
        diamonds=10, energy=30, energy_updated_at=timezone.now(),
    )


def _lab(user, stock=2):
    """A real level-4 serum lab (slayer unlocks at 4) with `stock` serums on the shelf."""
    Building.objects.create(owner=user, building_type="serum_lab", level=4)
    return SerumLab.objects.create(user=user, stock={"slayer": stock}, levels={"slayer": 1}, armed={})


def _creature(user):
    return Creature.objects.create(
        owner=user, name="سنگ‌دیو", element="earth", rarity="common", is_active=True,
        base_hp=100, base_atk=40, base_def=20, base_spd=20,
    )


def _boss():
    now = timezone.now()
    return WorldBoss.objects.create(
        name="تست غول‌کش", element="fire", max_hp=10_000_000, current_hp=10_000_000,
        expires_at=now + timezone.timedelta(minutes=20), status=WorldBoss.ACTIVE,
    )


def _row(user):
    row = SerumLab.objects.get(user=user)
    return row.armed or {}, row.stock or {}


def main():
    _wipe()
    boss = None
    try:
        user = _user(UID)
        _creature(user)
        _lab(user, 2)
        boss = _boss()

        # arm on the boss screen, then hit: that hit gets the serum and it comes off the shelf
        m = serums.toggle(user, "slayer")
        assert m["on"] is True, m
        first = worldboss.hit(user)
        armed, stock = _row(user)
        assert "slayer" not in armed, armed                        # spent by the hit
        assert int(stock.get("slayer", 0)) == 1, stock             # one of the two left on the shelf

        # the same boss, next hit, nothing armed: bare damage
        bare = worldboss.hit(user)
        assert bare["damage"] < first["damage"], (first["damage"], bare["damage"])
        # exactly one serum's worth of difference (level 1 = +15%)
        assert first["damage"] == round(bare["damage"] * 1.15), (first["damage"], bare["damage"])
        assert serums.moment(user, "slayer")["stock"] == 1

        # picking the second serum arms it again for the third hit
        serums.toggle(user, "slayer")
        third = worldboss.hit(user)
        assert third["damage"] == first["damage"], (first["damage"], third["damage"])
        armed, stock = _row(user)
        assert "slayer" not in armed, armed
        assert int(stock.get("slayer", 0)) == 0, stock

        # with an empty shelf there is nothing left to pick for a fourth hit
        assert serums.moment(user, "slayer") is None

        # putting a chosen serum back keeps it on the shelf (it was never spent)
        other = _user(OTHER)
        _creature(other)
        _lab(other, 1)
        serums.toggle(other, "slayer")
        assert serums.peek(other, "slayer") > 1
        serums.unarm(other, "slayer")
        armed, stock = _row(other)
        assert "slayer" not in armed, armed
        assert int(stock.get("slayer", 0)) == 1, stock
        assert serums.peek(other, "slayer") == 1.0

        # a spent serum is not refundable: the toggle now arms the NEXT one, never the used one
        serums.toggle(other, "slayer")
        worldboss.hit(other)
        armed, stock = _row(other)
        assert "slayer" not in armed, armed
        assert int(stock.get("slayer", 0)) == 0, stock
        try:
            serums.toggle(other, "slayer")
            raise AssertionError("a serum was armed with an empty shelf")
        except GameError:
            pass

        print("worldboss serum selftest ok", first["damage"], "vs bare", bare["damage"])
    finally:
        if boss is not None:
            WorldBoss.objects.filter(id=boss.id).delete()
        _wipe()


if __name__ == "__main__":
    main()