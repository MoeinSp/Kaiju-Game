"""Checks for the bank, earned gold, and group-defense sandbag rule.

Run: python -m game.bank_selftest
Deletes the users it creates.
"""

from __future__ import annotations

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from bio_lab.models import AttackLog, Building, Creature, DefenseTeam, User  # noqa: E402
from game import constants  # noqa: E402
from game.bank import (  # noqa: E402
    apply_group_loot,
    deposit,
    deposit_from_wallet,
    lootable_gold,
    settle_interest,
    snapshot,
    withdraw_to_wallet,
)
from game.creature import GameError  # noqa: E402
from game.defense import group_fight_defender  # noqa: E402
from game.wallet import spend_wallet  # noqa: E402

SENDER = 9100000011
BUYER = 9100000012
YOUNG = 9100000013


def _wipe():
    User.objects.filter(id__in=[SENDER, BUYER, YOUNG]).delete()


def _user(uid, *, days_old, coins=0, bank=0):
    user = User.objects.create(id=uid, first_name="t", coins=coins, bank_gold=bank, diamonds=5000)
    if days_old:
        User.objects.filter(id=uid).update(created_at=timezone.now() - timezone.timedelta(days=days_old))
        user.refresh_from_db()
    return user


def _hall(user, level):
    Building.objects.create(owner=user, building_type="trade_hall", level=level)
    Building.objects.create(owner=user, building_type="main_hall", level=level)


def _wins(user, n=100):
    AttackLog.objects.bulk_create([
        AttackLog(attacker=user, defender=None, defender_label="bot", is_fake_defender=True, attacker_won=True)
        for _ in range(n)
    ])


def check_prices():
    assert constants.creature_transfer_price_bounds("mythic", 1) == (625_000, 1_250_000)
    assert constants.creature_transfer_price_bounds("mythic", 3) == (2_500_000, 5_000_000)
    assert constants.creature_transfer_price_bounds("mythic", 4) is None
    assert constants.creature_transfer_price_bounds("legendary", 4) == (2_500_000, 5_000_000)
    assert constants.creature_transfer_price_bounds("legendary", 5) is None
    assert constants.creature_transfer_price_bounds("common", 1) == (31_250, 62_500)
    assert constants.bank_daily_withdraw(1) == 200_000
    assert constants.bank_daily_withdraw(5) == 1_000_000
    assert constants.trade_hall_gold_cap(0) == 0
    assert constants.group_shield_diamonds(80) == 60
    assert constants.group_shield_diamonds(150) == 113
    assert constants.SHIELD_SHOP_TIERS["8h"]["diamonds"] == 80
    assert constants.GROUP_SHIELD_SHOP_TIERS["7d"]["diamonds"] == 375
    assert constants.ARENA_SHIELD_HOURS // 2 == 4
    assert constants.GROUP_SHIELD_HOURS == 8


def check_wallet_and_bank():
    seller = _user(SENDER, days_old=10, coins=100_000, bank=500_000)
    _hall(seller, 5)
    assert settle_interest(seller) == 10_000
    seller.refresh_from_db()
    assert seller.bank_gold == 500_000
    assert seller.coins == 110_000
    assert seller.bank_interest_notice == 10_000
    assert seller.bank_interest_notice_days == 1
    assert settle_interest(seller) == 0
    moved = withdraw_to_wallet(seller, 5)
    seller.refresh_from_db()
    assert moved == 500_000
    assert seller.bank_gold == 0
    # 2% of the 500,000 that was in the bank at settle, then the withdrawal.
    assert seller.coins == 610_000
    assert lootable_gold(seller) == 610_000
    assert spend_wallet(seller, 610_000)
    assert seller.coins == 0
    assert not spend_wallet(seller, 1)
    shot = snapshot(seller, 5)
    assert shot["left"] == 500_000  # 1M budget, 500k used
    try:
        deposit(seller, 5_000_001, level=5)
        raise AssertionError("over-capacity deposit should fail")
    except GameError:
        pass
    deposit(seller, 1_000_000, level=5)
    assert seller.bank_gold == 1_000_000


