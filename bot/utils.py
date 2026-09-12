from asgiref.sync import sync_to_async
from telegram.error import BadRequest

from game import constants
from game.emoji import get_emoji


def mission_reward_text(m: dict) -> str:
    """One mission's payout, formatted. Lives here rather than in a handler so
    every screen that shows missions (private/group/battle) renders the same
    thing — a mission can pay coins, DNA and a speed-up card, and it's easy to
    forget one of them when each screen formats its own."""
    parts = [f"+{m['coins']} {get_emoji('coin')}"]
    if m.get("dna"):
        parts.append(f"+{m['dna']} {get_emoji('dna')}")
    if m.get("speedup"):
        # "+⏱ ۳۰ دقیقه" read as "this mission takes 30 minutes" or "you have 30
        # minutes left". Naming the item and what it does removes both readings.
        parts.append(f"+۱ کارت سرعت {constants.speedup_plain_label(m['speedup'])} ⏱")
    return " ".join(parts)


def _run_db_sync(func, args, kwargs):
    # Each pool thread keeps its own Django DB connection; drop any that Postgres
    # has since closed (idle-timeout) so a reused thread never hits a stale socket.
    from django.db import close_old_connections

    close_old_connections()
    try:
        return func(*args, **kwargs)
    finally:
        close_old_connections()


async def run_db(func, *args, **kwargs):
    """Runs a synchronous Django-ORM function off the event loop and awaits its result.

    thread_sensitive=False deliberately: it runs on a real thread POOL so many
    players' DB work executes in parallel. The old thread_sensitive=True funnelled
    EVERY query bot-wide onto one worker thread (a leftover from the SQLite dev DB) —
    on the Postgres production DB that just serialised everyone and made the bot feel
    slow under load. Postgres handles concurrent connections fine; per-transaction
    locking (select_for_update in the money paths) keeps writes correct."""
    return await sync_to_async(_run_db_sync, thread_sensitive=False)(func, args, kwargs)


import json
import logging
import os
import re
from pathlib import Path
from telegram import InputMediaPhoto

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets" / "images"
FILE_ID_CACHE_FILE = ASSETS_DIR / "telegram_file_ids.json"

_FILE_ID_CACHE: dict[str, str] = {}
_CACHE_LOADED = False


def _get_cache_token_prefix() -> str:
    try:
        from config import BOT_TOKEN
        return BOT_TOKEN.split(":")[0] if ":" in str(BOT_TOKEN) else "default"
    except Exception:
        return "default"


