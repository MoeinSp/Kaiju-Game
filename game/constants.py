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

# Names drawn from Persian myth (Shahnameh / Avestan lore) rather than invented
# words. **SPECIES is the single source of truth and the mapping is strictly
# one-to-one**: a name determines its element, and no name appears under two
# elements. That's not cosmetic — `name` is the fusion identity key (two creatures
# only fuse if their names match), so a name shared by two different species would
# let unrelated creatures fuse together.
SPECIES = {
    # fire — آتش و اژدها
    "سیمرغ": "fire",
    "اژدهاک": "fire",
    "آذرگشسب": "fire",
    "ضحاک": "fire",
    "فرنبغ": "fire",
    # water — آب و دریا
    "اپم‌نپات": "water",
    "آناهیتا": "water",
    "تیشتر": "water",
    "کرکس دریا": "water",
    "ماهی‌ور": "water",
    # earth — خاک و کوه
    "کرکدان": "earth",
    "البرزکوه": "earth",
    "اسپندارمذ": "earth",
    "گاوبرمایه": "earth",
    "سنگ‌دیو": "earth",
    # electric — رعد و باد
    "بهرام": "electric",
    "وایو": "electric",
    "هما": "electric",
    "رخش": "electric",
    "شهباز": "electric",
}

# derived view: element -> [names]. Built from SPECIES so the two can never drift.
SPECIES_NAMES: dict[str, list[str]] = {}
for _name, _element in SPECIES.items():
    SPECIES_NAMES.setdefault(_element, []).append(_name)


def species_element(name: str) -> str | None:
    """The element a species name belongs to, or None if it isn't a known species
    (e.g. a creature created before the registry existed)."""
    return SPECIES.get(name)

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

DUEL_WIN_COINS = 30  # legacy flat reward; free duels now use duel_win_reward() below
DUEL_WIN_XP = 20
DUEL_LOSE_XP = 5

# A free «دوئل» now costs energy (like every other combat action) and pays a reward
# that SCALES with the opponent you beat — flat 30 gold that ignored the fight and
# cost nothing was the complaint. Reward keys off the loser's level, so beating a
# stronger player is worth more, and there's a small DNA cut on top.
DUEL_ENERGY_COST = 1
DUEL_WIN_COINS_BASE = 20
DUEL_WIN_COINS_PER_OPP_LEVEL = 7
DUEL_WIN_COINS_CAP = 300
DUEL_WIN_XP_BASE = 15
DUEL_WIN_XP_PER_OPP_LEVEL = 3
DUEL_WIN_DNA_PER_OPP_LEVEL = 0.5  # e.g. beating a level-10 opponent → 5 DNA


def duel_win_reward(opponent_level: int) -> dict:
    """Scaled reward for winning a free duel against a creature at `opponent_level`."""
    lvl = max(1, opponent_level)
    return {
        "coins": min(DUEL_WIN_COINS_CAP, DUEL_WIN_COINS_BASE + lvl * DUEL_WIN_COINS_PER_OPP_LEVEL),
        "xp": DUEL_WIN_XP_BASE + lvl * DUEL_WIN_XP_PER_OPP_LEVEL,
        "dna": int(lvl * DUEL_WIN_DNA_PER_OPP_LEVEL),
    }

# only GOLD is transferable between players — DNA and diamonds are not, to keep
# the premium/genetic economies from being farmed across throwaway accounts
GIVE_RESOURCE_ALIASES = {
    "coins": "coins",
    "coin": "coins",
    "gold": "coins",
    "سکه": "coins",
    "طلا": "coins",
}
GIVE_RESOURCE_LABELS = {"coins": "طلا", "dna_fragments": "DNA", "diamonds": "الماس"}

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
MAX_ENERGY = 50
ENERGY_REGEN_MINUTES = 6   # empty -> full in 5 hours
ENERGY_REFILL_DIAMOND_COST = 15  # diamonds to instantly refill energy to full
FEED_ENERGY_COST = 1
RAID_ATTACK_ENERGY_COST = 1
GUARDIAN_CHALLENGE_ENERGY_COST = 1
HUNT_ENERGY_COST = 1
# a group «اتک» on a player loots this share of the loser's gold (capped), instead
# of a flat reward
GROUP_ATTACK_LOOT_PERCENT = 0.10
GROUP_ATTACK_LOOT_CAP = 500

# consecutive daily /start streak — resets if a day is missed, capped so late-game
# players don't snowball into absurd payouts
LOGIN_STREAK_BASE_COINS = 15
LOGIN_STREAK_COINS_PER_DAY = 10
LOGIN_STREAK_CAP_DAYS = 14
LOGIN_STREAK_DNA_EVERY = 5  # bonus DNA every N-day milestone
LOGIN_STREAK_DNA_BONUS = 10

# key -> {action, target, label, coins, dna}
# Missions may pay "speedup": <minutes> on top of coins/dna — that's the main way
# a player keeps a stock of build-timer cards, since they aren't sold anywhere.
MISSION_DEFS = {
    "feed_3": {"action": "feed", "target": 3, "label": "۳ بار تغذیه کن", "coins": 40, "dna": 0},
    "feed_10": {
        "action": "feed", "target": 10, "label": "۱۰ بار تغذیه کن", "coins": 100, "dna": 5, "speedup": 5,
    },
    "train_1": {"action": "train", "target": 1, "label": "۱ بار تمرین کن", "coins": 30, "dna": 0, "speedup": 1},
    "duel_win_1": {"action": "duel_win", "target": 1, "label": "۱ دوئل ببر", "coins": 50, "dna": 5},
    "duel_win_3": {
        "action": "duel_win", "target": 3, "label": "۳ دوئل ببر", "coins": 120, "dna": 10, "speedup": 30,
    },
    "raid_attack_2": {"action": "raid_attack", "target": 2, "label": "۲ بار به رید حمله کن", "coins": 40, "dna": 5},
    "raid_attack_5": {
        "action": "raid_attack", "target": 5, "label": "۵ بار به رید حمله کن", "coins": 90, "dna": 8, "speedup": 30,
    },
    "fusion_1": {"action": "fusion", "target": 1, "label": "۱ بار فیوژن کن", "coins": 60, "dna": 0, "speedup": 5},
    "guardian_challenge_1": {
        "action": "guardian_challenge",
        "target": 1,
        "label": "۱ بار برای محافظ گروه چالش بده",
        "coins": 50,
        "dna": 5,
    },
    "hunt_3": {
        "action": "hunt", "target": 3, "label": "۳ بار شکار انفرادی کن", "coins": 45, "dna": 3, "speedup": 5,
    },
    "hunt_10": {
        "action": "hunt", "target": 10, "label": "۱۰ بار شکار انفرادی کن", "coins": 150, "dna": 10, "speedup": 60,
    },
    "arena_attack_3": {
        "action": "arena_attack",
        "target": 3,
        "label": "۳ بار توی آرنا حمله کن",
        "coins": 70,
        "dna": 5,
        "speedup": 30,
    },
    "collect_5": {
        "action": "collect",
        "target": 5,
        "label": "۵ بار از ساختمون‌ها جمع‌آوری کن",
        "coins": 60,
        "dna": 5,
        "speedup": 5,
    },
}

