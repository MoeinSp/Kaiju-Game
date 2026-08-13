import random

ELEMENTS = ["fire", "water", "earth", "electric"]

# each element deals bonus damage to the one it points to, and takes bonus damage from the one before it
ELEMENT_STRONG_AGAINST = {
    "fire": "earth",
    "earth": "electric",
    "electric": "water",
    "water": "fire",
}

ELEMENT_WORDS = {
    "fire": "آتش",
    "water": "آب",
    "earth": "خاک",
    "electric": "الکتریسیته",
}

ELEMENT_EMOJI_KEYS = {
    "fire": "element_fire",
    "water": "element_water",
    "earth": "element_earth",
    "electric": "element_electric",
}

# plain-unicode labels — kept for contexts that can't render <tg-emoji> (button text,
# non-HTML messages). Prefer element_label() in any HTML message body.
ELEMENT_LABELS = {
    "fire": "🔥 آتش",
    "water": "💧 آب",
    "earth": "🪨 خاک",
    "electric": "⚡ الکتریسیته",
}


def element_label(element: str) -> str:
    """Owner-customizable element label for HTML message bodies."""
    from game.emoji import get_emoji

    return f"{get_emoji(ELEMENT_EMOJI_KEYS[element])} {ELEMENT_WORDS[element]}"

SPECIES_NAMES = {
    "fire": ["Emberling", "Cindrax", "Pyrofang"],
    "water": ["Hydrolarva", "Tidewhelp", "Aquafin"],
    "earth": ["Stoneback", "Terrapup", "Boulderkin"],
    "electric": ["Voltling", "Sparkjaw", "Thundrix"],
}

STRONG_MULTIPLIER = 1.3
WEAK_MULTIPLIER = 0.7

RARITY_ORDER = ["common", "rare", "epic", "legendary", "mythic"]

RARITY_LABELS = {
    "common": "⚪ معمولی",
    "rare": "🔵 نایاب",
    "epic": "🟣 حماسی",
    "legendary": "🟡 افسانه‌ای",
    "mythic": "🔴 اساطیری",
}

RARITY_STAT_MULTIPLIER = {
    "common": 1.0,
    "rare": 1.15,
    "epic": 1.35,
    "legendary": 1.6,
    "mythic": 2.0,
}

# chance that a fusion result upgrades one tier above the higher-rarity parent
RARITY_UPGRADE_CHANCE = {
    "common": 0.25,
    "rare": 0.15,
    "epic": 0.08,
    "legendary": 0.03,
}

# odds when opening a Bio-Crate (game/lootbox.py) — a completely separate roll from
# fusion's upgrade chance above, spec'd independently by the economy design
LOOTBOX_RARITY_WEIGHTS = {
    "common": 70,
    "rare": 20,
    "epic": 7,
    "legendary": 2.5,
    "mythic": 0.5,
}

STARTER_BASE_HP = 50
STARTER_BASE_ATK = 10
STARTER_BASE_DEF = 10
STARTER_BASE_SPD = 10

DUEL_WIN_COINS = 30
DUEL_WIN_XP = 20
DUEL_LOSE_XP = 5

GIVE_RESOURCE_ALIASES = {
    "coins": "coins",
    "coin": "coins",
    "gold": "coins",
    "سکه": "coins",
    "طلا": "coins",
    "dna": "dna_fragments",
    "dnas": "dna_fragments",
    "دی‌ان‌ای": "dna_fragments",
}
GIVE_RESOURCE_LABELS = {"coins": "طلا", "dna_fragments": "DNA"}

MUTATION_EVENT_STAT_LABELS = {
    "base_hp": "❤️ HP",
    "base_atk": "⚔️ ATK",
    "base_def": "🛡 DEF",
    "base_spd": "💨 SPD",
}
MUTATION_EVENT_HP_BONUS = (2, 6)
MUTATION_EVENT_OTHER_BONUS = (1, 3)

# interactive skill-based duel (the "advanced" battle mode, as opposed to /duel's
# instant auto-resolve — used when both players want to play the fight out live)
SKILL_USES_PER_BATTLE = 2
BATTLE_CRIT_CHANCE = 0.12
BATTLE_CRIT_MULTIPLIER = 1.5

