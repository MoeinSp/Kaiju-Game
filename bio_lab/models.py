from django.db import models
from django.utils import timezone


class User(models.Model):
    """A telegram player. NOT django.contrib.auth's User — this app has no
    login of its own; Django admin's staff accounts are a separate model."""

    id = models.BigIntegerField(primary_key=True)  # telegram user id
    username = models.CharField(max_length=64, null=True, blank=True)
    first_name = models.CharField(max_length=128, null=True, blank=True)
    lab_name = models.CharField(max_length=32, null=True, blank=True)  # first set free at /start; later renames cost diamonds
    lab_renames = models.IntegerField(default=0)  # how many paid renames so far — drives the escalating cost
    # welcome package — keep in sync with game.constants.STARTING_*
    coins = models.IntegerField(default=5000)
    dna_fragments = models.IntegerField(default=100)
    diamonds = models.IntegerField(default=100)  # premium currency: diamond collector, daily wheel, diamond boxes

    energy = models.IntegerField(default=20)  # keep in sync with game.constants.MAX_ENERGY
    energy_updated_at = models.DateTimeField(default=timezone.now)

    # overall progress for the lab as a whole, distinct from any one creature's
    # level — see game/lab.py, which owns the curve and the award table
    lab_xp = models.IntegerField(default=0)

    login_streak = models.IntegerField(default=0)
    last_login_day = models.CharField(max_length=10, null=True, blank=True)  # "YYYY-MM-DD" in the game timezone (game.daily.today_str)

    cup = models.IntegerField(default=0)  # arena rating; drives PvP matchmaking
    shield_until = models.DateTimeField(null=True, blank=True)  # anti-farm grace after being raided

    # re-engagement push (game/notifications.py). notifications_on is the master
    # opt-out; energy_full_notified fires the "energy is full" DM exactly once per
    # drain-and-refill cycle (reset when energy is spent); last_nudge_day dedups the
    # once-a-day "your daily reward is waiting" nudge.
    notifications_on = models.BooleanField(default=True)
    energy_full_notified = models.BooleanField(default=False)
    last_nudge_day = models.CharField(max_length=10, null=True, blank=True)

    alliance = models.ForeignKey(
        "Alliance", null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )

    is_banned = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.username or self.first_name or f"Player {self.id}"


class Alliance(models.Model):
    name = models.CharField(max_length=64, unique=True)
    leader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="+")
    treasury_gold = models.IntegerField(default=0)
    last_heisted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Creature(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="creatures")
    name = models.CharField(max_length=64)
    element = models.CharField(max_length=16)
    rarity = models.CharField(max_length=16, default="common")
    star_level = models.IntegerField(default=1)  # prestige tier from fusion "generations" — never player-set
    level = models.IntegerField(default=1)
    xp = models.IntegerField(default=0)

    base_hp = models.IntegerField(default=50)
    base_atk = models.IntegerField(default=10)
    base_def = models.IntegerField(default=10)
    base_spd = models.IntegerField(default=10)

    wings_lvl = models.IntegerField(default=0)
    armor_lvl = models.IntegerField(default=0)
    fangs_lvl = models.IntegerField(default=0)
    poison_lvl = models.IntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_trained_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.name} (#{self.id})"


class Equipment(models.Model):
    """One item in a player's armory. `equipped_on` is null while sitting in
    inventory; at most one Equipment per (creature, slot) should be equipped at
    once — enforced in game/equipment.py, not here, since "slot" describes how an
    item is being used rather than a fixed column on Creature."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="equipment")
    slot = models.CharField(max_length=16)  # one of game.constants.EQUIPMENT_SLOTS
    template_key = models.CharField(max_length=32)
    name = models.CharField(max_length=64)
    rarity = models.CharField(max_length=16, default="common")
    level = models.IntegerField(default=1)  # "+1" .. "+10"
    equipped_on = models.ForeignKey(
        Creature, null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment_items"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} +{self.level} (#{self.id})"


class Building(models.Model):
    """One idle-production building the player owns. Production is computed lazily
    from `last_collected_at` (same pattern as game/energy.py's stamina regen) —
    there's no background job ticking resources up, it's all math at read time."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="buildings")
    building_type = models.CharField(max_length=32)  # one of game.constants.BUILDING_TYPES
    level = models.IntegerField(default=0)  # 0 = not built yet; only the main hall starts at 1
    last_collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "building_type"], name="uq_owner_building_type")
        ]

    def __str__(self) -> str:
        return f"{self.building_type} Lv{self.level} ({self.owner_id})"


