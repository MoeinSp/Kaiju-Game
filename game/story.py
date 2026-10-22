"""«خط داستانی فصلی» — Story Chapters and Step-by-Step Quests.
Drives the top-of-dashboard active quest banner and guides players seamlessly
through the game's core progression loop.
"""

from typing import Any
from django.db import transaction

from bio_lab.models import Creature, User
from game import constants
from game.emoji import get_emoji


# ── STORY CHAPTER DEFINITIONS ───────────────────────────────────────────────

def _check_creature_level(user: User, target_lvl: int) -> tuple[int, int, bool]:
    c = Creature.objects.filter(owner=user, is_active=True).first()
    if not c:
        c = Creature.objects.filter(owner=user).order_by("-level").first()
    cur = c.level if c else 0
    return cur, target_lvl, cur >= target_lvl


def _check_hall_level(user: User, target_lvl: int) -> tuple[int, int, bool]:
    from game.buildings import main_hall_level
    cur = main_hall_level(user)
    return cur, target_lvl, cur >= target_lvl


def _check_hunts(user: User, target_count: int) -> tuple[int, int, bool]:
    from bio_lab.models import DailyActionLog
    from django.db.models import Sum
    cur = DailyActionLog.objects.filter(user=user, action="hunt").aggregate(t=Sum("count"))["t"] or 0
    return cur, target_count, cur >= target_count


def _check_arena_battles(user: User, target_count: int) -> tuple[int, int, bool]:
    from bio_lab.models import AttackLog
    cur = AttackLog.objects.filter(attacker=user).count()
    return cur, target_count, cur >= target_count


def _check_arena_cups(user: User, target_cups: int) -> tuple[int, int, bool]:
    cur = user.cup
    return cur, target_cups, cur >= target_cups


def _check_mugen_floor(user: User, target_floor: int) -> tuple[int, int, bool]:
    cur = user.mugen_tower_floor
    return cur, target_floor, cur >= target_floor


def _check_campaign_stage(user: User, target_stage: int) -> tuple[int, int, bool]:
    cur = user.campaign_stage
    return cur, target_stage, cur >= target_stage


def _check_builder_slots(user: User, target_slots: int) -> tuple[int, int, bool]:
    cur = user.builder_slots
    return cur, target_slots, cur >= target_slots


def _check_alliance(user: User, _target: int = 1) -> tuple[int, int, bool]:
    cur = 1 if user.alliance_id else 0
    return cur, 1, bool(user.alliance_id)


def _check_creatures_count(user: User, target_count: int) -> tuple[int, int, bool]:
    cur = Creature.objects.filter(owner=user).count()
    return cur, target_count, cur >= target_count


def _sum_action(user: User, action: str) -> int:
    from bio_lab.models import DailyActionLog
    from django.db.models import Sum
    return DailyActionLog.objects.filter(user=user, action=action).aggregate(t=Sum("count"))["t"] or 0


def _check_action(user: User, action: str, target: int) -> tuple[int, int, bool]:
    cur = _sum_action(user, action)
    return cur, target, cur >= target


def _check_building(user: User, building_type: str, target_lvl: int) -> tuple[int, int, bool]:
    from game.buildings import building_level
    cur = building_level(user, building_type)
    return cur, target_lvl, cur >= target_lvl


def _check_equipped(user: User, target: int) -> tuple[int, int, bool]:
    from bio_lab.models import Equipment
    cur = Equipment.objects.filter(owner=user, equipped_on__isnull=False).count()
    return cur, target, cur >= target


def _check_forged(user: User, target_lvl: int = 2) -> tuple[int, int, bool]:
    from bio_lab.models import Equipment
    done = Equipment.objects.filter(owner=user, level__gte=target_lvl).exists()
    return int(done), 1, done


def _check_research(user: User, _target: int = 1) -> tuple[int, int, bool]:
    from bio_lab.models import Research, ResearchUpgrade
    done = (Research.objects.filter(owner=user, level__gt=0).exists()
            or ResearchUpgrade.objects.filter(owner=user).exists())
    return int(done), 1, done


def _check_daily_missions(user: User, target: int) -> tuple[int, int, bool]:
    from bio_lab.models import MissionClaim
    cur = MissionClaim.objects.filter(user=user, mission_key__in=list(constants.MISSION_DEFS)).count()
    return cur, target, cur >= target


