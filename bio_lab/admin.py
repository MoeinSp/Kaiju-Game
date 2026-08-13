from django.contrib import admin

from bio_lab.models import (
    Creature,
    DailyActionLog,
    DuelLog,
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
    list_display = ("id", "username", "first_name", "coins", "dna_fragments", "created_at")
    search_fields = ("username", "first_name", "id")
    ordering = ("-created_at",)


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