# Starter pack of build-timer cards. The early main-hall upgrades are the slowest
# part of a new player's first session, so they get enough cards to blow through
# the first couple of them instead of staring at a countdown.
STARTING_SPEEDUP_CARDS = {5: 4, 30: 3, 60: 2, 720: 1}

# generous welcome package — a new player should be able to build, forge, and open
# a diamond box on day one instead of grinding before the game opens up
STARTING_COINS = 5000
STARTING_DNA = 100
STARTING_DIAMONDS = 100

# The first lab name is set free at /start. Changing it afterwards costs diamonds,
# and each rename costs more than the last so a name isn't churned casually:
# rename #1 = 100, #2 = 200, #3 = 300, …
LAB_RENAME_BASE_COST = 100


def lab_rename_cost(renames_done: int) -> int:
    return LAB_RENAME_BASE_COST * (max(0, renames_done) + 1)


FEED_COST_COINS = 20
FEED_XP_GAIN = 15

TRAIN_COOLDOWN_HOURS = 4
TRAIN_XP_GAIN = 40

# Creature level-up XP scales with the level so deep levels are a real grind.
# It used to be a flat 100 per level, which made level 30 as cheap to reach as
# level 2 and let a fed creature snowball forever. The curve is pinned so that
# the very first level-up (1 -> 2) still costs the old 100 — early game is
# unchanged — and then climbs: ~400 at level 5, ~1000 at level 10, ~2750 at
# level 20. Growth is super-linear (a linear term plus an exponential one) so
# each level costs strictly more than the last.
CREATURE_XP_BASE = 70
CREATURE_XP_LINEAR = 30
CREATURE_XP_EXPONENT = 1.5


def xp_for_creature_level(level: int) -> int:
    """XP needed to advance FROM `level` to `level`+1. Strictly increasing, so
    leveling a creature deep is a long-term investment rather than a formality."""
    return round(CREATURE_XP_BASE + CREATURE_XP_LINEAR * max(1, level) ** CREATURE_XP_EXPONENT)


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

# Each body part can only be upgraded up to a cap set by the creature's STAR level:
# 1★ → 20, 2★ → 40, … 5★ → 100 (the absolute max). Raising the cap needs fusion
# (more stars), so gear/part power can't outrun a creature's prestige tier.
PART_UPGRADE_CAP_PER_STAR = 20
PART_UPGRADE_MAX = PART_UPGRADE_CAP_PER_STAR * 5  # 100, at 5★


def part_upgrade_cap(star_level: int) -> int:
    return max(1, star_level) * PART_UPGRADE_CAP_PER_STAR

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

# How each bonus stat is written to a player. `is_percent` matters: crit_rate and
# lifesteal are stored as fractions (0.03), and printing those raw would show a
# "+0.03 crit" that reads like a rounding error rather than +3%.
EQUIPMENT_BONUS_LABELS = {
    "atk": ("حمله", False),
    "hp": ("جان", False),
    "def": ("دفاع", False),
    "spd": ("سرعت", False),
    "poison": ("زهر", False),
    "crit_rate": ("کریتیکال", True),
    "lifesteal": ("جون‌خواری", True),
}

# absolute ceiling (blacksmith level 5 x 5 levels each); the *effective* cap for a
# given player is game.blacksmith.equipment_cap(), based on their forge's level
EQUIPMENT_MAX_LEVEL = 25
EQUIPMENT_UPGRADE_BONUS_PCT = 0.15  # each +level adds 15% on top of the base bonus
EQUIPMENT_UPGRADE_GOLD_COST = 40  # per +level, scaled by current level in upgrade_cost-style formula
# same-slot equipment fusion (any sword into any sword): flexible but risky, unlike
# the risk-free exact-duplicate upgrade. Better/rarer sacrifices improve the odds.
EQUIPMENT_FUSE_FAIL_CHANCE = 0.35
EQUIPMENT_DUPES_TO_UPGRADE = 1  # duplicate equipment (same slot+template+rarity) consumed per +level

BASE_CRIT_CHANCE = 0.10
BASE_LIFESTEAL = 0.0

# ── Economy: loot boxes, fusion, wagered duels, alliance heist ────────────────
BIOCRATE_GOLD_COST = 250
BIOCRATE_DNA_COST = 15  # a biocrate now also costs DNA, giving DNA a real everyday use
BIOCRATE_CREATURE_CHANCE = 0.04  # 4% yields a creature; the other 96% is equipment

# Rarity split used ONLY when the crate rolls a creature (the 10% above). Tuned so
# the *absolute* odds work out to 8% common and a steep tail — i.e. of the whole
# crate: 8% common, 1.2% rare, 0.5% epic, 0.2% legendary, 0.1% mythic creature,
# and 90% equipment. Common is deliberately the overwhelming share of the creature
# slice so a monster from the gold crate is usually a starter, not a jackpot;
# diamond boxes stay the real way to chase rare creatures.
BIOCRATE_CREATURE_RARITY_WEIGHTS = {
    "common": 80,
    "rare": 12,
    "epic": 5,
    "legendary": 2,
    "mythic": 1,
}

