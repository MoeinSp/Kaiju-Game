import random

ELEMENTS = ["fire", "water", "earth", "electric"]

# each element deals bonus damage to the one it points to, and takes bonus damage from the one before it
ELEMENT_STRONG_AGAINST = {
    "fire": "earth",
    "earth": "electric",
    "electric": "water",
    "water": "fire",
}

ELEMENT_LABELS = {
    "fire": "🔥 آتش",
    "water": "💧 آب",
    "earth": "🪨 خاک",
    "electric": "⚡ الکتریسیته",
}

SPECIES_NAMES = {
    "fire": ["Emberling", "Cindrax", "Pyrofang"],
    "water": ["Hydrolarva", "Tidewhelp", "Aquafin"],
    "earth": ["Stoneback", "Terrapup", "Boulderkin"],
    "electric": ["Voltling", "Sparkjaw", "Thundrix"],
}

STRONG_MULTIPLIER = 1.3
WEAK_MULTIPLIER = 0.7

RARITY_ORDER = ["common", "rare", "epic", "legendary", "mutant"]

RARITY_LABELS = {
    "common": "⚪ معمولی",
    "rare": "🔵 نایاب",
    "epic": "🟣 حماسی",
    "legendary": "🟡 افسانه‌ای",
    "mutant": "🔴 جهش‌یافته",
}

RARITY_STAT_MULTIPLIER = {
    "common": 1.0,
    "rare": 1.15,
    "epic": 1.35,
    "legendary": 1.6,
    "mutant": 2.0,
}

# chance that a splice result upgrades one tier above the higher-rarity parent
RARITY_UPGRADE_CHANCE = {
    "common": 0.25,
    "rare": 0.15,
    "epic": 0.08,
    "legendary": 0.03,
}

STARTER_BASE_HP = 50
STARTER_BASE_ATK = 10
STARTER_BASE_DEF = 10
STARTER_BASE_SPD = 10

SPLICE_DNA_COST = 30

DUEL_WIN_COINS = 30
DUEL_WIN_XP = 20
DUEL_LOSE_XP = 5

GIVE_RESOURCE_ALIASES = {
    "coins": "coins",
    "coin": "coins",
    "سکه": "coins",
    "dna": "dna_fragments",
    "dnas": "dna_fragments",
    "دی‌ان‌ای": "dna_fragments",
}
GIVE_RESOURCE_LABELS = {"coins": "سکه", "dna_fragments": "DNA"}

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

# daily action caps — prevents infinite grinding of actions with no natural cooldown
ENERGY_CAPS = {
    "feed": 8,
    "raid_attack": 12,
}

# key -> {action, target, label, coins, dna}
MISSION_DEFS = {
    "feed_3": {"action": "feed", "target": 3, "label": "۳ بار تغذیه کن", "coins": 40, "dna": 0},
    "train_1": {"action": "train", "target": 1, "label": "۱ بار تمرین کن", "coins": 30, "dna": 0},
    "duel_win_1": {"action": "duel_win", "target": 1, "label": "۱ دوئل ببر", "coins": 50, "dna": 5},
    "raid_attack_2": {"action": "raid_attack", "target": 2, "label": "۲ بار به رید حمله کن", "coins": 40, "dna": 5},
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
