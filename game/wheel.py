import random

from django.db import transaction

from bio_lab.models import Creature, User
from bio_lab.repository import get_active_creature
from game import constants
from game.buildings import grant_speedup_card
from game.creature import add_capsules, combat_rating, effective_stats
from game.daily import consume_daily
from game.equipment import get_equipped_items
from game.lootbox import _roll_creature


def _compute_user_scale(user: User) -> tuple[float, int, int]:
    """Compute power-based and cup-based scaling factor for daily rewards."""
    active = get_active_creature(user)
    if active:
        equipped = get_equipped_items(active)
        stats = effective_stats(active, equipped)
        power = max(400, combat_rating(stats))
    else:
        power = 400

    cup = max(0, getattr(user, "cup", 0) or 0)

    # Power scaling factor: starter (500) -> 1.0x, mid (2500) -> 2.85x, high (8000) -> 6.0x
    power_factor = max(1.0, (power / 500.0) ** 0.65)
    # Cup scaling factor: 0 cups -> 1.0x, 1500 cups -> 1.5x, 3500 cups -> 2.16x
    cup_factor = 1.0 + (cup / 3000.0)

    scale = power_factor * cup_factor
    return scale, power, cup


def _roll_creature_rarity(cup: int, power: int) -> str:
    """Determine creature rarity chance based on user progression/cup."""
    if cup >= 3500 or power >= 7000:
        # High-end: Epic 40%, Legendary 50%, Mythic 10%
        rarities = ["epic", "legendary", "mythic"]
        weights = [40, 50, 10]
    elif cup >= 2000 or power >= 4000:
        # Mid-high: Rare 20%, Epic 50%, Legendary 28%, Mythic 2%
        rarities = ["rare", "epic", "legendary", "mythic"]
        weights = [20, 50, 28, 2]
    elif cup >= 800 or power >= 1800:
        # Mid: Common 15%, Rare 50%, Epic 30%, Legendary 5%
        rarities = ["common", "rare", "epic", "legendary"]
        weights = [15, 50, 30, 5]
    else:
        # Starter: Common 55%, Rare 35%, Epic 10%
        rarities = ["common", "rare", "epic"]
        weights = [55, 35, 10]

    return random.choices(rarities, weights=weights, k=1)[0]


def _build_prize_pool(user: User, scale: float, power: int, cup: int) -> list[dict]:
    """Build a dynamic weighted prize pool tailored to the player's power level."""
    # 1. Gold prizes (scaled)
    coins_small = max(1500, round(1800 * scale))
    coins_med = max(3500, round(4500 * scale))
    coins_large = max(8000, round(11000 * scale))

    # 2. DNA prizes (scaled)
    dna_small = max(15, round(18 * scale * 0.75))
    dna_med = max(35, round(45 * scale * 0.75))
    dna_large = max(80, round(100 * scale * 0.75))

    # 3. XP Food Capsules
    if power < 1500:
        capsule_tier = "small"
        capsule_label = "۳× غذای موش (XP)"
    elif power < 3500:
        capsule_tier = "medium"
        capsule_label = "۳× غذای مرغ (XP)"
    else:
        capsule_tier = "large"
        capsule_label = "۳× غذای گربه (XP)"

    # 4. Diamonds
    dia_small = random.choice([5, 7, 10])
    dia_med = random.choice([15, 20, 25])
    dia_large = random.choice([35, 50])

    # 5. Grand Jackpot
    jackpot_coins = round(25000 * scale)
    jackpot_dna = round(200 * scale * 0.75)
    jackpot_dia = 50

    prizes = [
        # Gold
        {"kind": "coins", "amount": coins_small, "weight": 20, "label": f"{coins_small:,} طلا"},
        {"kind": "coins", "amount": coins_med, "weight": 14, "label": f"{coins_med:,} طلا"},
        {"kind": "coins", "amount": coins_large, "weight": 7, "label": f"{coins_large:,} طلا (بسته بزرگ)"},

        # DNA
        {"kind": "dna", "amount": dna_small, "weight": 16, "label": f"{dna_small:,} DNA"},
        {"kind": "dna", "amount": dna_med, "weight": 10, "label": f"{dna_med:,} DNA"},
        {"kind": "dna", "amount": dna_large, "weight": 5, "label": f"{dna_large:,} DNA (ویال پیشرفته)"},

        # Diamonds
        {"kind": "diamonds", "amount": dia_small, "weight": 9, "label": f"{dia_small} الماس"},
        {"kind": "diamonds", "amount": dia_med, "weight": 4, "label": f"{dia_med} الماس"},
        {"kind": "diamonds", "amount": dia_large, "weight": 2, "label": f"{dia_large} الماس (صندوق جواهر)"},

        # XP Food Capsules
        {"kind": "xp_capsule", "tier": capsule_tier, "count": 3, "weight": 7, "label": capsule_label},

        # Speedup Cards
        {"kind": "speedup", "amount": 15, "weight": 4, "label": "کارت سرعت ۱۵ دقیقه"},
        {"kind": "speedup", "amount": 30, "weight": 2, "label": "کارت سرعت ۳۰ دقیقه"},

        # Creature Drop (~7% chance)
        {"kind": "creature", "rarity": _roll_creature_rarity(cup, power), "weight": 7, "label": "🐾 هیولای تصادفی!"},

        # Grand Jackpot (~1% chance)
        {
            "kind": "jackpot",
            "coins": jackpot_coins,
            "dna": jackpot_dna,
            "diamonds": jackpot_dia,
            "speedup": 30,
            "capsule_tier": capsule_tier,
            "capsule_count": 5,
            "weight": 1,
            "label": f"🎉 جک‌پات افسانه‌ای: {jackpot_coins:,} طلا + {jackpot_dna:,} DNA + {jackpot_dia} الماس + کارت ۳۰ دقیقه!",
        },
    ]
    return prizes


