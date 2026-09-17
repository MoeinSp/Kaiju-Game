"""Clash Royale-style Arena Chests.

- Earned ONLY by winning in PvP Arena.
- Maximum 4 chest slots per player.
- 4 Chest types with real-time unlock timers:
  * 🥈 Silver Chest: 3 hours
  * 🥇 Golden Chest: 8 hours
  * 🔮 Magical Chest: 12 hours
  * 👑 Mega Chest: 24 hours
- Rewards scale with the player's Cup League at the time the chest was won.
- Only 1 chest can unlock at a time (unless queued by a VIP subscriber).
- Subscribed users (Silver / Gold) can queue 1 chest to auto-unlock next.
"""

from __future__ import annotations

import datetime
import math
import random
from django.db import transaction
from django.utils import timezone

from bio_lab.models import ArenaChest, Creature, User
from game import constants
from game.creature import GameError
from game.equipment import roll_equipment
from game.lootbox import roll_rarity, _roll_creature
from game.subscription import is_subscription_active

ARENA_CHEST_MAX_SLOTS = 4

ARENA_CHEST_TIERS = {
    "silver": {
        "key": "silver",
        "name": "جعبه نقره‌ای",
        "emoji": "🥈",
        "unlock_hours": 3,
        "weight": 70.0,  # 70% drop rate
        "base_gold": 1200,
        "base_dna": 40,
        "creature_chance": 0.35,
        "guaranteed_creature_rarity": "common",
        "rarity_weights": {"common": 88, "rare": 11, "epic": 1},
        "equip_weights": {"common": 88, "rare": 11, "epic": 1},
    },
    "golden": {
        "key": "golden",
        "name": "جعبه طلایی",
        "emoji": "🥇",
        "unlock_hours": 8,
        "weight": 20.0,  # 20% drop rate
        "base_gold": 3800,
        "base_dna": 120,
        "creature_chance": 0.65,
        "guaranteed_creature_rarity": "rare",
        "rarity_weights": {"common": 50, "rare": 40, "epic": 9.5, "legendary": 0.5},
        "equip_weights": {"common": 50, "rare": 40, "epic": 9.5, "legendary": 0.5},
    },
    "magical": {
        "key": "magical",
        "name": "جعبه جادویی",
        "emoji": "🔮",
        "unlock_hours": 12,
        "weight": 7.5,  # 7.5% drop rate
        "base_gold": 10000,
        "base_dna": 350,
        "creature_chance": 0.85,
        "guaranteed_creature_rarity": "epic",
        "rarity_weights": {"rare": 40, "epic": 50, "legendary": 9.5, "mythic": 0.5},
        "equip_weights": {"rare": 40, "epic": 50, "legendary": 9.5, "mythic": 0.5},
    },
    "mega": {
        "key": "mega",
        "name": "جعبه مگا / امگا",
        "emoji": "👑",
        "unlock_hours": 24,
        "weight": 2.5,  # 2.5% drop rate
        "base_gold": 28000,
        "base_dna": 900,
        "creature_chance": 1.0,
        "guaranteed_creature_rarity": "legendary",
        "rarity_weights": {"epic": 40, "legendary": 52, "mythic": 8},
        "equip_weights": {"epic": 40, "legendary": 52, "mythic": 8},
    },
}


def get_guaranteed_rarity(chest_type: str, cup: int) -> str:
    """Return the minimum guaranteed creature/equipment rarity based on cup count.

    Mega (امگا / مگا):
      - 0-1499: epic (حداقل حماسی)
      - 1500-3499: legendary (حداقل افسانه‌ای)
      - 3500+: mythic (تضمینی قطعی اساطیری)
    Magical (جادویی):
      - 0-1499: rare (حداقل کمیاب)
      - 1500-3499: epic (حداقل حماسی)
      - 3500+: legendary (حداقل افسانه‌ای)
    Golden (طلایی):
      - 0-1499: common (حداقل معمولی با شانس کمیاب)
      - 1500-3499: rare (حداقل کمیاب)
      - 3500+: epic (حداقل حماسی)
    Silver (نقره‌ای):
      - 0-3499: common (حداقل معمولی)
      - 3500+: rare (حداقل کمیاب)
    """
    cup = max(0, int(cup))
    if chest_type == "mega":
        if cup >= 3500:
            return "mythic"
        elif cup >= 1500:
            return "legendary"
        return "epic"
    elif chest_type == "magical":
        if cup >= 3500:
            return "legendary"
        elif cup >= 1500:
            return "epic"
        return "rare"
    elif chest_type == "golden":
        if cup >= 3500:
            return "epic"
        elif cup >= 1500:
            return "rare"
        return "common"
    else:  # silver
        if cup >= 3500:
            return "rare"
        return "common"


