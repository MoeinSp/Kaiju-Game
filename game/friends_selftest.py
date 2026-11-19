"""Friends: a request is not a friendship, and nothing here pays gold.

Run: python -m game.friends_selftest
Deletes the users it creates.
"""

from __future__ import annotations

import datetime
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from bio_lab.models import Creature, DailyActionLog, DungeonProfile, FriendLink, SpeedupCard, User  # noqa: E402
from game import friends  # noqa: E402
from game.creature import GameError  # noqa: E402
from game.daily import today_str  # noqa: E402

A = 9200000001
B = 9200000002
EXTRA = list(range(9200000100, 9200000150))


def _wipe():
    User.objects.filter(id__in=[A, B, *EXTRA]).delete()


def _user(uid, name, *, day=""):
    return User.objects.create(
        id=uid, first_name=name, lab_name=name, coins=1000, dna_fragments=50, diamonds=10,
        last_active_day=day, cup=40,
    )


def main():
    _wipe()
    friends.reset_cooldowns()
    try:
        a = _user(A, "الف", day=today_str())
        b = _user(B, "ب", day=(datetime.date.fromisoformat(today_str()) - datetime.timedelta(days=1)).isoformat())
        assert friends.seen_label(a.last_active_day) == "امروز"
        assert friends.seen_label(b.last_active_day) == "دیروز"
        assert friends.ask(A, B) == "asked"
        assert friends.friend_count(A) == 0
        assert friends.ask(A, B) == "already"
        try:
            friends.accept(A, B)
            raise AssertionError("the asker must not accept their own request")
        except GameError:
            pass
        friends.accept(B, A)
        assert friends.are_friends(A, B)
        assert friends.friend_count(A) == 1
        text = friends.card(A, b)
        assert "ب" in text and "دیروز" in text
        assert "1000" not in text and "سپر" not in text and "بانک" not in text

        friends.reset_cooldowns()
        coins = (a.coins, b.coins)
        poked = friends.play(a, b, "تف", B)
        assert poked["ask"] is False and "درخواست دوستی" not in poked["text"]
        assert "از:" in poked["text"] and "به:" in poked["text"]
        assert "تف کرد. و" not in poked["text"]
        a.refresh_from_db()
        b.refresh_from_db()
        assert (a.coins, b.coins) == coins

        assert friends.cheer(A, B) is True
        assert friends.cheer(A, B) is False
        assert friends.cheers_today(B) == 1
        assert friends.visit(A, B) is True
        assert friends.visit(A, B) is False
        assert friends.ping(A, B) is True
        assert friends.ping(A, B) is False

        Creature.objects.create(owner=a, name="سیمرغ", element="fire", is_active=True, base_atk=30, base_hp=80)
        Creature.objects.create(owner=b, name="اژدها", element="water", is_active=True, base_atk=20, base_hp=90)
        log = friends.spar(a, b)
        assert "کاپ، طلا و انرژی جابه‌جا نشد" in log
        a.refresh_from_db()
        assert a.coins == 1000 and a.cup == 40
        try:
            friends.spar(a, b)
            raise AssertionError("a second spar the same day must fail")
        except GameError:
            pass

        friends.reset_cooldowns()
        licked = friends.play(a, b, "لیس", B)
        assert licked["ask"] is False and "۲ الماس" in licked["text"]
        b.refresh_from_db()
        a.refresh_from_db()
        assert b.diamonds == 12 and a.diamonds == 10 and a.coins == 1000
        DailyActionLog.objects.filter(user_id=A, action=friends.LICK_GIVE).update(count=15)
        friends.reset_cooldowns()
        capped = friends.play(a, b, "لیس", B)
        assert capped["ask"] is False and "۱۵" in capped["text"]
        b.refresh_from_db()
        assert b.diamonds == 12
        DailyActionLog.objects.filter(user_id=A, action=friends.LICK_GIVE).update(count=0)
        DailyActionLog.objects.filter(user_id=B, action=friends.LICK_TAKE).update(count=14)
        original_jackpot = friends._jackpot_kind
        friends._jackpot_kind = lambda: "gems"
        try:
            friends.reset_cooldowns()
            last = friends.play(a, b, "لیس", B)
            assert "۲۰ الماس" in last["text"]
            b.refresh_from_db()
            assert b.diamonds == 32
            from django.db import transaction
            with transaction.atomic():
                assert "۳۰ دقیقه‌ای" in friends._pay_lick(b, 12)
            assert SpeedupCard.objects.get(owner=b, minutes=30).count == 2
            friends._jackpot_kind = lambda: "key"
            with transaction.atomic():
                key_text = friends._pay_lick(b, 15)
            assert "کلید سیاه‌چال" in key_text
            b.refresh_from_db()
            assert b.coins == 101_000
            assert DungeonProfile.objects.get(user=b).keys == 1
        finally:
            friends._jackpot_kind = original_jackpot

        friends.remove(A, B)
        assert not friends.are_friends(A, B)
        assert friends.ask(A, B) == "asked"
        friends.decline(B, A)
        assert friends.ask(A, B) == "blocked"
        FriendLink.objects.filter(user_lo_id=A, user_hi_id=B).update(
            updated_at=timezone.now() - datetime.timedelta(hours=25),
        )
        assert friends.ask(A, B) == "asked"

        if friends.are_friends(A, B):
            friends.remove(A, B)
        else:
            FriendLink.objects.filter(user_lo_id=min(A, B), user_hi_id=max(A, B)).delete()
        friends.reset_cooldowns()
        stranger = friends.play(a, b, "لیس", B)
        assert stranger["ask"] is True and "هنوز دوست نیستید" in stranger["text"]
        b.refresh_from_db()
        assert b.diamonds == 32 and b.coins == 101_000
        FriendLink.objects.filter(user_lo_id=min(A, B), user_hi_id=max(A, B)).delete()
        others = [_user(uid, f"د{uid % 100}") for uid in EXTRA]
        for other in others:
            assert friends.ask(A, other.id) == "asked"
            friends.accept(other.id, A)
        assert friends.friend_count(A) == 50
        c = b
        assert friends.ask(A, c.id) == "full"
        assert friends.link_of(A, c.id) is None

        friends.reset_cooldowns()
        first = friends.play(a, c, "طعنه", c.id)
        assert first["quiet"] is False and "طلا" not in first["text"]
        second = friends.play(a, c, "طعنه", c.id)
        assert second["wait"] is True
        third = friends.play(a, c, "طعنه", c.id)
        assert third["quiet"] is True

        print("friends selftest ok")
    finally:
        _wipe()


if __name__ == "__main__":
    main()