class BuildingUpgrade(models.Model):
    """The single active upgrade job for a player. OneToOneField enforces "only one
    worker at a time" at the database level, not just in application code."""

    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name="building_upgrade")
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="+")
    target_level = models.IntegerField()
    started_at = models.DateTimeField(auto_now_add=True)
    finishes_at = models.DateTimeField()
    notified = models.BooleanField(default=False)  # "building upgrade finished" DM sent

    def __str__(self) -> str:
        return f"{self.building.building_type} -> Lv{self.target_level} ({self.owner_id})"


class CreatureAssignment(models.Model):
    """A creature stationed in a production building to raise its output.

    OneToOne on `creature` is the whole safety story: a creature can be in
    exactly one place, so "already working somewhere else" is a database
    guarantee rather than something every call site has to remember to check.
    How many a building can hold is its level (see game/workers.py)."""

    creature = models.OneToOneField(Creature, on_delete=models.CASCADE, related_name="assignment")
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="workers")
    assigned_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.creature_id} @ {self.building.building_type}"


class BreedingJob(models.Model):
    """One in-progress propagation. OneToOne on owner enforces "one at a time",
    the same way BuildingUpgrade enforces the single worker.

    Parents are NOT consumed — that's what separates this from fusion, which
    burns both. They're merely busy until `finishes_at`, and game/workers.py's
    availability check keeps them out of mines and off the active slot for the
    duration."""

    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name="breeding_job")
    parent_a = models.ForeignKey(Creature, on_delete=models.CASCADE, related_name="+")
    parent_b = models.ForeignKey(Creature, on_delete=models.CASCADE, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    finishes_at = models.DateTimeField()
    notified = models.BooleanField(default=False)  # "mating done, lay the egg" DM sent

    def __str__(self) -> str:
        return f"breeding {self.parent_a_id}+{self.parent_b_id} ({self.owner_id})"


class PassProgress(models.Model):
    """A player's Battle Pass progress for one season.

    Seasons aren't a table — the key is derived from the date (game/battlepass),
    so a new month just makes a fresh row on first award and the old one is kept
    as history. `premium` is the paid track unlock; `free_claimed` /
    `premium_claimed` are the highest tier already collected on each track, so a
    claim just grants everything between there and the current tier."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pass_progress")
    season_key = models.CharField(max_length=10)  # e.g. "2026-08"
    points = models.IntegerField(default=0)
    premium = models.BooleanField(default=False)
    free_claimed = models.IntegerField(default=0)
    premium_claimed = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "season_key"], name="uq_pass_progress")
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.season_key} pts={self.points}{' ✦' if self.premium else ''}"


class AchievementClaim(models.Model):
    """Records that `user` has claimed the reward for achievement `key`.

    Achievements themselves are defined in code (game/achievements.py) and their
    progress is derived from live state, so nothing about the *definition* lives
    here — this table only remembers which one-time rewards a player has already
    taken, so they can't be claimed twice."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="achievement_claims")
    key = models.CharField(max_length=48)
    claimed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="uq_achievement_claim")
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.key}"


class Egg(models.Model):
    """An egg laid in the Monster Cave, incubating on its own timer.

    Split from BreedingJob on purpose: BreedingJob is only the *mating* phase
    (parents locked). When that finishes the parents are freed and one of these
    is laid, so the player can send a new pair into the cave while this egg keeps
    incubating — there can be several eggs at once.

    The egg's contents stay a mystery: the rarity/species roll happens at hatch,
    from the recipe frozen here at lay time (base rarity, upgrade odds, both
    parents' name/element, inherited level). Freezing the recipe means hatching
    doesn't depend on the now-freed parents, which may have been leveled, used,
    or even sent back into the cave in the meantime."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="eggs")
    base_rarity = models.CharField(max_length=16, default="common")
    upgrade_chance = models.FloatField(default=0.0)
    parent_a_name = models.CharField(max_length=64)
    parent_a_element = models.CharField(max_length=16)
    parent_b_name = models.CharField(max_length=64)
    parent_b_element = models.CharField(max_length=16)
    inherit_level = models.IntegerField(default=1)
    started_at = models.DateTimeField(auto_now_add=True)
    finishes_at = models.DateTimeField()
    notified = models.BooleanField(default=False)  # "egg ready to hatch" DM sent

    class Meta:
        ordering = ("finishes_at",)

    def __str__(self) -> str:
        return f"egg<{self.parent_a_name}+{self.parent_b_name}> ({self.owner_id})"


class WordRewardClaim(models.Model):
    """Cooldown for the group chat's periodic reward words.

    One row per (user, group), NOT per word: «جایزه», «شانس», «گنج» and the rest
    all read and write this same timestamp, which is what makes their cooldown
    shared. Storing it per word would let someone cycle the synonyms and collect
    once per word."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="word_rewards")
    # string reference: Group is declared further down this module
    group = models.ForeignKey("Group", on_delete=models.CASCADE, related_name="word_rewards")
    last_claimed_at = models.DateTimeField()
    total_claims = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "group"], name="uq_word_reward_claim")
        ]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.group_id} x{self.total_claims}"


