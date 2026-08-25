import random

from django.db import transaction

from bio_lab.models import Creature, User
from game import constants, lab
from game.buildings import is_built, star_cap
from game.creature import GameError
from game.equipment import get_equipped_items

FUSION_BUILDING = "fusion_lab"


def assert_fusion_available(user: User) -> None:
    if not is_built(user, FUSION_BUILDING):
        raise GameError("اول باید 🔮 تالار ادغام رو از «🏗 ساختمون‌ها» بسازی.")


def ready_pairs(user: User) -> list[dict]:
    """Every (species, star) group the player owns two or more of, i.e. every pair
    they could fuse right now. Powers the fusion screen so a player is shown what
    they *can* do instead of hunting for a valid pair themselves.

    Returns [] when the lab isn't built — the caller explains that separately."""
    if not is_built(user, FUSION_BUILDING):
        return []

    cap = star_cap(user)
    # fusion identity is (name, rarity, star): two creatures only fuse if all three
    # match, so rarity is preserved up the whole 1★→5★ pyramid (16 same-rarity base
    # creatures for one 5★).
    groups: dict[tuple[str, str, int], list[Creature]] = {}
    for creature in Creature.objects.filter(owner=user, star_level__lt=cap).order_by("-level"):
        groups.setdefault((creature.name, creature.rarity, creature.star_level), []).append(creature)

    pairs = []
    for (name, rarity, star), members in groups.items():
        if len(members) < 2:
            continue
        pairs.append(
            {
                "name": name,
                "rarity": rarity,
                "star": star,
                "count": len(members),
                "parent_a": members[0],  # highest level first, so fusing keeps the best
                "parent_b": members[1],
            }
        )
    pairs.sort(key=lambda p: (-p["star"], p["name"]))
    return pairs


def fusion_partners(user: User, creature: Creature) -> list[Creature]:
    """Everything this creature can legally fuse with: same species name, same
    rarity, same star. Powers the picker UI so a player never gets offered an
    invalid pair. Empty when the fusion lab isn't built or the creature is already
    at the player's main-hall-derived star cap."""
    if not is_built(user, FUSION_BUILDING):
        return []
    if creature.star_level >= star_cap(user):
        return []
    return list(
        Creature.objects.filter(
            owner=user, name=creature.name, rarity=creature.rarity, star_level=creature.star_level
        )
        .exclude(id=creature.id)
        .order_by("-level")
    )


@transaction.atomic
def fuse(user: User, parent_a: Creature, parent_b: Creature) -> tuple[Creature, object | None]:
    """Burns both parents (gold cost, permanent deletion) and forges one creature a
    star above them. Both parents must be the SAME species at the SAME star — that
    restriction is what makes 1★→5★ a collection goal instead of a side effect of
    fusing whatever's lying around. The child keeps both parents' XP.

    Returns (child, inherited_item) — inherited_item is the Equipment moved onto the
    child if the FUSION_INHERIT_CHANCE roll hit and either parent had gear equipped,
    else None."""
    assert_fusion_available(user)
    if parent_a.owner_id != user.id or parent_b.owner_id != user.id:
        raise GameError("هر دو موجود باید مال خودت باشن.")
    if parent_a.id == parent_b.id:
        raise GameError("نمی‌تونی یه موجود رو با خودش ترکیب کنی.")
    if parent_a.name != parent_b.name:
        raise GameError("فقط دو هیولای هم‌نوع (با اسم یکسان) با هم ترکیب می‌شن.")
    if parent_a.rarity != parent_b.rarity:
        raise GameError("هر دو هیولا باید نایابیِ یکسان داشته باشن (مثلاً هر دو اساطیری).")
    if parent_a.star_level != parent_b.star_level:
        raise GameError("هر دو هیولا باید ستاره‌ی یکسان داشته باشن.")

    cap = star_cap(user)
    if parent_a.star_level >= cap:
        hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
        raise GameError(
            f"سقف ستاره‌ی فعلی تو {cap}⭐ ـه — برای بالاتر رفتن باید {hall} رو ارتقا بدی."
        )
    # rarity is now a fixed fusion-identity dimension, so the child keeps it (no
    # random tier upgrade) — that's what makes the same-name+same-rarity pyramid work
    rarity = parent_a.rarity
    cost = constants.fusion_cost(parent_a.star_level, rarity)
    if user.coins < cost:
        raise GameError(f"طلا کافی نداری! فیوژن این جفت {cost} طلا هزینه داره.")

    user.coins -= cost
    user.save(update_fields=["coins"])

    star_level = parent_a.star_level + 1  # both parents share a star, verified above

    def _inherit_stat(attr: str) -> int:
        """Best of both parents + a slice of the weaker + flat growth, so the child
        is guaranteed strictly stronger than either parent on every base stat."""
        a_val, b_val = getattr(parent_a, attr), getattr(parent_b, attr)
        blended = max(a_val, b_val) + min(a_val, b_val) * constants.FUSION_WEAK_PARENT_SHARE
        return round(blended + constants.FUSION_STAT_GROWTH[attr])

    child = Creature.objects.create(
        owner=user,
        name=parent_a.name,  # same species in, same species out — only the star climbs
        element=random.choice([parent_a.element, parent_b.element]),
        rarity=rarity,
        star_level=star_level,
        level=max(parent_a.level, parent_b.level),
        xp=parent_a.xp + parent_b.xp,
        base_hp=_inherit_stat("base_hp"),
        base_atk=_inherit_stat("base_atk"),
        base_def=_inherit_stat("base_def"),
        base_spd=_inherit_stat("base_spd"),
        # carry over the BEST body-part upgrades — the old code dropped these to 0,
        # so every gold spent upgrading fangs/armor/wings/poison was lost on fusion
        fangs_lvl=max(parent_a.fangs_lvl, parent_b.fangs_lvl),
        armor_lvl=max(parent_a.armor_lvl, parent_b.armor_lvl),
        wings_lvl=max(parent_a.wings_lvl, parent_b.wings_lvl),
        poison_lvl=max(parent_a.poison_lvl, parent_b.poison_lvl),
        is_active=True,
    )

    inherited_item = None
    if random.random() < constants.FUSION_INHERIT_CHANCE:
        parent_items = get_equipped_items(parent_a) + get_equipped_items(parent_b)
        if parent_items:
            inherited_item = random.choice(parent_items)
            inherited_item.equipped_on = child
            inherited_item.save(update_fields=["equipped_on"])

    lab.award(user, "fusion")

    parent_a.delete()
    parent_b.delete()
    # child is the new active creature — get_active_creature() assumes exactly one
    # is_active=True row per owner, so every other creature must yield the slot
    Creature.objects.filter(owner=user).exclude(id=child.id).update(is_active=False)

    return child, inherited_item