# Three Bio-Crate tiers paid with gold (+DNA). The cheapest is the original; the
# pricier ones cost more gold AND DNA but pay for themselves with a higher chance of
# a creature and a far better rarity spread — so the money is worth it.
BIOCRATE_TIERS = {
    "basic": {
        "label": "📦 باکس ژنتیکی معمولی", "gold": BIOCRATE_GOLD_COST, "dna": BIOCRATE_DNA_COST,
        "creature_chance": BIOCRATE_CREATURE_CHANCE, "weights": BIOCRATE_CREATURE_RARITY_WEIGHTS,
        "equip_weights": LOOTBOX_RARITY_WEIGHTS,  # standard: mostly common gear
    },
    "rare": {
        "label": "🎁 باکس ژنتیکی نایاب", "gold": 2000, "dna": 60,
        "creature_chance": 0.10,
        "weights": {"common": 45, "rare": 30, "epic": 15, "legendary": 7, "mythic": 3},
        # gear skews NAYAB: common is rare here, rare dominates
        "equip_weights": {"common": 10, "rare": 55, "epic": 25, "legendary": 8, "mythic": 2},
    },
    "epic": {
        "label": "💎 باکس ژنتیکی حماسی", "gold": 5000, "dna": 120,
        "creature_chance": 0.18,
        # mythic kept to ~1% of all opens (0.18 × 5/98) — a rare jackpot, not routine
        "weights": {"common": 25, "rare": 30, "epic": 26, "legendary": 12, "mythic": 5},
        # gear skews HAMASI: common almost never, epic dominates
        "equip_weights": {"common": 3, "rare": 20, "epic": 50, "legendary": 20, "mythic": 7},
    },
}
BIOCRATE_TIER_ORDER = ["basic", "rare", "epic"]

FUSION_GOLD_COST = 120  # legacy floor / fallback; real cost is fusion_cost() below
FUSION_INHERIT_CHANCE = 0.5  # child inherits one random equipped item from a parent
# Fusion is a major power spike (a strictly-stronger, higher-star creature), so its
# price has to climb hard with what you're forging — a flat 120 gold made pushing to
# 5★ almost free. Cost scales by the parents' shared STAR and their rarity.
FUSION_BASE_GOLD_COST = 400
FUSION_STAR_COST_MULT = {1: 1, 2: 3, 3: 8, 4: 20, 5: 40}  # keyed by the parents' current star
FUSION_RARITY_COST_MULT = {"common": 1.0, "rare": 1.8, "epic": 3.0, "legendary": 5.0, "mythic": 8.0}


def fusion_cost(parent_star: int, rarity: str) -> int:
    """Gold to fuse two creatures that are at `parent_star` and (the higher) `rarity`.
    Ranges from ~400 (1★ common) to ~256k (5★ mythic), so high-star fusion is a real
    long-term gold sink instead of pocket change."""
    star_mult = FUSION_STAR_COST_MULT.get(parent_star, FUSION_STAR_COST_MULT[max(FUSION_STAR_COST_MULT)])
    rarity_mult = FUSION_RARITY_COST_MULT.get(rarity, 1.0)
    return round(FUSION_BASE_GOLD_COST * star_mult * rarity_mult)
# A fused creature must ALWAYS come out stronger than either parent — otherwise the
# gold + the two creatures you sank into it bought a downgrade. The child inherits
# the BEST of each parent's base stat, keeps the higher of each body-part upgrade
# (previously reset to 0, silently deleting everything you'd paid to upgrade), then
# gains a slice of the weaker parent plus a flat growth bump on top.
FUSION_WEAK_PARENT_SHARE = 0.25   # child adds this much of the weaker parent's stat
FUSION_STAT_GROWTH = {"base_hp": 12, "base_atk": 3, "base_def": 3, "base_spd": 2}
FUSION_RARITY_UPGRADE_BUMP = 1.15  # extra multiplier when the fusion also upgrades rarity
# The child keeps the STRONGER parent's build; the weaker parent's investment
# (body-part upgrades it paid gold for, and its level) isn't just lost — a modest
# slice of it comes back to the child as XP. Kept small on purpose ("نه زیاد").
FUSION_WEAK_XP_PER_PART_LEVEL = 30
FUSION_WEAK_XP_PER_LEVEL = 12

DUEL_WAGER_MAX = 500

# ── Player-to-player trading (game/transfer.py) ───────────────────────────────
# Transferring a creature or a piece of gear: the RECEIVER pays diamonds (scaled by
# what they're getting), a 1-day cooldown applies to both sides, and the receiver
# must have progressed far enough (building levels) to hold it — so a throwaway fake
# account can't instantly stockpile high-star creatures.
TRANSFER_COOLDOWN_HOURS = 24

# creature diamond cost = star base × rarity multiplier. Star base is the mythic
# price the owner specified; lower rarities cost proportionally less.
CREATURE_TRANSFER_STAR_COST = {1: 200, 2: 300, 3: 500, 4: 750, 5: 1000}
CREATURE_TRANSFER_RARITY_MULT = {
    "common": 0.12, "rare": 0.25, "epic": 0.45, "legendary": 0.7, "mythic": 1.0,
}
# equipment is much cheaper — a flat per-rarity price
EQUIP_TRANSFER_COST = {
    "common": 15, "rare": 35, "epic": 70, "legendary": 130, "mythic": 250,
}
# receiver prerequisites by the creature's star: (main_hall level, fusion_lab level).
# main_hall is the whole game's bottleneck (weeks to max), so this is the real
# anti-fake-account gate — you can't receive a 5★ without a mature base.
CREATURE_TRANSFER_REQS = {
    1: {"main_hall": 1, "fusion_lab": 0},
    2: {"main_hall": 2, "fusion_lab": 0},
    3: {"main_hall": 3, "fusion_lab": 1},
    4: {"main_hall": 4, "fusion_lab": 2},
    5: {"main_hall": 5, "fusion_lab": 3},
}
# equipment receiver prereq: only the rarer gear needs a bit of a base
EQUIP_TRANSFER_MAIN_HALL_REQ = {
    "common": 1, "rare": 1, "epic": 2, "legendary": 3, "mythic": 3,
}


def creature_transfer_cost(star_level: int, rarity: str) -> int:
    star_base = CREATURE_TRANSFER_STAR_COST.get(star_level, CREATURE_TRANSFER_STAR_COST[max(CREATURE_TRANSFER_STAR_COST)])
    return round(star_base * CREATURE_TRANSFER_RARITY_MULT.get(rarity, 1.0))


def equip_transfer_cost(rarity: str) -> int:
    return EQUIP_TRANSFER_COST.get(rarity, EQUIP_TRANSFER_COST["common"])