ELEMENT_SKILLS = {
    "fire": {"name": "🔥 گلوله آتشین", "desc": "دمیج ویرانگر مضاعف", "power_mult": 2.0},
    "water": {"name": "💧 موج شفا", "desc": "۲۵٪ HP ماکسیمم رو ترمیم می‌کنه", "heal_pct": 0.25},
    "earth": {"name": "🪨 دیوار سنگی", "desc": "نیمی از ضربه‌ی بعدی حریف رو خنثی می‌کنه", "shield_pct": 0.5},
    "electric": {"name": "⚡ شوک برق", "desc": "حریف یک نوبت برق می‌گیره و از دست می‌ده", "stun": True},
}

# daily caps for actions with no natural cooldown of their own (guardian stipend is a
# once-a-day claim, not a grindable action, so a flat daily cap fits it — feed/raid_attack
# use the regenerating energy pool below instead, which encourages checking back
# throughout the day rather than dumping everything in one sitting)
ENERGY_CAPS = {
    "guardian_stipend": 1,
}

GUARDIAN_STIPEND_COINS = 25
GUARDIAN_STIPEND_DNA = 3

# regenerating stamina pool spent on feed/raid_attack — refills over real time instead
# of resetting once a day, so there's a reason to come back every couple hours
MAX_ENERGY = 20
ENERGY_REGEN_MINUTES = 12  # empty -> full in 4 hours
FEED_ENERGY_COST = 1
RAID_ATTACK_ENERGY_COST = 1
HUNT_ENERGY_COST = 1

# consecutive daily /start streak — resets if a day is missed, capped so late-game
# players don't snowball into absurd payouts
LOGIN_STREAK_BASE_COINS = 15
LOGIN_STREAK_COINS_PER_DAY = 10
LOGIN_STREAK_CAP_DAYS = 14
LOGIN_STREAK_DNA_EVERY = 5  # bonus DNA every N-day milestone
LOGIN_STREAK_DNA_BONUS = 10

# key -> {action, target, label, coins, dna}
MISSION_DEFS = {
    "feed_3": {"action": "feed", "target": 3, "label": "۳ بار تغذیه کن", "coins": 40, "dna": 0},
    "feed_10": {"action": "feed", "target": 10, "label": "۱۰ بار تغذیه کن", "coins": 100, "dna": 5},
    "train_1": {"action": "train", "target": 1, "label": "۱ بار تمرین کن", "coins": 30, "dna": 0},
    "duel_win_1": {"action": "duel_win", "target": 1, "label": "۱ دوئل ببر", "coins": 50, "dna": 5},
    "duel_win_3": {"action": "duel_win", "target": 3, "label": "۳ دوئل ببر", "coins": 120, "dna": 10},
    "raid_attack_2": {"action": "raid_attack", "target": 2, "label": "۲ بار به رید حمله کن", "coins": 40, "dna": 5},
    "raid_attack_5": {"action": "raid_attack", "target": 5, "label": "۵ بار به رید حمله کن", "coins": 90, "dna": 8},
    "fusion_1": {"action": "fusion", "target": 1, "label": "۱ بار فیوژن کن", "coins": 60, "dna": 0},
    "guardian_challenge_1": {
        "action": "guardian_challenge",
        "target": 1,
        "label": "۱ بار برای محافظ گروه چالش بده",
        "coins": 50,
        "dna": 5,
    },
    "hunt_3": {"action": "hunt", "target": 3, "label": "۳ بار شکار انفرادی کن", "coins": 45, "dna": 3},
}

STARTING_COINS = 200

FEED_COST_COINS = 20
FEED_XP_GAIN = 15

TRAIN_COOLDOWN_HOURS = 4
TRAIN_XP_GAIN = 40

XP_PER_LEVEL = 100

LEVEL_UP_HP = 10
LEVEL_UP_ATK = 2
LEVEL_UP_DEF = 2
LEVEL_UP_SPD = 1

BODY_PARTS = {
    "wings": {"label": "🦋 بال‌ها (سرعت)", "stat": "spd", "bonus": 2},
    "armor": {"label": "🛡 زره (دفاع)", "stat": "def", "bonus": 3},
    "fangs": {"label": "🦷 نیش (حمله)", "stat": "atk", "bonus": 3},
    "poison": {"label": "☠️ غدد سمی (زهر هر راند)", "stat": "poison", "bonus": 1},
}