def _check_arena_chest(user: User, _target: int = 1) -> tuple[int, int, bool]:
    from bio_lab.models import DailyResourceGain
    done = DailyResourceGain.objects.filter(user=user, source="arena_chest").exists()
    return int(done), 1, done


def _check_worldboss(user: User, _target: int = 1) -> tuple[int, int, bool]:
    from bio_lab.models import WorldBossHit
    done = WorldBossHit.objects.filter(user=user, hits__gt=0).exists()
    return int(done), 1, done


def _check_star(user: User, target_star: int) -> tuple[int, int, bool]:
    best = Creature.objects.filter(owner=user).order_by("-star_level").values_list("star_level", flat=True).first() or 0
    return best, target_star, best >= target_star


_CH = {
    1: "فصل ۱: تولد",
    2: "فصل ۲: پایگاه",
    3: "فصل ۳: کارهای روزانه",
    4: "فصل ۴: آرنا",
    5: "فصل ۵: تجهیزات",
    6: "فصل ۶: قدرت",
    7: "فصل ۷: ماجراجویی",
    8: "فصل ۸: افسانه",
}


def _q(chapter, title, desc, checker, reward, cta_label, cta_callback):
    return {"chapter": chapter, "chapter_name": _CH[chapter], "title": title, "desc": desc,
            "checker": checker, "reward": reward, "cta_label": cta_label, "cta_callback": cta_callback}


