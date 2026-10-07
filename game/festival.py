"""«🎪 جشنواره‌ی ماهانه» — one themed week every (Jalali) month with its own currency.

For FESTIVAL_DAYS days each month, everything the player already does (hunts, arena,
dispatch, missions, the world boss, …) ALSO drops «سکه‌ی جشنواره». The coins buy from a
temporary shop and expire when the festival ends, so there is always a concrete,
dated goal («900 coins by Friday for the festival kaiju»). A leaderboard of coins EARNED
pays diamonds to the top players when the festival closes.

No scheduler and no festival table: the active festival is derived from today's Jalali
date (key = «year-month»), the theme from the month, and per-player state lives in
FestivalProgress / FestivalPurchase. The notification job calls tick() to pay the
leaderboard once after a festival ends.

Earning is capped per action per day (EARN), so the coins measure «played every part of
the game this week», not «spammed one button».
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from bio_lab.models import Creature, FestivalProgress, FestivalPurchase, FestivalState, User
from bio_lab.repository import lab_display, lock_row
from game import constants
from game.creature import GameError

# the festival runs on these days of every Jalali month (inclusive)
FESTIVAL_START_DAY = 15
FESTIVAL_DAYS = 7
FESTIVAL_END_DAY = FESTIVAL_START_DAY + FESTIVAL_DAYS - 1

# action → (coins per action, how many actions a day earn coins)
EARN = {
    # hunts and attacks are 0.6× as many a day since the energy re-base → same coins a day
    "hunt": (2, 20),
    "arena_attack": (3, 13),
    "dispatch": (4, 7),
    "worldboss_hit": (3, 6),
    "raid_attack": (2, 5),
    "collect": (1, 5),
    "feed": (1, 5),
    "wheel_spin": (5, 1),
}
MISSION_DAILY_COINS = 3
MISSION_WEEKLY_COINS = 15

EARN_LABELS = {
    "hunt": "هر شکار", "arena_attack": "هر حمله‌ی آرنا", "dispatch": "هر مأموریت اعزامی",
    "worldboss_hit": "هر ضربه به غول سرگردان", "raid_attack": "هر ضربه به باس رید",
    "collect": "هر جمع‌آوری از ساختمان", "feed": "هر تغذیه", "wheel_spin": "گردونه‌ی روزانه",
}

# one theme per Jalali month (index = month - 1); `element` is the grand-prize kaiju's
THEMES = (
    ("🌸", "جشن شکوفه‌ها", "earth"), ("🌊", "خروش دریاچه", "water"), ("🔥", "شب‌های آتشفشان", "fire"),
    ("⚡", "طوفان تابستان", "electric"), ("🔮", "طوفان کریستال", "crystal"), ("⚛️", "شفق پلاسما", "plasma"),
    ("🍂", "جشن برگ‌ریزان", "earth"), ("🌧", "موسم باران", "water"), ("🌋", "آتش زمستانی", "fire"),
    ("❄️", "رعد یخ‌بندان", "electric"), ("💠", "بلورهای یخی", "crystal"), ("✨", "جشن پایان سال", "plasma"),
)

# ── subscribers («اشتراک») ────────────────────────────────────────────────────
# Extra festival coins on everything they earn, plus the two VIP_ONLY shop items. The
# bonus is applied PER ACTION (unit_value), rounded half up — a 2-coin hunt pays 3, a
# 3-coin attack 5, a 1-coin collect 2 — so a batch of 10 hunts pays exactly 10 single ones.
VIP_ONLY = ("vip_chest", "vip_mythic")
VIP_COIN_BONUS = 0.50
VIP_MYTHIC_RARITY = "mythic"
VIP_MYTHIC_ELEMENT = "crystal"


def unit_value(per: int, vip: bool) -> int:
    """Coins ONE action (or mission) worth `per` pays this kind of player."""
    return per + int(per * VIP_COIN_BONUS + 0.5) if vip else per


def day_max(vip: bool = False) -> int:
    """Coins of a perfect day: every action cap reached and every daily mission done
    (the weekly missions are not part of a «day»)."""
    actions = sum(unit_value(per, vip) * cap for per, cap in EARN.values())
    return actions + unit_value(MISSION_DAILY_COINS, vip) * len(constants.MISSION_DEFS)


def _price_of_days(days: int, vip: bool) -> int:
    """`days` perfect days, rounded up to a ten."""
    return -(-day_max(vip) * days // 10) * 10


# The big prizes are priced in PERFECT DAYS (owner's rule): the grand kaiju takes a
# non-subscriber at least this many days of hitting every cap; the mythic takes a
# subscriber the same number of perfect days at the subscriber rate.
GRAND_PRIZE_DAYS = 4
VIP_MYTHIC_DAYS = 4

# shop: key → (emoji, title, cost in festival coins, per-festival limit)
SHOP = {
    "capsule": ("🐭", "3 موش (غذای هیولا)", 15, 10),
    "speedup": ("⏩", "کارت سرعت 30 دقیقه‌ای", 25, 8),
    "gold": ("💰", "کیسه‌ی طلا (5 شکار)", 40, 8),
    "dna": ("🧬", "بسته‌ی دی‌ان‌ای (5 شکار)", 40, 8),
    "golden": ("🥇", "جعبه‌ی طلایی", 140, 3),
    "magical": ("🔮", "جعبه‌ی جادویی", 320, 2),
    "grand": ("👑", "هیولای افسانه‌ای جشنواره", _price_of_days(GRAND_PRIZE_DAYS, False), 1),
    # subscribers only (VIP_ONLY) — the free chest is their welcome gift of each festival
    "vip_chest": ("🎁", "هدیه‌ی اشتراک: جعبه‌ی جادویی", 0, 1),
    "vip_mythic": ("💠", "هیولای اساطیری کریستال", _price_of_days(VIP_MYTHIC_DAYS, True), 1),
}
SHOP_ORDER = ("capsule", "speedup", "gold", "dna", "golden", "magical", "grand", "vip_chest", "vip_mythic")
GRAND_RARITY = "legendary"


def is_vip(user: User) -> bool:
    from game.subscription import is_subscription_active

    return is_subscription_active(user)


# leaderboard (coins EARNED) prizes, paid once when the festival ends: (max rank, diamonds)
RANK_PRIZES = ((1, 150), (2, 100), (3, 75), (10, 30))
LEADERBOARD_SIZE = 10


def _jalali(d: datetime.date | None = None) -> tuple[int, int, int]:
    from game.battlepass import gregorian_to_jalali

    d = d or timezone.localdate()
    return gregorian_to_jalali(d.year, d.month, d.day)


def _key(jy: int, jm: int) -> str:
    return f"{jy}-{jm:02d}"


def active_key() -> str | None:
    """The running festival's key, or None between festivals."""
    jy, jm, jd = _jalali()
    return _key(jy, jm) if FESTIVAL_START_DAY <= jd <= FESTIVAL_END_DAY else None