def _load_file_id_cache():
    global _FILE_ID_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return
    try:
        if FILE_ID_CACHE_FILE.exists():
            with open(FILE_ID_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                prefix = _get_cache_token_prefix()
                if isinstance(data, dict):
                    if prefix in data and isinstance(data[prefix], dict):
                        _FILE_ID_CACHE = data[prefix]
                    elif all(isinstance(v, str) for v in data.values()):
                        # Backward compatibility if root is a flat dict
                        _FILE_ID_CACHE = data
                    else:
                        _FILE_ID_CACHE = {}
    except Exception as e:
        logger.warning("Failed to load telegram_file_ids.json: %s", e)
        _FILE_ID_CACHE = {}
    _CACHE_LOADED = True


def _save_file_id_cache():
    try:
        FILE_ID_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if FILE_ID_CACHE_FILE.exists():
            try:
                with open(FILE_ID_CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        data = raw
            except Exception:
                data = {}
        prefix = _get_cache_token_prefix()
        data[prefix] = _FILE_ID_CACHE
        with open(FILE_ID_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to save telegram_file_ids.json: %s", e)


def _to_rel_asset_key(path: str) -> str:
    try:
        p = Path(path).resolve()
        assets_p = ASSETS_DIR.resolve()
        return str(p.relative_to(assets_p)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def get_cached_file_id(path: str) -> str | None:
    _load_file_id_cache()
    key = _to_rel_asset_key(path)
    return _FILE_ID_CACHE.get(key)


def store_cached_file_id(path: str, file_id: str) -> None:
    if not path or not file_id:
        return
    _load_file_id_cache()
    key = _to_rel_asset_key(path)
    if _FILE_ID_CACHE.get(key) != file_id:
        _FILE_ID_CACHE[key] = file_id
        _save_file_id_cache()


def invalidate_cached_file_id(path: str) -> None:
    _load_file_id_cache()
    key = _to_rel_asset_key(path)
    if key in _FILE_ID_CACHE:
        del _FILE_ID_CACHE[key]
        _save_file_id_cache()


def safe_truncate_html(text: str, max_chars: int = 1000) -> str:
    """Truncates HTML text to max_chars without breaking tags or leaving unclosed tags."""
    if not text or len(text) <= max_chars:
        return text or ""

    cutoff = max_chars - 30
    last_nl = text.rfind("\n", 0, cutoff)
    if last_nl > cutoff // 2:
        truncated = text[:last_nl]
    else:
        truncated = text[:cutoff]

    last_open = truncated.rfind("<")
    last_close = truncated.rfind(">")
    if last_open > last_close:
        truncated = truncated[:last_open]

    tag_pattern = re.compile(r"<(/?[a-zA-Z0-9_-]+)(?:\s+[^>]*?)?>")
    open_tags = []

    for match in tag_pattern.finditer(truncated):
        tag_name = match.group(1)
        if tag_name.startswith("/"):
            name = tag_name[1:]
            if open_tags and open_tags[-1] == name:
                open_tags.pop()
        else:
            if not match.group(0).endswith("/>"):
                open_tags.append(tag_name)

    for tag in reversed(open_tags):
        truncated += f"</{tag}>"

    return truncated


async def send_screen(update, text, *, photo=None, reply_markup=None, parse_mode="HTML", **kwargs) -> None:
    """Render a screen the right way for however the player got here.
    Supports optional `photo` path. Uses Telegram file_id caching for instant CDN delivery.
    Handles transitions between photo and text messages smoothly and cleanly."""
    query = getattr(update, "callback_query", None)
    if query is None and hasattr(update, "data") and hasattr(update, "message"):
        query = update

    message = getattr(update, "effective_message", None) or getattr(query, "message", None)
    valid_photo = photo if (photo and os.path.exists(photo)) else None

    if valid_photo:
        if parse_mode == "HTML":
            caption = safe_truncate_html(text, 1000)
        else:
            caption = text[:1000] if text else ""
        plain_caption = re.sub(r"<[^>]+>", "", caption) if caption else ""
        cached_fid = get_cached_file_id(valid_photo)
        try:
            if query is not None and getattr(query, "message", None):
                has_photo = bool(getattr(query.message, "photo", None))
                if has_photo:
                    # Fast path 1: edit existing photo message with cached file_id
                    if cached_fid:
                        try:
                            res = await query.edit_message_media(
                                media=InputMediaPhoto(media=cached_fid, caption=caption, parse_mode=parse_mode),
                                reply_markup=reply_markup,
                            )
                            return res
                        except BadRequest as ex:
                            if any(k in str(ex).lower() for k in ("parse", "entity", "tag", "start tag")):
                                try:
                                    res = await query.edit_message_media(
                                        media=InputMediaPhoto(media=cached_fid, caption=plain_caption, parse_mode=None),
                                        reply_markup=reply_markup,
                                    )
                                    return res
                                except Exception:
                                    pass
                            logger.debug("edit_message_media with file_id failed, falling back: %s", ex)
                            invalidate_cached_file_id(valid_photo)
                            cached_fid = None
                        except Exception:
                            invalidate_cached_file_id(valid_photo)
                            cached_fid = None

                    # Cold path 1: upload file from disk and cache returned file_id
                    try:
                        with open(valid_photo, "rb") as f:
                            res = await query.edit_message_media(
                                media=InputMediaPhoto(media=f, caption=caption, parse_mode=parse_mode),
                                reply_markup=reply_markup,
                            )
                        if res and hasattr(res, "photo") and res.photo:
                            store_cached_file_id(valid_photo, res.photo[-1].file_id)
                        return res
                    except BadRequest as ex:
                        if any(k in str(ex).lower() for k in ("parse", "entity", "tag", "start tag")):
                            try:
                                with open(valid_photo, "rb") as f:
                                    res = await query.edit_message_media(
                                        media=InputMediaPhoto(media=f, caption=plain_caption, parse_mode=None),
                                        reply_markup=reply_markup,
                                    )
                                if res and hasattr(res, "photo") and res.photo:
                                    store_cached_file_id(valid_photo, res.photo[-1].file_id)
                                return res
                            except Exception:
                                pass
                    except Exception:
                        pass

                try:
                    await query.message.delete()
                except Exception:
                    pass

                # Fast path 2: send new photo using cached file_id
                if cached_fid:
                    try:
                        res = await query.message.chat.send_photo(
                            photo=cached_fid, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
                        )
                        return res
                    except BadRequest as ex:
                        if any(k in str(ex).lower() for k in ("parse", "entity", "tag", "start tag")):
                            try:
                                res = await query.message.chat.send_photo(
                                    photo=cached_fid, caption=plain_caption, reply_markup=reply_markup, parse_mode=None, **kwargs
                                )
                                return res
                            except Exception:
                                pass
                        logger.debug("send_photo with file_id failed, falling back: %s", ex)
                        invalidate_cached_file_id(valid_photo)
                        cached_fid = None
                    except Exception:
                        invalidate_cached_file_id(valid_photo)
                        cached_fid = None

                # Cold path 2: upload photo and cache returned file_id
                try:
                    with open(valid_photo, "rb") as f:
                        res = await query.message.chat.send_photo(
                            photo=f, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
                        )
                    if res and hasattr(res, "photo") and res.photo:
                        store_cached_file_id(valid_photo, res.photo[-1].file_id)
                    return res
                except BadRequest as ex:
                    if any(k in str(ex).lower() for k in ("parse", "entity", "tag", "start tag")):
                        try:
                            with open(valid_photo, "rb") as f:
                                res = await query.message.chat.send_photo(
                                    photo=f, caption=plain_caption, reply_markup=reply_markup, parse_mode=None, **kwargs
                                )
                            if res and hasattr(res, "photo") and res.photo:
                                store_cached_file_id(valid_photo, res.photo[-1].file_id)
                            return res
                        except Exception:
                            pass
                except Exception:
                    pass

            elif message is not None:
                # Fast path 3: reply photo using cached file_id
                if cached_fid:
                    try:
                        res = await message.reply_photo(
                            photo=cached_fid, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
                        )
                        return res
                    except BadRequest as ex:
                        if any(k in str(ex).lower() for k in ("parse", "entity", "tag", "start tag")):
                            try:
                                res = await message.reply_photo(
                                    photo=cached_fid, caption=plain_caption, reply_markup=reply_markup, parse_mode=None, **kwargs
                                )
                                return res
                            except Exception:
                                pass
                        logger.debug("reply_photo with file_id failed, falling back: %s", ex)
                        invalidate_cached_file_id(valid_photo)
                        cached_fid = None
                    except Exception:
                        invalidate_cached_file_id(valid_photo)
                        cached_fid = None

                # Cold path 3: upload photo via reply and cache returned file_id
                try:
                    with open(valid_photo, "rb") as f:
                        res = await message.reply_photo(
                            photo=f, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
                        )
                    if res and hasattr(res, "photo") and res.photo:
                        store_cached_file_id(valid_photo, res.photo[-1].file_id)
                    return res
                except BadRequest as ex:
                    if any(k in str(ex).lower() for k in ("parse", "entity", "tag", "start tag")):
                        try:
                            with open(valid_photo, "rb") as f:
                                res = await message.reply_photo(
                                    photo=f, caption=plain_caption, reply_markup=reply_markup, parse_mode=None, **kwargs
                                )
                            if res and hasattr(res, "photo") and res.photo:
                                store_cached_file_id(valid_photo, res.photo[-1].file_id)
                            return res
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Error in send_screen with photo: %s", e)
            pass

    if query is not None and getattr(query, "message", None):
        has_photo = bool(getattr(query.message, "photo", None))
        if has_photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            return await query.message.chat.send_message(
                text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
            )
        try:
            return await query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
            )
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise
        return getattr(query, "message", None)

    if message is not None:
        return await message.reply_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
        )
    return None


async def safe_edit_message_text(query, text, **kwargs):
    """query.edit_message_text(), but handles messages with existing photos (replaces with text),
    optional photo attachments, and swallows Telegram's 'Message is not modified' BadRequest."""
    photo = kwargs.pop("photo", None)
    if photo:
        return await send_screen(query, text, photo=photo, **kwargs)

    if getattr(query, "message", None) and getattr(query.message, "photo", None):
        try:
            await query.message.delete()
        except Exception:
            pass
        return await query.message.chat.send_message(text, **kwargs)

    try:
        return await query.edit_message_text(text, **kwargs)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise
    return getattr(query, "message", None)
