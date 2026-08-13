import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # telegram user id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coins: Mapped[int] = mapped_column(Integer, default=200)
    dna_fragments: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_trained_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped["User"] = relationship(back_populates="creatures")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # telegram chat id
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RaidBoss(Base):
    __tablename__ = "raid_bosses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    name: Mapped[str] = mapped_column(String(64))
    element: Mapped[str] = mapped_column(String(16))
    max_hp: Mapped[int] = mapped_column(Integer)
    current_hp: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    spawned_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RaidDamageLog(Base):
    __tablename__ = "raid_damage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raid_id: Mapped[int] = mapped_column(ForeignKey("raid_bosses.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    creature_id: Mapped[int] = mapped_column(ForeignKey("creatures.id"))
    damage: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DuelLog(Base):
    __tablename__ = "duel_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    opponent_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    winner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    log_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
