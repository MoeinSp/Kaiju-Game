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
# a GLOBAL per-player cooldown on WINNING a drop, so someone who joined the bot to
# 30 groups can't sweep a drop in each — matches the ~per-group spawn cadence
CLAIM_COOLDOWN_MINUTES = 10

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
                # gem jackpots are gone from groups — the diamonds are replaced by doubled gold
                "btn": "🌟 شانستو امتحان کن!", "res": {"coins": (600, 1200)}, "weight": 1},
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


# The diamond vein pays a flat random amount, INDEPENDENT of the claimer's power —
# diamonds are the premium currency, so a strong player shouldn't out-mine a weak one
# on the vein (it already has a per-hour + daily cap). Everything else DOES scale with
# power, and strongly, so a powerful kaiju is a real edge on gold/DNA drops.
VEIN_DIAMONDS_MIN = 10
VEIN_DIAMONDS_MAX = 30
# power's weight in the (non-vein) reward multiplier — doubled so a strong player earns
# markedly more gold/DNA from drops than a weak one.
DROP_POWER_FACTOR = 0.0030


def reward_for(user: User, kind: str) -> dict:
    """Random reward for `kind`. The vein is a flat, power-independent diamond roll;
    every other drop scales with the claimer's lab level and (heavily) creature power."""
    if kind == "vein":
        return {"diamonds": random.randint(VEIN_DIAMONDS_MIN, VEIN_DIAMONDS_MAX)}
    cfg = DROP_KINDS[kind]
    scale = 1 + lab.lab_level(user) * 0.06 + _active_power(user) * DROP_POWER_FACTOR
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
    # global anti-multi-group cooldown: on cooldown → can't win, drop stays open
    now = timezone.now()
    if user.drop_claim_ready_at is not None and user.drop_claim_ready_at > now:
        wait = int((user.drop_claim_ready_at - now).total_seconds())
        return {"status": "cooldown", "seconds_left": wait}
    # diamond veins get a stricter gate on top: a daily cap and a 1-hour cooldown,
    # so diamonds can't be swept across many groups. Blocked → drop stays open for
    # someone else, exactly like the general cooldown.
    is_vein = drop.kind == "vein"
    if is_vein:
        from game.daily import get_daily_count, record_action

        if get_daily_count(user, "diamond_vein") >= constants.DIAMOND_VEIN_DAILY_CAP:
            return {"status": "vein_limit", "cap": constants.DIAMOND_VEIN_DAILY_CAP}
        if user.vein_claim_ready_at is not None and user.vein_claim_ready_at > now:
            wait = int((user.vein_claim_ready_at - now).total_seconds())
            return {"status": "vein_cooldown", "seconds_left": wait}
    # the energy capsule is a free full-energy refill — cap it at ONCE PER DAY per
    # player (across every group) so it isn't swept for unlimited energy
    is_capsule = drop.kind == "capsule"
    if is_capsule:
        from game.daily import get_daily_count

        if get_daily_count(user, "energy_capsule") >= 1:
            return {"status": "capsule_limit"}
    reward = reward_for(user, drop.kind)
    # grant
    fields = ["drop_claim_ready_at"]
    user.drop_claim_ready_at = now + datetime.timedelta(minutes=CLAIM_COOLDOWN_MINUTES)
    if is_vein:
        user.vein_claim_ready_at = now + datetime.timedelta(minutes=constants.DIAMOND_VEIN_COOLDOWN_MINUTES)
        fields.append("vein_claim_ready_at")
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
    if reward.get("coins") or reward.get("dna") or reward.get("diamonds"):
        from game.ledger import record_gain

        record_gain(user, "drop", coins=reward.get("coins", 0),
                    dna=reward.get("dna", 0), diamonds=reward.get("diamonds", 0))
    # count this vein toward today's cap AFTER the grant succeeds (same atomic txn)
    if is_vein:
        record_action(user, "diamond_vein")
    if is_capsule:
        from game.daily import record_action as _record_action

        _record_action(user, "energy_capsule")

    drop.claimed_by = user
    drop.claimed_at = timezone.now()
    drop.reward_json = json.dumps(reward)
    drop.save(update_fields=["claimed_by", "claimed_at", "reward_json"])
    return {"status": "won", "winner": display_name(user), "reward": reward, "kind": drop.kind,
            "drop_id": drop.id, "group_id": drop.group_id, "message_id": drop.message_id}


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


# how long a drop's message stays before it's deleted to keep the group tidy:
# a lapsed/unclaimed one goes quickly (its "time's up" note is noise); a WON one
# lingers longer since "X won Y" is a nice moment.
DELETE_AFTER_EXPIRE_SECONDS = 60
DELETE_AFTER_WIN_SECONDS = 600  # 10 minutes


def delete_row(drop_id: int) -> None:
    GroupDrop.objects.filter(id=drop_id).delete()


def delete_due() -> list[dict]:
    """Fallback sweep (survives a bot restart): drops whose message is due for
    removal — unclaimed ones a minute after they lapsed, won ones ten minutes
    after the win. Returns their messages to delete and removes the rows. Precise
    timing is normally handled by scheduled jobs; this catches any a restart lost."""
    from django.db.models import Q

    now = timezone.now()
    expire_cutoff = now - datetime.timedelta(seconds=DELETE_AFTER_EXPIRE_SECONDS)
    win_cutoff = now - datetime.timedelta(seconds=DELETE_AFTER_WIN_SECONDS)
    rows = GroupDrop.objects.filter(message_id__isnull=False).filter(
        Q(claimed_by__isnull=True, expired_notified=True, expires_at__lte=expire_cutoff)
        | Q(claimed_by__isnull=False, claimed_at__lte=win_cutoff)
    )
    out = [{"id": d.id, "group_id": d.group_id, "message_id": d.message_id} for d in rows]
    if out:
        GroupDrop.objects.filter(id__in=[d["id"] for d in out]).delete()
    return out