def theme(key: str | None = None) -> dict:
    if key is None:
        _jy, jm, _jd = _jalali()
    else:
        jm = int(key.split("-")[1])
    emoji, title, element = THEMES[(jm - 1) % len(THEMES)]
    return {"emoji": emoji, "title": title, "element": element}


def _greg(jy: int, jm: int, jd: int) -> datetime.date:
    from game.battlepass import jalali_to_gregorian

    return datetime.date(*jalali_to_gregorian(jy, jm, jd))


def seconds_left() -> int:
    """Until the running festival ends (0 when none is running)."""
    if active_key() is None:
        return 0
    jy, jm, _jd = _jalali()
    end_day = _greg(jy, jm, FESTIVAL_END_DAY) + datetime.timedelta(days=1)
    now = timezone.localtime()
    end = timezone.make_aware(datetime.datetime.combine(end_day, datetime.time.min), now.tzinfo)
    return max(0, int((end - now).total_seconds()))


def seconds_until_next() -> int:
    """Until the next festival starts (0 while one is running)."""
    if active_key() is not None:
        return 0
    jy, jm, jd = _jalali()
    if jd > FESTIVAL_END_DAY:
        jy, jm = (jy + 1, 1) if jm == 12 else (jy, jm + 1)
    now = timezone.localtime()
    start = timezone.make_aware(
        datetime.datetime.combine(_greg(jy, jm, FESTIVAL_START_DAY), datetime.time.min), now.tzinfo
    )
    return max(0, int((start - now).total_seconds()))


