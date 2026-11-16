from bio_lab.models import Alliance, Creature, DailyActionLog, RaidBoss, User
from bio_lab.repository import display_name
from game import constants
from game.daily import today_str

# actions gated by the regenerating energy pool — the theoretical daily ceiling for
# each is (a full pool at day-start) + (every regen tick that can occur in 24h),
# divided by the action's energy cost. A DailyActionLog count above this is only
# possible if the energy check was bypassed somehow (bug or direct DB edit) — worth
# a human look, not proof of anything on its own.
ENERGY_GATED_ACTIONS = {
    "feed": constants.FEED_ENERGY_COST,
    "raid_attack": constants.RAID_ATTACK_ENERGY_COST,
    "hunt": constants.HUNT_ENERGY_COST,
}


def _theoretical_max_daily_actions(energy_cost: int) -> int:
    ticks_per_day = (24 * 60) // constants.ENERGY_REGEN_MINUTES
    return (constants.MAX_ENERGY + ticks_per_day) // max(energy_cost, 1)


ACTION_LABELS = {"feed": "تغذیه", "raid_attack": "حمله", "hunt": "شکار"}
SPRING_ENERGY = 12   # game/hunt.py «چشمه‌ی حیات»


def _energy_budget(user: User, day: str) -> tuple[int, int]:
    """(energy this player could have had today, full refills used): their own pool and regen
    speed, plus every diamond refill / group energy capsule (each a full pool) and spring."""
    from game.energy import get_energy_regen_interval_seconds, get_max_energy

    counts = dict(DailyActionLog.objects.filter(user_id=user.id, day=day,
                                                action__in=("energy_refill", "energy_capsule", "energy_spring"))
                  .values_list("action", "count"))
    pool = get_max_energy(user)
    ticks = int(86400 // max(1.0, get_energy_regen_interval_seconds(user)))
    refills = counts.get("energy_refill", 0) + counts.get("energy_capsule", 0)
    return pool * (1 + refills) + ticks + SPRING_ENERGY * counts.get("energy_spring", 0), refills


def find_suspicious_activity(day: str | None = None) -> list[dict]:
    day = day or today_str()
    flagged = []
    for action, cost in ENERGY_GATED_ACTIONS.items():
        # the free player's ceiling is only a pre-filter; each candidate is checked against their own
        floor_limit = _theoretical_max_daily_actions(cost)
        for log in DailyActionLog.objects.filter(action=action, day=day, count__gt=floor_limit):
            user = User.objects.filter(id=log.user_id).first()
            if user is None:
                continue
            budget, refills = _energy_budget(user, day)
            limit = budget // max(cost, 1)
            if log.count <= limit:
                continue
            flagged.append(
                {
                    "name": display_name(user),   # already HTML-safe (and carries the VIP badge)
                    "user_id": log.user_id,
                    "action": action,
                    "label": ACTION_LABELS.get(action, action),
                    "count": log.count,
                    "limit": limit,
                    "refills": refills,
                }
            )
    return flagged


def dashboard_stats() -> dict:
    return {
        "users": User.objects.count(),
        "creatures": Creature.objects.count(),
        "alliances": Alliance.objects.count(),
        "active_raids": RaidBoss.objects.filter(is_active=True).count(),
    }


def progress_report() -> dict:
    top_players = [{"name": display_name(u), "coins": u.coins} for u in User.objects.order_by("-coins")[:5]]
    top_creatures = list(
        Creature.objects.filter(is_active=True).order_by("-level")[:5]
    )
    return {
        **dashboard_stats(),
        "top_players": top_players,
        "top_creatures": [
            {"name": c.name, "level": c.level, "owner": display_name(User.objects.filter(id=c.owner_id).first())}
            for c in top_creatures
        ],
        "suspicious": find_suspicious_activity(),
    }
