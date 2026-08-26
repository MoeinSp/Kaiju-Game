"""Re-engagement push notifications — the bot's only *proactive* outbound path.

Everything else in this game is pull-based (the player opens the bot). This module
lets the bot reach back out: "your egg hatched", "you were raided", "your energy
is full". Those DMs are the single biggest retention lever a Telegram game has.

Design:

* A periodic job (bot/handlers/notify.py, driven by PTB's JobQueue) calls
  ``collect_due()`` every few minutes. That's a *sync* function: it queries the
  ORM for everything that just became notifiable, marks each one notified inside
  one transaction, and returns a list of ``(user_id, text)`` for the async job to
  send. Marking-before-send means a failed send (blocked bot) is simply lost
  rather than resent forever — the anti-spam bias is deliberate.
* Every event carries its own ``notified`` flag so nothing fires twice. Energy is
  the exception (no per-event row): it re-arms whenever the collector sees the
  player below full again, so the "energy full" DM fires once per drain/refill.
* ``notifications_on`` on the player is the master opt-out; a blocked-bot error is
  turned into an opt-out by the sender so we stop DMing someone who left.

Kept deliberately conservative: raids only notify within a short recent window (so
first run after deploy doesn't replay a player's whole raid history), and the
daily "come back" nudge is once a day, only in an evening window, and only for
players who were active in the last few days.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import AttackLog, BreedingJob, BuildingUpgrade, Egg, User
from game import constants
from game.daily import today_str
from game.energy import _synced_energy_and_anchor

# raids older than this are marked notified WITHOUT sending — avoids replaying
# history the first time the job runs (or after any downtime)
RAID_NOTIFY_WINDOW = datetime.timedelta(hours=2)

# the once-a-day "your daily reward is waiting" nudge only fires in this local-hour
# window (Asia/Tehran) and only for players seen within this many days
NUDGE_HOUR_START = 18
NUDGE_HOUR_END = 22
NUDGE_ACTIVE_WITHIN_DAYS = 5


def _date_str_days_ago(days: int) -> str:
    return (timezone.localtime(timezone.now()) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def collect_due() -> list[tuple[int, str]]:
    """Find every due notification, mark it sent, and return (user_id, text) pairs.

    Runs in sync/ORM context (via run_db); the async caller does the sending."""
    now = timezone.now()
    out: list[tuple[int, str]] = []

    with transaction.atomic():
        # ── eggs ready to hatch ───────────────────────────────────────────────
        for egg in Egg.objects.filter(notified=False, finishes_at__lte=now).select_related("owner"):
            if egg.owner.notifications_on:
                out.append((egg.owner_id, "🐣 <b>یه تخم توی غار هیولا سر باز کرد!</b> برو ببین چی ازش دراومد."))
            egg.notified = True
            egg.save(update_fields=["notified"])

        # ── cave mating finished (go lay the egg) ─────────────────────────────
        for job in BreedingJob.objects.filter(notified=False, finishes_at__lte=now).select_related("owner"):
            if job.owner.notifications_on:
                out.append((job.owner_id, "💞 <b>جفت‌گیری توی غار تموم شد!</b> برو تخم رو بردار تا والدها آزاد شن."))
            job.notified = True
            job.save(update_fields=["notified"])

        # ── building upgrade finished ─────────────────────────────────────────
        for up in BuildingUpgrade.objects.filter(notified=False, finishes_at__lte=now).select_related(
            "owner", "building"
        ):
            if up.owner.notifications_on:
                label = constants.BUILDING_LABELS.get(up.building.building_type, "ساختمونت")
                out.append((up.owner_id, f"🏗 <b>ارتقای {label} تموم شد!</b> برو جمعش کن و بعدی رو بساز."))
            up.notified = True
            up.save(update_fields=["notified"])

        # ── you were raided (recent only) ─────────────────────────────────────
        cutoff = now - RAID_NOTIFY_WINDOW
        for log in AttackLog.objects.filter(defender_notified=False, defender__isnull=False).select_related(
            "defender", "attacker"
        ):
            if log.defender.notifications_on and log.created_at >= cutoff:
                attacker_name = log.attacker_label or (
                    log.attacker.lab_name if log.attacker_id and log.attacker else "یه مهاجم"
                )
                power_note = f" (قدرت {log.attacker_power})" if log.attacker_power else ""
                revengeable = not log.is_fake_defender and log.attacker_id
                if log.attacker_won:
                    text = (
                        f"⚔️ <b>به آزمایشگاهت حمله شد!</b>\n"
                        f"🏭 مهاجم: <b>{attacker_name}</b>{power_note}\n"
                        f"💰 <b>{log.loot_gold}</b> طلا غارت شد.\n"
                        "می‌تونی همین‌جا جواب بدی 👇"
                    )
                else:
                    # defence held — still report who came and let them strike back
                    text = (
                        f"🛡 <b>یکی بهت حمله کرد ولی دفاعت موفق بود!</b>\n"
                        f"🏭 مهاجم: <b>{attacker_name}</b>{power_note}\n"
                        "می‌تونی همین‌جا حمله‌ی متقابل بزنی 👇"
                    )
                # 4th element = the attacker's user id, for a «🔍 جزییات حریف» button
                # on the defense report (defrep_opp:<id>).
                if revengeable:
                    out.append((log.defender_id, text, f"arena_revenge:{log.id}", log.attacker_id))
                else:
                    out.append((log.defender_id, text, None, log.attacker_id))
            log.defender_notified = True
            log.save(update_fields=["defender_notified"])

        # ── energy full (once per drain/refill cycle) ─────────────────────────
        for user in User.objects.filter(notifications_on=True):
            current, _ = _synced_energy_and_anchor(user)
            if current >= constants.MAX_ENERGY and not user.energy_full_notified:
                out.append((user.id, "⚡ <b>انرژیت پر شد!</b> وقتِ شکار و آرناست."))
                user.energy_full_notified = True
                user.save(update_fields=["energy_full_notified"])
            elif current < constants.MAX_ENERGY and user.energy_full_notified:
                user.energy_full_notified = False
                user.save(update_fields=["energy_full_notified"])

        # ── referral rewards (friend crossed the milestone → pay both) ────────
        from game import referral

        out.extend(referral.collect_rewards())

        # ── weekly alliance war settlement (top alliance's treasury bonus) ────
        from game import alliance

        out.extend(alliance.settle_war_if_needed())

        # ── one-day alliance wars whose 24h is up ─────────────────────────────
        out.extend(alliance.settle_due_wars())

        # ── daily "come back" nudge (evening window, recently-active only) ────
        local_hour = timezone.localtime(now).hour
        if NUDGE_HOUR_START <= local_hour < NUDGE_HOUR_END:
            today = today_str()
            active_cutoff = _date_str_days_ago(NUDGE_ACTIVE_WITHIN_DAYS)
            candidates = User.objects.filter(
                notifications_on=True,
                last_login_day__isnull=False,
                last_login_day__gte=active_cutoff,  # seen in the last few days
            ).exclude(last_login_day=today).exclude(last_nudge_day=today)
            for user in candidates:
                out.append(
                    (user.id, "🎁 <b>جایزه‌ی روزانه و گردونه‌ی شانست منتظرن!</b> یه سر بزن و استریکت رو نگه دار.")
                )
                user.last_nudge_day = today
                user.save(update_fields=["last_nudge_day"])

    return out


def set_notifications(user: User, on: bool) -> None:
    user.notifications_on = on
    user.save(update_fields=["notifications_on"])