def _last_ended_key() -> str:
    jy, jm, jd = _jalali()
    if jd > FESTIVAL_END_DAY:
        return _key(jy, jm)
    jy, jm = (jy - 1, 12) if jm == 1 else (jy, jm - 1)
    return _key(jy, jm)


# ── earning ───────────────────────────────────────────────────────────────────
def add_coins(user: User, amount: int) -> int:
    """Credit festival coins (no-op between festivals). Returns what was added."""
    key = active_key()
    amount = int(amount)
    if key is None or amount <= 0:
        return 0
    FestivalProgress.objects.get_or_create(user=user, festival_key=key)
    FestivalProgress.objects.filter(user=user, festival_key=key).update(
        coins=F("coins") + amount, earned=F("earned") + amount
    )
    return amount


def on_action(user: User, action: str, new_count: int, n: int = 1) -> int:
    """Hook for game.daily: `n` more of `action` were just recorded and today's counter
    is now `new_count`. Pays coins for the ones that fall under the daily cap."""
    rule = EARN.get(action)
    if rule is None or active_key() is None:
        return 0
    per, cap = rule
    before = new_count - n
    counted = max(0, min(new_count, cap) - min(before, cap))
    return add_coins(user, counted * unit_value(per, is_vip(user)))


def on_missions(user: User, completed: list[dict]) -> int:
    if not completed:
        return 0
    vip = is_vip(user)
    return add_coins(user, sum(
        unit_value(MISSION_WEEKLY_COINS if m.get("weekly") else MISSION_DAILY_COINS, vip) for m in completed
    ))


def today_progress(user: User) -> dict:
    """What the player has earned TODAY and what is still on the table, per source —
    read-only (never creates counter rows). `left` counts actions/missions, not coins."""
    from bio_lab.models import DailyActionLog, MissionClaim
    from game.daily import today_str, week_key

    vip = is_vip(user)
    day = today_str()
    counts = dict(DailyActionLog.objects.filter(user=user, day=day, action__in=list(EARN))
                  .values_list("action", "count"))
    rows = []
    for action, (per, cap) in EARN.items():
        done = min(cap, counts.get(action, 0))
        unit = unit_value(per, vip)
        rows.append({"key": action, "label": EARN_LABELS[action], "unit": unit, "cap": cap,
                     "done": done, "left": cap - done, "earned": done * unit, "max": cap * unit})
    daily_keys, weekly_keys = set(constants.MISSION_DEFS), set(constants.WEEKLY_MISSION_DEFS)
    d_done = MissionClaim.objects.filter(user=user, day=day, mission_key__in=daily_keys).count()
    w_done = MissionClaim.objects.filter(user=user, day=week_key(), mission_key__in=weekly_keys).count()
    d_unit, w_unit = unit_value(MISSION_DAILY_COINS, vip), unit_value(MISSION_WEEKLY_COINS, vip)
    daily = {"unit": d_unit, "cap": len(daily_keys), "done": d_done, "left": len(daily_keys) - d_done,
             "earned": d_done * d_unit, "max": len(daily_keys) * d_unit}
    weekly = {"unit": w_unit, "cap": len(weekly_keys), "done": w_done, "left": len(weekly_keys) - w_done}
    earned = sum(r["earned"] for r in rows) + daily["earned"]
    total = day_max(vip)
    return {"vip": vip, "rows": rows, "daily": daily, "weekly": weekly,
            "earned": earned, "max": total, "left": max(0, total - earned)}