# The tutorial story (rewritten 2026-10-14). ~30 small steps, each teaching ONE part of the
# game in the order a new player meets it. Diamonds were cut from 1,790 to 500 over the
# whole story (players were finishing it with ~1,900 diamonds — enough to buy or receive a
# maxed mythic); the rewards are now mostly what a beginner actually needs: gold, DNA,
# food, speed-up cards and tickets. The FIRST TWO steps must stay «hatch» and «first hunt»:
# the onboarding flow (bot/handlers/private.py) pays and skips exactly those two.
# Players who were mid-story when this changed were moved by migration 0100.
STORY_QUESTS: list[dict[str, Any]] = [
    # ── فصل ۱: تولد ──
    _q(1, "شکستن اولین تخم کایجو", "اولین کایجوی خودت رو از کپسول ژنتیکی متولد کن.",
       lambda u: (1 if Creature.objects.filter(owner=u).exists() else 0, 1, Creature.objects.filter(owner=u).exists()),
       {"coins": 1000, "dna": 200, "diamonds": 20}, "🥚 شکستن تخم", "onboarding:hatch"),
    _q(1, "اولین شکار", "با کایجوت برو شکار و غنیمت جمع کن. قبل از حمله قدرت حریف رو ببین.",
       lambda u: _check_hunts(u, 1), {"coins": 1500, "dna": 300, "diamonds": 10, "food": ("small", 3)}, "🎯 شکار", "menu:hunt"),
    _q(1, "اولین غذا", "به کایجوت غذا بده تا تجربه بگیره و سطحش بالا بره.",
       lambda u: _check_action(u, "feed", 1), {"coins": 1000, "food": ("small", 3)}, "🍖 تغذیه", "menu:feed"),
    _q(1, "کایجو سطح ۲", "با غذا یا ارتقای اندام‌ها، کایجوت رو به سطح ۲ برسون.",
       lambda u: _check_creature_level(u, 2), {"coins": 2000, "dna": 400, "speedup": 15}, "⬆️ ارتقا", "menu:upgrade"),
    # ── فصل ۲: پایگاه ──
    _q(2, "تالار مِهر", "تالار مِهر قلبِ پایگاهته؛ سطحش سقفِ بقیه‌ی ساختمون‌هاست.",
       lambda u: _check_hall_level(u, 1), {"coins": 2500, "dna": 500, "diamonds": 20}, "🏛 ساختمان‌ها", "menu:buildings"),
    _q(2, "اولین برداشت", "ساختمون‌ها خودشون منابع می‌سازن؛ یه بار ازشون برداشت کن.",
       lambda u: _check_action(u, "collect", 1), {"coins": 2000, "dna": 300, "speedup": 15}, "🏗 ساختمان‌ها", "menu:buildings"),
    _q(2, "جمع‌کننده‌ی طلا", "ساختمون «جمع‌کننده‌ی طلا» رو بساز تا حتی وقتی نیستی طلا جمع بشه.",
       lambda u: _check_building(u, "gold_collector", 1), {"coins": 3000, "dna": 500, "diamonds": 10}, "💰 ساختمان‌ها", "menu:buildings"),
    _q(2, "تالار مِهر سطح ۲", "تالار مِهر رو به سطح ۲ برسون تا ادغام، غار، اعزام و پاس فصلی باز بشن. فقط چند دقیقه طول می‌کشه.",
       lambda u: _check_hall_level(u, 2), {"coins": 5000, "dna": 1000, "diamonds": 30, "speedup": 30}, "🏛 ارتقای تالار مِهر", "menu:buildings"),
    # ── فصل ۳: کارهای روزانه ──
    _q(3, "گردونه‌ی روزانه", "هر روز یه چرخش رایگان داری؛ امروز بچرخونش.",
       lambda u: _check_action(u, "wheel_spin", 1), {"coins": 2000, "dna": 300, "food": ("medium", 1)}, "🎡 گردونه", "menu:wheel"),
    _q(3, "اولین مأموریت روزانه", "هر روز چند مأموریت ساده داری که جایزه می‌دن؛ یکیش رو کامل کن.",
       lambda u: _check_daily_missions(u, 1), {"coins": 3000, "dna": 500, "diamonds": 10}, "📋 مأموریت‌ها", "menu:missions"),
    _q(3, "اولین اعزام", "یه هیولای بیکار رو بفرست مأموریت اعزامی و چند ساعت بعد جایزه‌اش رو بگیر.",
       lambda u: _check_action(u, "dispatch", 1), {"coins": 3000, "dna": 500, "speedup": 15}, "🧭 اعزام", "menu:dispatch"),
    _q(3, "۵ بار شکار", "شکار اصلی‌ترین راه طلا و دی‌ان‌ایه؛ ۵ بار شکار برو.",
       lambda u: _check_hunts(u, 5), {"coins": 4000, "dna": 800, "diamonds": 20}, "🎯 شکار", "menu:hunt"),
    # ── فصل ۴: آرنا ──
    _q(4, "ورود به آرنا", "به یه بازیکن واقعی حمله کن؛ برنده بیشتر کاپ و غنیمت می‌بره.",
       lambda u: _check_arena_battles(u, 1), {"coins": 3000, "dna": 600, "diamonds": 10}, "⚔️ آرنا", "menu:arena"),
    _q(4, "اولین جعبه‌ی آرنا", "با هر برد توی آرنا یه جعبه می‌گیری؛ یکی رو باز کن.",
       lambda u: _check_arena_chest(u), {"coins": 3500, "dna": 700, "speedup": 30}, "📦 جعبه‌های آرنا", "menu:arena_chests"),
    _q(4, "کایجو سطح ۳", "کایجوت رو به سطح ۳ برسون.",
       lambda u: _check_creature_level(u, 3), {"coins": 3500, "dna": 700, "food": ("medium", 2)}, "⬆️ ارتقا", "menu:upgrade"),
    _q(4, "۵۰ کاپ", "توی آرنا ببر تا به ۵۰ کاپ برسی. عنصری رو انتخاب کن که بر حریف برتری داره.",
       lambda u: _check_arena_cups(u, 50), {"coins": 5000, "dna": 1000, "diamonds": 30}, "⚔️ آرنا", "menu:arena"),
    # ── فصل ۵: تجهیزات ──
    _q(5, "اولین تجهیزات", "یه تجهیزات (سلاح، زره، طلسم یا غلاف) به کایجوت بپوشون.",
       lambda u: _check_equipped(u, 1), {"coins": 4000, "dna": 800, "diamonds": 10}, "🛡 تجهیزات", "menu:inventory"),
    _q(5, "دو تجهیزات پوشیده", "هر جایگاه خالی یعنی قدرتی که از دست می‌دی؛ دو تجهیزات بپوشون.",
       lambda u: _check_equipped(u, 2), {"coins": 5000, "dna": 1000, "speedup": 30}, "🛡 تجهیزات", "menu:inventory"),
    _q(5, "کایجو سطح ۵", "کایجوت رو به سطح ۵ برسون.",
       lambda u: _check_creature_level(u, 5), {"coins": 7500, "dna": 1500, "diamonds": 20, "biocrate_tickets": 1}, "⬆️ ارتقا", "menu:upgrade"),
    # ── فصل ۶: قدرت ──
    _q(6, "طبقه‌ی ۱ برج موگن", "وارد برج موگن شو و نگهبان طبقه‌ی اول رو شکست بده.",
       lambda u: _check_mugen_floor(u, 2), {"coins": 8000, "dna": 1500, "diamonds": 20}, "🗼 برج موگن", "menu:mugen_tower"),
    _q(6, "تالار مِهر سطح ۳", "تالار مِهر رو به سطح ۳ برسون تا آهنگری، تالار ادغام و بخش‌های بیشتری باز بشن.",
       lambda u: _check_hall_level(u, 3), {"coins": 10000, "dna": 2000, "diamonds": 40, "speedup": 60}, "🏛 ارتقای تالار مِهر", "menu:buildings"),
    _q(6, "اولین آهنگری", "توی آهنگری یه تجهیزات رو ارتقا بده (حداقل +۲).",
       lambda u: _check_forged(u, 2), {"coins": 8000, "dna": 1500, "diamonds": 10}, "🔨 آهنگری", "menu:blacksmith"),
    _q(6, "اولین پژوهش", "توی آزمایشگاه یه پژوهش شروع کن؛ پژوهش‌ها همه‌ی کایجوهات رو برای همیشه قوی‌تر می‌کنن.",
       lambda u: _check_research(u), {"coins": 9000, "dna": 1800, "diamonds": 20}, "🔬 پژوهش", "menu:research"),
    # ── فصل ۷: ماجراجویی ──
    _q(7, "طبقه‌ی ۵ برج موگن", "تا طبقه‌ی ۵ برج موگن بالا برو.",
       lambda u: _check_mugen_floor(u, 6), {"coins": 12000, "dna": 2000, "diamonds": 30, "biocrate_tickets": 2}, "🗼 برج موگن", "menu:mugen_tower"),
    _q(7, "غول سرگردان", "روزی دو بار (عصر و شب) یه غول برای کل سرور پیدا می‌شه؛ حداقل یه ضربه بهش بزن.",
       lambda u: _check_worldboss(u), {"coins": 10000, "dna": 2000, "diamonds": 20}, "👹 غول سرگردان", "menu:worldboss"),
    _q(7, "دو کایجو", "با باکس‌ها، تخم یا گردونه، حداقل ۲ کایجو داشته باش.",
       lambda u: _check_creatures_count(u, 2), {"coins": 12000, "dna": 2500, "food": ("large", 1)}, "📦 باکس‌ها", "menu:biocrate"),
    # ── فصل ۸: افسانه ──
    _q(8, "اولین ادغام", "دو کایجوی هم‌نام و هم‌ستاره رو ادغام کن تا یه کایجوی ۲ ستاره بگیری.",
       lambda u: _check_star(u, 2), {"coins": 15000, "dna": 3000, "diamonds": 40}, "🔮 ادغام", "menu:fusion"),
    _q(8, "اتحاد", "به یه اتحاد بپیوند یا اتحاد خودت رو بساز؛ رید و جنگ اتحادها منتظرته.",
       lambda u: _check_alliance(u), {"coins": 20000, "dna": 3000, "diamonds": 50, "biocrate_tickets": 3}, "🤝 اتحاد", "menu:alliance_info"),
    _q(8, "کایجو سطح ۱۰", "یکی از کایجوهات رو به سطح ۱۰ برسون و افسانه‌ات رو کامل کن!",
       lambda u: _check_creature_level(u, 10), {"coins": 30000, "dna": 5000, "diamonds": 80, "biocrate_tickets": 5}, "⬆️ ارتقا", "menu:upgrade"),
]
for _i, _q_ in enumerate(STORY_QUESTS):
    _q_["id"] = _i