HEIST_STEAL_PERCENT = 0.20
HEIST_COOLDOWN_HOURS = 6
HEIST_DAILY_ATTEMPTS = 3
ENERGY_CAPS["heist"] = HEIST_DAILY_ATTEMPTS

# ── Star prestige — never player-set directly; the only source is fusion, which
# demands two creatures of the SAME species name at the SAME star. STAR_MAX is the
# absolute ceiling, but each player's real cap is their main hall's level (see
# game.buildings.star_cap) — so stars are gated behind base progression. ────────
STAR_MAX = 5
STAR_STAT_BONUS_PCT = 0.05

# ── Buildings: the backbone of progression. Everything is level-gated by the main
# hall — no other building may exceed its level, so the hall is the deliberate
# bottleneck the whole base plans around. Production accrues lazily (same pattern
# as game/energy.py's stamina regen — computed from last_collected_at, no
# background ticking).
#
# **Level 0 means "not built yet"** — only the main hall starts at level 1. The
# first "upgrade" of any other building is its construction.
MAIN_BUILDING = "main_hall"
BUILDING_TYPES = [
    "main_hall",
    "gold_collector",
    "diamond_collector",
    "dna_lab",
    "blacksmith",
    "fusion_lab",
]
BUILDING_LABELS = {
    "main_hall": "🏛 تالار مِهر",  # the main hall; everything else is capped by its level
    "gold_collector": "🏭 جمع‌کننده طلا",
    "diamond_collector": "💎 جمع‌کننده الماس",
    "dna_lab": "🧬 آزمایشگاه DNA",
    "blacksmith": "⚒ آهنگری",
    "fusion_lab": "🔮 تالار ادغام",
}
BUILDING_DESCRIPTIONS = {
    "main_hall": "قلب آزمایشگاه. سقف سطح بقیه‌ی ساختمون‌ها و سقف ستاره‌ی هیولاهات رو تعیین می‌کنه.",
    "gold_collector": "به‌مرور طلا تولید می‌کنه.",
    "diamond_collector": "به‌مرور الماس تولید می‌کنه (خیلی کند، چون الماس ارز ویژه‌ست).",
    "dna_lab": "به‌مرور DNA تولید می‌کنه.",
    "blacksmith": "برای ارتقای تجهیزات لازمه. هر سطحش سقف تجهیزات رو ۵ تا بالاتر می‌بره.",
    "fusion_lab": "برای ادغام دو هیولای هم‌نوع و بالا بردن ستاره لازمه.",
}
BUILDING_MAX_LEVEL = 5

# Lab-level required to REACH each building level. Lab level comes from actual play
# (missions, fusions, rewards…), which a throwaway account can't rush, so this stops
# someone from speed-maxing a building (e.g. the main hall) on day one.
BUILDING_LEVEL_LAB_REQ = {1: 0, 2: 3, 3: 7, 4: 12, 5: 18}

# Main-hall level required before a building can be CONSTRUCTED at all. This is
# separate from max_level_for(), which caps how high a building may go: the hall
# already limited every building's level, but everything was buildable from day
# one, so raising the hall only ever raised a ceiling — it never *revealed*
# anything. Staggering the unlocks gives each hall level its own reward and
# paces the opening instead of dumping six construction sites on a new player.
#
# One unlock per hall level, so raising the hall ALWAYS reveals a new building —
# there's never a "dead" level that only lifts a ceiling. The order follows the
# natural power curve: income first, then the resource the mid-game spends
# (DNA), then the two upgrade workshops, and finally the fusion hall — the
# creature-combination building that grants prestige stars — as the reward for
# fully maxing the main hall.
BUILDING_UNLOCK_HALL_LEVEL = {
    "gold_collector": 1,      # the first thing you build — income has to come first
    "dna_lab": 2,             # DNA feeds breeding and fusion costs
    "blacksmith": 3,          # gear upgrades open once there's gold to spend on them
    "diamond_collector": 4,   # the premium mine — a real mid/late-game payoff
    "fusion_lab": 5,          # stars & propagation: the reward for maxing the hall
}

# ── Upgrade pacing ────────────────────────────────────────────────────────────
# Explicit per-level tables rather than a formula, because the thing being tuned
# is a *total*: taking every building to level 5 should occupy about **3 weeks**
# of real time. A `base * level` formula can't express that shape — it makes the
# early levels too slow and the late ones nowhere near slow enough.
#
# Keys are the level being *reached* (so 1 is construction, 5 is the final tier).
#
# Total build time, with the single-worker rule that makes these add up:
#   5 buildings x (24+144+576+1440+2880)  = 25,320 min
#   main hall, which starts at level 1    =  5,040 min
#   --------------------------------------------------
#   30,360 min = 506 h = 21.1 days  (~3 weeks)
# Speed-up cards from missions and the wheel pull that down a bit for an active
# player; rushing with diamonds is faster still, deliberately. (These were 1.6x
# smaller for a ~13-day target; loot income was cut by the same 1.6x below so the
# gold-vs-clock balance is unchanged — gold stays felt-but-not-binding.)
# Doubled across the board, with the top two tiers stretched much further apart:
# level 4 = 3 days, level 5 = 5 days. Base progression is now a multi-week haul.
BUILDING_UPGRADE_MINUTES = {1: 48, 2: 288, 3: 1152, 4: 4320, 5: 7200}

# Gold is sized to be *felt but not binding*: the constraint is meant to be the
# clock, not the wallet. These came down when hunt and raid income was cut — with
# the old figures, gold overtook the timers as the bottleneck and full build-out
# needed ~21 days of income against a 13-day clock, which would have quietly
# broken the 1–2 week target. Full build-out is now ~63k, roughly 80% of what a
# moderately active player earns over those 13 days, leaving the rest for crates,
# fusion and the forge.
BUILDING_UPGRADE_GOLD = {1: 150, 2: 450, 3: 1200, 4: 2800, 5: 6000}

# rate_per_hour/cap_base scale by *level; diamond_collector's rate is deliberately
# tiny since diamonds are the premium currency. Buildings absent from this table
# (main hall, blacksmith, fusion lab) are pure gates — they unlock things instead
# of producing resources.
BUILDING_PRODUCTION = {
    "gold_collector": {"rate_per_hour": 20.0, "cap_base": 200, "resource": "coins"},
    "diamond_collector": {"rate_per_hour": 0.4, "cap_base": 4, "resource": "diamonds"},
    "dna_lab": {"rate_per_hour": 2.0, "cap_base": 20, "resource": "dna_fragments"},
}

