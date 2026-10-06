"""Mini App — step 1: the reachability probe.

Before building any real Mini App screens we need to know whether players can OPEN one
at all: a Mini App is an ordinary web page loaded from our own server inside Telegram's
webview. A player who reaches Telegram through an MTProto proxy (common in Iran) is NOT
using that proxy for the webview, so the page may never load, or load very slowly.

The bot's /apptest command sends a button that opens `/app/test/?u=<id>&s=<sig>`. Each
stage that reaches the server is recorded on the player's MiniAppProbe row:

  sent      the bot sent the button                      (bot/handlers/miniapp.py)
  html      the page request arrived here                → the network path works
  report    the page's script ran and POSTed timings     → JS + the round trip work
             · sdk   Telegram's WebApp object was there (the script is served from OUR
                     domain — telegram.org itself is filtered in Iran)
             · auth  the signed initData verified          → we can identify players
             · img   a ~200 KB game image loaded, with its time → what real screens cost

Everything is self-contained: no template, no static pipeline, no third-party host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from config import BOT_TOKEN

_DIR = Path(__file__).resolve().parent / "miniapp"
_TEST_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "images" / "features" / "feat_arena.jpg"
INIT_DATA_MAX_AGE = 24 * 3600


# ── signatures ────────────────────────────────────────────────────────────────
def probe_sig(user_id: int) -> str:
    """Short HMAC tying a probe link to one player, so a hit on the bare HTML (before any
    script runs) can be attributed — and nobody can mark someone else's probe."""
    return hmac.new(BOT_TOKEN.encode(), f"miniapp-probe:{int(user_id)}".encode(), hashlib.sha256).hexdigest()[:20]


def verify_init_data(init_data: str, max_age: int = INIT_DATA_MAX_AGE) -> dict | None:
    """Validate Telegram's `initData` (the Mini App's proof of who is using it) exactly as
    documented: HMAC-SHA256 of the sorted «key=value» lines with a key derived from the
    bot token. Returns the parsed fields (with `user` decoded) or None."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if max_age and time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        if "user" in pairs:
            pairs["user"] = json.loads(pairs["user"])
    except (ValueError, TypeError):
        return None
    return pairs


def _probe_for(request):
    """The MiniAppProbe row a signed link points at, or None."""
    from bio_lab.models import MiniAppProbe

    raw_uid, sig = request.GET.get("u", ""), request.GET.get("s", "")
    if not raw_uid.isdigit() or not hmac.compare_digest(sig, probe_sig(int(raw_uid))):
        return None
    probe, _ = MiniAppProbe.objects.get_or_create(user_id=int(raw_uid))
    return probe


# ── views ─────────────────────────────────────────────────────────────────────
@require_GET
def sdk_js(request):
    """Telegram's telegram-web-app.js, served from our own domain."""
    path = _DIR / "telegram-web-app.js"
    if not path.exists():
        raise Http404
    resp = FileResponse(open(path, "rb"), content_type="application/javascript; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


@require_GET
def test_image(request):
    """A real game image (~240 KB), never cached, so its load time means something."""
    if not _TEST_IMAGE.exists():
        raise Http404
    resp = FileResponse(open(_TEST_IMAGE, "rb"), content_type="image/jpeg")
    resp["Cache-Control"] = "no-store"
    return resp


@xframe_options_exempt  # Telegram Web / Desktop show Mini Apps inside an iframe
@require_GET
def test_page(request):
    probe = _probe_for(request)
    if probe is not None:
        probe.html_at = timezone.now()
        probe.opens += 1
        probe.save(update_fields=["html_at", "opens"])
    html = (_DIR / "test.html").read_text(encoding="utf-8")
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp


@csrf_exempt  # called by the page's fetch(); authenticated by the link signature instead
@require_POST
def test_report(request):
    probe = _probe_for(request)
    if probe is None:
        return JsonResponse({"ok": False, "error": "bad link"}, status=403)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "bad body"}, status=400)

    def _ms(key):
        try:
            return max(0, min(600_000, int(body.get(key))))
        except (TypeError, ValueError):
            return None

    verified = verify_init_data(str(body.get("init_data") or ""))
    probe.report_at = timezone.now()
    probe.sdk_ok = bool(body.get("sdk"))
    probe.auth_ok = bool(verified and int((verified.get("user") or {}).get("id", 0)) == probe.user_id)
    probe.load_ms = _ms("load_ms")
    probe.img_ms = _ms("img_ms")
    probe.img_ok = bool(body.get("img_ok"))
    probe.platform = str(body.get("platform") or "")[:24]
    probe.version = str(body.get("version") or "")[:16]
    probe.save(update_fields=["report_at", "sdk_ok", "auth_ok", "load_ms", "img_ms", "img_ok", "platform", "version"])
    return JsonResponse({"ok": True, "auth": probe.auth_ok})
