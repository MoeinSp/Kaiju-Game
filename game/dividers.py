"""Premium-emoji divider line «6-2 لوزی» (custom emoji pack «kaiju_v5b_by_HeroGameZbot»): a gold
converging double line with a lozenge in the middle, 11 pieces.

For HTML message bodies only (never button labels). The owner's rule: at most TWO of these in
one message — the report inside a battle message uses blank lines, the screen around it owns
the two lines. A client that can't show custom emoji sees «➖» placeholders, still a line.

Every other screen still writes plain «━━━━» / «────» rules; emojify() (run on every outgoing
HTML message by bot/main.py's send hook, after premiumize_html) turns them into this line —
the first two in a message, the rest into a blank line — so the whole bot shares one divider.
"""

import re

PACK = "kaiju_v5b_by_HeroGameZbot"
# artwork order, left → right
IDS = ("5967405759808413229", "5967358601067505819", "5967818540525298151", "5969566802668166762",
       "5969844583973002791", "5969927274978353163", "5967555718591552506", "5967780409805642437",
       "5967654983875698954", "5969981881192554539", "5967505510423863901")
RLM = "‏"   # the message is right-to-left: send the pieces reversed after an RLM so the art reads left → right

LINE = RLM + "".join(f'<tg-emoji emoji-id="{cid}">➖</tg-emoji>' for cid in reversed(IDS))


def line() -> str:
    return LINE


MAX_PER_MESSAGE = 2
EMOJI_CAP = 100   # Telegram shows at most 100 custom emoji in one message; past that, a plain blank line
# a line that is ONLY a rule of box-drawing dashes (a «━━ title ━━» heading is left alone)
_PLAIN = re.compile(r"^[ \t‏]*[━─═▬]{5,}[ \t]*$", re.MULTILINE)


def emojify(text: str) -> str:
    """Plain divider lines → the «6-2» line (at most MAX_PER_MESSAGE per message, the 6-2 lines
    already in it included); any further rule becomes a blank line."""
    if not text or not _PLAIN.search(text):
        return text
    state = {"left": MAX_PER_MESSAGE - text.count(LINE), "room": EMOJI_CAP - text.count("<tg-emoji"), "blank": False}

    def sub(_m):
        if state["left"] > 0 and state["room"] >= len(IDS):
            state["left"] -= 1
            state["room"] -= len(IDS)
            return LINE
        state["blank"] = True
        return ""

    out = _PLAIN.sub(sub, text)
    if state["blank"]:
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out
