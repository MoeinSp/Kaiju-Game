import random

from django.db import transaction

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError, InsufficientGoldError
from game.equipment import roll_equipment


def roll_rarity(weights: dict[str, float] | None = None) -> str:
    weights = weights or constants.LOOTBOX_RARITY_WEIGHTS
    rarities = list(weights)
    return random.choices(rarities, weights=list(weights.values()), k=1)[0]


def _roll_creature(user: User, rarity: str) -> Creature:
    """Dropped inactive — get_active_creature() assumes exactly one is_active=True
    row per owner, so a biocrate creature must never silently steal that slot from
    whatever the player already has active. Players activate it themselves via
    /select once they see it in /collection."""
    mult = constants.RARITY_STAT_MULTIPLIER[rarity]
    element = constants.random_element()
    return Creature.objects.create(
        owner=user,
        name=constants.random_species_name(element),
        element=element,
        rarity=rarity,
        base_hp=round(constants.STARTER_BASE_HP * mult),
        base_atk=round(constants.STARTER_BASE_ATK * mult),
        base_def=round(constants.STARTER_BASE_DEF * mult),
        base_spd=round(constants.STARTER_BASE_SPD * mult),
        is_active=False,
    )


# Bulk-open deal: pay for BULK_PAY boxes, open BULK_OPEN (one free).
BULK_PAY = 10
BULK_OPEN = 11


def _biocrate_roll_once(user: User, cfg: dict, tier: str) -> dict:
    """One biocrate outcome WITHOUT charging — shared by single and bulk opens.

    Decide creature-vs-equipment FIRST (per-tier chance), then roll rarity from the
    table that belongs to that outcome — pricier tiers give a creature more often
    and skew its rarity higher; equipment uses the tier's own gear table."""
    if random.random() < cfg["creature_chance"]:
        rarity = roll_rarity(cfg["weights"])
        creature = _roll_creature(user, rarity)
        return {"kind": "creature", "rarity": rarity, "creature": creature, "tier": tier}
    rarity = roll_rarity(cfg.get("equip_weights"))
    item = roll_equipment(user, rarity)
    return {"kind": "equipment", "rarity": rarity, "item": item, "tier": tier}


def _biocrate_cfg(tier: str) -> dict:
    cfg = constants.BIOCRATE_TIERS.get(tier)
    if cfg is None:
        raise GameError("این نوع باکس ژنتیکی وجود نداره.")
    return cfg


def _charge_biocrate(user: User, cfg: dict, times: int) -> None:
    gold, dna = cfg["gold"] * times, cfg["dna"] * times
    if user.coins < gold:
        raise InsufficientGoldError(
            f"طلا کافی نداری! این خرید <b>{gold:,}</b> طلا می‌خواد (الان {user.coins:,} داری).",
            need=gold, have=user.coins,
        )
    if user.dna_fragments < dna:
        raise GameError(
            f"{dna} DNA لازمه (الان {user.dna_fragments} داری). "
            "DNA از شکار، دخمه، آزمایشگاه DNA و پاداش آفلاین به‌دست می‌آد."
        )
    user.coins -= gold
    user.dna_fragments -= dna
    user.save(update_fields=["coins", "dna_fragments"])


@transaction.atomic
def open_biocrate(user: User, tier: str = "basic") -> dict:
    cfg = _biocrate_cfg(tier)
    _charge_biocrate(user, cfg, 1)
    return _biocrate_roll_once(user, cfg, tier)


@transaction.atomic
def open_biocrate_bulk(user: User, tier: str = "basic") -> dict:
    """Pay for BULK_PAY boxes, open BULK_OPEN (one free). Returns an aggregate."""
    cfg = _biocrate_cfg(tier)
    _charge_biocrate(user, cfg, BULK_PAY)
    rolls = [_biocrate_roll_once(user, cfg, tier) for _ in range(BULK_OPEN)]
    return _summarise_rolls(rolls, tier, paid=BULK_PAY, opened=BULK_OPEN)


def _summarise_rolls(rolls: list[dict], tier: str, paid: int, opened: int) -> dict:
    """Fold a bunch of box outcomes into counts for a compact 'you opened 11' screen.
    `best` is the single highest-rarity drop, to headline the reveal."""
    order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    creatures = [r for r in rolls if r["kind"] == "creature"]
    items = [r for r in rolls if r["kind"] == "equipment"]
    by_rarity: dict[str, int] = {}
    for r in rolls:
        by_rarity[r["rarity"]] = by_rarity.get(r["rarity"], 0) + 1
    best = max(rolls, key=lambda r: order.get(r["rarity"], 0))
    return {
        "bulk": True,
        "tier": tier,
        "paid": paid,
        "opened": opened,
        "rolls": rolls,
        "creatures": creatures,
        "items": items,
        "by_rarity": by_rarity,
        "best": best,
    }


def _diamond_box_cfg(tier: str) -> dict:
    if tier not in constants.DIAMOND_BOX_TIERS:
        raise GameError("این نوع جعبه‌ی الماسی وجود نداره.")
    return constants.DIAMOND_BOX_TIERS[tier]


def _charge_diamond_box(user: User, cfg: dict, times: int) -> None:
    cost = cfg["cost_diamonds"] * times
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! این خرید {cost} الماس هزینه داره (الان {user.diamonds} داری).")
    user.diamonds -= cost
    user.save(update_fields=["diamonds"])


def _diamond_box_roll_once(user: User, cfg: dict, tier: str) -> dict:
    rarity = roll_rarity(cfg["weights"])
    creature = _roll_creature(user, rarity)
    return {"kind": "creature", "rarity": rarity, "creature": creature, "tier": tier}


@transaction.atomic
def open_diamond_box(user: User, tier: str) -> dict:
    """Diamond boxes always yield a creature (never equipment) — this is the "open
    a new monster with diamonds" path the gold Bio-Crate doesn't guarantee."""
    cfg = _diamond_box_cfg(tier)
    _charge_diamond_box(user, cfg, 1)
    return _diamond_box_roll_once(user, cfg, tier)


@transaction.atomic
def open_diamond_box_bulk(user: User, tier: str) -> dict:
    """Pay for BULK_PAY diamond boxes, open BULK_OPEN (one free)."""
    cfg = _diamond_box_cfg(tier)
    _charge_diamond_box(user, cfg, BULK_PAY)
    rolls = [_diamond_box_roll_once(user, cfg, tier) for _ in range(BULK_OPEN)]
    return _summarise_rolls(rolls, tier, paid=BULK_PAY, opened=BULK_OPEN)
