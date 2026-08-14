"""Operator web panel.

Every view here is staff-only and follows the same shape: GET renders, POST
performs one named ``action`` and redirects back (post/redirect/get, so a
refresh can't re-fire a destructive operation). Errors go to django.contrib
.messages rather than raising, because the audience is an operator on a phone,
not a developer with a traceback.

The panel writes through the same ``game/`` functions the bot uses — never
directly to the ORM — so cache invalidation and validation can't drift between
the two front ends.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login
from django.contrib.auth.models import User as AuthUser
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from bio_lab.models import (
    Alliance,
    AttackLog,
    ButtonEmojiOverride,
    Creature,
    EmojiOverride,
    RequiredChannel,
    ThemeLoadout,
    User,
)
from game import backup as backup_mod
from game import button_emoji, button_style, emoji, force_join, moderation, theme
from game.creature import GameError

RESTORE_CONFIRM_PHRASE = "بازیابی"


def _post_action(request) -> str:
    return (request.POST.get("action") or "").strip()


def _int(request, field: str, default: int = 0) -> int:
    raw = (request.POST.get(field) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


# --- dashboard -------------------------------------------------------------


@staff_member_required(login_url="panel:login")
def dashboard(request):
    now = timezone.now()
    day_ago = now - timedelta(days=1)
    totals = User.objects.aggregate(
        coins=Sum("coins"), dna=Sum("dna_fragments"), diamonds=Sum("diamonds")
    )
    context = {
        "page": "dashboard",
        "stats": {
            "players": User.objects.count(),
            "banned": User.objects.filter(is_banned=True).count(),
            "new_today": User.objects.filter(created_at__gte=day_ago).count(),
            "creatures": Creature.objects.count(),
            "alliances": Alliance.objects.count(),
            "attacks_today": AttackLog.objects.filter(created_at__gte=day_ago).count(),
            "coins": totals["coins"] or 0,
            "dna": totals["dna"] or 0,
            "diamonds": totals["diamonds"] or 0,
        },
        "theme": {
            "emoji": EmojiOverride.objects.count(),
            "emoji_total": len(emoji.EMOJI_DEFS),
            "button_emoji": ButtonEmojiOverride.objects.count(),
            "button_emoji_total": len(button_emoji.BUTTON_EMOJI_DEFS),
            "palette_changed": sum(
                1
                for role, style in button_style.current_palette().items()
                if style != button_style.ROLE_DEFAULTS[role]
            ),
            "active_loadout": ThemeLoadout.objects.filter(is_active=True).first(),
            "loadouts": ThemeLoadout.objects.count(),
        },
        "backups": backup_mod.list_backups()[:3],
        "top_players": User.objects.order_by("-cup")[:8],
    }
    return render(request, "panel/dashboard.html", context)


# --- button colours --------------------------------------------------------


@staff_member_required(login_url="panel:login")
def button_styles(request):
    if request.method == "POST":
        action = _post_action(request)
        if action == "reset":
            button_style.reset_palette()
            messages.success(request, "پالت به حالت پیش‌فرض برگشت.")
        elif action == "save":
            applied = 0
            for role in button_style.ROLE_DEFS:
                chosen = request.POST.get(f"role_{role}")
                if chosen is None or chosen not in button_style.STYLE_CHOICES:
                    continue
                if chosen == button_style.ROLE_DEFAULTS[role]:
                    button_style.clear_role_style(role)
                else:
                    button_style.set_role_style(role, chosen)
                applied += 1
            messages.success(request, f"پالت ذخیره شد ({applied} نقش بررسی شد).")
        return redirect("panel:button_styles")

    palette = button_style.current_palette()
    roles = [
        {
            "key": role,
            "label": label,
            "help": help_text,
            "default": default,
            "current": palette[role],
            "is_default": palette[role] == default,
        }
        for role, (label, default, help_text) in button_style.ROLE_DEFS.items()
    ]
    return render(
        request,
        "panel/button_styles.html",
        {
            "page": "button_styles",
            "roles": roles,
            "choices": [
                {"value": value, "label": label, "color": color}
                for value, (label, color) in button_style.STYLE_CHOICES.items()
            ],
        },
    )


# --- premium emoji (buttons and message body) ------------------------------


def _emoji_page(request, *, kind: str):
    """Shared implementation — the two registries differ only in which module
    owns them, so one view body with a `kind` switch beats two near-copies that
    drift apart."""
    is_button = kind == "button"
    module = button_emoji if is_button else emoji
    defs = module.BUTTON_EMOJI_DEFS if is_button else module.EMOJI_DEFS
    cat_labels = module.BUTTON_CATEGORY_LABELS if is_button else module.CATEGORY_LABELS
    url_name = "panel:button_emoji" if is_button else "panel:text_emoji"

    if request.method == "POST":
        action = _post_action(request)
        key = (request.POST.get("key") or "").strip()
        if key not in defs:
            messages.error(request, "این کلید توی رجیستری نیست.")
            return redirect(url_name)
        if action == "clear":
            cleared = (
                button_emoji.clear_button_emoji(key) if is_button else emoji.clear_emoji(key)
            )
            theme.refresh_theme_caches()
            messages.success(
                request, f"«{defs[key][0]}» پاک شد." if cleared else "چیزی برای پاک کردن نبود."
            )
        elif action == "set":
            custom_id = (request.POST.get("custom_emoji_id") or "").strip()
            if not custom_id.isdigit():
                messages.error(request, "شناسه‌ی ایموجی پرمیوم باید فقط عدد باشه.")
                return redirect(url_name)
            placeholder = (request.POST.get("placeholder") or "").strip() or defs[key][1]
            if is_button:
                button_emoji.set_button_emoji(key, custom_id, placeholder)
            else:
                emoji.set_emoji(key, custom_id, placeholder)
            theme.refresh_theme_caches()
            messages.success(request, f"«{defs[key][0]}» به‌روزرسانی شد.")
        return redirect(url_name)

    overrides = {
        o.key: o
        for o in (
            ButtonEmojiOverride.objects.all() if is_button else EmojiOverride.objects.all()
        )
    }
    groups: dict[str, list[dict]] = {}
    for key, (label, fallback, category) in defs.items():
        groups.setdefault(category, []).append(
            {
                "key": key,
                "label": label,
                "fallback": fallback,
                "override": overrides.get(key),
            }
        )
    return render(
        request,
        "panel/emoji.html",
        {
            "page": "button_emoji" if is_button else "text_emoji",
            "title": "ایموجی پرمیوم دکمه‌ها" if is_button else "ایموجی پرمیوم متن‌ها",
            "intro": (
                "این ایموجی‌ها قبل از عنوان دکمه نشون داده می‌شن. اگه تنظیم نشن، همون ایموجی یونیکد پیش‌فرض توی عنوان می‌مونه."
                if is_button
                else "این ایموجی‌ها داخل متن پیام‌ها با تگ <tg-emoji> رندر می‌شن. برای کاربرهای بدون پرمیوم، جایگزین یونیکد نشون داده می‌شه."
            ),
            "groups": [
                {"key": cat, "label": cat_labels.get(cat, cat), "items": items}
                for cat, items in groups.items()
            ],
            "set_count": len(overrides),
            "total": len(defs),
        },
    )


@staff_member_required(login_url="panel:login")
def button_emoji_view(request):
    return _emoji_page(request, kind="button")


@staff_member_required(login_url="panel:login")
def text_emoji_view(request):
    return _emoji_page(request, kind="text")


# --- theme loadouts --------------------------------------------------------


@staff_member_required(login_url="panel:login")
def loadouts(request):
    if request.method == "POST":
        action = _post_action(request)
        try:
            if action == "save":
                loadout = theme.save_loadout(
                    request.POST.get("name", ""), request.POST.get("note", "")
                )
                messages.success(request, f"لودآوت «{loadout.name}» از وضعیت فعلی ساخته شد.")
            elif action == "overwrite":
                loadout = theme.save_loadout(
                    request.POST.get("name", ""),
                    request.POST.get("note", ""),
                    loadout_id=_int(request, "loadout_id"),
                )
                messages.success(request, f"لودآوت «{loadout.name}» با وضعیت فعلی به‌روز شد.")
            elif action == "activate":
                loadout, summary = theme.activate_loadout(_int(request, "loadout_id"))
                messages.success(
                    request,
                    f"لودآوت «{loadout.name}» اعمال شد — {summary['emoji']} ایموجی متن، "
                    f"{summary['button_emoji']} ایموجی دکمه، {summary['button_styles']} رنگ سفارشی.",
                )
            elif action == "duplicate":
                loadout = theme.duplicate_loadout(_int(request, "loadout_id"))
                messages.success(request, f"کپی ساخته شد: «{loadout.name}».")
            elif action == "delete":
                if theme.delete_loadout(_int(request, "loadout_id")):
                    messages.success(request, "لودآوت حذف شد.")
                else:
                    messages.error(request, "این لودآوت پیدا نشد.")
            elif action == "import":
                upload = request.FILES.get("file")
                if upload is None:
                    messages.error(request, "فایلی انتخاب نشده.")
                else:
                    loadout = theme.import_loadout(
                        request.POST.get("name", "") or upload.name.rsplit(".", 1)[0],
                        upload.read(),
                        request.POST.get("note", ""),
                    )
                    messages.success(
                        request,
                        f"لودآوت «{loadout.name}» وارد شد. برای اعمال، دکمه‌ی «فعال کن» رو بزن.",
                    )
        except theme.ThemeError as exc:
            messages.error(request, str(exc))
        return redirect("panel:loadouts")

    items = []
    for loadout in theme.list_loadouts():
        try:
            summary = theme.snapshot_summary(theme.loadout_snapshot(loadout))
            broken = False
        except theme.ThemeError:
            summary, broken = {"emoji": 0, "button_emoji": 0, "button_styles": 0}, True
        items.append({"obj": loadout, "summary": summary, "broken": broken})

    return render(
        request,
        "panel/loadouts.html",
        {
            "page": "loadouts",
            "items": items,
            "live": theme.snapshot_summary(theme.capture_snapshot()),
        },
    )


@staff_member_required(login_url="panel:login")
def loadout_export(request, loadout_id: int):
    loadout = ThemeLoadout.objects.filter(id=loadout_id).first()
    if loadout is None:
        raise Http404
    try:
        snapshot = theme.loadout_snapshot(loadout)
    except theme.ThemeError as exc:
        messages.error(request, str(exc))
        return redirect("panel:loadouts")
    body = json.dumps(snapshot, ensure_ascii=False, indent=2)
    response = HttpResponse(body, content_type="application/json; charset=utf-8")
    # ASCII-only filename: the name is Persian and a raw non-ASCII header value
    # breaks some clients, so the id carries the identity here
    response["Content-Disposition"] = f'attachment; filename="loadout-{loadout.id}.json"'
    return response


# --- backup / restore ------------------------------------------------------


def _relogin_after_restore(request, username: str) -> str:
    """Restore flushes every table, sessions included, so the operator who just
    pressed the button is logged out by their own action. If the archive contains
    an account with the same username, put them straight back in — otherwise say
    plainly that they'll need to log in with the restored credentials.

    The unconditional ``flush()`` is load-bearing. Django's ``login()`` reuses the
    current session when it already belongs to the same user, and this one's row
    no longer exists — the session middleware would then fail its closing UPDATE
    and turn the whole response into a 400, so the restore would look like it
    crashed even though it succeeded. Flushing drops the dead key so a fresh row
    gets INSERTed instead. It also matters when there's no account to log back
    in as: the request still has to finish cleanly.
    """
    request.session.flush()
    restored = AuthUser.objects.filter(username=username, is_staff=True).first()
    if restored is None:
        return "حساب کاربری‌ت توی این پشتیبان نبود، پس باید با یه حساب staff از خودِ پشتیبان وارد بشی."
    # the archive may carry a different password hash for this username; the
    # session we're creating here reflects the restored account, not the old one
    login(request, restored, backend="django.contrib.auth.backends.ModelBackend")
    return "نشست تو با حساب بازیابی‌شده تمدید شد (رمزت ممکنه همون رمز داخل پشتیبان باشه)."


@staff_member_required(login_url="panel:login")
def backups(request):
    if request.method == "POST":
        action = _post_action(request)
        try:
            if action == "create":
                info = backup_mod.create_backup(request.POST.get("label", ""))
                messages.success(
                    request,
                    f"پشتیبان ساخته شد: {info['name']} "
                    f"({info['manifest']['object_count']} رکورد).",
                )
            elif action == "delete":
                backup_mod.delete_backup(request.POST.get("name", ""))
                messages.success(request, "فایل پشتیبان حذف شد.")
            elif action == "upload":
                upload = request.FILES.get("file")
                if upload is None:
                    messages.error(request, "فایلی انتخاب نشده.")
                else:
                    info = backup_mod.store_upload(upload, upload.name.rsplit(".", 1)[0])
                    messages.success(request, f"فایل پشتیبان ذخیره شد: {info['name']}")
            elif action == "restore":
                if (request.POST.get("confirm") or "").strip() != RESTORE_CONFIRM_PHRASE:
                    messages.error(
                        request,
                        f"برای بازیابی باید دقیقاً کلمه‌ی «{RESTORE_CONFIRM_PHRASE}» رو تایپ کنی.",
                    )
                else:
                    username = request.user.get_username()
                    result = backup_mod.restore_from_file(request.POST.get("name", ""))
                    note = _relogin_after_restore(request, username)
                    messages.success(
                        request,
                        f"بازیابی انجام شد — {result['object_count']} رکورد برگردونده شد. {note}",
                    )
        except backup_mod.BackupError as exc:
            messages.error(request, str(exc))
        return redirect("panel:backups")

    return render(
        request,
        "panel/backups.html",
        {
            "page": "backups",
            "items": backup_mod.list_backups(),
            "confirm_phrase": RESTORE_CONFIRM_PHRASE,
            "backup_dir": str(backup_mod.backup_dir()),
        },
    )


@staff_member_required(login_url="panel:login")
def backup_download(request):
    try:
        path = backup_mod.resolve_backup(request.GET.get("name", ""))
    except backup_mod.BackupError as exc:
        messages.error(request, str(exc))
        return redirect("panel:backups")
    return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


# --- players ---------------------------------------------------------------


@staff_member_required(login_url="panel:login")
def players(request):
    if request.method == "POST":
        action = _post_action(request)
        identifier = (request.POST.get("identifier") or "").strip()
        try:
            if action == "charge":
                user, changed = moderation.charge_user(
                    identifier,
                    coins=_int(request, "coins"),
                    dna=_int(request, "dna"),
                    diamonds=_int(request, "diamonds"),
                )
                # charge_user returns the *new* balances, not the deltas
                parts = "، ".join(f"{k} → {v:,}" for k, v in changed.items())
                messages.success(request, f"«{user}» شارژ شد ({parts or 'بدون تغییر'}).")
            elif action == "ban":
                user = moderation.set_banned(identifier, True)
                messages.success(request, f"«{user}» مسدود شد.")
            elif action == "unban":
                user = moderation.set_banned(identifier, False)
                messages.success(request, f"مسدودی «{user}» برداشته شد.")
        except GameError as exc:
            messages.error(request, str(exc))
        return redirect(f"{reverse('panel:players')}?q={identifier}")

    query = (request.GET.get("q") or "").strip()
    rows = User.objects.annotate(creature_count=Count("creatures"))
    if query:
        filters = Q(username__icontains=query) | Q(lab_name__icontains=query) | Q(
            first_name__icontains=query
        )
        if query.lstrip("-").isdigit():
            filters |= Q(id=int(query))
        rows = rows.filter(filters)
    return render(
        request,
        "panel/players.html",
        {"page": "players", "query": query, "rows": rows.order_by("-cup")[:60]},
    )


# --- force-join channels ---------------------------------------------------


@staff_member_required(login_url="panel:login")
def channels(request):
    if request.method == "POST":
        action = _post_action(request)
        try:
            if action == "add":
                chat_id = _int(request, "chat_id")
                if not chat_id:
                    messages.error(request, "شناسه‌ی عددی کانال لازمه.")
                else:
                    force_join.add_channel(
                        chat_id,
                        (request.POST.get("username") or "").strip().lstrip("@") or None,
                        (request.POST.get("title") or "").strip() or None,
                    )
                    messages.success(request, "کانال اضافه شد.")
            elif action == "reward":
                force_join.set_reward(
                    _int(request, "channel_id"),
                    _int(request, "coins"),
                    _int(request, "dna"),
                    _int(request, "diamonds"),
                )
                messages.success(request, "جایزه‌ی عضویت ذخیره شد.")
            elif action == "duration":
                raw = (request.POST.get("hours") or "").strip()
                force_join.set_duration(_int(request, "channel_id"), int(raw) if raw else None)
                messages.success(request, "مدت اعتبار به‌روز شد.")
            elif action == "delete":
                force_join.remove_channel(_int(request, "channel_id"))
                messages.success(request, "کانال حذف شد.")
        except (GameError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("panel:channels")

    return render(
        request,
        "panel/channels.html",
        {
            "page": "channels",
            "rows": RequiredChannel.objects.order_by("id"),
            "now": timezone.now(),
        },
    )
