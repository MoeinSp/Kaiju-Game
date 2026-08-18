"""Idle / AFK rewards + a rotating daily dungeon — the classic idle-game hooks.

* **Idle chest** — loot accrues in real time whether you're playing or away, up to
  a cap, and you collect the lump on return. The rate scales with how far you've
  pushed (campaign + lab level), so progressing makes even your downtime pay more.
  The cap (12h) is the "come back at least once a day" nudge.
* **Daily dungeon** — a single free run per day whose reward type rotates daily
  (gold / DNA / XP), so there's a fresh reason to log in every day and the reward
  scales with your progress.

Lazy like everything else: accrual is computed from `idle_since`, and the dungeon
is deduped by the last-run day. No cron.
"""

from __future__ import annotations

from django.utils import timezone

from bio_lab.models import User
from game import lab
from game.daily import today_str

IDLE_CAP_HOURS = 12


def _idle_rates(user: User) -> tuple[float, float]:
    lvl = lab.lab_level(user)
    stage = user.campaign_stage
    coins_per_hour = 40 + stage * 6 + lvl * 4
    dna_per_hour = 1 + stage * 0.2 + lvl * 0.1
    return coins_per_hour, dna_per_hour


def idle_status(user: User) -> dict:
    elapsed_h = (timezone.now() - user.idle_since).total_seconds() / 3600
    elapsed_h = max(0.0, min(IDLE_CAP_HOURS, elapsed_h))
    cph, dph = _idle_rates(user)
    return {
        "hours": elapsed_h,
        "coins": round(cph * elapsed_h),
        "dna": round(dph * elapsed_h),
        "capped": elapsed_h >= IDLE_CAP_HOURS,
        "cap_hours": IDLE_CAP_HOURS,
    }


def collect_idle(user: User) -> dict:
    """Grant the accrued idle loot and reset the accrual clock. Returns what was
    granted (may be zero if collected again immediately)."""
    st = idle_status(user)
    fields = ["idle_since"]
    if st["coins"]:
        user.coins += st["coins"]; fields.append("coins")
    if st["dna"]:
        user.dna_fragments += st["dna"]; fields.append("dna_fragments")
    user.idle_since = timezone.now()
    user.save(update_fields=fields)
    return {"coins": st["coins"], "dna": st["dna"]}


# ── rotating daily dungeon ────────────────────────────────────────────────────
DUNGEONS = [
    {"key": "gold", "emoji": "💰", "title": "دخمه‌ی طلا", "resource": "coins"},
    {"key": "dna", "emoji": "🧬", "title": "دخمه‌ی DNA", "resource": "dna"},
    {"key": "xp", "emoji": "⭐", "title": "دخمه‌ی تجربه", "resource": "xp"},
]


def today_dungeon() -> dict:
    day_of_year = timezone.localtime(timezone.now()).timetuple().tm_yday
    return DUNGEONS[day_of_year % len(DUNGEONS)]


def dungeon_reward(user: User) -> dict:
    lvl = lab.lab_level(user)
    stage = user.campaign_stage
    res = today_dungeon()["resource"]
    if res == "coins":
        return {"coins": 300 + stage * 30 + lvl * 20}
    if res == "dna":
        return {"dna": 20 + stage + lvl}
    return {"xp": 50 + stage * 3 + lvl * 2}


def dungeon_status(user: User) -> dict:
    return {
        "dungeon": today_dungeon(),
        "reward": dungeon_reward(user),
        "can_run": user.last_dungeon_day != today_str(),
    }


def run_dungeon(user: User) -> dict | None:
    """One free dungeon run per day. Returns the reward, or None if already run."""
    today = today_str()
    if user.last_dungeon_day == today:
        return None
    reward = dungeon_reward(user)
    if "xp" in reward:
        lab.add_lab_xp(user, reward["xp"])  # also feeds pass / war / alliance perks
    fields = ["last_dungeon_day"]
    if reward.get("coins"):
        user.coins += reward["coins"]; fields.append("coins")
    if reward.get("dna"):
        user.dna_fragments += reward["dna"]; fields.append("dna_fragments")
    user.last_dungeon_day = today
    user.save(update_fields=fields)
    return reward
