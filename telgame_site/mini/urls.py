"""Mini App URLs (mounted under /app/ by telgame_site/urls.py).

Feature modules are DISCOVERED, never listed by hand: every `telgame_site/mini/api/<name>.py`
that defines `routes = [(sub_path, view), ...]` is mounted at /app/api/<name>/<sub_path>, and
every file in static/screens/ is loaded by the shell page. Adding a feature = adding files.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import importlib
import json
import mimetypes
import os
import pkgutil
import threading
from email.utils import formatdate
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotModified
from django.urls import path
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET

from bio_lab.models import Creature, Equipment
from telgame_site.mini import api as api_pkg
from telgame_site.mini.core import MINI_DIR, STATIC_DIR, _sig, meta

THUMB_SIZE = 320          # grid tiles
THUMB_SIZE_LARGE = 720    # detail views (`&s=l`)
_FONTS = {"regular": "Vazirmatn-Regular.ttf", "bold": "Vazirmatn-Bold.ttf"}
_SDK = MINI_DIR.parent / "miniapp" / "telegram-web-app.js"
IMMUTABLE = "public, max-age=31536000, immutable"


# ── small helpers ─────────────────────────────────────────────────────────────
def _accepts(request, token: str, header: str = "Accept-Encoding") -> bool:
    return token in request.headers.get(header, "")


def _not_modified(request, etag: str):
    return HttpResponseNotModified() if request.headers.get("If-None-Match") == etag else None


_PACKED: dict = {}   # file path -> (stamp, version, raw, gzipped); static files only, safe to keep per worker


def _packed(target: Path):
    """A static file read once per worker: (version, raw bytes, gzipped bytes). Re-read when
    the file changes. Only for files that never depend on the player (SDK, fonts)."""
    stat = target.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    hit = _PACKED.get(str(target))
    if hit is None or hit[0] != stamp:
        raw = target.read_bytes()
        hit = (stamp, hashlib.sha1(raw).hexdigest()[:12], raw, gzip.compress(raw, 9, mtime=0))
        _PACKED[str(target)] = hit
    return hit[1], hit[2], hit[3]


def _send_packed(request, target: Path, content_type: str, fallback_cache: str):
    """Serve a `_packed` file: gzip when accepted, `immutable` when the URL names its version."""
    version, raw, packed = _packed(target)
    etag = f'"{version}"'
    resp = _not_modified(request, etag)
    if resp is None:
        use_gzip = _accepts(request, "gzip")
        resp = HttpResponse(packed if use_gzip else raw, content_type=content_type)
        if use_gzip:
            resp["Content-Encoding"] = "gzip"
    resp["ETag"] = etag
    resp["Vary"] = "Accept-Encoding"
    resp["Cache-Control"] = IMMUTABLE if request.GET.get("v") == version else fallback_cache
    return resp


# ── shell ─────────────────────────────────────────────────────────────────────
def _versioned(rel: str) -> str:
    f = STATIC_DIR / rel
    return f"/app/s/{rel}?v={int(f.stat().st_mtime)}"


def _inline_json(value) -> str:
    """JSON that is safe inside a <script> block."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _static_versions() -> dict:
    """{"img/bg_home.jpg": mtime, …} for the pictures in static/img — the app builds versioned
    (= cache-forever) URLs from it with K.asset()."""
    out = {}
    for f in sorted((STATIC_DIR / "img").glob("*")):
        if f.is_file():
            out[f"img/{f.name}"] = int(f.stat().st_mtime)
    return out


