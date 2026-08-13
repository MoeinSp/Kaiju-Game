from django.contrib import admin

from bio_lab.models import (
    Alliance,
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
    User,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "username",
        "first_name",
        "coins",
        "dna_fragments",
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
    list_display = ("id", "name", "owner", "element", "rarity", "level", "is_active", "created_at")
    list_filter = ("element", "rarity", "is_active")
    search_fields = ("name", "owner__username")


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