class SpeedupCard(models.Model):
    """Consumable items that shave time off the active BuildingUpgrade. `minutes`
    is one of game.constants.SPEEDUP_MINUTES — a fixed, small set of denominations,
    so a per-(owner, minutes) counter is simpler than a row-per-card table."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="speedup_cards")
    minutes = models.IntegerField()
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "minutes"], name="uq_owner_speedup_minutes")
        ]

    def __str__(self) -> str:
        return f"{self.minutes}m x{self.count} ({self.owner_id})"


class Group(models.Model):
    id = models.BigIntegerField(primary_key=True)  # telegram chat id
    title = models.CharField(max_length=128, null=True, blank=True)
    guardian_creature = models.ForeignKey(
        Creature, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.title or str(self.id)


class RaidBoss(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="raid_bosses")
    name = models.CharField(max_length=64)
    element = models.CharField(max_length=16)
    max_hp = models.IntegerField()
    current_hp = models.IntegerField()
    is_active = models.BooleanField(default=True)
    spawned_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.current_hp}/{self.max_hp})"


class RaidDamageLog(models.Model):
    raid = models.ForeignKey(RaidBoss, on_delete=models.CASCADE, related_name="damage_logs")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    creature = models.ForeignKey(Creature, on_delete=models.CASCADE)
    damage = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class GroupMembership(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="uq_group_member")]


class GroupEventLog(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    event_key = models.CharField(max_length=32)
    day = models.CharField(max_length=10)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "event_key", "day"], name="uq_group_event")
        ]


class DailyActionLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    action = models.CharField(max_length=32)
    day = models.CharField(max_length=10)  # "YYYY-MM-DD" in the game timezone (game.daily.today_str)
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "action", "day"], name="uq_daily_action")
        ]


class MissionClaim(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    mission_key = models.CharField(max_length=32)
    day = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "mission_key", "day"], name="uq_mission_claim")
        ]


class InteractiveBattle(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    player_a = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    player_b = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    creature_a = models.ForeignKey(Creature, on_delete=models.CASCADE, related_name="+")
    creature_b = models.ForeignKey(Creature, on_delete=models.CASCADE, related_name="+")

    hp_a = models.IntegerField()
    hp_b = models.IntegerField()
    skill_uses_a = models.IntegerField(default=2)
    skill_uses_b = models.IntegerField(default=2)
    shield_active_a = models.BooleanField(default=False)
    shield_active_b = models.BooleanField(default=False)
    stunned_a = models.BooleanField(default=False)
    stunned_b = models.BooleanField(default=False)

    turn = models.CharField(max_length=1)  # "a" or "b"
    status = models.CharField(max_length=16, default="pending")  # pending/active/finished/declined
    log = models.TextField(default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DuelLog(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    challenger = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    opponent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    winner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    wager_gold = models.IntegerField(default=0)
    log_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class AttackLog(models.Model):
    """One arena raid. `defender` is null for a bot opponent — those exist so the
    arena still works on a small player base, and `defender_label` keeps the fake
    lab name that was shown so the defender's raid history reads consistently."""

    attacker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attacks_made")
    defender = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="attacks_received"
    )
    defender_label = models.CharField(max_length=64)  # lab name shown at raid time (real or fake)
    is_fake_defender = models.BooleanField(default=False)
    attacker_won = models.BooleanField()
    loot_gold = models.IntegerField(default=0)
    cup_delta = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    defender_notified = models.BooleanField(default=False)  # "you were raided" DM sent to the defender

    def __str__(self) -> str:
        return f"{self.attacker_id} -> {self.defender_label} ({'W' if self.attacker_won else 'L'})"


class SeasonResult(models.Model):
    """A player's finishing position in one weekly cup season.

    Written when a season is closed out. `cup_before` is what they ended on and
    `cup_after` is the floor they were reset to — kept so the history explains
    itself without re-deriving the reset formula that was in force at the time."""

    week_key = models.CharField(max_length=10)  # ISO "YYYY-Www", e.g. 2026-W33
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="season_results")
    rank = models.IntegerField()
    cup_before = models.IntegerField()
    cup_after = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["week_key", "user"], name="uq_season_result")
        ]
        ordering = ("-week_key", "rank")

    def __str__(self) -> str:
        return f"{self.week_key} #{self.rank} — {self.user_id}"


