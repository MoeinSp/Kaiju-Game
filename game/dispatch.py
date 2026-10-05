"""«🧭 مأموریت اعزامی» — send IDLE creatures away on timed missions (2 / 6 / 12 hours)
and come back later to collect the loot.

The design goals, in order:

* **Give the rest of the collection a job.** Hunts, the arena and the tower only ever use
  the active creature; everything else sat in the collection. A mission takes any idle
  creature, so breeding/keeping more than one good kaiju now pays.
* **A reason to come back.** Missions run on real-time timers (lazy — no background job
  resolves them; the reward is frozen at dispatch and paid on collect).
* **Limited, never a second hunt.** A fixed number of offers per day, a couple of slots,
  and rewards sized to a small slice of a day's hunting (see GOLD_FACTOR).

Rules:

* Every player gets OFFERS_PER_DAY offers per game day (Asia/Tehran), generated from
  (user id, day) — deterministic, so the board is stable all day with no storage. Each
  offer can be taken once.
* A mission needs ONE idle creature (game.workers.assert_free — the same one-job rule as
  mines and the cave) that meets the offer's minimum rarity. Sending the offer's
  PREFERRED element pays ELEMENT_MATCH_BONUS more — a soft requirement, never a wall.
* The reward scales with the creature's power at dispatch and is FROZEN then, so nothing
  the player does mid-mission changes it. A hidden bonus item may be rolled too.
* The creature is busy until the mission is collected or cancelled (cancel = no reward,
  the offer becomes available again).
"""

from __future__ import annotations

import datetime
import random

from django.db import IntegrityError, transaction
from django.utils import timezone

from bio_lab.models import Creature, DispatchMission, User
from bio_lab.repository import creature_name, lock_row
from game import constants
from game.creature import GameError, add_capsules, add_xp, creature_power
from game.daily import today_str
from game.emoji import get_emoji

OFFERS_PER_DAY = 5
SLOTS_BASE = 2
SLOTS_SUBSCRIBER = 3  # any active subscription adds one slot

DURATIONS = (2, 6, 12)
# the day's five offers always come as this mix (shuffled), so every day has short
# check-in missions AND one long overnight one
_DAILY_DURATION_MIX = (2, 2, 6, 6, 12)

# Gold for a gold-focused mission = creature power × this. For scale: one won «هم‌سطح»
# hunt pays ≈ 0.39 × power, so 2h ≈ 1 hunt, 6h ≈ 3 hunts, 12h ≈ 4.5 hunts (longer is less
# gold per hour — it's the low-attention option, not the efficient one). With 2 slots and
# 5 offers a day the whole board is worth roughly 10–13 hunts: a side income.
GOLD_FACTOR = {2: 0.45, 6: 1.10, 12: 1.80}
DNA_PER_GOLD = 0.03  # same gold:DNA ratio hunts use
MIN_COINS = 50

# what a mission is «about» → (gold share, DNA share) of the base
_FOCUS = {
    "gold": (1.00, 0.40),
    "dna": (0.40, 1.60),
    "mixed": (0.75, 1.00),
}

ELEMENT_MATCH_BONUS = 0.25
XP_PER_HOUR = 12

# chance of a hidden bonus item, by duration
BONUS_CHANCE = {2: 0.10, 6: 0.25, 12: 0.45}
_BONUS_DIAMONDS = {2: (1, 3), 6: (2, 6), 12: (4, 10)}
_BONUS_CAPSULE = {2: ("small", 2), 6: ("medium", 1), 12: ("medium", 2)}
_BONUS_SPEEDUP = {2: 5, 6: 15, 12: 30}

