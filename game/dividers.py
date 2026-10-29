"""Premium-emoji divider line «6-2 لوزی» (custom emoji pack «kaiju_v5b_by_HeroGameZbot»): a gold
converging double line with a lozenge in the middle, 11 pieces.

For HTML message bodies only (never button labels). The owner's rule: at most TWO of these in
one message — the report inside a battle message uses blank lines, the screen around it owns
the two lines. A client that can't show custom emoji sees «➖» placeholders, still a line.
"""

PACK = "kaiju_v5b_by_HeroGameZbot"
# artwork order, left → right
IDS = ("5967405759808413229", "5967358601067505819", "5967818540525298151", "5969566802668166762",
       "5969844583973002791", "5969927274978353163", "5967555718591552506", "5967780409805642437",
       "5967654983875698954", "5969981881192554539", "5967505510423863901")
RLM = "‏"   # the message is right-to-left: send the pieces reversed after an RLM so the art reads left → right

LINE = RLM + "".join(f'<tg-emoji emoji-id="{cid}">➖</tg-emoji>' for cid in reversed(IDS))


def line() -> str:
    return LINE
