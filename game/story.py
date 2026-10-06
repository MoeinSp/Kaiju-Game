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


STORY_QUESTS: list[dict[str, Any]] = [
    # ── CHAPTER 1: بیداری کایجو ──
    {
        "id": 0,
        "chapter": 1,
        "chapter_name": "فصل ۱: بیداری هیولا",
        "title": "شکستن اولین تخم کایجو",
        "desc": "اولین کایجوی خودت رو از کپسول ژنتیکی متولد کن.",
        "checker": lambda u: (1 if Creature.objects.filter(owner=u).exists() else 0, 1, Creature.objects.filter(owner=u).exists()),
        "reward": {"coins": 1000, "dna": 200, "diamonds": 50},
        "cta_label": "🥚 شکستن تخم",
        "cta_callback": "onboarding:hatch",
    },
    {
        "id": 1,
        "chapter": 1,
        "chapter_name": "فصل ۱: بیداری هیولا",
        "title": "اولین شکار در جنگل تاریک",
        "desc": "با کایجوی خودت به یک شکار برو و غنایم جمع کن.",
        "checker": lambda u: _check_hunts(u, 1),
        "reward": {"coins": 1500, "dna": 300, "diamonds": 30},
        "cta_label": "🏹 رفتن به شکار",
        "cta_callback": "menu:hunt",
    },
    {
        "id": 2,
        "chapter": 1,
        "chapter_name": "فصل ۱: بیداری هیولا",
        "title": "ارتقای کایجو به سطح ۲",
        "desc": "با تغذیه یا ارتقای اجزای بدن، کایجوی خودت رو به سطح ۲ برسون.",
        "checker": lambda u: _check_creature_level(u, 2),
        "reward": {"coins": 2000, "dna": 400, "speedup": 15},
        "cta_label": "⚒ ارتقای هیولا",
        "cta_callback": "menu:upgrade",
    },
    {
        "id": 3,
        "chapter": 1,
        "chapter_name": "فصل ۱: بیداری هیولا",
        "title": "تأسیس تالار مِهر (سطح ۱)",
        "desc": "ساختمان تالار اصلی پایگاهت رو بررسی و آماده کن.",
        "checker": lambda u: _check_hall_level(u, 1),
        "reward": {"coins": 2500, "dna": 500, "diamonds": 50},
        "cta_label": "🏗 ساختمان‌های پایگاه",
        "cta_callback": "menu:buildings",
    },

    # ── CHAPTER 2: نبردهای میدان و شجاعت ──
    {
        "id": 4,
        "chapter": 2,
        "chapter_name": "فصل ۲: نبردهای آرنا",
        "title": "ورود به میدان آرنا",
        "desc": "اولین مبارزه خودت رو در آرنا با کایجوی بازیکنان دیگر انجام بده.",
        "checker": lambda u: _check_arena_battles(u, 1),
        "reward": {"coins": 3000, "dna": 600, "diamonds": 50},
        "cta_label": "🏟 رفتن به آرنا",
        "cta_callback": "menu:arena",
    },
    {
        "id": 5,
        "chapter": 2,
        "chapter_name": "فصل ۲: نبردهای آرنا",
        "title": "ارتقای کایجو به سطح ۳",
        "desc": "قدرت کایجوت رو افزایش بده و سطحش رو به ۳ برسون.",
        "checker": lambda u: _check_creature_level(u, 3),
        "reward": {"coins": 3500, "dna": 700, "speedup": 30},
        "cta_label": "⚒ ارتقای کایجو",
        "cta_callback": "menu:upgrade",
    },
    {
        "id": 6,
        "chapter": 2,
        "chapter_name": "فصل ۲: نبردهای آرنا",
        "title": "کسب ۵۰ کاپ در آرنا",
        "desc": "در نبردهای آرنا پیروز شو و به ۵۰ کاپ افتخار برس.",
        "checker": lambda u: _check_arena_cups(u, 50),
        "reward": {"coins": 4000, "diamonds": 100, "speedup": 30},
        "cta_label": "🏟 نبرد در آرنا",
        "cta_callback": "menu:arena",
    },

    # ── CHAPTER 3: امپراتوری پایگاه و منابع ──
    {
        "id": 7,
        "chapter": 3,
        "chapter_name": "فصل ۳: امپراتوری پایگاه",
        "title": "ارتقای تالار مِهر به سطح ۲",
        "desc": "ساختمان تالار اصلی رو به سطح ۲ ارتقا بده تا امکانات جدید باز بشن.",
        "checker": lambda u: _check_hall_level(u, 2),
        "reward": {"coins": 5000, "dna": 1000, "diamonds": 80},
        "cta_label": "🏗 ارتقای ساختمان",
        "cta_callback": "menu:buildings",
    },
    {
        "id": 8,
        "chapter": 3,
        "chapter_name": "فصل ۳: امپراتوری پایگاه",
        "title": "۵ بار شکار",
        "desc": "در جنگل به ۵ شکار برو تا منابع لازم برای رشد پایگاه رو کسب کنی.",
        "checker": lambda u: _check_hunts(u, 5),
        "reward": {"coins": 6000, "dna": 1200, "diamonds": 60},
        "cta_label": "🏹 رفتن به شکار",
        "cta_callback": "menu:hunt",
    },
    {
        "id": 9,
        "chapter": 3,
        "chapter_name": "فصل ۳: امپراتوری پایگاه",
        "title": "ارتقای کایجو به سطح ۵",
        "desc": "کایجوت رو به سطح ۵ برسون تا مهارت‌هاش شکوفا بشن.",
        "checker": lambda u: _check_creature_level(u, 5),
        "reward": {"coins": 7500, "diamonds": 100, "biocrate_tickets": 1},
        "cta_label": "⚒ ارتقای کایجو",
        "cta_callback": "menu:upgrade",
    },

    # ── CHAPTER 4: صعود به برج موگن ──
    {
        "id": 10,
        "chapter": 4,
        "chapter_name": "فصل ۴: صعود به برج موگن",
        "title": "فتح طبقه ۱ برج موگن",
        "desc": "وارد سیاه‌چال بی‌پایان برج موگن شو و طبقه اول رو فتح کن.",
        "checker": lambda u: _check_mugen_floor(u, 2),
        "reward": {"coins": 8000, "dna": 1500, "diamonds": 100},
        "cta_label": "🏰 ورود به برج موگن",
        "cta_callback": "menu:mugen_tower",
    },
    {
        "id": 11,
        "chapter": 4,
        "chapter_name": "فصل ۴: صعود به برج موگن",
        "title": "ارتقای تالار مِهر به سطح ۳",
        "desc": "تالار پایگاه رو به سطح ۳ برسون تا تالار ادغام و غار پرورش در دسترس باشن.",
        "checker": lambda u: _check_hall_level(u, 3),
        "reward": {"coins": 10000, "dna": 2000, "diamonds": 120},
        "cta_label": "🏗 ارتقای تالار",
        "cta_callback": "menu:buildings",
    },
    {
        "id": 12,
        "chapter": 4,
        "chapter_name": "فصل ۴: صعود به برج موگن",
        "title": "فتح طبقه ۵ برج موگن",
        "desc": "تا طبقه ۵ برج موگن بالا برو و باس‌های نگهبان رو شکست بده.",
        "checker": lambda u: _check_mugen_floor(u, 6),
        "reward": {"coins": 12000, "diamonds": 150, "biocrate_tickets": 2},
        "cta_label": "🏰 صعود در موگن",
        "cta_callback": "menu:mugen_tower",
    },

    # ── CHAPTER 5: ژنتیک پیشرفته و اتحاد ──
    {
        "id": 13,
        "chapter": 5,
        "chapter_name": "فصل ۵: ژنتیک پیشرفته و اتحاد",
        "title": "داشتن حداقل ۲ کایجو در کلکسیون",
        "desc": "با باز کردن باکس‌های ژنتیکی یا تخم، ۲ کایجو در آزمایشگاه داشته باش.",
        "checker": lambda u: _check_creatures_count(u, 2),
        "reward": {"coins": 15000, "dna": 2500, "diamonds": 150},
        "cta_label": "📦 باز کردن باکس",
        "cta_callback": "menu:biocrate",
    },
    {
        "id": 14,
        "chapter": 5,
        "chapter_name": "فصل ۵: ژنتیک پیشرفته و اتحاد",
        "title": "عضویت یا ساخت اتحاد",
        "desc": "به یک اتحاد بپیوند یا اتحاد خودت رو برای جنگ‌های گروهی بساز.",
        "checker": lambda u: _check_alliance(u),
        "reward": {"coins": 20000, "dna": 3000, "diamonds": 250, "biocrate_tickets": 3},
        "cta_label": "🤝 بخش اتحادها",
        "cta_callback": "menu:alliance_info",
    },
    {
        "id": 15,
        "chapter": 5,
        "chapter_name": "فصل ۵: ژنتیک پیشرفته و اتحاد",
        "title": "ارتقای کایجو به سطح ۱۰ (افسانه)",
        "desc": "یکی از کایجوهای خودت رو به سطح ۱۰ برسون تا اسطوره بشه!",
        "checker": lambda u: _check_creature_level(u, 10),
        "reward": {"coins": 30000, "diamonds": 500, "biocrate_tickets": 5},
        "cta_label": "⚒ ارتقای کایجو",
        "cta_callback": "menu:upgrade",
    },
]


