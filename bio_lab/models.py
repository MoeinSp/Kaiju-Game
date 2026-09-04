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

    energy = models.IntegerField(default=50)  # keep in sync with game.constants.MAX_ENERGY
    energy_updated_at = models.DateTimeField(default=timezone.now)

    # overall progress for the lab as a whole, distinct from any one creature's
    # level — see game/lab.py, which owns the curve and the award table
    lab_xp = models.IntegerField(default=0)

    login_streak = models.IntegerField(default=0)
    last_login_day = models.CharField(max_length=10, null=True, blank=True)  # "YYYY-MM-DD" in the game timezone (game.daily.today_str)

    cup = models.IntegerField(default=0)  # arena rating; drives PvP matchmaking
    shield_until = models.DateTimeField(null=True, blank=True)  # anti-farm grace after being raided (arena)
    group_shield_until = models.DateTimeField(null=True, blank=True)  # separate 4h grace after a group «اتک»

    # «جایزه»/«کایجو» word-reward cooldown — GLOBAL per player (not per-group), so
    # joining the bot to 30 groups no longer multiplies the payout. reward_ready_at
    # is when the next claim is allowed; reward_total_claims is the lifetime count.
    reward_ready_at = models.DateTimeField(null=True, blank=True)
    reward_total_claims = models.IntegerField(default=0)
    # global cooldown on winning a group flash-drop, for the same anti-multi-group reason
    drop_claim_ready_at = models.DateTimeField(null=True, blank=True)
    # separate, stricter cooldown specifically for winning a DIAMOND VEIN drop — plus a
    # daily cap (DailyActionLog "diamond_vein") — so diamonds can't be farmed by sweeping
    # veins across dozens of groups. See game/groupdrops.claim().
    vein_claim_ready_at = models.DateTimeField(null=True, blank=True)

    # trading (game/transfer.py): a creature and an equipment transfer each carry a
    # 1-day cooldown that applies to BOTH sending and receiving, so a player (or a
    # fake account) can't rapidly funnel items around
    kaiju_transfer_ready_at = models.DateTimeField(null=True, blank=True)
    equip_transfer_ready_at = models.DateTimeField(null=True, blank=True)

    # re-engagement push (game/notifications.py). notifications_on is the master
    # opt-out; energy_full_notified fires the "energy is full" DM exactly once per
    # drain-and-refill cycle (reset when energy is spent); last_nudge_day dedups the
    # once-a-day "your daily reward is waiting" nudge.
    notifications_on = models.BooleanField(default=True)
    energy_full_notified = models.BooleanField(default=False)
    last_nudge_day = models.CharField(max_length=10, null=True, blank=True)

    # referrals (game/referral.py). referred_by is the telegram id of whoever's
    # invite link brought this player in (set once, at their first /start);
    # referral_bonus_paid marks that both sides have collected the reward earned
    # when this player crossed the referral milestone.
    referred_by = models.BigIntegerField(null=True, blank=True)
    referral_bonus_paid = models.BooleanField(default=False)

    # highest PvE campaign stage cleared (game/campaign.py); 0 = none yet
    campaign_stage = models.IntegerField(default=0)

    # last day the player claimed the limited-time event's daily reward
    # (game/events.py), so it can be claimed once per day during an event
    last_event_claim_day = models.CharField(max_length=10, null=True, blank=True)

    # gacha pity counter for the featured banner (game/banner.py): pulls since the
    # last legendary+; at the pity threshold the next pull is a guaranteed legendary
    banner_pity = models.IntegerField(default=0)

    # idle/AFK rewards (game/idle.py): loot accrues from this timestamp and is
    # collected on return; last_dungeon_day gates the once-a-day resource dungeon.
    idle_since = models.DateTimeField(default=timezone.now)
    last_dungeon_day = models.CharField(max_length=10, null=True, blank=True)

    # cosmetic prestige (game/titles.py): the title key the player has equipped to
    # show on their profile/leaderboards; last_shop_day gates the daily rotating shop
    title = models.CharField(max_length=32, null=True, blank=True)
    last_shop_day = models.CharField(max_length=10, null=True, blank=True)

    # how many building upgrades this player can run at once. Starts at 1 (the single
    # «کارگر»); buying the 2nd builder from the shop bumps it to 2 (game/buildings.py).
    builder_slots = models.IntegerField(default=1)

    alliance = models.ForeignKey(
        "Alliance", null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )

    is_banned = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)  # granted by the owner; full panel except admin management
    # blocked specifically from the in-bot purchase / receipt flow (a lighter sanction
    # than a full ban — they can still play, just can't submit payment receipts)
    receipt_blocked = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.username or self.first_name or f"Player {self.id}"


