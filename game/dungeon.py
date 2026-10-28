"""«سیاه‌چال» — a repeatable roguelite dungeon, opened by clearing the 50 campaign stages.

One RUN: the player enters with a key and their normal Team (bio_lab.models.Team, up to 3
kaiju). There are ROOMS rooms, each offering 2–3 visible doors (fight, elite, treasure,
spring, trap, merchant), then the boss. Fights are 1-v-1: the player picks which team member
faces the enemy, and the result follows the game's one duel rule (higher power wins; the
element-advantaged side counts ×1.2, ×1.3 with the «برتری مضاعف» rune). HP persists through
the run — a win costs the fighter some HP, a loss knocks it out, and a low-HP kaiju fights
weaker (HP_POWER_FLOOR) — so springs, runes and who-fights-what matter. After every won fight
the player picks one of three run-runes (temporary buffs for this run only).

Loot is banked as the run goes and paid at the end: everything on a win (+ a relic chance),
half of it after a defeat or quitting. «رون سیاه» is the dungeon's own currency for its shop.

The whole run lives in DungeonRun.state (schema in `_new_state`); every action below locks the
player row and the run, so a double tap can't act twice.
"""

from __future__ import annotations

import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, DungeonProfile, DungeonRun, Team, User
from bio_lab.repository import lock_row
from game import constants
from game.creature import GameError, creature_power

UNLOCK_STAGE = 50          # campaign stages to clear first
ROOMS = 7                  # rooms before the boss (the boss is room ROOMS + 1)
DAILY_KEYS = 2
KEY_CAP = 4                # the daily refill stops here (bought keys may go above)
KEY_HARD_CAP = 10
LOSS_PAYOUT = 0.5          # share of the banked loot kept after a defeat / quitting
HP_POWER_FLOOR = 0.6       # a kaiju at 1 HP fights at 60% of its power, full HP at 100%
# difficulty tiers: enemy power ×, gold/DNA ×, black runes ×, relic chance on a win, shadow cores
# dropped by the boss (min, max), and the tier that must be cleared first (best_difficulty)
DIFFICULTIES = {
    "normal": {"label": "معمولی", "power": 1.0, "loot": 1.0, "runes": 1, "relic": 0.25, "cores": (0, 0), "need": 0},
    "hard": {"label": "سخت", "power": 1.4, "loot": 1.6, "runes": 2, "relic": 0.45, "cores": (1, 1), "need": 1},
    "nightmare": {"label": "کابوس", "power": 2.3, "loot": 2.5, "runes": 3, "relic": 0.70, "cores": (2, 3), "need": 2},
}
_DIFF_RANK = {k: i + 1 for i, k in enumerate(DIFFICULTIES)}   # cleared normal → 1, hard → 2, nightmare → 3
_DIFF_BADGE = {"normal": "explorer", "hard": "hard", "nightmare": "nightmare"}

# «بی‌انتها»: floors without end, a boss every ENDLESS_BOSS_EVERY floors, opened by a hard win.
# Enemies grow ENDLESS_GROWTH per floor from ENDLESS_BASE; the run only ends when the team falls
# (or the player leaves), and pays everything banked — there is no «win» to lose half of.
# The deepest floor of the week goes on the weekly table; the top 10 are paid at the week's end.
ENDLESS = "endless"
# loot ×0.2: a deep endless run is ~50 floors, so per-floor pay must be a fraction of a normal
# room's or it would out-earn nightmare (simulated: ×1.0 paid a maxed team ~430k gold a key)
ENDLESS_DIFF = {"label": "بی‌انتها", "power": 1.0, "loot": 0.2, "runes": 1, "relic": 0.0, "cores": (0, 0), "need": 2}
ENDLESS_BOSS_EVERY = 10
ENDLESS_BASE = 3000
ENDLESS_GROWTH = 0.07
ENDLESS_BOSS_RELIC = 0.30
ENDLESS_CORE_FROM = 30      # a shadow core on boss floors 30, 50, 70, … (every ENDLESS_CORE_EVERY from here)
ENDLESS_CORE_EVERY = 20
ENDLESS_REWARDS = ((1, 300, 5), (2, 200, 4), (3, 150, 3)) + tuple((r, 80, 1) for r in range(4, 11))  # rank, runes, cores

# «عنصر هفته»: every week the dungeon leans to one element — most enemies have it, and kaiju
# strong against it fight WEEK_BOOST stronger in every dungeon mode
WEEK_BOOST = 0.10
WEEK_ELEMENT_SHARE = 0.65
WEEK_ELEMENTS = ("fire", "water", "earth", "electric")


