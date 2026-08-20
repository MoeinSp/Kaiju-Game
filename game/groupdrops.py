"""Flash reward drops in group chats — a fast, competitive engagement hook.

Every so often the bot drops a themed reward into an active group with a claim
button; the FIRST member to tap it wins a random reward scaled to their own lab
level and creature power. Because claiming is a callback query (button tap), it
always reaches the bot regardless of group privacy mode — so this works in any
group the bot is in, unlike anything that needs to read chat messages.

Cadence and settlement are lazy, driven by the same JobQueue as notifications
(bot/handlers/groupdrops.py): each tick may spawn a drop per eligible group
(rate-limited), and claiming is a single atomic first-writer-wins update.
"""

from __future__ import annotations

import datetime
import json
import random

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from bio_lab.models import Group, GroupDrop, GroupMembership, User
from game import constants, lab

# how often drops appear: checked every job tick; a group won't get a new one
# within MIN_GAP of its last, and each open drop self-expires after EXPIRE.
SPAWN_CHANCE = 0.4          # per eligible group, per tick
MIN_GAP_MINUTES = 10        # ≤ ~6/hour per group
EXPIRE_MINUTES = 12         # unclaimed drops lapse after this
MIN_GROUP_MEMBERS = 2       # only real, active groups

DROP_KINDS = {
    "chest":   {"emoji": "🎁", "title": "صندوقچه‌ی گنج", "flavor": "یه صندوقچه‌ی گنج وسط گروه افتاد!",
                "btn": "🎁 بازش کن!", "res": {"coins": (120, 260), "dna": (0, 6)}, "weight": 5},
    "ambush":  {"emoji": "⚔️", "title": "هیولای وحشی", "flavor": "یه هیولای وحشی ظاهر شد! سریع باش!",
                "btn": "⚔️ حمله کن!", "res": {"coins": (90, 200), "dna": (2, 8)}, "weight": 5},
    "vein":    {"emoji": "💎", "title": "رگه‌ی الماس", "flavor": "یه رگه‌ی الماس درخشید!",
                "btn": "💎 برش دار!", "res": {"diamonds": (2, 7)}, "weight": 3},
    "egg":     {"emoji": "🥚", "title": "تخم رمزآلود", "flavor": "یه تخم رمزآلود از آسمون افتاد!",
                "btn": "🥚 بردار!", "res": {"dna": (10, 26), "coins": (60, 140)}, "weight": 4},
    "capsule": {"emoji": "⚡", "title": "کپسول انرژی", "flavor": "یه کپسول انرژی پیدا شد!",
                "btn": "⚡ بگیرش!", "res": {"energy": "full", "coins": (50, 120)}, "weight": 3},
    "jackpot": {"emoji": "🌟", "title": "جک‌پات نادر", "flavor": "🌟 یه جک‌پات نادر ظاهر شد!!",
                "btn": "🌟 شانستو امتحان کن!", "res": {"coins": (300, 600), "diamonds": (3, 10)}, "weight": 1},
}


def _pick_kind() -> str:
    keys = list(DROP_KINDS)
    return random.choices(keys, weights=[DROP_KINDS[k]["weight"] for k in keys], k=1)[0]


def _active_power(user: User) -> int:
    from bio_lab.repository import get_active_creature
    from game.creature import effective_stats
    from game.equipment import get_equipped_items

    c = get_active_creature(user)
    if c is None:
        return 0
    s = effective_stats(c, get_equipped_items(c))
    return round(s["hp"] + s["atk"] + s["def"] + s["spd"])


def reward_for(user: User, kind: str) -> dict:
    """Random reward for `kind`, scaled by the claimer's level + power."""
    cfg = DROP_KINDS[kind]
    scale = 1 + lab.lab_level(user) * 0.06 + _active_power(user) * 0.0015
    out: dict = {}
    for res, spec in cfg["res"].items():
        if res == "energy":
            out["energy"] = "full"
            continue
        lo, hi = spec
        val = round(random.randint(lo, hi) * scale)
        if val > 0:
            out[res] = val
    return out


def reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("energy") == "full":
        parts.append("انرژی کامل")
    return " + ".join(parts) or "—"