# (key, emoji, title, flavor, focus)
TEMPLATES = (
    ("ruins", "🏛", "کاوش خرابه‌های باستانی", "زیر ستون‌های شکسته هنوز گنج‌هایی جا مونده.", "gold"),
    ("mine", "⛏", "حفاری رگه‌ی گمشده", "یه رگه‌ی طلای دست‌نخورده توی دل کوه پیدا شده.", "gold"),
    ("caravan", "🐪", "اسکورت کاروان بازرگان", "بازرگان‌ها برای عبور امن از دره پول خوبی می‌دن.", "gold"),
    ("wreck", "⚓", "غواصی در لاشه‌ی کشتی", "یه کشتی غرق‌شده با صندوق‌های سالم، کف دریاچه‌ست.", "gold"),
    ("swamp", "🧪", "نمونه‌برداری از مرداب", "مرداب سمی پر از بافت‌های زنده‌ی کمیابه.", "dna"),
    ("nest", "🪺", "جست‌وجوی لانه‌ی وحشی", "توی لانه‌های رهاشده پوسته و ژن تازه پیدا می‌شه.", "dna"),
    ("fossil", "🦴", "بیرون‌کشیدن فسیل کهن", "فسیل یه غول باستانی زیر یخ‌ها مونده.", "dna"),
    ("patrol", "🛡", "گشت‌زنی مرز پایگاه", "یه دور کامل دور پایگاه؛ هر چی سر راه بود مال تو.", "mixed"),
    ("crater", "☄️", "بررسی دهانه‌ی شهاب‌سنگ", "شهاب‌سنگ تازه‌ای افتاده؛ فلز و نمونه هر دو داره.", "mixed"),
    ("forest", "🌲", "شکار آرام در جنگل مه‌آلود", "یه گشت بی‌سروصدا لای درخت‌های کهن‌سال.", "mixed"),
)
_TEMPLATE_BY_KEY = {t[0]: t for t in TEMPLATES}

# minimum rarity an offer may ask for, by duration (picked per offer from these)
_MIN_RARITY_OPTIONS = {
    2: ("common",),
    6: ("common", "rare"),
    12: ("rare", "epic"),
}


def slots(user: User) -> int:
    from game.subscription import is_subscription_active

    return SLOTS_SUBSCRIBER if is_subscription_active(user) else SLOTS_BASE


def _taken_idxs(user: User, day: str) -> set[int]:
    return set(
        DispatchMission.objects.filter(owner=user, offer_day=day).values_list("offer_idx", flat=True)
    )


def offers_for(user: User, day: str | None = None) -> list[dict]:
    """Today's board for this player. Pure function of (user id, day) plus which offers
    were already taken — the same list every time it's opened that day."""
    day = day or today_str()
    rng = random.Random(f"dispatch:{user.id}:{day}")
    durations = list(_DAILY_DURATION_MIX)
    rng.shuffle(durations)
    templates = rng.sample(TEMPLATES, k=OFFERS_PER_DAY)
    taken = _taken_idxs(user, day)
    out = []
    for idx, (hours, tpl) in enumerate(zip(durations, templates)):
        key, emoji, title, flavor, focus = tpl
        out.append({
            "idx": idx,
            "day": day,
            "key": key,
            "emoji": emoji,
            "title": title,
            "flavor": flavor,
            "focus": focus,
            "hours": hours,
            "element": rng.choice(constants.ELEMENTS),
            "min_rarity": rng.choice(_MIN_RARITY_OPTIONS[hours]),
            "taken": idx in taken,
        })
    return out


def get_offer(user: User, idx: int, day: str | None = None) -> dict:
    offers = offers_for(user, day)
    if not 0 <= idx < len(offers):
        raise GameError("این مأموریت دیگه توی فهرست امروز نیست.")
    return offers[idx]


def active_missions(user: User) -> list[DispatchMission]:
    return list(
        DispatchMission.objects.filter(owner=user, status=DispatchMission.ACTIVE)
        .select_related("creature")
        .order_by("finishes_at", "id")
    )


def is_ready(mission: DispatchMission) -> bool:
    return mission.finishes_at <= timezone.now()


def seconds_left(mission: DispatchMission) -> int:
    return max(0, int((mission.finishes_at - timezone.now()).total_seconds()))


def meets_rarity(creature: Creature, offer: dict) -> bool:
    order = constants.RARITY_ORDER
    return order.index(creature.rarity) >= order.index(offer["min_rarity"])


def eligible_creatures(user: User, offer: dict) -> list[Creature]:
    """Idle creatures that may take this offer — rarest/strongest first (the order
    game.workers.free_creatures already uses for every picker)."""
    from game.workers import free_creatures

    return [c for c in free_creatures(user) if meets_rarity(c, offer)]


def _power_of(user: User, creature: Creature) -> int:
    from game import research
    from game.equipment import get_equipped_items

    research.attach_research(user, creature)
    return creature_power(creature, get_equipped_items(creature))


