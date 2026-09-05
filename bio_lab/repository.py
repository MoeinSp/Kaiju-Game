import html

from bio_lab.models import Creature, Group, GroupMembership, User


def get_or_create_user(tg_user) -> tuple[User, bool]:
    user, created = User.objects.get_or_create(
        id=tg_user.id, defaults={"username": tg_user.username, "first_name": tg_user.first_name}
    )
    if not created and (user.username != tg_user.username or user.first_name != tg_user.first_name):
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.save(update_fields=["username", "first_name"])
    return user, created


def display_name(user: User) -> str:
    """Escaped for direct interpolation into parse_mode="HTML" messages — usernames
    and first names are user-controlled and may contain '<', '&', etc."""
    if user.username:
        return f"@{html.escape(user.username)}"
    if user.first_name:
        return html.escape(user.first_name)
    return f"بازیکن {user.id}"


def mention(user: User) -> str:
    """A clickable Telegram mention of the account (tg://user link), for leaderboards
    where the person — not the lab or creature — is the identity. The visible label
    is the escaped account name; tapping it opens the user's profile."""
    label = display_name(user)
    return f'<a href="tg://user?id={user.id}">{label}</a>'


def lab_display(user: User) -> str:
    """The lab's name, escaped for parse_mode="HTML" message bodies.

    Lab names are typed by the player at their first /start, so they're exactly
    as untrusted as usernames — a lab called ``<b>`` or ``a & b`` would break
    (or inject into) every leaderboard it appears on. Every screen that shows a
    lab goes through here for the same reason display_name() exists.

    Falls back to a stable placeholder rather than the player's @username: these
    are game-facing lists where the lab is the identity, not the person."""
    if user.lab_name:
        return html.escape(user.lab_name)
    return f"آزمایشگاه {user.id}"


def get_or_create_group(chat) -> Group:
    group, _ = Group.objects.get_or_create(id=chat.id, defaults={"title": chat.title})
    return group


def creature_name(creature: Creature) -> str:
    """A creature's display name: the player's chosen nickname (نام) if set, else its
    species/breed (نژاد). Custom names are validated in game/naming.py to contain no
    HTML metacharacters, so this is safe to interpolate into parse_mode="HTML" bodies
    exactly like the trusted species name it replaces."""
    nick = (getattr(creature, "custom_name", "") or "").strip()
    return nick or creature.name


def creature_has_nickname(creature: Creature) -> bool:
    """True when the player has given this creature a custom name distinct from its
    breed — the cue to also show a «نژاد: …» line beneath the name on cards."""
    nick = (getattr(creature, "custom_name", "") or "").strip()
    return bool(nick) and nick != creature.name


def get_active_creature(user: User) -> Creature | None:
    return Creature.objects.filter(owner=user, is_active=True).first()


def team_choices(user: User, limit: int = 3) -> list[Creature]:
    """The creatures a player can pick to fight with — their configured Team (up to 3),
    or, if they haven't set one, their strongest few. The active creature may be among
    them, which is fine. Used by the «انتخاب موجود دیگر از تیم» swap on attack screens."""
    from bio_lab.models import Team

    team = Team.objects.filter(owner=user).select_related("slot1", "slot2", "slot3").first()
    if team is not None:
        picked = [c for c in team.creatures() if c is not None]  # .creatures() is a method
        if picked:
            return picked[:limit]
    # no team set → the strongest few by base-stat sum (cheap proxy; the caller shows
    # the exact power on each button)
    creatures = list(Creature.objects.filter(owner=user))
    creatures.sort(key=lambda c: c.base_hp + c.base_atk + c.base_def + c.base_spd, reverse=True)
    return creatures[:limit]


def touch_membership(group: Group, user: User) -> None:
    """Records that `user` has been active in `group`, so group-scoped features
    (leaderboard, guardian) know who to consider."""
    GroupMembership.objects.get_or_create(group=group, user=user)


def group_member_creatures(group: Group) -> list[Creature]:
    member_ids = GroupMembership.objects.filter(group=group).values_list("user_id", flat=True)
    # select_related("owner") so leaderboards can mention the account without a lazy
    # FK query firing on the event loop (SynchronousOnlyOperation)
    return list(Creature.objects.filter(owner_id__in=member_ids, is_active=True).select_related("owner"))


def resolve_user(identifier: str) -> User | None:
    """Looks a player up for owner-only moderation, by any of: numeric telegram id,
    @username, or lab name (case-insensitive). Lab names are unique (enforced when
    set — see bot.handlers.private), so they're a safe identifier."""
    identifier = identifier.strip().lstrip("@")
    if identifier.isdigit():
        return User.objects.filter(id=int(identifier)).first()
    return (
        User.objects.filter(username__iexact=identifier).first()
        or User.objects.filter(lab_name__iexact=identifier).first()
    )


def lab_name_taken(name: str, exclude_user_id: int | None = None) -> bool:
    """Whether another player already uses this lab name (case-insensitive). Used
    to keep lab names unique so they work as a moderation identifier."""
    qs = User.objects.filter(lab_name__iexact=name.strip())
    if exclude_user_id is not None:
        qs = qs.exclude(id=exclude_user_id)
    return qs.exists()