class Alliance(models.Model):
    name = models.CharField(max_length=64, unique=True)
    leader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="+")
    deputy = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    treasury_gold = models.IntegerField(default=0)
    last_heisted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # treasury-funded, alliance-wide buildings (game/alliance.py) — every member
    # benefits. xp/pass are the original two; fortress/barracks/vault were added
    # later. All share the buy_perk() upgrade machinery and a per-building level.
    xp_perk_level = models.IntegerField(default=0)       # آکادمی — +lab XP
    pass_perk_level = models.IntegerField(default=0)     # معبد — +battle-pass points
    fortress_level = models.IntegerField(default=0)      # دژ — less gold stolen in heists
    barracks_level = models.IntegerField(default=0)      # پادگان — +power in the 1-day war
    vault_level = models.IntegerField(default=0)         # خزانه — passive daily treasury income
    vault_collected_at = models.DateTimeField(null=True, blank=True)  # last vault income collection
    hall_level = models.IntegerField(default=0)          # تالار — +10 member capacity per level (base 50, max 100)

    # weekly alliance war: members' activity adds war points; the top alliance's
    # treasury wins a bonus at week's end. war_week scopes points to the current
    # ISO week so they reset cleanly.
    war_points = models.IntegerField(default=0)
    war_week = models.CharField(max_length=10, null=True, blank=True)

    # membership gate: auto_accept=True → anyone who meets min_join_power joins
    # instantly (the original behaviour). False → they send a request the leader or
    # deputy approves. min_join_power is the minimum active-creature power to join/request.
    auto_accept = models.BooleanField(default=True)
    min_join_power = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.name


class AllianceJoinRequest(models.Model):
    """A pending request to join an alliance (only used when auto_accept is off).

    A player may have several outstanding requests to different alliances at once;
    the FIRST one accepted wins and the rest are invalidated (they've joined
    elsewhere). Unique per (user, alliance) so re-sending is idempotent."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="join_requests")
    alliance = models.ForeignKey(Alliance, on_delete=models.CASCADE, related_name="join_requests")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "alliance"], name="uq_alliance_join_request")
        ]
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.user_id} → {self.alliance_id}"


class AllianceWarState(models.Model):
    """Single-row table remembering the last week whose alliance war was settled,
    so the weekly treasury bonus is paid exactly once (mirrors SeasonState)."""

    last_settled_week = models.CharField(max_length=10, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"war settled: {self.last_settled_week or '—'}"


class AllianceWar(models.Model):
    """A one-day war between two alliances (game/alliance.py). Matchmaking pairs
    similar-strength alliances; each side starts with a base score (member power ×
    barracks bonus) and members «rally» to add more. When ends_at passes the higher
    score wins a treasury bonus. Settled exactly once by the notification job."""

    ACTIVE = "active"
    SETTLED = "settled"

    alliance_a = models.ForeignKey(Alliance, on_delete=models.CASCADE, related_name="wars_as_a")
    alliance_b = models.ForeignKey(Alliance, on_delete=models.CASCADE, related_name="wars_as_b")
    score_a = models.IntegerField(default=0)
    score_b = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=12, default=ACTIVE)  # active | settled
    winner = models.ForeignKey(Alliance, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    def __str__(self) -> str:
        return f"{self.alliance_a_id} vs {self.alliance_b_id} ({self.status})"


class AllianceWarHit(models.Model):
    """One member's rally in a war — capped at one per member per war (unique)."""

    war = models.ForeignKey(AllianceWar, on_delete=models.CASCADE, related_name="hits")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    power = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["war", "user"], name="uq_alliance_war_hit")
        ]


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
    # production accrued but not yet collected, LOCKED at the rate it was earned at.
    # Changing workers folds the current accrual in here (instead of auto-collecting it),
    # so a worker swap never loses the pending output AND a strong worker can't retro-
    # boost hours already earned. total pending = banked_pending + accrual-since-last.
    banked_pending = models.FloatField(default=0.0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "building_type"], name="uq_owner_building_type")
        ]

    def __str__(self) -> str:
        return f"{self.building_type} Lv{self.level} ({self.owner_id})"


