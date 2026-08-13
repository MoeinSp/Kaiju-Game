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


def find_suspicious_activity(day: str | None = None) -> list[dict]:
    day = day or today_str()
    flagged = []
    for action, cost in ENERGY_GATED_ACTIONS.items():
        limit = _theoretical_max_daily_actions(cost)
        for log in DailyActionLog.objects.filter(action=action, day=day, count__gt=limit):
            user = User.objects.filter(id=log.user_id).first()
            flagged.append(
                {
                    "name": display_name(user) if user else str(log.user_id),
                    "user_id": log.user_id,
                    "action": action,
                    "count": log.count,
                    "limit": limit,
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