def format_reward_text(reward: dict[str, Any]) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"<code>+{reward['coins']:,}</code> طلا {get_emoji('coin')}")
    if reward.get("dna"):
        parts.append(f"<code>+{reward['dna']:,}</code> دی‌ان‌ای {get_emoji('dna')}")
    if reward.get("diamonds"):
        parts.append(f"<code>+{reward['diamonds']:,}</code> الماس {get_emoji('diamond')}")
    if reward.get("speedup"):
        parts.append(f"<code>{reward['speedup']}</code> دقیقه سرعت ⏱")
    if reward.get("biocrate_tickets"):
        parts.append(f"<code>{reward['biocrate_tickets']}×</code> بلیط 🎟")
    if reward.get("food"):
        tier, count = reward["food"]
        cfg = constants.XP_CAPSULES.get(tier, {})
        parts.append(f"<code>{count}×</code> {cfg.get('label', 'غذا')} {cfg.get('emoji', '🍖')}")
    return " | ".join(parts)


def active_cta_action(user: User) -> str | None:
    """The menu action the player's CURRENT story quest points at («mugen_tower» for
    «فتح طبقه ۱ برج موگن»), or None. The main-hall unlock gate lets this one section
    through — otherwise the story asked for something the menu refused to open (the
    Mugen quests come long before the hall level that unlocks the tower), and every
    later quest was stuck behind it. No query: reads only user.story_step."""
    step = getattr(user, "story_step", 0)
    if step >= len(STORY_QUESTS):
        return None
    cb = STORY_QUESTS[step]["cta_callback"]
    return cb[5:] if cb.startswith("menu:") else None