class SeasonState(models.Model):
    """Single-row table remembering which week has already been closed out, so a
    season is never settled twice no matter how often the check runs."""

    last_closed_week = models.CharField(max_length=10, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"last closed: {self.last_closed_week or '—'}"


class EmojiOverride(models.Model):
    """Maps a semantic key (e.g. "coin") to a Telegram Premium custom emoji, set by
    the bot owner via /set_emoji. Rendered with <tg-emoji emoji-id="..."> in HTML
    messages; falls back to `placeholder` for everyone else (non-Premium viewers
    still see `placeholder` too — Premium is what unlocks the custom graphic)."""

    key = models.CharField(max_length=32, unique=True)
    custom_emoji_id = models.CharField(max_length=64)
    placeholder = models.CharField(max_length=16)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.key} -> {self.placeholder}"


class ButtonEmojiOverride(models.Model):
    """Premium custom emoji shown as a button's leading icon, set by the owner.

    Separate from EmojiOverride on purpose: message-body emoji are rendered with
    <tg-emoji> inside HTML text, while a button icon is a bare
    `icon_custom_emoji_id` field on InlineKeyboardButton. They're different
    mechanisms with different keys, so the owner can style a button independently
    of the matching in-text emoji."""

    key = models.CharField(max_length=48, unique=True)  # one of game.button_emoji.BUTTON_EMOJI_DEFS
    custom_emoji_id = models.CharField(max_length=64)
    placeholder = models.CharField(max_length=16)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.key} -> {self.placeholder}"


class ButtonStyleOverride(models.Model):
    """Remaps one semantic button *role* to a Telegram button colour.

    Rows only exist for roles that differ from game.button_style.ROLE_DEFS, so an
    empty table means "stock palette" — which keeps the default reachable by
    deletion rather than by storing a copy of it."""

    role = models.CharField(max_length=32, unique=True)  # one of game.button_style.ROLE_DEFS
    style = models.CharField(max_length=16, blank=True)  # "" | primary | success | danger
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.role} -> {self.style or 'none'}"


class ButtonKeyStyle(models.Model):
    """Colour for one *specific* button, overriding whatever its role says.

    Keyed by game.button_emoji.BUTTON_EMOJI_DEFS — the same registry that names
    buttons for their Premium icons — so the panel can show one row per button
    with both its icon and its colour. Only buttons built with an ``emoji_key``
    can be targeted this way; everything else follows its role.

    A missing row means "follow the role". An empty ``style`` is different: it
    means the operator explicitly asked for no colour on this one button."""

    key = models.CharField(max_length=48, unique=True)
    style = models.CharField(max_length=16, blank=True)  # "" | primary | success | danger
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.key} -> {self.style or 'none'}"


class ThemeLoadout(models.Model):
    """A saved "look" of the bot — emoji + button colours, stored as one JSON blob.

    The payload is denormalised on purpose. A loadout is a *snapshot*, so it must
    keep meaning what it meant on the day it was saved; foreign keys to the live
    override tables would let editing today's theme silently rewrite yesterday's
    saved one. It also makes export/import a file copy instead of a graph walk.

    Contains no game state — see game/theme.py for why."""

    name = models.CharField(max_length=48, unique=True)
    note = models.CharField(max_length=200, blank=True, default="")
    payload = models.TextField()  # JSON, schema game.theme.SNAPSHOT_VERSION
    is_active = models.BooleanField(default=False)  # last one applied
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_active", "name")

    def __str__(self) -> str:
        return f"{self.name}{' ✓' if self.is_active else ''}"


class RequiredChannel(models.Model):
    """A channel players must join before using the bot at all (enforced by
    bot.middleware.enforce_force_join). Added by forwarding a message from the
    channel — chat_id/username/title come straight from that forward, never typed."""

    chat_id = models.BigIntegerField(unique=True)  # telegram channel id
    username = models.CharField(max_length=64, null=True, blank=True)  # without '@'
    title = models.CharField(max_length=128, null=True, blank=True)
    invite_link = models.CharField(max_length=256, null=True, blank=True)
    reward_coins = models.IntegerField(default=0)
    reward_dna = models.IntegerField(default=0)
    reward_diamonds = models.IntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = permanent
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.title or (f"@{self.username}" if self.username else str(self.chat_id))


class ChannelJoinClaim(models.Model):
    """Records that `user` already collected the join reward for `channel`, so
    leaving and rejoining can't be used to farm the reward repeatedly."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    channel = models.ForeignKey(RequiredChannel, on_delete=models.CASCADE)
    claimed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "channel"], name="uq_channel_join_claim")
        ]