def get_chest_effective_weights(chest_type: str, cup: int, base_weights: dict[str, float]) -> dict[str, float]:
    """Filter base weights so that any rarity below get_guaranteed_rarity is strictly excluded."""
    guaranteed = get_guaranteed_rarity(chest_type, cup)
    min_idx = constants.RARITY_ORDER.index(guaranteed)
    filtered = {
        r: w for r, w in (base_weights or {}).items()
        if r in constants.RARITY_ORDER and constants.RARITY_ORDER.index(r) >= min_idx and w > 0
    }
    if not filtered:
        return {guaranteed: 100.0}
    return filtered


def get_user_chests(user: User) -> list[ArenaChest]:
    """Return all chests belonging to user, sorted by slot number (1..4).
    Also refreshes any chest whose unlock timer has elapsed."""
    advance_user_chests(user)
    return list(ArenaChest.objects.filter(user=user).order_by("slot"))


def get_free_slot(user: User) -> int | None:
    used_slots = set(ArenaChest.objects.filter(user=user).values_list("slot", flat=True))
    for s in range(1, ARENA_CHEST_MAX_SLOTS + 1):
        if s not in used_slots:
            return s
    return None


def roll_chest_tier() -> str:
    tiers = list(ARENA_CHEST_TIERS.keys())
    weights = [ARENA_CHEST_TIERS[t]["weight"] for t in tiers]
    return random.choices(tiers, weights=weights, k=1)[0]


def league_multiplier(cup: int) -> float:
    """Multiplier for gold and DNA based on player's cup league (1.0x at Bronze to ~3.25x at Champion)."""
    lg = constants.league_for_cup(cup)
    try:
        idx = constants.LEAGUES.index(lg)
    except ValueError:
        idx = 0
    return 1.0 + (idx * 0.15)


@transaction.atomic
def award_chest_on_win(user: User) -> ArenaChest | None:
    """Award a chest upon winning a ranked arena duel. Returns the chest, or None if slots are full."""
    slot = get_free_slot(user)
    if slot is None:
        return None
    tier = roll_chest_tier()
    chest = ArenaChest.objects.create(
        user=user,
        slot=slot,
        chest_type=tier,
        cup_at_drop=user.cup,
        status="locked",
    )
    return chest


def advance_user_chests(user: User) -> None:
    """Transition any expired unlocking chests to 'ready' and auto-start any queued chest.
    Correctly accounts for elapsed time since the previous chest finished."""
    now = timezone.now()
    while True:
        unlocking = ArenaChest.objects.filter(user=user, status="unlocking").first()
        if unlocking:
            if unlocking.unlock_finishes_at and unlocking.unlock_finishes_at <= now:
                finish_time = unlocking.unlock_finishes_at
                unlocking.status = "ready"
                unlocking.save(update_fields=["status"])

                # Check if there is a queued chest to auto-start
                queued = ArenaChest.objects.filter(user=user, status="queued").order_by("slot").first()
                if queued:
                    cfg = ARENA_CHEST_TIERS.get(queued.chest_type, ARENA_CHEST_TIERS["silver"])
                    duration = datetime.timedelta(hours=cfg["unlock_hours"])
                    start_time = finish_time
                    fin_time = start_time + duration
                    queued.unlock_starts_at = start_time
                    queued.unlock_finishes_at = fin_time
                    if fin_time <= now:
                        queued.status = "ready"
                        queued.save(update_fields=["status", "unlock_starts_at", "unlock_finishes_at"])
                        continue
                    else:
                        queued.status = "unlocking"
                        queued.save(update_fields=["status", "unlock_starts_at", "unlock_finishes_at"])
                        break
                else:
                    break
            else:
                break
        else:
            # If no chest is actively unlocking, any queued chest should immediately start unlocking
            queued = ArenaChest.objects.filter(user=user, status="queued").order_by("slot").first()
            if queued:
                cfg = ARENA_CHEST_TIERS.get(queued.chest_type, ARENA_CHEST_TIERS["silver"])
                duration = datetime.timedelta(hours=cfg["unlock_hours"])
                start_time = now
                fin_time = start_time + duration
                queued.unlock_starts_at = start_time
                queued.unlock_finishes_at = fin_time
                if fin_time <= now:
                    queued.status = "ready"
                    queued.save(update_fields=["status", "unlock_starts_at", "unlock_finishes_at"])
                    continue
                else:
                    queued.status = "unlocking"
                    queued.save(update_fields=["status", "unlock_starts_at", "unlock_finishes_at"])
                    break
            else:
                break