def preview_reward(user: User, offer: dict, creature: Creature) -> dict:
    """The guaranteed part of the reward if `creature` takes `offer` right now (the
    hidden bonus item is rolled at dispatch and only revealed on collect)."""
    power = _power_of(user, creature)
    hours = offer["hours"]
    gold_share, dna_share = _FOCUS[offer["focus"]]
    match = creature.element == offer["element"]
    mult = 1 + (ELEMENT_MATCH_BONUS if match else 0)
    base = power * GOLD_FACTOR[hours] * mult
    return {
        "coins": max(MIN_COINS, round(base * gold_share)),
        "dna": max(1, round(base * DNA_PER_GOLD * dna_share)),
        "xp": XP_PER_HOUR * hours,
        "power": power,
        "element_match": match,
    }


def _roll_bonus(hours: int) -> dict:
    if random.random() >= BONUS_CHANCE[hours]:
        return {}
    kind = random.choices(("diamonds", "capsule", "speedup"), weights=(40, 35, 25), k=1)[0]
    if kind == "diamonds":
        return {"diamonds": random.randint(*_BONUS_DIAMONDS[hours])}
    if kind == "capsule":
        tier, count = _BONUS_CAPSULE[hours]
        return {"capsule": tier, "capsule_count": count}
    return {"speedup": _BONUS_SPEEDUP[hours]}


@transaction.atomic
def start(user: User, offer_idx: int, creature_id: int) -> DispatchMission:
    """Dispatch one idle creature on today's offer `offer_idx`."""
    from game.workers import assert_free

    lock_row(user)  # the player row is the mutex for slots + «offer taken once»
    offer = get_offer(user, offer_idx)
    if offer["taken"]:
        raise GameError("این مأموریت رو امروز قبلاً فرستادی.")
    active = DispatchMission.objects.filter(owner=user, status=DispatchMission.ACTIVE).count()
    if active >= slots(user):
        raise GameError(
            f"هر {slots(user)} جایگاه اعزامت پره. صبر کن یکی برگرده یا جایزه‌ی آماده رو بگیر."
        )
    creature = Creature.objects.select_for_update().filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این هیولا توی کلکسیون تو نیست.")
    assert_free(user, creature, for_action="بفرستی مأموریت")
    if not meets_rarity(creature, offer):
        need = constants.RARITY_LABELS[offer["min_rarity"]]
        raise GameError(f"این مأموریت حداقل یه هیولای {need} می‌خواد.")

    reward = preview_reward(user, offer, creature)
    reward.update(_roll_bonus(offer["hours"]))
    try:
        with transaction.atomic():
            return DispatchMission.objects.create(
                owner=user,
                creature=creature,
                offer_day=offer["day"],
                offer_idx=offer["idx"],
                template_key=offer["key"],
                hours=offer["hours"],
                reward=reward,
                finishes_at=timezone.now() + datetime.timedelta(hours=offer["hours"]),
            )
    except IntegrityError:
        raise GameError("این مأموریت رو امروز قبلاً فرستادی.")


def _pay(user: User, mission: DispatchMission) -> dict:
    """Apply a finished mission's frozen reward to the (already locked) user."""
    from game.buildings import grant_speedup_card
    from game.ledger import record_gain

    reward = dict(mission.reward or {})
    coins, dna, diamonds = int(reward.get("coins", 0)), int(reward.get("dna", 0)), int(reward.get("diamonds", 0))
    user.coins += coins
    user.dna_fragments += dna
    user.diamonds += diamonds
    fields = ["coins", "dna_fragments", "diamonds"]
    if reward.get("capsule"):
        add_capsules(user, reward["capsule"], int(reward.get("capsule_count", 1)))
        fields.append("xp_capsules")
    user.save(update_fields=fields)
    if reward.get("speedup"):
        grant_speedup_card(user, int(reward["speedup"]), count=1)
    record_gain(user, "dispatch", coins=coins, dna=dna, diamonds=diamonds)

    levels = 0
    creature = mission.creature
    if creature is not None and reward.get("xp"):
        creature = Creature.objects.select_for_update().get(id=creature.id)
        levels = add_xp(creature, int(reward["xp"]))
        creature.save()
    mission.status = DispatchMission.COLLECTED
    mission.save(update_fields=["status"])
    return {"mission": mission, "reward": reward, "levels": levels, "creature": creature}


