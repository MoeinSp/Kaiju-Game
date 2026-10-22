"""Premium-emoji divider lines («kaiju_lines_by_HeroGameZbot» custom emoji pack).

Each style is four pieces: the right end, a repeating segment, a centre ornament and the left
end. divider() strings them together for HTML message bodies (never button labels). A client
that can't show custom emoji sees the «➖» placeholders, so the line still reads as a line.
"""

PACK = "kaiju_lines_by_HeroGameZbot"
IDS = {
    "neon": {"capR": "5967562500344913651", "seg": "5967762302223524354", "mid": "5967502538306494642", "capL": "5970052378785751541"},
    "helix": {"capR": "5967352150026625209", "seg": "5967290109224034461", "mid": "5969866123233994312", "capL": "5967594111304212487"},
    "gold": {"capR": "5969831415603274254", "seg": "5967777776990690493", "mid": "5969599118002101844", "capL": "5967822801132855282"},
    "beads": {"capR": "5967461448354373522", "seg": "5969662069337759678", "mid": "5967352991840215009", "capL": "5969591305456590472"},
    "win": {"capR": "5967440858281156695", "seg": "5969878707488171565", "mid": "5969616057353117312", "capL": "5967502224773880827"},
    "lose": {"capR": "5967377477448768891", "seg": "5967511725241539847", "mid": "5969554871249018969", "capL": "5967399519220931677"},
}
RLM = "‏"   # keeps the pieces right-to-left, so each end cap lands on its own side


def _e(cid: str) -> str:
    return f'<tg-emoji emoji-id="{cid}">➖</tg-emoji>'


def divider(style: str = "neon", half: int = 4) -> str:
    """One divider line: end + `half` segments + ornament + `half` segments + end."""
    p = IDS.get(style) or IDS["neon"]
    return RLM + _e(p["capR"]) + _e(p["seg"]) * half + _e(p["mid"]) + _e(p["seg"]) * half + _e(p["capL"])