def check_young_and_loot():
    young = _user(YOUNG, days_old=1, coins=0)
    _hall(young, 5)
    try:
        deposit(young, 30_001, level=5)
        raise AssertionError("young account accepted too much")
    except GameError:
        pass
    deposit(young, 30_000, level=5)
    young.save(update_fields=["bank_gold"])
    assert young.bank_gold == 30_000
    young.bank_interest_on = None
    young.save(update_fields=["bank_interest_on"])
    assert settle_interest(young) == 600
    young.refresh_from_db()
    assert young.bank_gold == 30_000
    assert young.coins == 600
    assert settle_interest(young) == 0
    young.bank_interest_on = None
    young.save(update_fields=["bank_interest_on"])
    assert settle_interest(young) == 600
    young.refresh_from_db()
    assert young.bank_interest_notice == 1_200
    assert young.bank_interest_notice_days == 2
    from game.bank import interest_notice_text, take_interest_notice
    assert "۲" in interest_notice_text(1_200, 2) or "2" in interest_notice_text(1200, 2)
    assert take_interest_notice(young) == (1_200, 2)
    assert take_interest_notice(young) == (0, 0)
    young.coins = 50_000
    young.save(update_fields=["coins"])
    deposit_from_wallet(young, 5, 20_000)
    young.refresh_from_db()
    assert young.bank_gold == 50_000
    assert young.coins == 30_000
    # gold left in the removed split, and a stale in-memory zero, still deposit
    young.unearned_coins = 4_000
    young.save(update_fields=["unearned_coins"])
    young.coins = 0
    deposit_from_wallet(young, 5, 2_000)
    young.refresh_from_db()
    assert young.unearned_coins == 0
    assert young.coins == 32_000
    assert young.bank_gold == 52_000
    veteran = _user(BUYER, days_old=20, coins=80_000, bank=0)
    assert lootable_gold(veteran) == 80_000
    apply_group_loot(veteran, 5_000)
    assert veteran.coins == 75_000


def check_sandbag():
    from bio_lab.models import Building, CreatureAssignment

    owner = User.objects.get(id=SENDER)
    weak = Creature.objects.create(owner=owner, name="ضعیف", element="fire", rarity="common", is_active=True, base_atk=10, base_hp=50)
    strong = Creature.objects.create(owner=owner, name="قوی", element="water", rarity="mythic", is_active=False, base_atk=800, base_hp=800)
    DefenseTeam.objects.create(owner=owner, slot1=weak)
    attacker = Creature.objects.create(owner=owner, name="مهاجم", element="earth", rarity="common", is_active=False, base_atk=20)
    # attacker creature owned by same user is fine for the picker
    chosen = group_fight_defender(owner, attacker, 20)
    assert chosen.id == strong.id, chosen.name
    DefenseTeam.objects.filter(owner=owner).update(slot1=strong)
    chosen = group_fight_defender(owner, attacker, 20)
    assert chosen.id == strong.id

    # bypass that players reported: put the mythic ON the defense team, then park it in a
    # mine — the old 80% check still counted it, pick_defender skipped it, weak active fought
    mine, _ = Building.objects.get_or_create(owner=owner, building_type="gold_collector",
                                             defaults={"level": 1})
    if mine.level < 1:
        mine.level = 1
        mine.save(update_fields=["level"])
    CreatureAssignment.objects.filter(creature=strong).delete()
    CreatureAssignment.objects.create(building=mine, creature=strong)
    chosen = group_fight_defender(owner, attacker, 20)
    assert chosen.id == strong.id, f"busy defense mythic must still fight, got {chosen and chosen.name}"

    # same with a second strong in the cave while a weak one is the only free defender
    from bio_lab.models import BreedingJob
    strong2 = Creature.objects.create(owner=owner, name="قوی۲", element="fire", rarity="mythic",
                                      is_active=False, base_atk=790, base_hp=790)
    mate = Creature.objects.create(owner=owner, name="جفت", element="fire", rarity="common",
                                   is_active=False, base_atk=10, base_hp=50)
    BreedingJob.objects.filter(owner=owner).delete()
    BreedingJob.objects.create(owner=owner, parent_a=strong2, parent_b=mate,
                               finishes_at=timezone.now() + timezone.timedelta(hours=6))
    DefenseTeam.objects.filter(owner=owner).update(slot1=strong, slot2=strong2)
    chosen = group_fight_defender(owner, attacker, 20)
    assert chosen.id in (strong.id, strong2.id), f"mine+cave sandbag got {chosen and chosen.name}"


def main():
    _wipe()
    try:
        check_prices()
        check_wallet_and_bank()
        check_young_and_loot()
        _wins(User.objects.get(id=SENDER))
        check_sandbag()
        print("bank selftest ok")
    finally:
        _wipe()


if __name__ == "__main__":
    sys.exit(main() or 0)