@transaction.atomic
def spin(user: User) -> dict:
    # consume the daily spin ATOMICALLY before granting, so a rapid double-tap can't
    # spin twice off one day's allowance.
    consume_daily(user, "wheel_spin")
    
    scale, power, cup = _compute_user_scale(user)
    prizes = _build_prize_pool(user, scale, power, cup)
    weights = [p["weight"] for p in prizes]
    prize = random.choices(prizes, weights=weights, k=1)[0]
    
    _apply_prize(user, prize)
    return prize


def _apply_prize(user: User, prize: dict) -> None:
    kind = prize["kind"]
    if kind == "coins":
        amount = prize["amount"]
        user.coins += amount
        user.save(update_fields=["coins"])
        _ledger(user, coins=amount)
    elif kind == "dna":
        amount = prize["amount"]
        user.dna_fragments += amount
        user.save(update_fields=["dna_fragments"])
        _ledger(user, dna=amount)
    elif kind == "diamonds":
        amount = prize["amount"]
        user.diamonds += amount
        user.save(update_fields=["diamonds"])
        _ledger(user, diamonds=amount)
    elif kind == "xp_capsule":
        tier = prize["tier"]
        count = prize.get("count", 1)
        add_capsules(user, tier, count)
    elif kind == "speedup":
        amount = prize["amount"]
        grant_speedup_card(user, amount, count=1)
    elif kind == "creature":
        rarity = prize["rarity"]
        creature = _roll_creature(user, rarity)
        rarity_fa = constants.RARITY_LABELS.get(rarity, rarity)
        elem_fa = constants.ELEMENT_WORDS.get(creature.element, creature.element)
        prize["label"] = f"🐾 هیولای جدید: [{rarity_fa}] {creature.name} ({elem_fa})"
        prize["creature_id"] = creature.id
        prize["creature_name"] = creature.name
        prize["rarity"] = rarity
    elif kind == "jackpot":
        user.coins += prize["coins"]
        user.dna_fragments += prize["dna"]
        user.diamonds += prize["diamonds"]
        user.save(update_fields=["coins", "dna_fragments", "diamonds"])
        _ledger(user, coins=prize["coins"], dna=prize["dna"], diamonds=prize["diamonds"])
        grant_speedup_card(user, prize["speedup"], count=1)
        add_capsules(user, prize["capsule_tier"], prize["capsule_count"])


def _ledger(user: User, **kw) -> None:
    from game.ledger import record_gain

    record_gain(user, "wheel", **kw)