# each blacksmith level raises the equipment ceiling by this much, so a level-1
# forge caps items at +5 and a maxed level-5 forge at +25
EQUIPMENT_LEVELS_PER_BLACKSMITH_LEVEL = 5

# ── Stationed creatures ───────────────────────────────────────────────────────
# A production building hosts up to `level` creatures, and each one raises output
# by its own level times this rate. A level-10 creature is +20%, so raising a
# creature and then putting it to work compounds — which is the point.
# The cap stops a full late-game roster from turning idle income into the whole
# economy; hunting and raiding have to stay worth doing.
WORKER_BONUS_PER_CREATURE_LEVEL = 0.06  # 3× stronger — a stationed worker matters a lot now
WORKER_BONUS_CAP = 6.0  # up to +600% output from stationed creatures (raised so rarity has room to matter)
# a stationed worker's contribution is multiplied by its rarity — a mythic worker is
# worth several commons of the same level, so rarer monsters are better miners
WORKER_RARITY_MULT = {"common": 1.0, "rare": 1.4, "epic": 2.0, "legendary": 3.0, "mythic": 4.5}

# ── Monster Cave / egg incubation (game/breeding.py) ──────────────────────────
# Two phases, deliberately decoupled:
#   1. MATING — the two parents are busy in the cave. When it finishes, an egg is
#      laid and the parents are FREED, so a new pair can go straight back in.
#   2. HATCHING — the laid egg then incubates on its OWN timer, independent of the
#      cave, and hatches into a mystery creature.
# Both are keyed to the better parent's rarity ("type and breed"); the total for
# the rarest tops out at a full day (mating 6h + hatch 18h = 24h).
CAVE_MATING_MINUTES = {
    "common": 45,       # 45m
    "rare": 90,         # 1.5h
    "epic": 150,        # 2.5h
    "legendary": 210,   # 3.5h
    "mythic": 300,      # 5h — parents freed after this; the egg then incubates on its own
}
# Egg incubation is now keyed to BOTH parents (sum of their rarity indices), so a
# mythic+mythic pair waits far longer than a mythic+legendary one — the rarer the
# pair, the longer the egg. Times deliberately pushed high: the two headline points
# the design targets are mythic+mythic = 48h and mythic+legendary = 36h.
# index sum: common=0 … mythic=4, so 0 (c+c) … 8 (m+m).
EGG_HATCH_HOURS_BY_RARITY_SUM = {
    0: 3,    # common + common
    1: 5,
    2: 7,
    3: 10,
    4: 14,
    5: 20,
    6: 27,   # legendary + legendary  (or mythic + epic)
    7: 36,   # mythic + legendary
    8: 48,   # mythic + mythic
}


def egg_hatch_minutes(rarity_a: str, rarity_b: str) -> int:
    s = RARITY_ORDER.index(rarity_a) + RARITY_ORDER.index(rarity_b)
    return EGG_HATCH_HOURS_BY_RARITY_SUM[s] * 60


# Chance the egg lands at the parents' TOP rarity (it can never exceed it — two
# legendaries can't make a mythic). Otherwise it drops one tier. Same-species pairs
# are far more reliable than cross-species ones.
CAVE_TOP_CHANCE_SAME_SPECIES = 0.60
CAVE_TOP_CHANCE_DIFF_SPECIES = 0.30


def cave_top_chance(same_species: bool) -> float:
    return CAVE_TOP_CHANCE_SAME_SPECIES if same_species else CAVE_TOP_CHANCE_DIFF_SPECIES
# Finishing an egg early is a FIXED per-rarity diamond price (not the time-based
# building formula), and deliberately far higher than a building speed-up — a rare
# creature should be a real diamond decision, not a cheap skip.
EGG_HATCH_DIAMOND_COST = {
    "common": 120,
    "rare": 250,
    "epic": 450,
    "legendary": 650,
    "mythic": 800,
}
BREEDING_DNA_COST = {
    "common": 20,
    "rare": 45,
    "epic": 90,
    "legendary": 160,
    "mythic": 260,
}
# Bonuses to the offspring's rarity-upgrade roll. They reward a *considered*
# pairing over two random creatures — matching element, matching species, and raw
# power all push the odds up, but the cap keeps it a roll rather than a formula.
BREEDING_SAME_ELEMENT_BONUS = 0.10
BREEDING_SAME_SPECIES_BONUS = 0.05
BREEDING_POWER_PER_BONUS_POINT = 60  # every 60 combined power adds 1 percentage point
BREEDING_POWER_BONUS_CAP = 0.15
BREEDING_MAX_UPGRADE_CHANCE = 0.60
# the newborn inherits half its parents' average level, so propagation beats a
# lootbox creature without handing over a finished fighter
BREEDING_LEVEL_INHERIT = 0.5

# ── Speed-up cards: consumable items that shave time off the active building
# upgrade. Fixed denominations in minutes, rewarded by the daily wheel and a
# handful of other activities rather than sold directly. ───────────────────────
SPEEDUP_MINUTES = [1, 5, 30, 60, 720, 1440]
SPEEDUP_LABELS = {
    1: "⏱ ۱ دقیقه",
    5: "⏱ ۵ دقیقه",
    30: "⏱ ۳۰ دقیقه",
    60: "⏱ ۱ ساعت",
    720: "⏱ ۱۲ ساعت",
    1440: "⏱ ۲۴ ساعت",
}
# Just the duration, no clock glyph — for sentences that already say "card", where
# SPEEDUP_LABELS' bare "⏱ ۳۰ دقیقه" reads like a countdown rather than an item.
SPEEDUP_PLAIN_LABELS = {
    1: "۱ دقیقه‌ای",
    5: "۵ دقیقه‌ای",
    30: "۳۰ دقیقه‌ای",
    60: "۱ ساعته",
    720: "۱۲ ساعته",
    1440: "۲۴ ساعته",
}