def _diff(key: str | None) -> dict:
    return ENDLESS_DIFF if key == ENDLESS else DIFFICULTIES.get(key or "normal", DIFFICULTIES["normal"])


def weekly_element(week: str | None = None) -> str:
    """The week's dominant element (game.season.week_key, so it turns over with the arena season)."""
    from game.season import week_key

    w = week or week_key()
    year, _, num = w.partition("-W")
    # only the four base elements: crystal and plasma have no counter, so a week of them would
    # bless nobody
    return WEEK_ELEMENTS[(int(year) + int(num or 0)) % len(WEEK_ELEMENTS)]


def week_favoured(element: str) -> list[str]:
    """Elements that beat the week's element — their kaiju get WEEK_BOOST in the dungeon."""
    return [e for e in constants.ELEMENTS if constants.is_strong_against(e, element)]
# normal difficulty is sized to a team that just cleared campaign stage 50 (~13k team power,
# ~4.4k per kaiju); maxed players get their challenge from the harder tiers (phase 2)
ROOM_POWER = (3000, 5000)  # first room → last room, before the boss
BOSS_POWER = 6500

DOOR_WEIGHTS = {"fight": 45, "elite": 15, "treasure": 15, "spring": 10, "trap": 10, "merchant": 5}
DOORS = {
    "fight": ("⚔️", "نبرد"), "elite": ("💀", "نخبه"), "treasure": ("🎁", "گنج"),
    "spring": ("⛲", "چشمه"), "trap": ("⚠️", "تله"), "merchant": ("🛒", "تاجر"),
}
ENEMY_NAMES = ("سایه‌خوار", "نگهبان تاریک", "شبح غار", "خزنده‌ی سنگی", "گرگ سایه", "عنکبوت بلور",
               "دیو مه", "مار استخوانی", "شکارچی کور", "روح زنجیری")
BOSS_NAMES = ("فرمانروای سیاه‌چال", "اژدهای سایه", "ملکه‌ی تاریکی", "غول زنجیرشکن")

# run-runes: key → (label, description). «خشم عنصری» carries its element: "fury:fire".
RUNES = {
    "resolve": ("اراده", "+۶٪ قدرت همه‌ی هیولاها"),
    "fury": ("خشم {el}", "+۱۲٪ قدرت هیولاهای {el}"),
    "stone_skin": ("پوست سنگی", "جونی که نبردها کم می‌کنن ۳۰٪ کمتر"),
    "vampire": ("خون‌آشام", "بعد از هر برد، ۱۵٪ جون برنده برمی‌گرده"),
    "first_strike": ("ضربه‌ی اول", "نبرد بعدی +۳۰٪ قدرت"),
    "treasure_hunter": ("گنج‌یاب", "+۲۵٪ طلا و DNA از اینجا به بعد"),
    "elemental_edge": ("برتری مضاعف", "برتری عنصری ×۱.۳ به‌جای ×۱.۲"),
    "revive": ("احیا", "یکی از هیولاهای از پا افتاده با ۴۰٪ جون برمی‌گرده"),
}

SHOP = {
    # 80, not 40: a nightmare win pays ~40-50 runes back, so a 40-rune key bought itself again
    # and farmed shadow cores without end
    "key": {"label": "کلید سیاه‌چال", "price": 80},
    "magical_chest": {"label": "جعبه‌ی جادویی", "price": 40},
    "legendary_gear": {"label": "تجهیزات افسانه‌ای تصادفی", "price": 60},
    "relic": {"label": "اثر باستانی تصادفی (۳ روزه)", "price": 70},
    "mega_chest": {"label": "جعبه‌ی امگا", "price": 90},
    "shadow_core": {"label": "هسته‌ی سایه (فرم سیاه)", "price": 60},
}


# ══════════════════════════ profile / keys ══════════════════════════
def unlocked(user: User) -> bool:
    return user.campaign_stage >= UNLOCK_STAGE


def profile(user: User) -> DungeonProfile:
    """The player's dungeon wallet, with today's free keys added (once per game day)."""
    from game import relics
    from game.daily import today_str

    prof, _ = DungeonProfile.objects.get_or_create(user=user)
    today = today_str()
    if prof.keys_day != today:
        with transaction.atomic():
            prof = DungeonProfile.objects.select_for_update().get(pk=prof.pk)
            if prof.keys_day != today:
                extra = relics.bonus(user, "shadow_key")
                prof.keys = max(prof.keys, min(KEY_CAP + extra, prof.keys + DAILY_KEYS + extra))
                prof.keys_day = today
                prof.save(update_fields=["keys", "keys_day"])
    return prof


