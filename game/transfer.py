"""Player-to-player trading of creatures and equipment (group «انتقال …» words).

Rules (see constants.py):
* the RECEIVER pays diamonds, scaled by what they're getting;
* a 1-day cooldown applies to BOTH sides, separately for creatures and gear;
* the receiver must have progressed far enough (building levels) to hold it, which
  is the real gate against a fresh fake account instantly hoarding rare items.
Gold transfers stay in game/… «انتقال طلا» — this module is only creatures/gear.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, Equipment, User
from game import constants
from game.buildings import building_level
from game.creature import GameError


class TransferFundsError(GameError):
    """Raised when the receiver can't afford a transfer — carries the numbers so the
    handler can render a nice message with the diamond emoji + a price-guide button."""

    def __init__(self, cost: int, have: int, kind: str):
        self.cost, self.have, self.kind = cost, have, kind
        super().__init__(f"گیرنده {cost} الماس لازم داره ولی {have} تا داره.")


def _fa(n: int) -> str:
    """Latin → Persian digits, so numbers sit cleanly in the right-to-left text."""
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def creature_prices_text() -> str:
    """Full diamond-cost breakdown for creature transfers, by rarity then star. Rarest
    first (the interesting end), each rarity on its own line, so it's easy to scan."""
    from game.emoji import get_emoji

    d = get_emoji("diamond")
    lines = [f"{d} <b>هزینه‌ی انتقال هیولا</b>  <i>(الماس — گیرنده می‌ده)</i>", ""]
    for rarity in reversed(constants.RARITY_ORDER):
        stars = " · ".join(
            f"{_fa(star)}★ <b>{_fa(constants.creature_transfer_cost(star, rarity))}</b>"
            for star in sorted(constants.CREATURE_TRANSFER_STAR_COST)
        )
        lines.append(f"{constants.RARITY_LABELS[rarity]}\n<blockquote>{stars}</blockquote>")
    return "\n".join(lines)


def equip_prices_text() -> str:
    from game.emoji import get_emoji

    d = get_emoji("diamond")
    lines = [
        f"{d} <b>هزینه‌ی انتقال تجهیزات</b>  <i>(الماس — گیرنده می‌ده)</i>",
        "<i>قیمت‌های زیر برای آیتم مکس‌ان؛ هرچی سطح آیتم پایین‌تر باشه ارزون‌تره "
        "(۲۰٪ تا ۸۰٪ قیمت مکس).</i>",
        "",
    ]
    for rarity in reversed(constants.RARITY_ORDER):
        lines.append(f"{constants.RARITY_LABELS[rarity]} — <b>{_fa(constants.equip_transfer_cost(rarity))}</b>")
    return "\n".join(lines)