@xframe_options_exempt  # Telegram Web / Desktop show Mini Apps inside an iframe
@require_GET
def shell(request):
    """The single page of the app: core css/js first, then every screen module.

    To save round trips on a slow connection the page also carries what every player needs
    anyway: the label tables (`meta`, identical for everyone) and the versions of the static
    pictures. shell.html itself starts the profile request before the bundle has loaded."""
    css = ["core.css"] + sorted(p.relative_to(STATIC_DIR).as_posix() for p in (STATIC_DIR / "screens").glob("*.css"))
    js = ["core.js"] + sorted(p.relative_to(STATIC_DIR).as_posix() for p in (STATIC_DIR / "screens").glob("*.js")) + ["boot.js"]
    html = (MINI_DIR / "shell.html").read_text(encoding="utf-8")
    if settings.DEBUG and "bundle" not in request.GET:   # separate files are easier to debug locally
        css_html = "\n".join(f'<link rel="stylesheet" href="{_versioned(c)}">' for c in css)
        js_html = "\n".join(f'<script src="{_versioned(j)}"></script>' for j in js)
    else:                                                 # production: two requests instead of ~35
        css_html = f'<link rel="stylesheet" href="/app/bundle.css?v={_bundle("css", css)[0]}">'
        js_html = f'<script src="/app/bundle.js?v={_bundle("js", js)[0]}"></script>'
    sdk_html = f'<script src="/app/sdk.js?v={_packed(_SDK)[0]}"></script>' if _SDK.exists() else '<script src="/app/tg.js"></script>'
    data_html = "<script>window.__KMETA=" + _inline_json(meta()) + ";window.__KV=" + _inline_json(_static_versions()) + ";</script>"
    html = (html.replace("<!--CSS-->", css_html).replace("<!--JS-->", js_html)
            .replace("<!--SDK-->", sdk_html).replace("<!--DATA-->", data_html))
    body = html.encode("utf-8")
    resp = HttpResponse(content_type="text/html; charset=utf-8")
    if _accepts(request, "gzip"):
        body = gzip.compress(body, 6, mtime=0)
        resp["Content-Encoding"] = "gzip"
    resp.content = body
    resp["Vary"] = "Accept-Encoding"
    resp["Cache-Control"] = "no-store"
    return resp


_BUNDLES: dict = {}   # kind -> (stamp, version, raw, gzipped); static files only, safe to keep per worker


def _bundle(kind: str, files: list[str]):
    """All css (or js) files joined into one body; rebuilt when any file changes."""
    paths = [STATIC_DIR / rel for rel in files]
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in paths)
    hit = _BUNDLES.get(kind)
    if hit is None or hit[0] != stamp:
        sep = "\n;\n" if kind == "js" else "\n"
        raw = sep.join(f"/* {p.name} */\n" + p.read_text(encoding="utf-8") for p in paths).encode("utf-8")
        hit = (stamp, hashlib.sha1(raw).hexdigest()[:12], raw, gzip.compress(raw, 9, mtime=0))
        _BUNDLES[kind] = hit
    return hit[1], hit[2], hit[3]


@require_GET
def bundle(request, kind: str):
    names = sorted(p.relative_to(STATIC_DIR).as_posix() for p in (STATIC_DIR / "screens").glob(f"*.{kind}"))
    files = ["core.css"] + names if kind == "css" else ["core.js"] + names + ["boot.js"]
    version, raw, packed = _bundle(kind, files)
    use_gzip = _accepts(request, "gzip")
    resp = HttpResponse(packed if use_gzip else raw,
                        content_type=("text/css" if kind == "css" else "application/javascript") + "; charset=utf-8")
    if use_gzip:
        resp["Content-Encoding"] = "gzip"
    resp["Vary"] = "Accept-Encoding"
    resp["Cache-Control"] = IMMUTABLE if request.GET.get("v") == version else "no-cache"
    return resp


@require_GET
def sdk(request):
    """Telegram's telegram-web-app.js from our own domain, gzip-compressed (120 KB → 19 KB) and
    cached for good under its content hash. (/app/tg.js still serves the plain file.)"""
    if not _SDK.exists():
        raise Http404
    return _send_packed(request, _SDK, "application/javascript; charset=utf-8", "public, max-age=86400")