def active_run(user: User, lock: bool = False) -> DungeonRun | None:
    qs = DungeonRun.objects.filter(user=user, status=DungeonRun.ACTIVE)
    if lock:
        qs = qs.select_for_update()
    return qs.order_by("-id").first()


# ══════════════════════════ helpers ══════════════════════════
def _power_of(user: User, creature: Creature) -> int:
    from game import research
    from game.equipment import get_equipped_items

    research.attach_research(user, creature)
    return creature_power(creature, get_equipped_items(creature))


def _rng(state: dict) -> random.Random:
    state["n"] = state.get("n", 0) + 1
    return random.Random(f"{state['seed']}:{state['n']}")


def _rune_count(state: dict, key: str) -> int:
    return sum(1 for r in state["runes"] if r == key or r.startswith(key + ":"))


def rune_label(key: str, plain: bool = False) -> tuple[str, str]:
    """(label, description) of a run-rune. `plain` = no emoji (the Mini App's rule)."""
    base, _, el = key.partition(":")
    label, desc = RUNES[base]
    el_name = (constants.ELEMENT_WORDS.get(el, el) if plain else constants.element_label(el)) if el else ""
    return label.format(el=el_name), desc.format(el=el_name)


def member_power(state: dict, member: dict, *, for_fight: bool = True) -> int:
    """A team member's power in this run: its power at entry × HP × the run's runes."""
    hp_factor = HP_POWER_FLOOR + (1 - HP_POWER_FLOOR) * max(0, member["hp"]) / 100
    mult = 1 + 0.06 * _rune_count(state, "resolve")
    mult += 0.12 * sum(1 for r in state["runes"] if r == f"fury:{member['element']}")
    if for_fight and state.get("first_strike"):
        mult += 0.30
    if state.get("week_el") and constants.is_strong_against(member["element"], state["week_el"]):
        mult += WEEK_BOOST   # «برکت هفته»
    return round(member["power"] * hp_factor * mult)


def _advantage(state: dict) -> float:
    return 1.3 if _rune_count(state, "elemental_edge") else constants.ELEMENT_ADVANTAGE_POWER_FACTOR


def duel(state: dict, member: dict, enemy: dict) -> tuple[int, int]:
    """(my effective power, enemy effective power) — the game's duel rule with this run's runes."""
    mine, theirs = member_power(state, member), enemy["power"]
    if constants.is_strong_against(member["element"], enemy["element"]):
        mine = round(mine * _advantage(state))
    elif constants.is_strong_against(enemy["element"], member["element"]):
        theirs = round(theirs * constants.ELEMENT_ADVANTAGE_POWER_FACTOR)
    return mine, theirs


def _enemy(state: dict, kind: str) -> dict:
    rng = _rng(state)
    room = state["room"]
    tier = _diff(state.get("difficulty"))["power"]
    if state.get("endless"):
        base = ENDLESS_BASE * (1 + ENDLESS_GROWTH * (room - 1)) * rng.uniform(0.95, 1.05)
        power = round(base * {"boss": 1.4, "elite": 1.25}.get(kind, 1.0))
        name = rng.choice(BOSS_NAMES if kind == "boss" else ENEMY_NAMES)
    elif kind == "boss":
        power = round(BOSS_POWER * tier * rng.uniform(0.97, 1.03))
        name = rng.choice(BOSS_NAMES)
    else:
        power = (ROOM_POWER[0] + (room - 1) * (ROOM_POWER[1] - ROOM_POWER[0]) / max(1, ROOMS - 1)) * rng.uniform(0.95, 1.05)
        power = round(power * tier * (1.25 if kind == "elite" else 1.0))
        name = rng.choice(ENEMY_NAMES)
    week_el = state.get("week_el")
    element = week_el if week_el and rng.random() < WEEK_ELEMENT_SHARE else rng.choice(constants.ELEMENTS)
    return {"kind": kind, "name": name, "element": element, "power": power}


def _doors(state: dict) -> list[str]:
    rng = _rng(state)
    count = 3 if rng.random() < 0.7 else 2
    pool = dict(DOOR_WEIGHTS)
    out = []
    while len(out) < count and pool:
        pick = rng.choices(list(pool), weights=list(pool.values()), k=1)[0]
        out.append(pick)
        pool.pop(pick)
    return out