# Diamonds can also finish an upgrade outright, priced from the time still left.
# Deliberately cheap per minute at the short end (a minimum charge stops 1-minute
# finishes being free) and linear after that, so diamonds are a convenience for
# impatient players rather than a way to buy the whole tech tree instantly.
DIAMOND_FINISH_MIN_COST = 1
DIAMOND_FINISH_PER_HOUR = 6  # diamonds per remaining hour, rounded up


def diamond_finish_cost(remaining_seconds: float) -> int:
    """Diamonds needed to instantly finish an upgrade with this much time left."""
    import math as _math

    hours = max(0.0, remaining_seconds) / 3600
    return max(DIAMOND_FINISH_MIN_COST, _math.ceil(hours * DIAMOND_FINISH_PER_HOUR))

# ── Daily prize wheel: one free spin/day (capped via ENERGY_CAPS below), a
# weighted table of small prizes across every resource plus speed-up cards. ─────
WHEEL_DAILY_LIMIT = 1
ENERGY_CAPS["wheel_spin"] = WHEEL_DAILY_LIMIT

# ── Casino (paid gamble, «کازینو») ─────────────────────────────────────────────
# Four tables: one free spin/day plus three paid tiers (cheap→expensive). Each
# table is weighted with a real chance of «nothing» (the house edge) and a rare
# jackpot, so it reads as a gamble rather than a guaranteed payout. The paid
# tiers' expected value sits a little under their cost.
CASINO_TIERS = {
    "free": {
        # the free option IS the daily wheel — same limit & prizes (game.wheel)
        "label": "🎁 چرخ رایگان روزانه (قرعه‌کشی)", "cost": 0, "currency": None, "daily": True,
        "desc": "همون قرعه‌کشیِ رایگان روزانه‌ست — روزی یک‌بار.",
    },
    "bronze": {
        # coin-only payouts (no diamonds — a coin table that paid the premium
        # currency would be a coin→diamond pump) with a real house edge: coin EV
        # ~119 against the 150 cost.
        "label": "🥉 میز برنزی", "cost": 150, "currency": "coins", "daily": False,
        "desc": "شرط ۱۵۰ طلا.",
        "prizes": [
            {"kind": "nothing", "amount": 0, "weight": 20, "label": "باختی 😔"},
            {"kind": "coins", "amount": 100, "weight": 30, "label": "۱۰۰ طلا"},
            {"kind": "coins", "amount": 220, "weight": 20, "label": "۲۲۰ طلا"},
            {"kind": "coins", "amount": 400, "weight": 9, "label": "۴۰۰ طلا"},
            {"kind": "dna", "amount": 5, "weight": 5, "label": "۵ DNA"},
            {"kind": "coins", "amount": 900, "weight": 1, "label": "🎉 جک‌پات ۹۰۰ طلا"},
        ],
    },
    "silver": {
        "label": "🥈 میز نقره‌ای", "cost": 600, "currency": "coins", "daily": False,
        "desc": "شرط ۶۰۰ طلا.",
        "prizes": [
            {"kind": "nothing", "amount": 0, "weight": 22, "label": "باختی 😔"},
            {"kind": "coins", "amount": 400, "weight": 30, "label": "۴۰۰ طلا"},
            {"kind": "coins", "amount": 850, "weight": 20, "label": "۸۵۰ طلا"},
            {"kind": "coins", "amount": 1600, "weight": 9, "label": "۱۶۰۰ طلا"},
            {"kind": "dna", "amount": 18, "weight": 7, "label": "۱۸ DNA"},
            {"kind": "coins", "amount": 4000, "weight": 2, "label": "🎉 جک‌پات ۴۰۰۰ طلا"},
        ],
    },
    "gold": {
        "label": "🥇 میز طلایی (الماسی)", "cost": 12, "currency": "diamonds", "daily": False,
        "desc": "شرط ۱۲ الماس.",
        "prizes": [
            {"kind": "nothing", "amount": 0, "weight": 14, "label": "باختی 😔"},
            {"kind": "diamonds", "amount": 7, "weight": 25, "label": "۷ الماس"},
            {"kind": "diamonds", "amount": 14, "weight": 22, "label": "۱۴ الماس"},
            {"kind": "diamonds", "amount": 25, "weight": 14, "label": "۲۵ الماس"},
            {"kind": "coins", "amount": 3000, "weight": 8, "label": "۳۰۰۰ طلا"},
            {"kind": "dna", "amount": 45, "weight": 4, "label": "۴۵ DNA"},
            {"kind": "diamonds", "amount": 90, "weight": 2, "label": "🎉 جک‌پات ۹۰ الماس"},
        ],
    },
}
CASINO_TIER_ORDER = ["free", "bronze", "silver", "gold"]
WHEEL_PRIZES = [
    {"key": "coins_small", "kind": "coins", "amount": 50, "weight": 28, "label": "۵۰ طلا"},
    {"key": "coins_medium", "kind": "coins", "amount": 150, "weight": 14, "label": "۱۵۰ طلا"},
    {"key": "dna_small", "kind": "dna", "amount": 3, "weight": 20, "label": "۳ DNA"},
    {"key": "dna_medium", "kind": "dna", "amount": 8, "weight": 8, "label": "۸ DNA"},
    {"key": "diamonds_small", "kind": "diamonds", "amount": 2, "weight": 12, "label": "۲ الماس"},
    {"key": "speedup_5", "kind": "speedup", "amount": 5, "weight": 8, "label": "کارت سرعت ۵ دقیقه"},
    {"key": "speedup_30", "kind": "speedup", "amount": 30, "weight": 6, "label": "کارت سرعت ۳۰ دقیقه"},
    {"key": "speedup_60", "kind": "speedup", "amount": 60, "weight": 3, "label": "کارت سرعت ۱ ساعت"},
    {"key": "jackpot", "kind": "coins", "amount": 500, "weight": 1, "label": "🎉 جک‌پات ۵۰۰ طلا"},
]