def due_spawns() -> list[dict]:
    """Decide which groups get a drop this tick and create the (open) rows.
    Returns [{id, group_id, kind, emoji, title, flavor, btn}] for the sender."""
    now = timezone.now()
    gap = now - datetime.timedelta(minutes=MIN_GAP_MINUTES)
    eligible = (
        Group.objects.annotate(n=Count("groupmembership"))
        .filter(n__gte=MIN_GROUP_MEMBERS)
    )
    out = []
    for group in eligible:
        recent = GroupDrop.objects.filter(group=group, created_at__gte=gap).exists()
        open_now = GroupDrop.objects.filter(
            group=group, claimed_by__isnull=True, expires_at__gt=now
        ).exists()
        if recent or open_now or random.random() > SPAWN_CHANCE:
            continue
        kind = _pick_kind()
        drop = GroupDrop.objects.create(
            group=group, kind=kind, expires_at=now + datetime.timedelta(minutes=EXPIRE_MINUTES)
        )
        cfg = DROP_KINDS[kind]
        out.append({"id": drop.id, "group_id": group.id, "kind": kind,
                    "emoji": cfg["emoji"], "title": cfg["title"], "flavor": cfg["flavor"], "btn": cfg["btn"]})
    return out


def set_message_id(drop_id: int, message_id: int) -> None:
    GroupDrop.objects.filter(id=drop_id).update(message_id=message_id)


@transaction.atomic
def claim(drop_id: int, tg_user) -> dict:
    """First-writer-wins claim. Returns a result dict:
    {status: 'won'|'taken'|'expired'|'gone', ...}."""
    from bio_lab.repository import display_name, get_or_create_user

    drop = GroupDrop.objects.select_for_update().filter(id=drop_id).first()
    if drop is None:
        return {"status": "gone"}
    if drop.claimed_by_id is not None:
        return {"status": "taken", "winner": display_name(drop.claimed_by)}
    if timezone.now() >= drop.expires_at:
        return {"status": "expired"}

    user, _ = get_or_create_user(tg_user)
    reward = reward_for(user, drop.kind)
    # grant
    fields = []
    if reward.get("coins"):
        user.coins += reward["coins"]; fields.append("coins")
    if reward.get("dna"):
        user.dna_fragments += reward["dna"]; fields.append("dna_fragments")
    if reward.get("diamonds"):
        user.diamonds += reward["diamonds"]; fields.append("diamonds")
    if reward.get("energy") == "full":
        user.energy = constants.MAX_ENERGY
        user.energy_updated_at = timezone.now()
        fields += ["energy", "energy_updated_at"]
    if fields:
        user.save(update_fields=list(set(fields)))

    drop.claimed_by = user
    drop.reward_json = json.dumps(reward)
    drop.save(update_fields=["claimed_by", "reward_json"])
    return {"status": "won", "winner": display_name(user), "reward": reward, "kind": drop.kind}


def expire_due() -> list[dict]:
    """Open drops that just lapsed, so the sender can edit their message once.
    Returns [{id, group_id, message_id, kind}] and marks them notified."""
    now = timezone.now()
    rows = GroupDrop.objects.filter(
        claimed_by__isnull=True, expired_notified=False, expires_at__lte=now, message_id__isnull=False
    )
    out = [{"id": d.id, "group_id": d.group_id, "message_id": d.message_id, "kind": d.kind} for d in rows]
    if out:
        GroupDrop.objects.filter(id__in=[d["id"] for d in out]).update(expired_notified=True)
    return out


# a lapsed-and-edited drop's message is deleted this long after it expired, so the
# "time's up" note lingers only briefly instead of cluttering the group
DELETE_AFTER_EXPIRE_SECONDS = 60


def delete_row(drop_id: int) -> None:
    GroupDrop.objects.filter(id=drop_id).delete()


def delete_due() -> list[dict]:
    """Fallback sweep (survives a bot restart): expired, already-noted, unclaimed
    drops whose grace minute is up — returns their messages to delete and removes
    the rows. Precise 1-minute deletion is normally handled by a scheduled job;
    this just catches any the restart dropped."""
    cutoff = timezone.now() - datetime.timedelta(seconds=DELETE_AFTER_EXPIRE_SECONDS)
    rows = GroupDrop.objects.filter(
        claimed_by__isnull=True, expired_notified=True, message_id__isnull=False, expires_at__lte=cutoff
    )
    out = [{"id": d.id, "group_id": d.group_id, "message_id": d.message_id} for d in rows]
    if out:
        GroupDrop.objects.filter(id__in=[d["id"] for d in out]).delete()
    return out