def _loot_add(state: dict, coins: int = 0, dna: int = 0, runes: int = 0) -> dict:
    diff = _diff(state.get("difficulty"))
    boost = (1 + 0.25 * _rune_count(state, "treasure_hunter")) * diff["loot"]
    got = {"coins": round(coins * boost), "dna": round(dna * boost), "runes": runes * diff["runes"]}
    for k, v in got.items():
        state["loot"][k] = state["loot"].get(k, 0) + v
    return got


def _alive(state: dict) -> list[dict]:
    return [m for m in state["team"] if m["hp"] > 0]


def _log(state: dict, line: str) -> None:
    state["log"] = (state.get("log") or [])[-5:] + [line]


def heal_member(user: User, member_id: int | None, pct: int) -> str:
    """«سرم ترمیم» (game/serums.py): +pct HP to one team member still standing — the one asked
    for, or the most hurt. A downed member can't be brought back with it."""
    user, run = _load(user)
    state = run.state
    alive = [m for m in state["team"] if 0 < m["hp"] < 100]
    if member_id is not None:
        m = next((x for x in state["team"] if x["id"] == member_id), None)
        if m is None:
            raise GameError("این هیولا توی تیم سیاه‌چالت نیست.")
        if m["hp"] <= 0:
            raise GameError("این عضو از پا افتاده؛ ترمیم فقط روی عضوی اثر داره که هنوز سرپاست.")
        if m["hp"] >= 100:
            raise GameError("جون این عضو پره.")
    else:
        if not alive:
            raise GameError("هیچ عضوی از تیمت زخمی نیست (یا همه از پا افتادن).")
        m = min(alive, key=lambda x: x["hp"])
    before = m["hp"]
    m["hp"] = min(100, m["hp"] + int(pct))
    _log(state, f"سرم ترمیم: {m['name']} +{m['hp'] - before}٪ جون")
    run.state = state
    run.save(update_fields=["state"])
    return f"{m['name']}: جون از {before}٪ رسید به {m['hp']}٪"


def _next_room(state: dict) -> None:
    state["room"] += 1
    state["enemy"] = None
    if state.get("endless"):
        if state["room"] % ENDLESS_BOSS_EVERY == 0:
            state["phase"] = "fight"
            state["enemy"] = _enemy(state, "boss")
        else:
            state["phase"] = "doors"
            state["doors"] = _doors(state)
        return
    if state["room"] > ROOMS:
        state["phase"] = "fight"
        state["enemy"] = _enemy(state, "boss")
    else:
        state["phase"] = "doors"
        state["doors"] = _doors(state)


def _rune_offer(state: dict) -> list[str]:
    rng = _rng(state)
    elements = sorted({m["element"] for m in state["team"]})
    pool = ["resolve", "stone_skin", "vampire", "first_strike", "treasure_hunter", "elemental_edge"]
    pool += [f"fury:{el}" for el in elements]
    if any(m["hp"] <= 0 for m in state["team"]):
        pool.append("revive")
    rng.shuffle(pool)
    return pool[:3]


def _new_state(user: User, members: list[Creature], endless: bool = False) -> dict:
    state = {
        "seed": random.randrange(1 << 30), "n": 0, "room": 1, "phase": "doors",
        "endless": endless, "week_el": weekly_element(),
        "team": [{"id": c.id, "name": c.name, "element": c.element, "rarity": c.rarity, "star": c.star_level,
                  "power": _power_of(user, c), "hp": 100} for c in members],
        "runes": [], "loot": {"coins": 0, "dna": 0, "runes": 0, "cores": 0}, "log": [],
        "enemy": None, "doors": [], "rune_offer": [], "merchant": [], "first_strike": False,
    }
    state["doors"] = _doors(state)
    return state


# ══════════════════════════ actions ══════════════════════════
def _load(user: User) -> tuple[User, DungeonRun]:
    user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
    run = active_run(user, lock=True)
    if run is None:
        raise GameError("الان توی سیاه‌چال نیستی. از صفحه‌ی سیاه‌چال وارد شو.")
    return user, run


def _need(run: DungeonRun, phase: str) -> dict:
    state = run.state
    if state.get("phase") != phase:
        raise GameError("این گزینه دیگه معتبر نیست؛ صفحه رو دوباره باز کن.")
    return state


def _progress(user: User, state: dict) -> None:
    """«بی‌انتها»: every cleared floor counts on the weekly table straight away (a run left
    open still keeps its best)."""
    if state.get("endless") and state.get("room", 1) > 1:
        record_floor(user, state["room"] - 1)


