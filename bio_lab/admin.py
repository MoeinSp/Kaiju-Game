from django.contrib import admin

from bio_lab.models import (
    Alliance,
    Building,
    BuildingUpgrade,
    ChannelJoinClaim,
    Creature,
    DailyActionLog,
    DuelLog,
    EmojiOverride,
    Equipment,
    Group,
    GroupEventLog,
    GroupMembership,
    InteractiveBattle,
    MissionClaim,
    RaidBoss,
    RaidDamageLog,
    RequiredChannel,
    SpeedupCard,
    User,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "username",
        "first_name",
        "lab_name",
        "coins",
        "dna_fragments",
        "diamonds",
        "energy",
        "login_streak",
        "alliance",
        "is_banned",
        "created_at",
    )
    list_filter = ("alliance", "is_banned")
    search_fields = ("username", "first_name", "id")
    ordering = ("-created_at",)


@admin.register(Alliance)
class AllianceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "leader", "member_count", "treasury_gold", "last_heisted_at", "created_at")
    search_fields = ("name",)

    @admin.display(description="اعضا")
    def member_count(self, obj: Alliance) -> int:
        return obj.members.count()


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "slot", "rarity", "level", "equipped_on", "created_at")
    list_filter = ("slot", "rarity")
    search_fields = ("name", "owner__username", "template_key")


@admin.register(EmojiOverride)
class EmojiOverrideAdmin(admin.ModelAdmin):
    list_display = ("key", "placeholder", "custom_emoji_id", "updated_at")


@admin.register(Creature)
class CreatureAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "element", "rarity", "star_level", "level", "is_active", "created_at")
    list_filter = ("element", "rarity", "star_level", "is_active")
    search_fields = ("name", "owner__username")


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "building_type", "level", "last_collected_at")
    list_filter = ("building_type",)


@admin.register(BuildingUpgrade)
class BuildingUpgradeAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "building", "target_level", "started_at", "finishes_at")


@admin.register(SpeedupCard)
class SpeedupCardAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "minutes", "count")
    list_filter = ("minutes",)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "guardian_creature", "created_at")
    search_fields = ("title",)


@admin.register(RaidBoss)
class RaidBossAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "name", "element", "current_hp", "max_hp", "is_active", "spawned_at")
    list_filter = ("is_active", "element")


@admin.register(RaidDamageLog)
class RaidDamageLogAdmin(admin.ModelAdmin):
    list_display = ("id", "raid", "user", "creature", "damage", "created_at")
    ordering = ("-created_at",)


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "user")


@admin.register(GroupEventLog)
class GroupEventLogAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "event_key", "day")
    list_filter = ("event_key",)


@admin.register(DailyActionLog)
class DailyActionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "action", "day", "count")
    list_filter = ("action",)


@admin.register(MissionClaim)
class MissionClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "mission_key", "day", "created_at")
    list_filter = ("mission_key",)


@admin.register(InteractiveBattle)
class InteractiveBattleAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "player_a", "player_b", "status", "turn", "created_at")
    list_filter = ("status",)


@admin.register(DuelLog)
class DuelLogAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "challenger", "opponent", "winner", "created_at")
    ordering = ("-created_at",)


@admin.register(RequiredChannel)
class RequiredChannelAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "username", "chat_id", "reward_coins", "reward_dna", "expires_at", "created_at")


@admin.register(ChannelJoinClaim)
class ChannelJoinClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "channel", "claimed_at")
    ordering = ("-claimed_at",)