class BuildingUpgrade(models.Model):
    """An active upgrade job. A player may run up to User.builder_slots of these at
    once (1 by default, 2 after buying the second builder) — the cap is enforced in
    game/buildings.start_upgrade, and a per-building uniqueness guard stops the same
    building being upgraded twice concurrently."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="building_upgrades")
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


class Team(models.Model):
    """A player's chosen squad of up to three creatures for 3v3 team battles
    (game/teambattle.py, used by the campaign). Slots are nullable and SET_NULL so
    fusing or releasing a creature just empties its slot rather than erroring."""

    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name="team")
    slot1 = models.ForeignKey(Creature, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    slot2 = models.ForeignKey(Creature, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    slot3 = models.ForeignKey(Creature, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def creatures(self) -> list:
        return [c for c in (self.slot1, self.slot2, self.slot3) if c is not None]

    def __str__(self) -> str:
        return f"team of {self.owner_id}"


class CodexEntry(models.Model):
    """One species a player has discovered (ever owned). Persistent so the Codex
    stays complete even after the creature is fused or released. Recorded lazily
    from currently-owned creatures whenever the Codex is viewed (game/codex.py)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="codex_entries")
    species = models.CharField(max_length=64)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "species"], name="uq_codex_entry")
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.species}"


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
    base_rarity = models.CharField(max_length=16, default="common")  # the TOP attainable rarity
    upgrade_chance = models.FloatField(default=0.0)                  # P(hit base_rarity)
    # what the egg becomes on a miss. Empty for legacy eggs → hatch() falls back to
    # one tier below base_rarity (the old behaviour).
    fallback_rarity = models.CharField(max_length=16, default="", blank=True)
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
    raid_level = models.IntegerField(default=1)  # climbs each time the group fells a raid boss
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.title or str(self.id)


class GroupDrop(models.Model):
    """A flash reward dropped into a group chat. The bot posts a message with a
    claim button; the FIRST member to tap it wins a random reward scaled to their
    own level/power. `claimed_by` null = still up for grabs. Reliability note:
    claiming is a button (callback query), which always reaches the bot regardless
    of group privacy mode — unlike reading a chat message."""

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="drops")
    kind = models.CharField(max_length=24)  # one of game.groupdrops.DROP_KINDS
    message_id = models.IntegerField(null=True, blank=True)  # set after posting, for expiry edits
    claimed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    claimed_at = models.DateTimeField(null=True, blank=True)  # when the winner claimed it
    reward_json = models.TextField(blank=True, default="")  # what the winner got, for display
    expires_at = models.DateTimeField()
    expired_notified = models.BooleanField(default=False)  # message edited to "expired"
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.kind}@{self.group_id} {'claimed' if self.claimed_by_id else 'open'}"