@transaction.atomic
def start(user: User, difficulty: str = "normal") -> DungeonRun:
    user = User.objects.select_for_update().get(id=user.id)
    if difficulty not in DIFFICULTIES and difficulty != ENDLESS:
        raise GameError("این سطح سختی وجود نداره.")
    if not unlocked(user):
        raise GameError(f"🔒 سیاه‌چال بعد از فتح هر {UNLOCK_STAGE} مرحله‌ی دانجن باز می‌شه "
                        f"(الان مرحله‌ی {user.campaign_stage}).")
    if active_run(user) is not None:
        raise GameError("یه ورود نیمه‌کاره داری؛ اول اون رو تموم کن.")
    team = Team.objects.filter(owner=user).first()
    members = team.creatures() if team else []
    if not members:
        raise GameError("اول از «تیم حمله» حداقل یه هیولا توی تیمت بذار.")
    prof = profile(user)
    prof = DungeonProfile.objects.select_for_update().get(pk=prof.pk)
    need = _diff(difficulty)["need"]
    if prof.best_difficulty < need:
        prev = list(DIFFICULTIES)[need - 1]
        raise GameError(f"🔒 «{_diff(difficulty)['label']}» بعد از یه برد در سختی «{DIFFICULTIES[prev]['label']}» باز می‌شه.")
    if prof.keys < 1:
        raise GameError("🗝 کلید نداری! هر روز ۲ کلید رایگان می‌گیری، یا از فروشگاه سیاه‌چال بخر.")
    prof.keys -= 1
    prof.runs += 1
    prof.save(update_fields=["keys", "runs"])
    state = _new_state(user, members, endless=difficulty == ENDLESS)
    state["difficulty"] = difficulty   # read by _enemy / _loot_add
    if difficulty == ENDLESS:   # the week's best before this run — «a new record?» at the end
        from bio_lab.models import EndlessScore
        from game.season import week_key

        state["start_best"] = (EndlessScore.objects.filter(user=user, week=week_key())
                               .values_list("best_floor", flat=True).first() or 0)
    return DungeonRun.objects.create(user=user, difficulty=difficulty, state=state)


@transaction.atomic
def choose_door(user: User, index: int) -> dict:
    user, run = _load(user)
    state = _need(run, "doors")
    if not 0 <= index < len(state["doors"]):
        raise GameError("این در وجود نداره.")
    kind = state["doors"][index]
    room = state["room"]
    event = {"door": kind}
    if kind in ("fight", "elite"):
        state["enemy"] = _enemy(state, kind)
        state["phase"] = "fight"
    elif kind == "treasure":
        rng = _rng(state)
        got = _loot_add(state, 2000 + 300 * room, 60 + 10 * room, rng.choice((0, 0, 1, 2)))
        _log(state, f"🎁 گنج پیدا کردی: {got['coins']:,} طلا، {got['dna']:,} DNA"
                    + (f"، {got['runes']} رون سیاه" if got["runes"] else ""))
        event["got"] = got
        _next_room(state)
    elif kind == "spring":
        for m in _alive(state):
            m["hp"] = min(100, m["hp"] + 35)
        _log(state, "⛲ از چشمه نوشیدی: همه‌ی هیولاهای سرپا ۳۵٪ جون گرفتن.")
        _next_room(state)
    elif kind == "trap":
        cut = 15 if not _rune_count(state, "stone_skin") else 10
        for m in _alive(state):
            m["hp"] = max(5, m["hp"] - cut)
        _log(state, f"⚠️ تله! همه‌ی هیولاهای سرپا {cut}٪ جون از دست دادن.")
        _next_room(state)
    else:  # merchant
        rng = _rng(state)
        offers = _rune_offer(state)[:2]
        state["merchant"] = [{"rune": r, "price": round((3000 + 500 * room) * rng.uniform(0.9, 1.1), -2)} for r in offers]
        state["phase"] = "merchant"
    _progress(user, state)
    run.state = state
    run.save(update_fields=["state"])
    event["state"] = state
    return event