# ── state for the screens ─────────────────────────────────────────────────────
def progress(user: User, key: str | None = None) -> FestivalProgress | None:
    key = key or active_key()
    if key is None:
        return None
    return FestivalProgress.objects.filter(user=user, festival_key=key).first()


def leaderboard(key: str, limit: int = LEADERBOARD_SIZE) -> list[dict]:
    rows = (
        FestivalProgress.objects.filter(festival_key=key, earned__gt=0)
        .select_related("user").order_by("-earned", "id")[:limit]
    )
    return [{"rank": i, "name": lab_display(r.user), "earned": r.earned, "user_id": r.user_id}
            for i, r in enumerate(rows, start=1)]


def rank_prize(rank: int) -> int:
    for max_rank, diamonds in RANK_PRIZES:
        if rank <= max_rank:
            return diamonds
    return 0


def status(user: User) -> dict:
    key = active_key()
    data = {"active": key is not None, "key": key, "theme": theme(key), "vip": is_vip(user),
            "seconds_left": seconds_left(), "seconds_until_next": seconds_until_next()}
    if key is None:
        return data
    p = progress(user, key)
    earned = p.earned if p else 0
    data["today"] = today_progress(user)
    data.update({
        "coins": p.coins if p else 0,
        "earned": earned,
        "rank": (FestivalProgress.objects.filter(festival_key=key, earned__gt=earned).count() + 1) if earned else None,
        "top": leaderboard(key, 5),
    })
    return data


def shop_state(user: User) -> dict:
    key = active_key()
    if key is None:
        raise GameError("الان جشنواره‌ای در جریان نیست.")
    bought = dict(
        FestivalPurchase.objects.filter(user=user, festival_key=key).values_list("item_key", "count")
    )
    p = progress(user, key)
    vip = is_vip(user)
    items = []
    for item_key in SHOP_ORDER:
        emoji, title, cost, limit = SHOP[item_key]
        if item_key == "grand":
            title = f"{title} ({constants.element_label(theme(key)['element'])})"
        items.append({"key": item_key, "emoji": emoji, "title": title, "cost": cost,
                      "left": max(0, limit - bought.get(item_key, 0)),
                      "vip": item_key in VIP_ONLY, "locked": item_key in VIP_ONLY and not vip})
    return {"coins": p.coins if p else 0, "items": items, "theme": theme(key), "vip": vip}