@require_GET
def static_file(request, rel: str):
    target = (STATIC_DIR / rel).resolve()
    if STATIC_DIR.resolve() not in target.parents or not target.is_file():
        raise Http404
    stat = target.stat()
    is_image = target.suffix.lower() in (".png", ".jpg", ".jpeg")
    # pictures go out as WebP when the client takes it (the 135 KB logo PNG shrinks to a fraction)
    converted = _thumb(target, 0, "webp") if is_image and _accepts(request, "image/webp", "Accept") else None
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}{"w" if converted else ""}"'
    resp = _not_modified(request, etag)
    if resp is None:
        if converted is not None:
            resp = FileResponse(open(converted, "rb"), content_type="image/webp")
        else:
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix in (".js", ".css", ".svg"):
                ctype += "; charset=utf-8"
            resp = FileResponse(open(target, "rb"), content_type=ctype)
        resp["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
    resp["ETag"] = etag
    if is_image:
        resp["Vary"] = "Accept"
    # a URL with ?v=<mtime> never changes. Without it: pictures may be reused for a day, code
    # is revalidated (cheaply now: the ETag is answered with an empty 304)
    resp["Cache-Control"] = IMMUTABLE if request.GET.get("v") else "public, max-age=86400" if is_image else "no-cache"
    return resp


@require_GET
def font(request, weight: str):
    """The Persian UI font, from our own domain (font CDNs are unreliable from Iran);
    gzip-compressed on the way (123 KB → 68 KB each)."""
    name = _FONTS.get(weight)
    target = Path(settings.BASE_DIR) / "assets" / "fonts" / name if name else None
    if target is None or not target.exists():
        raise Http404
    return _send_packed(request, target, "font/ttf", "public, max-age=2592000, immutable")


# ── images ────────────────────────────────────────────────────────────────────
_WEBP: bool | None = None


def _webp_ok() -> bool:
    global _WEBP
    if _WEBP is None:
        try:
            from PIL import features

            _WEBP = bool(features.check("webp"))
        except Exception:  # noqa: BLE001
            _WEBP = False
    return _WEBP


def _thumb(source, size: int, fmt: str = "jpeg") -> Path | None:
    """A cached copy of `source`, at most `size` px on its long side (0 = original size), as
    progressive JPEG or as WebP (about a third smaller). Made on the first request; after
    that a request costs two stat() calls. Written to a temp file and renamed, so another
    worker never serves a half-written picture."""
    from PIL import Image

    from game.media import CACHE_DIR

    src = Path(source)
    if fmt == "webp" and not _webp_ok():
        return None
    try:
        stat = src.stat()
    except OSError:
        return None
    large = size == 0 or size > THUMB_SIZE
    quality = (72 if large else 70) if fmt == "webp" else (76 if large else 74)
    key = f"{src}:{stat.st_mtime_ns}:{stat.st_size}:{size}:{fmt}:{quality}"
    out_dir = CACHE_DIR / "thumbs"
    out = out_dir / (hashlib.md5(key.encode()).hexdigest()[:20] + (".webp" if fmt == "webp" else ".jpg"))
    if out.exists():
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        with Image.open(src) as im:
            alpha = fmt == "webp" and (im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info)
            if size and not alpha:
                im.draft("RGB", (size, size))        # JPEG sources: decode at a reduced size
            im = im.convert("RGBA" if alpha else "RGB")
            if size:
                im.thumbnail((size, size), Image.LANCZOS)
            if fmt == "webp":
                im.save(tmp, "WEBP", quality=86 if alpha else quality, method=4)
            else:
                im.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
        os.replace(tmp, out)
    except Exception:  # noqa: BLE001 — a broken source must end as a 404 / the original, not a 500
        try:
            tmp.unlink()
        except OSError:
            pass
        return out if out.exists() else None
    return out


def _serve_thumb(request, source, cache: str):
    size = THUMB_SIZE_LARGE if request.GET.get("s") == "l" else THUMB_SIZE
    thumb = None
    if source:
        if _accepts(request, "image/webp", "Accept"):
            thumb = _thumb(source, size, "webp")
        if thumb is None:
            thumb = _thumb(source, size, "jpeg")
    if thumb is None:
        raise Http404
    etag = f'"{thumb.stem}"'
    resp = _not_modified(request, etag)
    if resp is None:
        resp = FileResponse(open(thumb, "rb"), content_type="image/webp" if thumb.suffix == ".webp" else "image/jpeg")
    resp["ETag"] = etag
    resp["Vary"] = "Accept"
    resp["Cache-Control"] = cache
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
    # The art changes when the creature grows / the item is upgraded. URLs made by core.py
    # carry `v` (stage, star, rarity / level) and change with it → the browser may keep them
    # for a month; an old URL without `v` keeps the short cache.
    return _serve_thumb(request, source, "private, max-age=2592000, immutable" if request.GET.get("v") else "private, max-age=3600")


@require_GET
def asset_image(request, rel: str):
    """Thumbnail of a file under assets/images/ (signed by core.asset_img)."""
    from game.media import ASSETS_DIR

    if ".." in rel or not hmac.compare_digest(request.GET.get("k", ""), _sig("a", rel)):
        raise Http404
    return _serve_thumb(request, ASSETS_DIR / rel, IMMUTABLE if request.GET.get("v") else "public, max-age=86400")


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
    path("bundle.css", bundle, {"kind": "css"}),
    path("bundle.js", bundle, {"kind": "js"}),
    path("sdk.js", sdk),
    path("s/<path:rel>", static_file),
    path("font/<str:weight>.ttf", font),
    path("img/a/<path:rel>", asset_image),
    path("img/<str:kind>/<int:obj_id>.jpg", object_image),
    *_api_routes(),
]
