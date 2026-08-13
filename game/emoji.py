from bio_lab.models import EmojiOverride

# key -> human label shown to the owner in /list_emoji. Scoped to the handful of icons
# that repeat on nearly every screen (resource/stat indicators) — one-off decorative
# emoji (🎉, 🏆, 🥚, ...) stay plain unicode since customizing those everywhere isn't
# worth the churn. Add more keys here + wire get_emoji() into new spots if wanted later.
EMOJI_KEYS = {
    "coin": "💰 سکه",
    "dna": "🧬 DNA",
    "energy": "⚡ انرژی",
    "hp": "❤️ HP",
    "atk": "⚔️ ATK",
    "def": "🛡 DEF",
    "spd": "💨 SPD",
}

_cache: dict[str, EmojiOverride] | None = None


def _load_cache() -> dict[str, EmojiOverride]:
    global _cache
    _cache = {o.key: o for o in EmojiOverride.objects.all()}
    return _cache


def refresh_cache() -> None:
    """Call after any EmojiOverride write so lookups reflect it without a bot restart."""
    _load_cache()


def get_emoji(key: str, fallback: str) -> str:
    """Returns HTML for `key`: a <tg-emoji> wrapper if the owner set a Premium custom
    emoji for it, otherwise the plain unicode `fallback`. Safe to call from anywhere —
    reads an in-memory cache, not the database, after the first (eager-warmed) load."""
    cache = _cache if _cache is not None else _load_cache()
    override = cache.get(key)
    if override is not None:
        return f'<tg-emoji emoji-id="{override.custom_emoji_id}">{override.placeholder}</tg-emoji>'
    return fallback


def set_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    EmojiOverride.objects.update_or_create(
        key=key, defaults={"custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
    )
    refresh_cache()


def clear_emoji(key: str) -> bool:
    deleted, _ = EmojiOverride.objects.filter(key=key).delete()
    refresh_cache()
    return deleted > 0


def list_overrides() -> list[EmojiOverride]:
    return list(EmojiOverride.objects.order_by("key"))