@transaction.atomic
def fight(user: User, creature_id: int) -> dict:
    user, run = _load(user)
    state = _need(run, "fight")
    member = next((m for m in state["team"] if m["id"] == creature_id), None)
    if member is None:
        raise GameError("این هیولا توی تیم این ورود نیست.")
    if member["hp"] <= 0:
        raise GameError("این هیولا از پا افتاده و نمی‌تونه بجنگه.")
    enemy = state["enemy"]
    mine, theirs = duel(state, member, enemy)
    won = mine >= theirs
    state["first_strike"] = False
    result = {"won": won, "mine": mine, "theirs": theirs, "member": member["name"], "enemy": enemy}
    if won:
        margin = mine / max(1, theirs)
        loss = max(10, min(40, round(40 - (margin - 1) * 100)))
        if _rune_count(state, "stone_skin"):
            loss = round(loss * 0.7)
        member["hp"] = max(1, member["hp"] - loss)
        if _rune_count(state, "vampire"):
            member["hp"] = min(100, member["hp"] + 15)
        room = state["room"]
        if enemy["kind"] == "boss" and state.get("endless"):
            got = _loot_add(state, 3000 + 300 * room, 120 + 10 * room, 4)
            if room >= ENDLESS_CORE_FROM and (room - ENDLESS_CORE_FROM) % ENDLESS_CORE_EVERY == 0:
                state["loot"]["cores"] = state["loot"].get("cores", 0) + 1
                got["cores"] = 1
            if random.random() < ENDLESS_BOSS_RELIC:
                from game import relics

                found = relics.grant(user)
                got["relic"] = found["label"]
                _log(state, f"🏺 اثر باستانی پیدا کردی: {found['label']}")
        elif enemy["kind"] == "boss":
            got = _loot_add(state, 6000, 250, 4)
        elif enemy["kind"] == "elite":
            got = _loot_add(state, 2 * (1200 + 250 * room), 2 * (30 + 8 * room), 0 if state.get("endless") else 2)
        else:
            got = _loot_add(state, 1200 + 250 * room, 30 + 8 * room, 0 if state.get("endless") else 1)
        result.update(hp_lost=loss, got=got)
        _log(state, f"⚔️ {member['name']} «{enemy['name']}» رو شکست داد ({mine:,} در برابر {theirs:,}).")
        if enemy["kind"] == "boss" and not state.get("endless"):
            run.state = state
            result["finish"] = _finish(user, run, DungeonRun.WON)
            return result
        state["enemy"] = None
        state["rune_offer"] = _rune_offer(state)
        state["phase"] = "rune"
    else:
        member["hp"] = 0
        _log(state, f"💀 {member['name']} جلوی «{enemy['name']}» از پا افتاد ({mine:,} در برابر {theirs:,}).")
        if not _alive(state):
            run.state = state
            result["finish"] = _finish(user, run, DungeonRun.LOST)
            return result
    run.state = state
    run.save(update_fields=["state"])
    result["state"] = state
    return result


def _apply_rune(state: dict, key: str) -> None:
    if key == "revive":
        down = next((m for m in state["team"] if m["hp"] <= 0), None)
        if down is not None:
            down["hp"] = 40
            _log(state, f"✨ {down['name']} احیا شد.")
        return
    if key == "first_strike":
        state["first_strike"] = True
    state["runes"].append(key)


@transaction.atomic
def pick_rune(user: User, index: int) -> dict:
    user, run = _load(user)
    state = _need(run, "rune")
    if not 0 <= index < len(state["rune_offer"]):
        raise GameError("این رون وجود نداره.")
    key = state["rune_offer"][index]
    _apply_rune(state, key)
    state["rune_offer"] = []
    _next_room(state)
    _progress(user, state)
    run.state = state
    run.save(update_fields=["state"])
    return {"rune": key, "state": state}


@transaction.atomic
def merchant_buy(user: User, index: int | None) -> dict:
    """Buy one of the merchant's runes with real gold (index None = walk past)."""
    user, run = _load(user)
    state = _need(run, "merchant")
    bought = None
    if index is not None:
        if not 0 <= index < len(state["merchant"]):
            raise GameError("این پیشنهاد وجود نداره.")
        offer = state["merchant"][index]
        price = int(offer["price"])
        if user.coins < price:
            raise GameError(f"طلا کافی نداری ({price:,} لازمه، {user.coins:,} داری).")
        User.objects.filter(id=user.id).update(coins=user.coins - price)
        _apply_rune(state, offer["rune"])
        bought = offer
        _log(state, f"🛒 از تاجر «{rune_label(offer['rune'])[0]}» خریدی ({price:,} طلا).")
    state["merchant"] = []
    _next_room(state)
    _progress(user, state)
    run.state = state
    run.save(update_fields=["state"])
    return {"bought": bought, "state": state}


@transaction.atomic
def quit_run(user: User) -> dict:
    user, run = _load(user)
    return {"finish": _finish(user, run, DungeonRun.QUIT)}


