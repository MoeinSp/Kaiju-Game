"""One look for every «… باز شد!» message: chests (world boss, alliance war, auction, packs, the
cup, arena slots, the weekly mission box, the dungeon shop) and boxes (genetic and monster boxes,
in DM and in groups).

Each loot on its own line (طلا / DNA / الماس); creatures and gear grouped by rarity, rarest
first, so a big batch reads at a glance. `chests_text` takes the dicts of
game.arena_chests.grant_chest_contents / open_chest; `box_reveal` / `boxes_text` the results of
game.lootbox.
"""

from __future__ import annotations

import html
from collections import defaultdict

from game import constants
from game.emoji import get_emoji

DIV = "━━━━━━━━━━━━━━━━━━━━"
_RARER_FIRST = list(reversed(constants.RARITY_ORDER))


def _e(s) -> str:
    return html.escape(str(s or ""))


def _rarity(r: str) -> str:
    return constants.RARITY_LABELS.get(r, r)


def loot_lines(coins: int = 0, dna: int = 0, diamonds: int = 0) -> list[str]:
    """«💰 طلا: +X» / «🧬 DNA: +X» / «💎 الماس: +X» — one line each, zero ones left out."""
    out = []
    if coins:
        out.append(f"{get_emoji('coin')} طلا: <code>+{coins:,}</code>")
    if dna:
        out.append(f"{get_emoji('dna')} DNA: <code>+{dna:,}</code>")
    if diamonds:
        out.append(f"{get_emoji('diamond')} الماس: <code>+{diamonds:,}</code>")
    return out


def grouped(pairs: list[tuple[str, str]], icon: str, title: str) -> list[str]:
    """[(rarity, name)…] → «🦖 هیولاها (12)» and one line per rarity: «🟣 حماسی ×4: a، b، …»."""
    if not pairs:
        return []
    by = defaultdict(list)
    for rarity, name in pairs:
        by[rarity].append(_e(name))
    lines = [f"{icon} <b>{title} ({len(pairs)})</b>"]
    for rarity in _RARER_FIRST:
        names = by.get(rarity)
        if names:
            lines.append(f"{_rarity(rarity)}{f' ×{len(names)}' if len(names) > 1 else ''}: {'، '.join(names)}")
    return lines


def _item_name(item) -> str:
    lvl = getattr(item, "level", 1) or 1
    return f"{item.name} (+{lvl})" if lvl > 1 else item.name


def drop_line(rarity: str, creature=None, item=None) -> str:
    if creature is not None:
        return f"{get_emoji('creature')} هیولا: {_rarity(rarity)} «{_e(creature.name)}»"
    if item is not None:
        return f"🛡 تجهیزات: {_rarity(rarity)} «{_e(_item_name(item))}»"
    return ""


def _chest_emoji(r: dict) -> str:
    return get_emoji(f"chest_{r.get('tier')}", r.get("emoji") or "🎁")


# ══════════════════════════ chests ══════════════════════════
def chest_card(r: dict, sub: str = "", footer: str = "") -> str:
    """One opened chest: title, its loot line by line, then the creature / gear it held."""
    lines = [f"{_chest_emoji(r)} <b>{_e(r['name'])} باز شد!</b>"] + ([sub] if sub else []) + [DIV]
    lines += loot_lines(r.get("coins", 0), r.get("dna", 0), r.get("diamonds", 0))
    drop = drop_line(r.get("rarity"), r.get("creature"), r.get("item"))
    if drop:
        lines += ["", drop]
    if footer:
        lines += [DIV, footer]
    return "\n".join(lines)