# ── shop ──────────────────────────────────────────────────────────────────────
def _grant(user: User, item_key: str, key: str) -> str:
    """Hand over one shop item to the (locked) user. Returns a short «you got …» text."""
    from game.arena_chests import grant_chest_contents
    from game.buildings import grant_speedup_card
    from game.creature import add_capsules
    from game.daily import mission_unit

    if item_key == "capsule":
        add_capsules(user, "small", 3)
        user.save(update_fields=["xp_capsules"])
        return "3 موش"
    if item_key == "speedup":
        grant_speedup_card(user, 30, count=1)
        return "یک کارت سرعت 30 دقیقه‌ای"
    if item_key in ("gold", "dna"):
        gold_unit, dna_unit = mission_unit(user)
        if item_key == "gold":
            amount = gold_unit * 5
            user.coins += amount
            user.save(update_fields=["coins"])
            return f"{amount:,} طلا"
        amount = dna_unit * 5
        user.dna_fragments += amount
        user.save(update_fields=["dna_fragments"])
        return f"{amount:,} دی‌ان‌ای"
    if item_key in ("golden", "magical", "vip_chest"):
        chest = "magical" if item_key == "vip_chest" else item_key
        c = grant_chest_contents(user, chest, user.cup, source="festival")
        got = [f"{c['coins']:,} طلا", f"{c['dna']:,} دی‌ان‌ای"]
        if c["diamonds"]:
            got.append(f"{c['diamonds']} الماس")
        rarity = constants.RARITY_LABELS.get(c["rarity"], c["rarity"])
        if c["creature"] is not None:
            got.append(f"هیولای {rarity} «{c['creature'].name}»")
        elif c["item"] is not None:
            got.append(f"تجهیزات {rarity} «{c['item'].name}»")
        return f"{c['name']}: " + " + ".join(got)
    if item_key in ("grand", "vip_mythic"):
        element = VIP_MYTHIC_ELEMENT if item_key == "vip_mythic" else theme(key)["element"]
        rarity = VIP_MYTHIC_RARITY if item_key == "vip_mythic" else GRAND_RARITY
        canon = constants.canonical_base_stats(rarity, 1)
        creature = Creature.objects.create(
            owner=user, name=constants.random_species_name(element), element=element,
            rarity=rarity, is_active=False, **canon,
        )
        return (f"هیولای {constants.RARITY_LABELS[rarity]} «{creature.name}» "
                f"({constants.element_label(element)})")
    raise GameError("این آیتم وجود نداره.")


@transaction.atomic
def buy(user: User, item_key: str) -> dict:
    key = active_key()
    if key is None:
        raise GameError("جشنواره تموم شده؛ سکه‌ها دیگه قابل خرج نیستن.")
    if item_key not in SHOP:
        raise GameError("این آیتم وجود نداره.")
    _emoji, title, cost, limit = SHOP[item_key]
    lock_row(user)
    if item_key in VIP_ONLY and not is_vip(user):
        raise GameError("این آیتم فقط برای کساییه که اشتراک فعال دارن.")
    FestivalProgress.objects.get_or_create(user=user, festival_key=key)
    p = FestivalProgress.objects.select_for_update().get(user=user, festival_key=key)
    if p.coins < cost:
        raise GameError(f"سکه‌ی جشنواره کافی نداری ({p.coins} از {cost}).")
    purchase, _ = FestivalPurchase.objects.select_for_update().get_or_create(
        user=user, festival_key=key, item_key=item_key
    )
    if purchase.count >= limit:
        raise GameError("سقف خرید این آیتم توی این جشنواره پر شده.")
    p.coins -= cost
    p.save(update_fields=["coins"])
    purchase.count += 1
    purchase.save(update_fields=["count"])
    return {"title": title, "cost": cost, "got": _grant(user, item_key, key), "coins_left": p.coins}


# ── leaderboard payout ────────────────────────────────────────────────────────
@transaction.atomic
def tick() -> list[tuple]:
    """Called from the notification job: pays the leaderboard of the festival that last
    ended, exactly once. Returns (user_id, text, "festival") DMs."""
    state, _ = FestivalState.objects.get_or_create(id=1)
    ended = _last_ended_key()
    if state.last_settled_key == ended or active_key() == ended:
        return []
    state = FestivalState.objects.select_for_update().get(id=1)
    if state.last_settled_key == ended:
        return []
    state.last_settled_key = ended
    state.save(update_fields=["last_settled_key"])

    th = theme(ended)
    out: list[tuple] = []
    for row in leaderboard(ended):
        diamonds = rank_prize(row["rank"])
        if diamonds <= 0:
            continue
        User.objects.filter(pk=row["user_id"]).update(diamonds=F("diamonds") + diamonds)
        out.append((
            row["user_id"],
            f"{th['emoji']} <b>جشنواره‌ی «{th['title']}» تموم شد!</b>\n"
            f"رتبه‌ی تو: <b>{row['rank']}</b> با <code>{row['earned']:,}</code> سکه\n"
            f"💎 جایزه: <code>{diamonds}</code> الماس",
            "festival",
        ))
    return out