# ── Diamond boxes: paid with diamonds, always yield a creature (unlike the gold
# Bio-Crate, which can give equipment too) — this is the "open a new monster with
# diamonds" tier. Each tier is a fully independent rarity-weight table; the
# top tier deliberately skews heavily toward legendary/mythic. ─────────────────
DIAMOND_BOX_TIERS = {
    "bronze": {
        "label": "🥉 جعبه‌ی الماسی برنزی",
        "cost_diamonds": 20,
        "weights": {"common": 70, "rare": 22, "epic": 6, "legendary": 1.8, "mythic": 0.2},
    },
    "silver": {
        "label": "🥈 جعبه‌ی الماسی نقره‌ای",
        "cost_diamonds": 50,
        "weights": {"common": 50, "rare": 30, "epic": 14, "legendary": 5, "mythic": 1},
    },
    "gold": {
        "label": "🥇 جعبه‌ی الماسی طلایی",
        "cost_diamonds": 120,
        "weights": {"common": 25, "rare": 30, "epic": 25, "legendary": 15, "mythic": 5},
    },
    "legendary": {
        "label": "🔴 جعبه‌ی افسانه‌ای",
        "cost_diamonds": 300,
        "weights": {"common": 5, "rare": 15, "epic": 30, "legendary": 35, "mythic": 15},
    },
}

# ── Blacksmith: levels equipment with pure gold, no duplicate needed (that's what
# game.equipment.upgrade_item is for). The tradeoff is risk — past a safe floor the
# attempt can fail and only burn the gold, so duplicates stay the reliable path. ──
FORGE_BASE_GOLD_COST = 60  # scaled by the item's current level
FORGE_RARITY_COST_MULT = {"common": 1.0, "rare": 1.3, "epic": 1.7, "legendary": 2.2, "mythic": 3.0}
FORGE_SAFE_LEVEL = 3  # attempts up to this target level never fail
FORGE_FAIL_CHANCE_PER_LEVEL = 0.08  # each level past the safe floor adds this much failure chance
FORGE_MAX_FAIL_CHANCE = 0.45

# ── Cup arena (PvP raiding): matchmaking is by cup, loot is a slice of the
# defender's gold, and a fresh defender gets a shield so they can't be farmed.
# The soft cap below is the important bit — see game/arena.py's cup_delta(). ─────
# Loot is tuned against the hunt, since both cost one energy. A successful raid
# pays roughly 1.8x a normal hunt — it was 3x, and before that 7x. The point of a
# raid is no longer the gold: it's the cup. Gold that arrives faster than it can
# be spent makes the build timers meaningless, and building is the spine of the
# game, so raiding has to be a grind you work at rather than a faucet.
ARENA_LOOT_PERCENT = 0.10
ARENA_LOOT_MIN = 9  # a raid on a broke player still pays something, but barely
ARENA_SHIELD_HOURS = 8
GROUP_SHIELD_HOURS = 4  # separate anti-farm grace after being hit by a group «اتک»

# Purchasable arena shields (diamonds). Unlike the free 8h grace after being raided,
# these are bought up-front for long stretches of protection. Each attack YOU launch
# spends SHIELD_ATTACK_COST_HOURS off your shield instead of dropping it entirely —
# so a week-long shield survives ~21 raids before you're exposed again.
SHIELD_ATTACK_COST_HOURS = 8
SHIELD_SHOP_TIERS = {
    "8h":  {"hours": 8,   "diamonds": 20,  "label": "🛡 سپر ۸ ساعته"},
    "24h": {"hours": 24,  "diamonds": 50,  "label": "🛡 سپر ۲۴ ساعته"},
    "3d":  {"hours": 72,  "diamonds": 120, "label": "🛡 سپر ۳ روزه"},
    "7d":  {"hours": 168, "diamonds": 240, "label": "🛡 سپر یک‌هفته‌ای"},
}
# A separate, cheaper shield against group «اتک» (uses group_shield_until). Group
# aggression is lower-stakes than arena raiding, so protection costs less.
GROUP_SHIELD_SHOP_TIERS = {
    "8h":  {"hours": 8,   "diamonds": 10,  "label": "🛡 سپر گروه ۸ ساعته"},
    "24h": {"hours": 24,  "diamonds": 24,  "label": "🛡 سپر گروه ۲۴ ساعته"},
    "3d":  {"hours": 72,  "diamonds": 60,  "label": "🛡 سپر گروه ۳ روزه"},
    "7d":  {"hours": 168, "diamonds": 110, "label": "🛡 سپر گروه یک‌هفته‌ای"},
}
ARENA_ATTACK_ENERGY_COST = 1

# Loot is capped against the ATTACKER's own progression stage, not the defender's
# wallet. Without this, one lucky match against a hoarder hands a new player more
# gold than hours of hunting and skips the whole early economy; with it, raiding
# is reliably a bit better than hunting instead of a jackpot.
# The cap is what most raids actually pay, since 5% of an active player's purse
# usually exceeds it. Keyed to the ATTACKER's level, not the defender's wealth,
# so one lucky match against a rich player can't skip a week of progression.
# Trimmed alongside the hunt-loot cut so a raid stays ~1.8x a hunt (the intended
# ratio) instead of drifting into a relative jackpot once hunting was nerfed.
ARENA_LOOT_CAP_BASE = 22
ARENA_LOOT_CAP_PER_LEVEL = 4

ARENA_CUP_WIN_BASE = 22
ARENA_CUP_LOSS_BASE = 14
ARENA_CUP_MIN_DELTA = 3  # never award/deduct less than this, so every fight moves the needle
ARENA_CUP_MAX_DELTA = 70  # wider so a big rating gap really swings the cup
ARENA_CUP_GAP_DIVISOR = 5  # +1 cup per this many points of rating gap (was 8 — steeper now)

# Arena/PvP wins now also pay a little DNA, scaled by the attacker's level.
ARENA_WIN_DNA_BASE = 2
ARENA_WIN_DNA_PER_LEVEL = 0.2
GROUP_ATTACK_WIN_DNA = 3   # winning a group «اتک» on a player
RAID_HIT_DNA = 1           # every raid-boss hit drips a little DNA on top of the kill split
ARENA_MATCH_CUP_BAND = 500  # real opponents within +/- this cup range are eligible (closer cups preferred)
ARENA_STARTING_CUP = 0

# A player's cup is soft-capped by their actual creature power: past the ceiling
# implied by their power, wins award steeply less. Without this a weak player could
# ride a lucky streak into a bracket that then farms them forever. Tuned generously
# enough that a normally-progressing player is never damped — it's a guard rail for
# outliers, not a tax on everyone.
ARENA_CUP_PER_POWER = 3.0  # rescaled with the combat-accurate power metric (was 4.0)
ARENA_OVERCAP_DAMPING = 0.25  # cup gain multiplier once you're above your deserved cup