def _finish(user: User, run: DungeonRun, status: str) -> dict:
    """Close the run and pay out the banked loot (all on a win, LOSS_PAYOUT otherwise)."""
    from game import relics
    from game.ledger import record_gain

    state = run.state
    endless = bool(state.get("endless"))
    share = 1.0 if (status == DungeonRun.WON or endless) else LOSS_PAYOUT
    loot = {k: int(v * share) for k, v in state["loot"].items()}
    loot["cores"] = state["loot"].get("cores", 0) if endless else 0
    relic = None
    diff = _diff(run.difficulty)
    badge = None
    if status == DungeonRun.WON:
        loot["runes"] += random.randint(1, 3) * diff["runes"]
        loot["cores"] = random.randint(*diff["cores"])
        if random.random() < diff["relic"]:
            relic = relics.grant(user)
        from game import shadow

        if shadow.award_badge(user, _DIFF_BADGE.get(run.difficulty, "explorer")):
            badge = shadow.BADGES[user.dungeon_badge]
    from django.db.models import F

    User.objects.filter(id=user.id).update(coins=F("coins") + loot["coins"], dna_fragments=F("dna_fragments") + loot["dna"])
    record_gain(user, "dungeon", coins=loot["coins"], dna=loot["dna"])
    prof = DungeonProfile.objects.select_for_update().get(user=user)
    prof.black_runes += loot["runes"]
    prof.shadow_cores += loot["cores"]
    reached = state["room"] if endless else min(state["room"], ROOMS + 1)
    prof.best_room = max(prof.best_room, reached)
    unlocked_tier = None
    if status == DungeonRun.WON:
        prof.clears += 1
        rank = _DIFF_RANK.get(run.difficulty, 1)
        if rank > prof.best_difficulty:
            prof.best_difficulty = rank
            nxt = [k for k, d in DIFFICULTIES.items() if d["need"] == rank]
            unlocked_tier = DIFFICULTIES[nxt[0]]["label"] if nxt else None
    prof.save(update_fields=["black_runes", "shadow_cores", "best_room", "clears", "best_difficulty"])
    run.status = status
    run.finished_at = timezone.now()
    run.state = state
    run.save(update_fields=["status", "finished_at", "state"])
    if status == DungeonRun.WON:
        from game import lab

        lab.award(user, "campaign_win")
    out = {"status": status, "loot": loot, "relic": relic, "room": reached, "share": share,
           "difficulty": run.difficulty, "badge": badge, "unlocked": unlocked_tier, "endless": endless}
    if endless:
        out["floor"] = max(0, state["room"] - 1)
        out.update(record_floor(user, out["floor"]))
        out["new_record"] = out["floor"] > state.get("start_best", 0)   # floors are saved as they're cleared
    return out


@transaction.atomic
def shop_buy(user: User, key: str) -> dict:
    """Spend black runes in the dungeon shop."""
    user = User.objects.select_for_update().get(id=user.id)
    item = SHOP.get(key)
    if item is None:
        raise GameError("این کالا وجود نداره.")
    prof = DungeonProfile.objects.select_for_update().get(pk=profile(user).pk)
    if prof.black_runes < item["price"]:
        raise GameError(f"رون سیاه کافی نداری ({item['price']} لازمه، {prof.black_runes} داری).")
    out = {"key": key, "label": item["label"]}
    if key == "key":
        if prof.keys >= KEY_HARD_CAP:
            raise GameError(f"حداکثر {KEY_HARD_CAP} کلید می‌تونی داشته باشی.")
        prof.keys += 1
    elif key in ("magical_chest", "mega_chest"):
        from game.arena_chests import grant_chest_contents

        out["chest"] = grant_chest_contents(user, "magical" if key == "magical_chest" else "mega", user.cup, source="dungeon")
    elif key == "legendary_gear":
        from game.equipment import roll_equipment

        out["item"] = roll_equipment(user, "legendary")
    elif key == "relic":
        from game import relics

        out["relic"] = relics.grant(user)
    elif key == "shadow_core":
        prof.shadow_cores += 1
    prof.black_runes -= item["price"]
    prof.save(update_fields=["black_runes", "keys", "shadow_cores"])
    return out