class RaidBoss(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="raid_bosses")
    name = models.CharField(max_length=64)
    element = models.CharField(max_length=16)
    level = models.IntegerField(default=1)  # the group's raid level when this boss spawned
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
    attacker_label = models.CharField(max_length=64, default="")  # attacker's lab name at raid time
    attacker_power = models.IntegerField(default=0)              # attacker's creature power at raid time
    defender_label = models.CharField(max_length=64)  # lab name shown at raid time (real or fake)
    is_fake_defender = models.BooleanField(default=False)
    attacker_won = models.BooleanField()
    loot_gold = models.IntegerField(default=0)
    cup_delta = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    defender_notified = models.BooleanField(default=False)  # "you were raided" DM sent to the defender
    revenge_taken = models.BooleanField(default=False)      # defender has revenged this attack

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


class ShopItem(models.Model):
    """An owner-authored shop offer (game/itemshop.py). A single item OR a multi-part
    «pack» — the contents are a JSON list of reward components (coins/dna/diamonds/
    speedup/creature/equipment). Priced in coins and/or diamonds, both optional."""

    title = models.CharField(max_length=64)
    emoji = models.CharField(max_length=8, default="🎁")
    description = models.CharField(max_length=256, blank=True, default="")
    price_coins = models.IntegerField(default=0)
    price_diamonds = models.IntegerField(default=0)
    contents_json = models.TextField(default="[]")  # list[{"type": ..., ...}]
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    max_per_user = models.IntegerField(default=0)  # 0 = unlimited; N = each player may buy at most N
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.title} ({self.price_coins}c/{self.price_diamonds}d)"


class ShopItemPurchase(models.Model):
    """How many times a given player has bought a given owner-authored ShopItem —
    used to enforce ShopItem.max_per_user."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    item = models.ForeignKey(ShopItem, on_delete=models.CASCADE, related_name="purchases")
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "item"], name="uq_shop_purchase")]


class BotConfig(models.Model):
    """Single-row (id=1) table of owner-tunable global settings that aren't part of
    a theme/loadout and aren't game state — e.g. the public "game group" the bot
    invites players into from the main menu. Read through an in-memory cache
    (game.botconfig) because it's consulted while building keyboards in async
    handler code, where a lazy DB query would raise SynchronousOnlyOperation."""

    group_game_url = models.CharField(max_length=256, default="", blank=True)  # https://t.me/... invite/link
    group_game_title = models.CharField(max_length=48, default="", blank=True)  # button label override
    # owner-configurable "buy in-game" button shown at the bottom of the main menu —
    # can point anywhere (a payment bot, a channel post, a site). Empty = no button.
    buy_url = models.CharField(max_length=256, default="", blank=True)
    buy_title = models.CharField(max_length=48, default="", blank=True)  # button label override
    backup_interval_hours = models.IntegerField(default=0)  # 0 = auto-backup off
    backup_last_at = models.DateTimeField(null=True, blank=True)  # last auto-backup sent
    # where auto-backups are sent; null = the owner's own DM (the default)
    backup_chat_id = models.BigIntegerField(null=True, blank=True)
    # diamonds charged for an instant full energy refill (the «شارژ انرژی» button).
    # Owner-tunable from the admin panel; read through the game.botconfig cache because
    # the refill button is built inside async handler code.
    energy_refill_diamonds = models.IntegerField(default=25)
    # ── in-bot purchase (buy gold/DNA/diamonds with real money) ───────────────
    # Toman price per unit the owner charges; 0 on a resource = not for sale.
    buy_price_per_gold = models.FloatField(default=0.0)      # Toman per 1 gold
    buy_price_per_dna = models.FloatField(default=0.0)       # Toman per 1 DNA
    buy_price_per_diamond = models.FloatField(default=0.0)   # Toman per 1 diamond
    buy_card_number = models.CharField(max_length=64, default="", blank=True)
    buy_card_holder = models.CharField(max_length=96, default="", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"group={self.group_game_url or '—'}"


class DailyShopOffer(models.Model):
    """Owner-editable pricing/availability for one rotating daily-shop offer. There's
    one row per game.shop POOL key — the offer's contents/title/emoji stay defined in
    code (keyed by `key`), and the owner only tunes price / currency / on-off from the
    admin panel. Rows are seeded lazily from the code defaults on first read."""

    key = models.CharField(max_length=32, unique=True)
    cost = models.IntegerField(default=0)
    currency = models.CharField(max_length=12, default="coins")  # "coins" | "diamonds"
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.key}: {self.cost} {self.currency}{'' if self.is_active else ' (off)'}"


class DailyShopDay(models.Model):
    """A per-day schedule for the daily shop, on a repeating 3-day cycle. `slot` is
    `date.toordinal() % 3`, so the same slot recurs every 3 days — which is exactly
    the "if a day isn't set, repeat what it was 3 days ago" behaviour: a configured
    slot keeps showing until the owner reconfigures it. `offers_json` is the ordered
    list of {key, cost, currency} shown that day (first = featured). An unconfigured
    slot falls back to the default rotating pool."""

    slot = models.IntegerField(unique=True)  # date.toordinal() % 3
    offers_json = models.TextField(default="[]")  # [{"key","cost","currency"}]
    configured = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"day-slot {self.slot} ({'set' if self.configured else 'unset'})"


class DailyShopItem(models.Model):
    """The daily-shop CATALOG: every offer the shop can show, owner-managed. The 7
    built-in offers are seeded here on first use; the owner can add new ones (a kaiju,
    equipment, packs — any itemshop contents) and permanently delete any of them.
    `cost`/`currency` are the default price; per-day scheduling (DailyShopDay) can
    override price/limit per slot. `contents_json` is the itemshop reward-component
    list, granted via game.itemshop.grant_contents."""

    key = models.CharField(max_length=40, unique=True)
    emoji = models.CharField(max_length=8, default="🎁")
    title = models.CharField(max_length=64)
    contents_json = models.TextField(default="[]")  # itemshop components
    cost = models.IntegerField(default=0)
    currency = models.CharField(max_length=12, default="coins")  # "coins" | "diamonds"
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.title} ({self.key})"


class DailyShopPurchase(models.Model):
    """How many times a player has bought a given daily-shop offer TODAY — enforces the
    owner-set per-offer purchase limit (1 / 2 / unlimited). Keyed by the game-timezone
    day string so the limit resets each day."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    key = models.CharField(max_length=32)
    day = models.CharField(max_length=10)  # game.daily.today_str()
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key", "day"], name="uq_daily_shop_purchase")
        ]