# GLOBAL diminishing returns on cup, independent of power. The deserved-cup guard
# above only reins in players climbing ABOVE their power; a genuinely strong player
# could still ride the ladder to 10k because their deserved cup is enormous. This
# soft cap makes every extra cup cost more no matter how strong you are: each win's
# gain is scaled by SOFTCAP/(SOFTCAP+cup), and losses grow the further past the
# softcap you sit. Net effect — the ladder compresses at the top, ~5000 is a real
# grind and ~10000 is nearly asymptotic, and the field stays in close competition.
ARENA_CUP_SOFTCAP = 2500

# Fake opponents shown when no real player sits in the cup band — their lab names
# are obviously flavored so the roster never looks empty on a small player base.
ARENA_FAKE_LAB_NAMES = [
    "آزمایشگاه سایه",
    "کارگاه زیستی نور",
    "پایگاه متروکه",
    "لانه‌ی سرد",
    "مرکز ژنتیک آبی",
    "پناهگاه شماره ۷",
    "ایستگاه دورافتاده",
    "آشیانه‌ی زنگاری",
    "قرارگاه مه‌آلود",
    "کندوی فولادی",
]
# Bot loot scales with the attacker's own level so raiding bots keeps pace with
# progression instead of going stale — the spread stays wide so it still gambles.
# Deliberately averaging a little under the real-opponent cap, so matchmaking
# against actual players stays the preferable outcome — but wide enough that a
# bot raid is still a gamble worth taking rather than a consolation prize.
ARENA_FAKE_LOOT_BASE = (10, 31)
ARENA_FAKE_LOOT_PER_LEVEL = 2.5


def arena_loot_cap(attacker_level: int) -> int:
    return ARENA_LOOT_CAP_BASE + max(0, attacker_level) * ARENA_LOOT_CAP_PER_LEVEL


def arena_fake_loot_range(attacker_level: int) -> tuple[int, int]:
    # ARENA_FAKE_LOOT_PER_LEVEL is a float, so bonus is too — int() it, otherwise
    # random.randint() (used on this range) raises TypeError on the fake-opponent
    # branch and the whole "find opponent" flow silently dies.
    bonus = int(max(0, attacker_level) * ARENA_FAKE_LOOT_PER_LEVEL)
    return ARENA_FAKE_LOOT_BASE[0] + bonus, ARENA_FAKE_LOOT_BASE[1] + bonus


# Loot from a BOT/fake opponent scales super-linearly with the raider's cup, so a
# high-cup player who can't find a real target isn't stuck on ~9 coins. Real players
# still pay the flat 10%-of-gold (arena.expected_loot); this is bot-only.
ARENA_FAKE_LOOT_MIN = 25


def arena_fake_loot(cup: int) -> int:
    cup = max(0, cup)
    return round(ARENA_FAKE_LOOT_MIN + cup * 0.2 + (cup ** 1.3) / 60)


# ── Weekly cup season ──────────────────────────────────────────────────────────
# Resetting to zero every week would throw away a week of work; resetting to
# nothing at all would let the first month's leaders sit on top forever. So each
# player restarts at a floor set by where they finished, plus a slice of whatever
# they earned above it.
SEASON_RANK_FLOORS = [
    (1, 600),  # champion
    (3, 450),
    (10, 320),
    (25, 200),
    (50, 120),
]
SEASON_DEFAULT_FLOOR = 60
SEASON_CARRYOVER_PCT = 0.15


def forge_cost(item_level: int, rarity: str) -> int:
    return round(FORGE_BASE_GOLD_COST * item_level * FORGE_RARITY_COST_MULT.get(rarity, 1.0))


def forge_fail_chance(target_level: int) -> float:
    if target_level <= FORGE_SAFE_LEVEL:
        return 0.0
    return min(FORGE_MAX_FAIL_CHANCE, (target_level - FORGE_SAFE_LEVEL) * FORGE_FAIL_CHANCE_PER_LEVEL)


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


def element_advantage_chain() -> str:
    """Compact one-line cycle of who beats whom, e.g. 🔥 › 🪨 › ⚡ › 💧 › 🔥."""
    from game.emoji import get_emoji

    seq = ["fire", "earth", "electric", "water", "fire"]
    return "🔁 ترتیب برتری عنصری: " + " › ".join(get_emoji(ELEMENT_EMOJI_KEYS[e]) for e in seq)


def element_advantage_lines() -> str:
    """Who-beats-whom, one pairing per line — easier to read than the cramped cycle.
    Each element on its own row pointing at the element it's strong against."""
    lines = ["🔁 <b>برتری عنصری:</b>"]
    for e in ("fire", "earth", "electric", "water"):  # follow the beat cycle
        lines.append(f"{element_label(e)} ⟶ {element_label(ELEMENT_STRONG_AGAINST[e])}")
    return "\n".join(lines)


def element_matchup_note(my_element: str, opp_element: str) -> str:
    """A one-line elemental heads-up for a fight preview: warns when the opponent's
    element beats yours, cheers when yours beats theirs, empty when neutral."""
    if ELEMENT_STRONG_AGAINST.get(opp_element) == my_element:
        return (
            f"⚠️ <b>احتمال باخت بیشتره:</b> {element_label(opp_element)} به "
            f"{element_label(my_element)} برتری داره."
        )
    if ELEMENT_STRONG_AGAINST.get(my_element) == opp_element:
        return (
            f"✅ <b>برتری عنصری با توئه:</b> {element_label(my_element)} به "
            f"{element_label(opp_element)} برتری داره."
        )
    return ""


def next_rarity(rarity: str) -> str:
    idx = RARITY_ORDER.index(rarity)
    return RARITY_ORDER[min(idx + 1, len(RARITY_ORDER) - 1)]


def prev_rarity(rarity: str) -> str:
    idx = RARITY_ORDER.index(rarity)
    return RARITY_ORDER[max(idx - 1, 0)]


def higher_rarity(rarity_a: str, rarity_b: str) -> str:
    return rarity_a if RARITY_ORDER.index(rarity_a) >= RARITY_ORDER.index(rarity_b) else rarity_b


def render_bar(current: int, total: int, width: int = 10) -> str:
    total = max(total, 1)
    filled = min(width, max(0, round(width * max(current, 0) / total)))
    return "▓" * filled + "░" * (width - filled)