def get_active_quest(user: User) -> dict[str, Any] | None:
    """Returns the current active story quest dictionary with live progress and claim state,
    or None if all story quests are completed."""
    step = getattr(user, "story_step", 0)
    if step >= len(STORY_QUESTS):
        return None
    q = STORY_QUESTS[step]
    cur, target, is_done = q["checker"](user)
    return {
        "id": q["id"],
        "chapter": q["chapter"],
        "chapter_name": q["chapter_name"],
        "title": q["title"],
        "desc": q["desc"],
        "cur": cur,
        "target": target,
        "is_done": is_done,
        "reward": q["reward"],
        "reward_text": format_reward_text(q["reward"]),
        "cta_label": q["cta_label"],
        "cta_callback": q["cta_callback"],
        "step_num": step + 1,
        "total_steps": len(STORY_QUESTS),
    }


FEED_QUEST_TITLE = "اولین غذا"


def starter_food(user: User) -> bool:
    """«اولین غذا» asks for one feeding, and feeding needs food. The tutorial used to skip the
    steps before it without paying their food, so players reached it with none and could not
    move on (2026-10-22: 92 of 144 players on that step had no food at all). A player on that
    step with an empty pantry gets 3 small foods, once ever. Call it where the feed screen
    opens. Returns True when it gave the food."""
    step = getattr(user, "story_step", 0)
    if step >= len(STORY_QUESTS) or STORY_QUESTS[step]["title"] != FEED_QUEST_TITLE:
        return False
    from game.creature import add_capsules, capsule_counts
    from game.daily import record_action

    with transaction.atomic():
        locked = User.objects.select_for_update().get(id=user.id)
        if sum(capsule_counts(locked).values()) > 0 or _sum_action(locked, "story_food") > 0:
            return False
        add_capsules(locked, "small", 3)
        locked.save(update_fields=["xp_capsules"])
        record_action(locked, "story_food")
    user.xp_capsules = locked.xp_capsules
    return True


def claim_active_quest(user: User) -> dict[str, Any]:
    """Claims the reward of the active story quest and advances story_step."""
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        quest = get_active_quest(user)
        if not quest:
            return {"success": False, "msg": "همه مأموریت‌های داستانی تکمیل شدند!"}
        if not quest["is_done"]:
            return {"success": False, "msg": "هنوز شرایط این مأموریت کامل نشده است."}

        reward = quest["reward"]
        if reward.get("coins"):
            user.coins += reward["coins"]
        if reward.get("dna"):
            user.dna_fragments += reward["dna"]
        if reward.get("diamonds"):
            user.diamonds += reward["diamonds"]
        if reward.get("biocrate_tickets"):
            user.biocrate_tickets += reward["biocrate_tickets"]
        if reward.get("speedup"):
            from game.buildings import grant_speedup_card
            grant_speedup_card(user, reward["speedup"], count=1)
        if reward.get("food"):
            from game.creature import add_capsules
            add_capsules(user, *reward["food"])   # saved by user.save() below

        user.story_step += 1
        user.save()

        next_q = get_active_quest(user)
        return {
            "success": True,
            "claimed_quest": quest,
            "reward_text": quest["reward_text"],
            "next_quest": next_q,
        }
