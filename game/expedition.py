"""Group Expeditions (اعزام کاروان و مأموریت‌های تیمی در گروه).

Players initiate or join team expeditions (2-4 members) in groups with «اعزام».
1 global daily limit per player across all groups.
"""

from __future__ import annotations

import datetime
import random
from django.db import transaction
from django.utils import timezone

from bio_lab.models import GroupExpedition, User
from bio_lab.repository import display_name
from game.creature import GameError
from game.emoji import get_emoji

EXPEDITION_DESTINATIONS = [
    {"name": "دره استخوان‌های اژدها", "gold": (3000, 6000), "dna": (100, 250), "diamonds": (5, 15)},
    {"name": "خرابه‌های شهر باستانی", "gold": (4000, 8000), "dna": (80, 200), "diamonds": (10, 20)},
    {"name": "غار بلورهای بنفش", "gold": (2500, 5000), "dna": (150, 300), "diamonds": (15, 30)},
]


def can_join_expedition(user: User) -> tuple[bool, str]:
    """Check if user has used their daily expedition quota (1 per day)."""
    if user.last_expedition_at is not None:
        user_local_date = timezone.localdate(user.last_expedition_at)
        today_local_date = timezone.localdate()
        if user_local_date == today_local_date:
            return False, "⏳ هر کاربر روزی یک‌بار می‌تواند در اعزام کاروان شرکت کند.\n(سهمیه امروز شما تمام شده است؛ فردا دوباره امتحان کنید.)"
    return True, ""


@transaction.atomic
def start_expedition_recruitment(creator: User, group_id: int, group_title: str) -> GroupExpedition:
    """Start a new expedition team recruitment in group."""
    creator = User.objects.select_for_update().get(id=creator.id)
    ok, msg = can_join_expedition(creator)
    if not ok:
        raise GameError(msg)

    # Check if user already is in an active pending expedition anywhere
    pending = GroupExpedition.objects.filter(
        members=creator,
        status="recruiting",
        expires_at__gt=timezone.now(),
    ).first()
    if pending:
        raise GameError("⚠️ شما در حال حاضر در یک کاروان در حال عضوگیری حضور دارید!")

    # Check if there is already an active recruitment in this group
    existing = GroupExpedition.objects.filter(
        group_id=group_id,
        status="recruiting",
        expires_at__gt=timezone.now(),
    ).first()
    if existing:
        raise GameError("⚠️ در حال حاضر یک کاروان در حال عضوگیری در این گروه است!")

    dest = random.choice(EXPEDITION_DESTINATIONS)
    exp = GroupExpedition.objects.create(
        group_id=group_id,
        group_title=group_title or f"Group {group_id}",
        creator=creator,
        status="recruiting",
        target_name=dest["name"],
        difficulty="normal",
        expires_at=timezone.now() + datetime.timedelta(minutes=5),
    )
    exp.members.add(creator)
    return exp


@transaction.atomic
def join_expedition(user: User, expedition_id: int) -> GroupExpedition:
    """Join an open expedition team."""
    user = User.objects.select_for_update().get(id=user.id)
    ok, msg = can_join_expedition(user)
    if not ok:
        raise GameError(msg)

    exp = GroupExpedition.objects.select_for_update().filter(id=expedition_id, status="recruiting").first()
    if exp is None or exp.expires_at <= timezone.now():
        raise GameError("این کاروان منقضی یا بسته شده است.")

    if exp.members.filter(id=user.id).exists():
        raise GameError("شما قبلاً به این کاروان ملحق شده‌اید!")

    pending = GroupExpedition.objects.filter(
        members=user,
        status="recruiting",
        expires_at__gt=timezone.now(),
    ).exclude(id=expedition_id).first()
    if pending:
        raise GameError("⚠️ شما در حال حاضر در یک کاروان فعال دیگر عضو هستید!")

    if exp.members.count() >= 4:
        raise GameError("ظرفیت کاروان تکمیل است (حداکثر ۴ نفر)!")

    exp.members.add(user)
    return exp


@transaction.atomic
def launch_expedition(user: User, expedition_id: int) -> dict:
    """Launch the expedition, resolve events, and distribute rewards."""
    exp = GroupExpedition.objects.select_for_update().filter(id=expedition_id, status="recruiting").first()
    if exp is None:
        raise GameError("این کاروان پیدا نشد یا قبلاً راهی شده است.")

    if exp.creator_id != user.id:
        raise GameError("فقط سرپرست کاروان (سازنده) می‌تواند دستور حرکت را صادر کند.")

    members = list(exp.members.select_for_update().all())
    if len(members) < 2:
        raise GameError("برای حرکت کاروان حداقل ۲ نفر باید در تیم حضور داشته باشند.")

    dest = next((d for d in EXPEDITION_DESTINATIONS if d["name"] == exp.target_name), EXPEDITION_DESTINATIONS[0])

    from bio_lab.repository import get_active_creature
    from game.creature import creature_power

    total_power = 0
    for m in members:
        c = get_active_creature(m)
        if c:
            total_power += max(50, creature_power(c))
        else:
            total_power += 100
    avg_power = max(100, total_power / len(members))
    power_mult = max(1.0, avg_power / 400.0)
    dna_mult = max(1.0, 1.0 + (avg_power / 800.0))

    gold_pool = round(random.randint(*dest["gold"]) * power_mult) * len(members)
    dna_pool = round(random.randint(*dest["dna"]) * dna_mult) * len(members)
    diamond_pool = random.randint(*dest["diamonds"]) * len(members)

    per_gold = gold_pool // len(members)
    per_dna = dna_pool // len(members)
    per_diamond = diamond_pool // len(members)

    now = timezone.now()
    for m in members:
        m.coins += per_gold
        m.dna_fragments += per_dna
        m.diamonds += per_diamond
        m.last_expedition_at = now
        m.save(update_fields=["coins", "dna_fragments", "diamonds", "last_expedition_at"])

    exp.status = "completed"
    exp.total_gold = gold_pool
    exp.total_dna = dna_pool
    exp.total_diamonds = diamond_pool
    exp.save(update_fields=["status", "total_gold", "total_dna", "total_diamonds"])

    return {
        "expedition": exp,
        "destination": dest["name"],
        "members": members,
        "per_gold": per_gold,
        "per_dna": per_dna,
        "per_diamond": per_diamond,
    }
