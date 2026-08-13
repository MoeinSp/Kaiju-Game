from django.db import models
from django.utils import timezone


class User(models.Model):
    """A telegram player. NOT django.contrib.auth's User — this app has no
    login of its own; Django admin's staff accounts are a separate model."""

    id = models.BigIntegerField(primary_key=True)  # telegram user id
    username = models.CharField(max_length=64, null=True, blank=True)
    first_name = models.CharField(max_length=128, null=True, blank=True)
    coins = models.IntegerField(default=200)
    dna_fragments = models.IntegerField(default=0)

    energy = models.IntegerField(default=20)  # keep in sync with game.constants.MAX_ENERGY
    energy_updated_at = models.DateTimeField(default=timezone.now)

    login_streak = models.IntegerField(default=0)
    last_login_day = models.CharField(max_length=10, null=True, blank=True)  # "YYYY-MM-DD" (UTC)

    alliance = models.ForeignKey(
        "Alliance", null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.username or self.first_name or f"Player {self.id}"


class Alliance(models.Model):
    name = models.CharField(max_length=64, unique=True)
    leader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Creature(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="creatures")
    name = models.CharField(max_length=64)
    element = models.CharField(max_length=16)
    rarity = models.CharField(max_length=16, default="common")
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
    day = models.CharField(max_length=10)  # "YYYY-MM-DD" (UTC)
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
    log_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


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