# «ارتقای تالار مِهر به سطح ۲» used to be the 8th quest, behind 50 arena cups — but hall
# level 2 is what opens fusion, the cave, dispatch and the season pass, and 86% of players
# never reached it. It now comes right after the tutorial steps (and the upgrade itself
# takes minutes — constants.MAIN_HALL_LEVEL2_MINUTES). A player who was mid-story when the
# order changed at worst re-claims one small quest they had already finished.
_hall2 = next(q for q in STORY_QUESTS if q["title"] == "ارتقای تالار مِهر به سطح ۲")
STORY_QUESTS.remove(_hall2)
STORY_QUESTS.insert(4, {**_hall2, "chapter": 2, "chapter_name": "فصل ۲: نبردهای آرنا",
                        "cta_label": "🏛 ارتقای تالار مِهر",
                        "desc": "تالار مِهر رو به سطح ۲ برسون تا ادغام، غار، مأموریت اعزامی و پاس فصلی باز بشن. فقط چند دقیقه طول می‌کشه."})
for _i, _q in enumerate(STORY_QUESTS):
    _q["id"] = _i


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

        user.story_step += 1
        user.save()

        next_q = get_active_quest(user)
        return {
            "success": True,
            "claimed_quest": quest,
            "reward_text": quest["reward_text"],
            "next_quest": next_q,
        }