@transaction.atomic
def collect(user: User, mission_id: int) -> dict:
    lock_row(user)
    mission = (
        DispatchMission.objects.select_for_update()
        .filter(id=mission_id, owner=user, status=DispatchMission.ACTIVE)
        .select_related("creature")
        .first()
    )
    if mission is None:
        raise GameError("این مأموریت پیدا نشد یا جایزه‌ش قبلاً گرفته شده.")
    if not is_ready(mission):
        raise GameError("این مأموریت هنوز تموم نشده.")
    return _pay(user, mission)


@transaction.atomic
def collect_all(user: User) -> list[dict]:
    lock_row(user)
    ready = list(
        DispatchMission.objects.select_for_update()
        .filter(owner=user, status=DispatchMission.ACTIVE, finishes_at__lte=timezone.now())
        .select_related("creature")
        .order_by("finishes_at", "id")
    )
    return [_pay(user, m) for m in ready]


@transaction.atomic
def cancel(user: User, mission_id: int) -> None:
    """Call the creature back early: no reward, and the offer can be taken again."""
    mission = (
        DispatchMission.objects.select_for_update()
        .filter(id=mission_id, owner=user, status=DispatchMission.ACTIVE)
        .first()
    )
    if mission is None:
        raise GameError("این مأموریت پیدا نشد.")
    if is_ready(mission):
        raise GameError("این مأموریت تموم شده — جایزه‌ش رو بگیر.")
    mission.delete()


def prune_old(user: User, keep_days: int = 7) -> None:
    """Collected rows only matter for «offer already taken today»; drop the old ones."""
    cutoff = (timezone.localdate() - datetime.timedelta(days=keep_days)).isoformat()
    DispatchMission.objects.filter(
        owner=user, status=DispatchMission.COLLECTED, offer_day__lt=cutoff
    ).delete()


def template(mission_or_key) -> tuple:
    key = mission_or_key if isinstance(mission_or_key, str) else mission_or_key.template_key
    return _TEMPLATE_BY_KEY.get(key, TEMPLATES[0])


def reward_text(reward: dict, *, with_bonus: bool = True) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{get_emoji('coin')} <code>{int(reward['coins']):,}</code> طلا")
    if reward.get("dna"):
        parts.append(f"{get_emoji('dna')} <code>{int(reward['dna']):,}</code> DNA")
    if reward.get("xp"):
        parts.append(f"{get_emoji('star')} <code>{int(reward['xp']):,}</code> تجربه")
    if with_bonus:
        if reward.get("diamonds"):
            parts.append(f"{get_emoji('diamond')} <code>{int(reward['diamonds'])}</code> الماس")
        if reward.get("capsule"):
            cap = constants.XP_CAPSULES[reward["capsule"]]
            parts.append(f"{cap['emoji']} <code>{int(reward.get('capsule_count', 1))}</code> {cap['label']}")
        if reward.get("speedup"):
            parts.append(f"⏩ کارت سرعت <code>{int(reward['speedup'])}</code> دقیقه‌ای")
    return " ┃ ".join(parts) or "—"


def has_bonus(reward: dict) -> bool:
    return bool(reward.get("diamonds") or reward.get("capsule") or reward.get("speedup"))


def collect_finished_notifications() -> list[tuple[int, str]]:
    """(user_id, text) for every mission that just finished — each is announced once.
    Called from game.notifications.collect_due (sync)."""
    out = []
    due = DispatchMission.objects.filter(
        status=DispatchMission.ACTIVE, notified=False, finishes_at__lte=timezone.now()
    ).select_related("owner", "creature")
    for mission in due:
        if mission.owner.notifications_on:
            _key, emoji, title, _flavor, _focus = template(mission)
            name = creature_name(mission.creature) if mission.creature_id else "هیولات"
            out.append((
                mission.owner_id,
                f"🧭 <b>{name} از مأموریت برگشت!</b>\n{emoji} {title} تموم شد؛ بیا جایزه‌ش رو بگیر.",
            ))
        mission.notified = True
        mission.save(update_fields=["notified"])
    return out