@transaction.atomic
def start_unlock(user: User, chest_id: int) -> ArenaChest:
    advance_user_chests(user)
    chest = ArenaChest.objects.select_for_update().filter(id=chest_id, user=user).first()
    if not chest:
        raise GameError("این جعبه پیدا نشد.")
    if chest.status != "locked":
        raise GameError(f"این جعبه در وضعیت {chest.status} قرار دارد.")

    # Check if another chest is actively unlocking
    active = ArenaChest.objects.filter(user=user, status="unlocking").exists()
    if active:
        raise GameError("هم‌اکنون یک جعبه دیگر در حال باز شدن است! نمی‌توانی دو جعبه را همزمان باز کنی.")

    cfg = ARENA_CHEST_TIERS.get(chest.chest_type, ARENA_CHEST_TIERS["silver"])
    now = timezone.now()
    duration = datetime.timedelta(hours=cfg["unlock_hours"])

    chest.status = "unlocking"
    chest.unlock_starts_at = now
    chest.unlock_finishes_at = now + duration
    chest.save(update_fields=["status", "unlock_starts_at", "unlock_finishes_at"])
    return chest


@transaction.atomic
def queue_chest(user: User, chest_id: int) -> ArenaChest:
    advance_user_chests(user)
    if not is_subscription_active(user):
        raise GameError("قابلیت در صف گذاشتن جعبه فقط مخصوص دارندگان اشتراک ویژه (نقره‌ای و طلایی) است!")

    chest = ArenaChest.objects.select_for_update().filter(id=chest_id, user=user).first()
    if not chest:
        raise GameError("این جعبه پیدا نشد.")
    if chest.status != "locked":
        raise GameError("تنها جعبه‌های قفل شده را می‌توانی در صف بگذاری.")

    # If no chest is actively unlocking, start it directly!
    if not ArenaChest.objects.filter(user=user, status="unlocking").exists():
        return start_unlock(user, chest_id)

    # Check if user already has a queued chest
    if ArenaChest.objects.filter(user=user, status="queued").exists():
        raise GameError("هم‌اکنون یک جعبه در صف داری! سقف صف ۱ جعبه است.")

    chest.status = "queued"
    chest.save(update_fields=["status"])
    return chest


def seconds_until_ready(chest: ArenaChest) -> int:
    if chest.status == "ready":
        return 0
    if chest.status != "unlocking" or not chest.unlock_finishes_at:
        cfg = ARENA_CHEST_TIERS.get(chest.chest_type, ARENA_CHEST_TIERS["silver"])
        return cfg["unlock_hours"] * 3600
    now = timezone.now()
    rem = (chest.unlock_finishes_at - now).total_seconds()
    return max(0, int(rem))


def speedup_diamond_cost(chest: ArenaChest) -> int:
    secs = seconds_until_ready(chest)
    if secs <= 0:
        return 0
    # 1 diamond per 15 minutes remaining, minimum 1 diamond
    mins = math.ceil(secs / 60)
    return max(1, math.ceil(mins / 15))