def chests_text(results: list[dict], what: str = "جعبه") -> str:
    """Several chests opened at once («🎁 15 جعبه باز شد!»): how many of each, the summed loot,
    then every creature and piece of gear grouped by rarity. A single chest gets chest_card."""
    if len(results) == 1:
        return chest_card(results[0])
    kinds: dict[str, list] = {}
    for r in results:
        kinds.setdefault(r["name"], [r, 0])[1] += 1
    tally = " · ".join(f"{_chest_emoji(r)} {_e(name.replace('جعبه‌ی ', '').replace('جعبه ', ''))} ×{n}"
                       for name, (r, n) in kinds.items())
    lines = [f"{get_emoji('gift')} <b>{len(results)} {what} باز شد!</b>", f"<i>{tally}</i>", DIV]
    lines += loot_lines(sum(r.get("coins", 0) for r in results), sum(r.get("dna", 0) for r in results),
                        sum(r.get("diamonds", 0) for r in results))
    cr = grouped([(r["rarity"], r["creature"].name) for r in results if r.get("creature") is not None],
                 get_emoji("creature"), "هیولاها")
    it = grouped([(r["rarity"], _item_name(r["item"])) for r in results if r.get("item") is not None
                  and r.get("creature") is None], "🛡", "تجهیزات")
    for block in (cr, it):
        if block:
            lines += [""] + block
    return "\n".join(lines)


# ══════════════════════════ boxes (game.lootbox) ══════════════════════════
def pay_lines(summary: dict) -> list[str]:
    """How a batch of genetic boxes was paid, one fact per line."""
    out = []
    if summary.get("from_tickets"):
        out.append(f"🎟 با بلیط: <code>{summary['from_tickets']}</code>")
    if summary.get("paid_boxes"):
        out.append(f"💳 با هزینه: <code>{summary['paid_boxes']}</code> "
                   f"(<code>{summary['gold_spent']:,}</code> طلا + <code>{summary['dna_spent']:,}</code> DNA)")
    out.append(f"🎟 بلیط باقی‌مونده: <code>{summary.get('tickets_left', 0)}</code>")
    return out


def box_reveal(label: str, r: dict, free: bool = False, pay: dict | None = None) -> str:
    """One box: the prize behind a spoiler (tap to reveal), then how it was paid."""
    lines = [f"{label} <b>باز شد!</b>"] + ([f"<i>{get_emoji('gift')} هدیه‌ی رایگان امروزت</i>"] if free else []) + [DIV]
    creature = r.get("creature")
    if creature is not None:
        lines.append(f"<tg-spoiler>{get_emoji('egg')} <b>{_e(creature.name)}</b>\n"
                     f"{constants.element_label(creature.element)} · {_rarity(r['rarity'])}</tg-spoiler>")
        lines.append("<i>توی کلکسیون می‌تونی فعالش کنی.</i>")
    else:
        it = r["item"]
        lines.append(f"<tg-spoiler>{constants.EQUIPMENT_SLOT_LABELS.get(it.slot, '🎒')} <b>{_e(_item_name(it))}</b>\n"
                     f"{_rarity(r['rarity'])}</tg-spoiler>")
        lines.append("<i>توی تجهیزات می‌تونی تجهیزش کنی.</i>")
    if pay is not None:
        lines += [""] + pay_lines(pay)
    return "\n".join(lines)


def boxes_text(label: str, summary: dict, pay: bool = False) -> str:
    """A batch of boxes (×10 / ×11): the best drop, then creatures and gear grouped by rarity."""
    opened, paid = summary["opened"], summary.get("paid", summary["opened"])
    lines = [f"{label} ×{opened} <b>باز شد!</b>"]
    if opened > paid:
        lines.append(f"<i>{paid} پرداختی + {opened - paid} هدیه</i>")
    lines.append(DIV)
    best = summary.get("best")
    if best:
        obj = best["creature"] if best["kind"] == "creature" else best["item"]
        name = obj.name if best["kind"] == "creature" else _item_name(obj)
        lines.append(f"⭐ بهترینش: {_rarity(best['rarity'])} «{_e(name)}»")
    cr = grouped([(r["rarity"], r["creature"].name) for r in summary.get("creatures", [])], get_emoji("creature"), "هیولاها")
    it = grouped([(r["rarity"], _item_name(r["item"])) for r in summary.get("items", [])], "🛡", "تجهیزات")
    for block in (cr, it):
        if block:
            lines += [""] + block
    if pay:
        lines += [DIV] + pay_lines(summary)
    return "\n".join(lines)