# ══════════════════════════ read-only view ══════════════════════════
def view(user: User) -> dict:
    """Everything a screen needs: wallet, unlock state, and the active run (if any) with each
    member's fight power against the current enemy."""
    prof = profile(user) if unlocked(user) else None
    run = active_run(user)
    out = {
        "unlocked": unlocked(user), "stage": user.campaign_stage, "unlock_stage": UNLOCK_STAGE,
        "keys": prof.keys if prof else 0, "black_runes": prof.black_runes if prof else 0,
        "runs": prof.runs if prof else 0, "clears": prof.clears if prof else 0,
        "shadow_cores": prof.shadow_cores if prof else 0,
        "best_difficulty": prof.best_difficulty if prof else 0,
        "difficulties": [{"key": k, "label": d["label"], "open": (prof.best_difficulty if prof else 0) >= d["need"]}
                         for k, d in DIFFICULTIES.items()],
        "endless_open": (prof.best_difficulty if prof else 0) >= ENDLESS_DIFF["need"],
        "week_el": weekly_element(),
        "week_favoured": week_favoured(weekly_element()),
        "run": None,
    }
    if run is not None:
        state = run.state
        members = []
        for m in state["team"]:
            row = dict(m, now_power=member_power(state, m))
            if state.get("enemy") and m["hp"] > 0:
                mine, theirs = duel(state, m, state["enemy"])
                row.update(vs=mine, enemy_vs=theirs, wins=mine >= theirs)
            members.append(row)
        out["run"] = {"id": run.id, "state": state, "members": members, "rooms": ROOMS}
        from game import serums

        out["heal_serums"] = serums.stock_of(user, "heal")
    return out


# ══════════════════════════ «بی‌انتها»: the weekly table ══════════════════════════
def record_floor(user: User, floor: int) -> dict:
    """Keep the player's deepest floor of the week. Returns {week_best, rank, new_record}."""
    from bio_lab.models import EndlessScore
    from game.season import week_key

    week = week_key()
    row, _ = EndlessScore.objects.get_or_create(user=user, week=week)
    new = floor > row.best_floor
    if new:
        row.best_floor = floor
        row.reached_at = timezone.now()
        row.save(update_fields=["best_floor", "reached_at"])
    return {"week_best": row.best_floor, "rank": _rank(row), "new_record": new}


def _rank(row) -> int:
    from bio_lab.models import EndlessScore
    from django.db.models import Q

    if row.best_floor <= 0:
        return 0
    return EndlessScore.objects.filter(week=row.week).filter(
        Q(best_floor__gt=row.best_floor) | Q(best_floor=row.best_floor, reached_at__lt=row.reached_at)).count() + 1


def board(user: User, limit: int = 10) -> dict:
    """This week's table (top `limit`), the player's own line, and last week's winners."""
    from bio_lab.models import EndlessScore, EndlessWeek
    from game.season import seconds_until_next_week, week_key

    week = week_key()
    rows = list(EndlessScore.objects.filter(week=week, best_floor__gt=0).select_related("user")
                .order_by("-best_floor", "reached_at")[:limit])
    mine = EndlessScore.objects.filter(user=user, week=week).first()
    last = EndlessWeek.objects.order_by("-week").first()
    return {
        "week": week, "left": seconds_until_next_week(),
        "rows": [{"rank": i, "user": r.user, "floor": r.best_floor} for i, r in enumerate(rows, start=1)],
        "mine": {"floor": mine.best_floor, "rank": _rank(mine)} if mine and mine.best_floor else None,
        "rewards": ENDLESS_REWARDS,
        "last": {"week": last.week, "results": last.results} if last else None,
    }


def settle_due_week() -> list[tuple[int, str]]:
    """Pay last week's top 10 (once — EndlessWeek.week is unique). Returns the DMs to send."""
    import datetime

    from django.db import IntegrityError

    from bio_lab.models import EndlessScore, EndlessWeek
    from game.season import week_key

    last_week = week_key(timezone.now() - datetime.timedelta(days=7))
    if EndlessWeek.objects.filter(week=last_week).exists():
        return []
    rows = list(EndlessScore.objects.filter(week=last_week, best_floor__gt=0)
                .order_by("-best_floor", "reached_at")[:len(ENDLESS_REWARDS)])
    dms = []
    try:
        with transaction.atomic():
            results = []
            for (rank, runes, cores), row in zip(ENDLESS_REWARDS, rows):
                prof, _ = DungeonProfile.objects.select_for_update().get_or_create(user_id=row.user_id)
                prof.black_runes += runes
                prof.shadow_cores += cores
                prof.save(update_fields=["black_runes", "shadow_cores"])
                results.append({"rank": rank, "user_id": row.user_id, "floor": row.best_floor, "runes": runes, "cores": cores})
                dms.append((row.user_id,
                            f"🌀 <b>جدول هفتگی سیاه‌چال بی‌انتها</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                            f"رتبه‌ی <b>{rank}</b> شدی با رسیدن به طبقه‌ی <code>{row.best_floor}</code>!\n"
                            f"🔮 <code>+{runes}</code> رون سیاه · 🌑 <code>+{cores}</code> هسته‌ی سایه"))
            EndlessWeek.objects.create(week=last_week, results=results)
    except IntegrityError:   # another process settled it a moment ago
        return []
    return dms

