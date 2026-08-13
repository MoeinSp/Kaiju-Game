import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime.datetime:
    # naive UTC on purpose: SQLite drops tzinfo on round-trip, so mixing
    # aware/naive datetimes here would raise on comparison after a reload.
    return datetime.datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # telegram user id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    coins: Mapped[int] = mapped_column(Integer, default=200)
    dna_fragments: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    creatures: Mapped[list["Creature"]] = relationship(back_populates="owner")


class Creature(Base):
    __tablename__ = "creatures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(64))
    element: Mapped[str] = mapped_column(String(16))
    rarity: Mapped[str] = mapped_column(String(16), default="common")
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)

    base_hp: Mapped[int] = mapped_column(Integer, default=50)
    base_atk: Mapped[int] = mapped_column(Integer, default=10)
    base_def: Mapped[int] = mapped_column(Integer, default=10)
    base_spd: Mapped[int] = mapped_column(Integer, default=10)

    wings_lvl: Mapped[int] = mapped_column(Integer, default=0)
    armor_lvl: Mapped[int] = mapped_column(Integer, default=0)
    fangs_lvl: Mapped[int] = mapped_column(Integer, default=0)
    poison_lvl: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    last_trained_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    owner: Mapped["User"] = relationship(back_populates="creatures")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # telegram chat id
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class RaidBoss(Base):
    __tablename__ = "raid_bosses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    name: Mapped[str] = mapped_column(String(64))
    element: Mapped[str] = mapped_column(String(16))
    max_hp: Mapped[int] = mapped_column(Integer)
    current_hp: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    spawned_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class RaidDamageLog(Base):
    __tablename__ = "raid_damage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raid_id: Mapped[int] = mapped_column(ForeignKey("raid_bosses.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    creature_id: Mapped[int] = mapped_column(ForeignKey("creatures.id"))
    damage: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class GroupEventLog(Base):
    __tablename__ = "group_event_logs"
    __table_args__ = (UniqueConstraint("group_id", "event_key", "day", name="uq_group_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    event_key: Mapped[str] = mapped_column(String(32))
    day: Mapped[str] = mapped_column(String(10))


class DailyActionLog(Base):
    __tablename__ = "daily_action_logs"
    __table_args__ = (UniqueConstraint("user_id", "action", "day", name="uq_daily_action"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32))
    day: Mapped[str] = mapped_column(String(10))  # "YYYY-MM-DD" (UTC)
    count: Mapped[int] = mapped_column(Integer, default=0)


class MissionClaim(Base):
    __tablename__ = "mission_claims"
    __table_args__ = (UniqueConstraint("user_id", "mission_key", "day", name="uq_mission_claim"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    mission_key: Mapped[str] = mapped_column(String(32))
    day: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class InteractiveBattle(Base):
    __tablename__ = "interactive_battles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))

    player_a_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    player_b_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    creature_a_id: Mapped[int] = mapped_column(ForeignKey("creatures.id"))
    creature_b_id: Mapped[int] = mapped_column(ForeignKey("creatures.id"))

    hp_a: Mapped[int] = mapped_column(Integer)
    hp_b: Mapped[int] = mapped_column(Integer)
    skill_uses_a: Mapped[int] = mapped_column(Integer, default=2)
    skill_uses_b: Mapped[int] = mapped_column(Integer, default=2)
    shield_active_a: Mapped[bool] = mapped_column(Boolean, default=False)
    shield_active_b: Mapped[bool] = mapped_column(Boolean, default=False)
    stunned_a: Mapped[bool] = mapped_column(Boolean, default=False)
    stunned_b: Mapped[bool] = mapped_column(Boolean, default=False)

    turn: Mapped[str] = mapped_column(String(1))  # "a" or "b"
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/active/finished/declined
    log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    player_a: Mapped["User"] = relationship(foreign_keys=[player_a_id])
    player_b: Mapped["User"] = relationship(foreign_keys=[player_b_id])
    creature_a: Mapped["Creature"] = relationship(foreign_keys=[creature_a_id])
    creature_b: Mapped["Creature"] = relationship(foreign_keys=[creature_b_id])


class DuelLog(Base):
    __tablename__ = "duel_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    opponent_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    winner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    log_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