@transaction.atomic
def speedup_with_diamonds(user: User, chest_id: int) -> ArenaChest:
    user = User.objects.select_for_update().get(id=user.id)
    chest = ArenaChest.objects.select_for_update().filter(id=chest_id, user=user).first()
    if not chest:
        raise GameError("این جعبه پیدا نشد.")
    if chest.status not in ("unlocking", "locked", "queued"):
        raise GameError("این جعبه در حال حاضر آماده باز شدنه!")

    cost = speedup_diamond_cost(chest)
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! باز کردن فوری این جعبه {cost} الماس لازم داره (الان {user.diamonds} داری).")

    user.diamonds -= cost
    user.save(update_fields=["diamonds"])

    chest.status = "ready"
    chest.unlock_finishes_at = timezone.now()
    chest.save(update_fields=["status", "unlock_finishes_at"])
    advance_user_chests(user)
    return chest


@transaction.atomic
def open_chest(user: User, chest_id: int) -> dict:
    advance_user_chests(user)
    user = User.objects.select_for_update().get(id=user.id)
    chest = ArenaChest.objects.select_for_update().filter(id=chest_id, user=user).first()
    if not chest:
        raise GameError("این جعبه پیدا نشد.")

    now = timezone.now()
    is_ready = chest.status == "ready" or (chest.status == "unlocking" and chest.unlock_finishes_at and chest.unlock_finishes_at <= now)
    if not is_ready:
        raise GameError("تایمر این جعبه هنوز تمام نشده است!")

    cfg = ARENA_CHEST_TIERS.get(chest.chest_type, ARENA_CHEST_TIERS["silver"])
    mult = league_multiplier(chest.cup_at_drop)

    coins = round(cfg["base_gold"] * mult)
    dna = round(cfg["base_dna"] * mult)

    # Creature vs Equipment
    creature = None
    item = None
    give_creature = random.random() < cfg["creature_chance"]
    guaranteed = get_guaranteed_rarity(chest.chest_type, chest.cup_at_drop)

    if give_creature:
        c_weights = get_chest_effective_weights(chest.chest_type, chest.cup_at_drop, cfg["rarity_weights"])
        rarity = roll_rarity(c_weights)
        if constants.RARITY_ORDER.index(rarity) < constants.RARITY_ORDER.index(guaranteed):
            rarity = guaranteed
        creature = _roll_creature(user, rarity)
    else:
        e_weights = get_chest_effective_weights(chest.chest_type, chest.cup_at_drop, cfg.get("equip_weights", {}))
        rarity = roll_rarity(e_weights)
        if constants.RARITY_ORDER.index(rarity) < constants.RARITY_ORDER.index(guaranteed):
            rarity = guaranteed
        item = roll_equipment(user, rarity)

    # Small diamond bonus on higher tier chests
    diamonds = 0
    if chest.chest_type == "magical":
        diamonds = random.randint(2, 5)
    elif chest.chest_type == "mega":
        diamonds = random.randint(8, 20)

    user.coins += coins
    user.dna_fragments += dna
    if diamonds:
        user.diamonds += diamonds
    user.save(update_fields=["coins", "dna_fragments", "diamonds"])

    from game.ledger import record_gain
    record_gain(user, "arena_chest", coins=coins, dna=dna, diamonds=diamonds)

    chest_type_key = chest.chest_type
    slot_num = chest.slot
    chest.delete()

    advance_user_chests(user)

    # If there is a chest currently unlocking (or recently auto-started from queue), pass it
    next_started = ArenaChest.objects.filter(user=user, status="unlocking").first()

    return {
        "tier": chest_type_key,
        "name": cfg["name"],
        "emoji": cfg["emoji"],
        "slot": slot_num,
        "coins": coins,
        "dna": dna,
        "diamonds": diamonds,
        "creature": creature,
        "item": item,
        "rarity": rarity,
        "next_started": next_started,
    }


@transaction.atomic
def admin_grant_chest(user: User, chest_type: str) -> ArenaChest:
    """Admin tool to grant an arena chest to a player's first free slot."""
    cfg = ARENA_CHEST_TIERS.get(chest_type)
    if not cfg:
        raise GameError("نوع جعبه نامعتبر است.")
    slot = get_free_slot(user)
    if slot is None:
        raise GameError("تمامی ۴ جایگاه جعبه‌های این کاربر پر است.")
    return ArenaChest.objects.create(
        user=user,
        slot=slot,
        chest_type=chest_type,
        cup_at_drop=user.cup,
        status="locked",
    )