class DailyResourceGain(models.Model):
    """How much gold / DNA / diamonds a player GAINED on a given day, broken down by
    source. Written by game.ledger.record_gain at the main income points; read by the
    owner's player-search to spot suspicious daily jumps (e.g. a diamond balloon).
    Positive gains only — spending is not recorded here."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    day = models.CharField(max_length=10)  # game.daily.today_str()
    source = models.CharField(max_length=32)  # 'hunt','arena','raid','mission','drop',…
    coins = models.BigIntegerField(default=0)
    dna = models.BigIntegerField(default=0)
    diamonds = models.BigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "day", "source"], name="uq_daily_resource_gain")
        ]
        indexes = [models.Index(fields=["user", "day"], name="bio_lab_dai_user_id_day_idx")]


class PurchaseRequest(models.Model):
    """One in-bot purchase: the player picks how much gold/DNA/diamonds to buy, the bot
    quotes a Toman price and shows the owner's card, the player uploads a receipt photo,
    and the owner approves/rejects it. Approval grants the resources."""

    STATUS_CHOICES = [
        ("awaiting_receipt", "awaiting_receipt"),
        ("pending", "pending"),
        ("approved", "approved"),
        ("rejected", "rejected"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="purchases")
    coins = models.BigIntegerField(default=0)
    dna = models.BigIntegerField(default=0)
    diamonds = models.BigIntegerField(default=0)
    price_toman = models.BigIntegerField(default=0)
    status = models.CharField(max_length=20, default="awaiting_receipt")
    receipt_file_id = models.CharField(max_length=256, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]
