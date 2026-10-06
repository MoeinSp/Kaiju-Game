"""Mini App URLs (mounted under /app/ by telgame_site/urls.py).

Feature modules are DISCOVERED, never listed by hand: every `telgame_site/mini/api/<name>.py`
that defines `routes = [(sub_path, view), ...]` is mounted at /app/api/<name>/<sub_path>, and
every file in static/screens/ is loaded by the shell page. Adding a feature = adding files.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import mimetypes
import pkgutil
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.urls import path
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET

from bio_lab.models import Creature, Equipment
from telgame_site.mini import api as api_pkg
from telgame_site.mini.core import MINI_DIR, STATIC_DIR, _sig

THUMB_SIZE = 320          # grid tiles
THUMB_SIZE_LARGE = 720    # detail views (`&s=l`)
_FONTS = {"regular": "Vazirmatn-Regular.ttf", "bold": "Vazirmatn-Bold.ttf"}


# ── shell ─────────────────────────────────────────────────────────────────────
def _versioned(rel: str) -> str:
    f = STATIC_DIR / rel
    return f"/app/s/{rel}?v={int(f.stat().st_mtime)}"


@xframe_options_exempt  # Telegram Web / Desktop show Mini Apps inside an iframe
@require_GET
def shell(request):
    """The single page of the app: core css/js first, then every screen module."""
    css = ["core.css"] + sorted(p.relative_to(STATIC_DIR).as_posix() for p in (STATIC_DIR / "screens").glob("*.css"))
    js = ["core.js"] + sorted(p.relative_to(STATIC_DIR).as_posix() for p in (STATIC_DIR / "screens").glob("*.js")) + ["boot.js"]
    html = (MINI_DIR / "shell.html").read_text(encoding="utf-8")
    html = html.replace("<!--CSS-->", "\n".join(f'<link rel="stylesheet" href="{_versioned(c)}">' for c in css))
    html = html.replace("<!--JS-->", "\n".join(f'<script src="{_versioned(j)}"></script>' for j in js))
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp


@require_GET
def static_file(request, rel: str):
    target = (STATIC_DIR / rel).resolve()
    if STATIC_DIR.resolve() not in target.parents or not target.is_file():
        raise Http404
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix in (".js", ".css", ".svg"):
        ctype += "; charset=utf-8"
    resp = FileResponse(open(target, "rb"), content_type=ctype)
    # the shell links every file with ?v=<mtime>, so a versioned URL never changes
    resp["Cache-Control"] = "public, max-age=31536000, immutable" if request.GET.get("v") else "no-cache"
    return resp


@require_GET
def font(request, weight: str):
    """The Persian UI font, from our own domain (font CDNs are unreliable from Iran)."""
    name = _FONTS.get(weight)
    target = Path(settings.BASE_DIR) / "assets" / "fonts" / name if name else None
    if target is None or not target.exists():
        raise Http404
    resp = FileResponse(open(target, "rb"), content_type="font/ttf")
    resp["Cache-Control"] = "public, max-age=2592000, immutable"
    return resp


# ── images ────────────────────────────────────────────────────────────────────
def _thumb(source, size: int) -> Path | None:
    """A cached JPEG of `source`, at most `size` px on its long side (made on first request)."""
    from PIL import Image

    from game.media import CACHE_DIR

    src = Path(source)
    if not src.exists():
        return None
    out_dir = CACHE_DIR / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stat = src.stat()
    name = hashlib.md5(f"{src}:{stat.st_mtime_ns}:{stat.st_size}:{size}".encode()).hexdigest()[:20] + ".jpg"
    out = out_dir / name
    if not out.exists():
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size))
            im.save(out, "JPEG", quality=80 if size > THUMB_SIZE else 78, optimize=True)
    return out


def _serve_thumb(request, source, max_age: int):
    size = THUMB_SIZE_LARGE if request.GET.get("s") == "l" else THUMB_SIZE
    thumb = _thumb(source, size) if source else None
    if thumb is None:
        raise Http404
    resp = FileResponse(open(thumb, "rb"), content_type="image/jpeg")
    resp["Cache-Control"] = f"private, max-age={max_age}"
    return resp


@require_GET
def object_image(request, kind: str, obj_id: int):
    """Thumbnail of one creature / equipment piece. An <img> can't send the auth header, so
    the URL carries a short HMAC of the id (see core.creature_img / equipment_img)."""
    from game.media import get_creature_image_path, get_equipment_image_path

    if kind not in ("c", "e") or not hmac.compare_digest(request.GET.get("k", ""), _sig(kind, obj_id)):
        raise Http404
    obj = (Creature if kind == "c" else Equipment).objects.filter(id=obj_id).first()
    if obj is None:
        raise Http404
    source = get_creature_image_path(obj) if kind == "c" else get_equipment_image_path(obj)
    # the art changes when the creature levels/stars up → short cache
    return _serve_thumb(request, source, 3600)


@require_GET
def asset_image(request, rel: str):
    """Thumbnail of a file under assets/images/ (signed by core.asset_img)."""
    from game.media import ASSETS_DIR

    if ".." in rel or not hmac.compare_digest(request.GET.get("k", ""), _sig("a", rel)):
        raise Http404
    return _serve_thumb(request, ASSETS_DIR / rel, 86400)


# ── routes ────────────────────────────────────────────────────────────────────
def _api_routes():
    out = []
    for info in pkgutil.iter_modules(api_pkg.__path__):
        module = importlib.import_module(f"{api_pkg.__name__}.{info.name}")
        for sub, view in getattr(module, "routes", []):
            out.append(path(f"api/{info.name}/{sub}", view))
    return out


urlpatterns = [
    path("", shell),
    path("s/<path:rel>", static_file),
    path("font/<str:weight>.ttf", font),
    path("img/a/<path:rel>", asset_image),
    path("img/<str:kind>/<int:obj_id>.jpg", object_image),
    *_api_routes(),
]
