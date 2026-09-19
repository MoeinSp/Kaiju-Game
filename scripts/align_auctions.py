import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from bio_lab.models import BlackMarketAuction
from game.blackmarket import get_next_blackmarket_deadline

if __name__ == "__main__":
    dl = get_next_blackmarket_deadline()
    count = BlackMarketAuction.objects.filter(is_settled=False).update(ends_at=dl)
    print(f"Successfully aligned {count} active auctions to deadline: {dl}")