def _fmt_wait(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"


def _check_cooldown(user: User, field: str, who: str) -> None:
    ready = getattr(user, field)
    if ready is not None and ready > timezone.now():
        wait = int((ready - timezone.now()).total_seconds())
        raise GameError(
            f"⏳ هر {constants.TRANSFER_COOLDOWN_HOURS} ساعت فقط یک‌بار می‌شه انتقال داد یا گرفت. "
            f"{who} به‌تازگی یه انتقال داشته — {_fmt_wait(wait)} دیگه می‌تونه دوباره."
        )


def _set_cooldown(users: list[User], field: str) -> None:
    until = timezone.now() + datetime.timedelta(hours=constants.TRANSFER_COOLDOWN_HOURS)
    for u in users:
        setattr(u, field, until)


def _check_equip_blacksmith(receiver: User, item: Equipment) -> None:
    """The receiver's forge must be able to support the item's level (blacksmith
    caps equipment at level×5), so nobody suddenly receives gear far beyond what
    their own base could produce. Raises GameError if their forge is too low."""
    bs_req = constants.equip_transfer_blacksmith_req(item.level)
    bs = building_level(receiver, "blacksmith")
    if bs < bs_req:
        raise GameError(
            f"گیرنده برای گرفتن تجهیزاتِ +{item.level} باید آهنگری سطح {bs_req} داشته باشه "
            f"(الان {bs}). اول آهنگریش رو ارتقا بده."
        )


def _creature_reqs(star_level: int) -> dict:
    return constants.CREATURE_TRANSFER_REQS.get(
        star_level, constants.CREATURE_TRANSFER_REQS[max(constants.CREATURE_TRANSFER_REQS)]
    )


def preview_creature_transfer(sender: User, receiver: User, creature_id: int) -> dict:
    """Validate a creature transfer WITHOUT moving anything — used to show the receiver
    a confirm prompt before their diamonds are spent. Raises GameError on any problem.
    Returns {creature, cost}."""
    if sender.id == receiver.id:
        raise GameError("نمی‌تونی به خودت منتقل کنی.")
    _check_cooldown(sender, "kaiju_transfer_ready_at", "فرستنده")
    _check_cooldown(receiver, "kaiju_transfer_ready_at", "گیرنده")
    creature = Creature.objects.filter(id=creature_id, owner=sender).first()
    if creature is None:
        raise GameError("همچین هیولایی با این کد توی کلکسیونت نیست.")
    if creature.is_active:
        raise GameError("هیولای فعال رو نمی‌شه منتقل کرد — اول یکی دیگه رو فعال کن.")
    from game.workers import creature_status

    status = creature_status(sender, creature)
    if status is not None:
        raise GameError(f"«{creature.name}» الان مشغوله ({status}) — اول آزادش کن.")
    reqs = _creature_reqs(creature.star_level)
    mh, fl = building_level(receiver, "main_hall"), building_level(receiver, "fusion_lab")
    if mh < reqs["main_hall"] or fl < reqs["fusion_lab"]:
        need = f"تالار مِهر سطح {reqs['main_hall']}"
        if reqs["fusion_lab"]:
            need += f" و تالار ادغام سطح {reqs['fusion_lab']}"
        raise GameError(
            f"گیرنده برای گرفتن یه هیولای {creature.star_level}⭐ باید {need} داشته باشه "
            f"(الان: مِهر {mh}، ادغام {fl})."
        )
    cost = constants.creature_transfer_cost(creature.star_level, creature.rarity)
    if receiver.diamonds < cost:
        raise TransferFundsError(cost, receiver.diamonds, "creature")
    return {"creature": creature, "cost": cost}


def preview_equip_transfer(sender: User, receiver: User, equip_id: int) -> dict:
    """Validate an equipment transfer without moving anything. Returns {item, cost}."""
    if sender.id == receiver.id:
        raise GameError("نمی‌تونی به خودت منتقل کنی.")
    _check_cooldown(sender, "equip_transfer_ready_at", "فرستنده")
    _check_cooldown(receiver, "equip_transfer_ready_at", "گیرنده")
    item = Equipment.objects.filter(id=equip_id, owner=sender).first()
    if item is None:
        raise GameError("همچین تجهیزاتی با این کد توی انبارت نیست.")
    mh_req = constants.EQUIP_TRANSFER_MAIN_HALL_REQ.get(item.rarity, 1)
    mh = building_level(receiver, "main_hall")
    if mh < mh_req:
        raise GameError(
            f"گیرنده برای گرفتن تجهیزاتِ {constants.RARITY_LABELS[item.rarity]} باید تالار مِهر سطح "
            f"{mh_req} داشته باشه (الان {mh})."
        )
    _check_equip_blacksmith(receiver, item)
    cost = constants.equip_transfer_cost(item.rarity, item.level)
    if receiver.diamonds < cost:
        raise TransferFundsError(cost, receiver.diamonds, "equip")
    return {"item": item, "cost": cost}


@transaction.atomic
def transfer_creature(sender: User, receiver: User, creature_id: int, price: int = 0) -> dict:
    """Give one of the sender's creatures to the receiver. The receiver pays the
    diamond fee (a sink) plus, if the seller set one, `price` gold that goes TO the
    seller. Enforces the shared cooldown, the receiver's building prerequisites, and
    that the creature isn't the sender's active/busy one.

    The creature leaves the sender COMPLETELY: any gear it wears is returned to the
    sender's inventory first, so the transferred creature carries no trace of the
    sender's data (no equipped items, not active, not a worker)."""
    price = max(0, int(price))
    sender = User.objects.select_for_update().get(id=sender.id)
    receiver = User.objects.select_for_update().get(id=receiver.id)
    if sender.id == receiver.id:
        raise GameError("نمی‌تونی به خودت منتقل کنی.")
    _check_cooldown(sender, "kaiju_transfer_ready_at", "فرستنده")
    _check_cooldown(receiver, "kaiju_transfer_ready_at", "گیرنده")

    creature = Creature.objects.filter(id=creature_id, owner=sender).first()
    if creature is None:
        raise GameError("همچین هیولایی با این کد توی کلکسیونت نیست.")
    if creature.is_active:
        raise GameError("هیولای فعال رو نمی‌شه منتقل کرد — اول یکی دیگه رو فعال کن.")
    from game.workers import creature_status

    status = creature_status(sender, creature)
    if status is not None:
        raise GameError(f"«{creature.name}» الان مشغوله ({status}) — اول آزادش کن.")

    # receiver must have a mature enough base for this star
    reqs = constants.CREATURE_TRANSFER_REQS.get(
        creature.star_level, constants.CREATURE_TRANSFER_REQS[max(constants.CREATURE_TRANSFER_REQS)]
    )
    mh = building_level(receiver, "main_hall")
    fl = building_level(receiver, "fusion_lab")
    if mh < reqs["main_hall"] or fl < reqs["fusion_lab"]:
        need = f"تالار مِهر سطح {reqs['main_hall']}"
        if reqs["fusion_lab"]:
            need += f" و تالار ادغام سطح {reqs['fusion_lab']}"
        raise GameError(
            f"گیرنده برای گرفتن یه هیولای {creature.star_level}⭐ باید {need} داشته باشه "
            f"(الان: مِهر {mh}، ادغام {fl})."
        )

    cost = constants.creature_transfer_cost(creature.star_level, creature.rarity)
    if receiver.diamonds < cost:
        raise TransferFundsError(cost, receiver.diamonds, "creature")
    if price > 0 and receiver.coins < price:
        raise GameError(f"گیرنده {price:,} طلا برای این قیمت لازم داره ولی {receiver.coins:,} داره.")

    # strip the creature clean: return every worn item to the SENDER's armory so the
    # creature transfers naked and leaves no equipped-gear trace on the sender.
    Equipment.objects.filter(equipped_on=creature).update(equipped_on=None)

    receiver.diamonds -= cost
    if price > 0:
        receiver.coins -= price
        sender.coins += price
    creature.owner = receiver
    creature.is_active = False
    creature.save(update_fields=["owner", "is_active"])

    _set_cooldown([sender, receiver], "kaiju_transfer_ready_at")
    sender.save(update_fields=["kaiju_transfer_ready_at", "coins"])
    receiver.save(update_fields=["diamonds", "coins", "kaiju_transfer_ready_at"])
    return {"creature": creature, "cost": cost, "price": price}


@transaction.atomic
def transfer_equipment(sender: User, receiver: User, equip_id: int, price: int = 0) -> dict:
    """Give one of the sender's equipment pieces to the receiver. Receiver pays the
    diamond fee (sink) plus, if set, `price` gold to the seller. Auto-unequips it
    from the sender's creature first, so nothing of the sender's remains on it."""
    price = max(0, int(price))
    sender = User.objects.select_for_update().get(id=sender.id)
    receiver = User.objects.select_for_update().get(id=receiver.id)
    if sender.id == receiver.id:
        raise GameError("نمی‌تونی به خودت منتقل کنی.")
    _check_cooldown(sender, "equip_transfer_ready_at", "فرستنده")
    _check_cooldown(receiver, "equip_transfer_ready_at", "گیرنده")

    item = Equipment.objects.filter(id=equip_id, owner=sender).first()
    if item is None:
        raise GameError("همچین تجهیزاتی با این کد توی انبارت نیست.")

    mh_req = constants.EQUIP_TRANSFER_MAIN_HALL_REQ.get(item.rarity, 1)
    mh = building_level(receiver, "main_hall")
    if mh < mh_req:
        raise GameError(
            f"گیرنده برای گرفتن تجهیزاتِ {constants.RARITY_LABELS[item.rarity]} باید تالار مِهر سطح "
            f"{mh_req} داشته باشه (الان {mh})."
        )
    _check_equip_blacksmith(receiver, item)

    cost = constants.equip_transfer_cost(item.rarity, item.level)
    if receiver.diamonds < cost:
        raise TransferFundsError(cost, receiver.diamonds, "equip")
    if price > 0 and receiver.coins < price:
        raise GameError(f"گیرنده {price:,} طلا برای این قیمت لازم داره ولی {receiver.coins:,} داره.")

    receiver.diamonds -= cost
    if price > 0:
        receiver.coins -= price
        sender.coins += price
    item.owner = receiver
    item.equipped_on = None  # can't stay equipped on the sender's creature
    item.save(update_fields=["owner", "equipped_on"])

    _set_cooldown([sender, receiver], "equip_transfer_ready_at")
    sender.save(update_fields=["equip_transfer_ready_at", "coins"])
    receiver.save(update_fields=["diamonds", "coins", "equip_transfer_ready_at"])
    return {"item": item, "cost": cost, "price": price}
