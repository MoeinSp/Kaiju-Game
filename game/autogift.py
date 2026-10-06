"""«شارژ همگانی خودکار» — the admin's recurring gift to every player.

The admin sets an interval in days and an amount of gold / DNA (/ diamonds) in the panel;
tick() (called from the 5-minute notification job) pays everyone when the interval has
passed, exactly once per period, and returns a short DM for the recently-active players.

It reuses game.moderation.gift_all, so it is the same single UPDATE as the manual
«هدیه به همه» button — every user row, banned or not, active or not.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from bio_lab.models import AutoGift, User
from game.creature import GameError
from game.emoji import get_emoji

MAX_INTERVAL_DAYS = 60
ANNOUNCE_ACTIVE_DAYS = 7
SEND_HOURS = (10, 22)  # a due gift waits for these local hours, so nobody is DM'd at night


def get() -> AutoGift:
    cfg, _ = AutoGift.objects.get_or_create(id=1)
    return cfg


def next_run_at(cfg: AutoGift | None = None) -> datetime.datetime | None:
    cfg = cfg or get()
    if not cfg.enabled or cfg.last_at is None:
        return None
    return cfg.last_at + datetime.timedelta(days=cfg.interval_days)


def configure(interval_days: int, coins: int, dna: int, diamonds: int = 0) -> AutoGift:
    """Set the schedule and switch it on. The first payout comes one full interval from
    now (the clock restarts), never immediately."""
    if not 1 <= interval_days <= MAX_INTERVAL_DAYS:
        raise GameError(f"فاصله باید بین ۱ تا {MAX_INTERVAL_DAYS} روز باشه.")
    if coins < 0 or dna < 0 or diamonds < 0 or not (coins or dna or diamonds):
        raise GameError("حداقل یکی از مقدارها باید بیشتر از صفر باشه.")
    cfg = get()
    cfg.interval_days, cfg.coins, cfg.dna, cfg.diamonds = interval_days, coins, dna, diamonds
    cfg.enabled = True
    cfg.last_at = timezone.now()
    cfg.save()
    return cfg


def toggle() -> AutoGift:
    cfg = get()
    if not cfg.enabled and not (cfg.coins or cfg.dna or cfg.diamonds):
        raise GameError("اول مقدار و فاصله رو تنظیم کن.")
    cfg.enabled = not cfg.enabled
    if cfg.enabled:
        cfg.last_at = timezone.now()  # switching on restarts the clock — no surprise payout
    cfg.save(update_fields=["enabled", "last_at"])
    return cfg


def tick() -> list[tuple]:
    """Pay the gift when it is due. Sync / ORM context, inside the caller's transaction."""
    from game.moderation import gift_all

    cfg = AutoGift.objects.select_for_update().filter(id=1).first()
    if cfg is None or not cfg.enabled:
        return []
    now = timezone.now()
    if cfg.last_at is None:
        cfg.last_at = now
        cfg.save(update_fields=["last_at"])
        return []
    if now < cfg.last_at + datetime.timedelta(days=cfg.interval_days):
        return []
    if not SEND_HOURS[0] <= timezone.localtime(now).hour < SEND_HOURS[1]:
        return []
    gift_all(cfg.coins, cfg.dna, cfg.diamonds)
    cfg.last_at = now
    cfg.runs += 1
    cfg.save(update_fields=["last_at", "runs"])

    parts = []
    if cfg.coins:
        parts.append(f"{get_emoji('coin')} <code>+{cfg.coins:,}</code> طلا")
    if cfg.dna:
        parts.append(f"{get_emoji('dna')} <code>+{cfg.dna:,}</code> DNA")
    if cfg.diamonds:
        parts.append(f"{get_emoji('diamond')} <code>+{cfg.diamonds:,}</code> الماس")
    text = "🎁 <b>شارژ همگانی!</b>\n" + " ┃ ".join(parts) + "\n<i>به حسابت اضافه شد.</i>"
    since = now - datetime.timedelta(days=ANNOUNCE_ACTIVE_DAYS)
    recipients = User.objects.filter(
        notifications_on=True, is_banned=False, energy_updated_at__gte=since
    ).values_list("id", flat=True)
    return [(uid, text, "gift") for uid in recipients]