# ── Equipment ────────────────────────────────────────────────────────────────
# 4 slots per creature; at most one equipped item per slot (enforced in
# game/equipment.py, not at the DB level, since a slot is a property of *where*
# an Equipment row is equipped, not a fixed column on Creature).
EQUIPMENT_SLOTS = ["weapon", "armor", "rune", "offhand"]

EQUIPMENT_SLOT_LABELS = {
    "weapon": "⚔️ سلاح",
    "armor": "🛡 زره",
    "rune": "💍 طلسم",
    "offhand": "🧪 غلاف",
}

EQUIPMENT_TEMPLATES = {
    "weapon": ["پنجه‌های فولادی", "شمشیر لیزری", "تبر استخوانی"],
    "armor": ["زره تیتانیومی", "فلس‌های اژدها", "سپر انرژی"],
    "rune": ["حلقه سم", "گردنبند خون‌خوار"],
    "offhand": ["غدد اسیدی", "پرتاب‌کننده آتش"],
}

# base bonus at rarity=common, level=1 — scales up by RARITY_STAT_MULTIPLIER and by
# EQUIPMENT_UPGRADE_BONUS_PCT per +level. "poison" reuses the creature poison-gland
# stat as the DoT mechanic instead of inventing a parallel damage-over-time system.
EQUIPMENT_BASE_BONUS = {
    "weapon": {"atk": 4, "crit_rate": 0.03},
    "armor": {"hp": 15, "def": 4},
    "rune": {"spd": 3, "lifesteal": 0.03},
    "offhand": {"poison": 2},
}

EQUIPMENT_MAX_LEVEL = 10
EQUIPMENT_UPGRADE_BONUS_PCT = 0.15  # each +level adds 15% on top of the base bonus
EQUIPMENT_UPGRADE_GOLD_COST = 40  # per +level, scaled by current level in upgrade_cost-style formula
EQUIPMENT_DUPES_TO_UPGRADE = 1  # duplicate equipment (same slot+template+rarity) consumed per +level

BASE_CRIT_CHANCE = 0.10
BASE_LIFESTEAL = 0.0

# ── Economy: loot boxes, fusion, wagered duels, alliance heist ────────────────
BIOCRATE_GOLD_COST = 150
BIOCRATE_CREATURE_CHANCE = 0.5  # else yields an equipment piece

FUSION_GOLD_COST = 120
FUSION_INHERIT_CHANCE = 0.5  # child inherits one random equipped item from a parent

DUEL_WAGER_MAX = 500

HEIST_STEAL_PERCENT = 0.20
HEIST_COOLDOWN_HOURS = 6
HEIST_DAILY_ATTEMPTS = 3
ENERGY_CAPS["heist"] = HEIST_DAILY_ATTEMPTS


def upgrade_cost(current_level: int) -> int:
    return 50 * (current_level + 1)


def random_element() -> str:
    return random.choice(ELEMENTS)


def random_species_name(element: str) -> str:
    return random.choice(SPECIES_NAMES[element])


def element_multiplier(attacker_element: str, defender_element: str) -> float:
    if ELEMENT_STRONG_AGAINST[attacker_element] == defender_element:
        return STRONG_MULTIPLIER
    if ELEMENT_STRONG_AGAINST[defender_element] == attacker_element:
        return WEAK_MULTIPLIER
    return 1.0


def next_rarity(rarity: str) -> str:
    idx = RARITY_ORDER.index(rarity)
    return RARITY_ORDER[min(idx + 1, len(RARITY_ORDER) - 1)]


def higher_rarity(rarity_a: str, rarity_b: str) -> str:
    return rarity_a if RARITY_ORDER.index(rarity_a) >= RARITY_ORDER.index(rarity_b) else rarity_b


def render_bar(current: int, total: int, width: int = 10) -> str:
    total = max(total, 1)
    filled = min(width, max(0, round(width * max(current, 0) / total)))
    return "▓" * filled + "░" * (width - filled)
