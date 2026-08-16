# Kaiju Bio-Lab 🧬

A Persian-language Telegram game bot: collect monsters, evolve them, build a lab,
and fight — in private chat **and** in group chats, played with plain words
instead of slash commands. Built with **Python**, **Django ORM**, and
**python-telegram-bot**, with a separate operator **web panel** and one-command
**Docker** deployment.

> Persian-first: the entire player-facing experience is in Persian (فارسی). This
> README is in English for developers.

---

## What's inside

| Area | Summary |
|------|---------|
| **Creatures** | Levels, five rarity tiers, prestige stars from fusion, four equipment slots |
| **Fusion & breeding** | Fusion burns two same-name/same-star creatures for a guaranteed star-up; breeding keeps the parents but locks them up for hours and rolls a new creature |
| **Buildings** | One-worker idle economy: gold/DNA/diamond mines, blacksmith, fusion lab, main hall — unlocked one per hall level, maxing everything takes 1–2 weeks |
| **Stationed creatures** | Put a creature in a mine to raise its output; higher-level creatures help more |
| **Arena** | Weekly cup ladder, cup-based matchmaking (never power-based), 8-hour raid shields, lazy season settlement |
| **Hunting** | Solo PvE, one previewable opponent at a time |
| **Group play** | The whole game works in groups via ~24 Persian trigger words, owner-scoped inline cards, a periodic shared-cooldown reward, and a categorised, teaching help |
| **Lab level** | A per-player progress number (super-quadratic curve) that drives the leaderboard — no mechanical bonus, on purpose |
| **Guide** | A two-part in-game guide (how it works / where things are), sent on first run and always on the menu, shared by the group and the DM |
| **Web panel** | Operator dashboard: recolour every button (by role or individually), Premium-emoji theming, save/switch cosmetic *loadouts*, whole-DB backup & restore, player admin, forced-join channels |
| **Theming** | Telegram Premium custom emoji and three button colours, all reconfigurable live with no restart |

## Architecture in one paragraph

The `game/` package is pure game logic against the Django ORM and never touches
Telegram. `bot/` bridges it to python-telegram-bot: because PTB is async and the
ORM is sync, **every DB access happens inside a `_sync()` function run through
`run_db()`** — the project's cardinal rule, since a lazy query from async code
raises `SynchronousOnlyOperation`. Everything is computed lazily at read time
(energy regen, building production, upgrade completion, season settlement) — there
is no cron. The bot and the web panel share one database and one codebase, so a
palette change made in the panel is live in the bot on the very next button it
builds.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture notes and the reasoning
behind the balance constants.

---

## Run it locally

```bash
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # then fill in BOT_TOKEN + OWNER_TELEGRAM_ID
python manage.py migrate
python manage.py createsuperuser                    # for the web panel
python -m bot.main                                  # the bot (long-polls by default)
python manage.py runserver                          # the panel, at /panel/
```

With `.env`'s `POSTGRES_DB` empty it uses a local SQLite file; set it to switch
to Postgres.

## Run it in production (Docker)

Postgres + panel + bot, one command:

```bash
cp .env.example .env    # fill in the required values below
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Required in `.env`: `BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `DJANGO_SECRET_KEY`,
`POSTGRES_PASSWORD`. For webhook mode and TLS, see [`DEPLOY.md`](DEPLOY.md).

The bot runs **long-polling** out of the box (no inbound port needed). Set
`WEBHOOK_URL` + `WEBHOOK_SECRET` to switch to webhooks; add `--profile proxy` to
get a bundled Caddy that terminates HTTPS for the panel and the webhook on one
domain without colliding with anything else on the host.

---

## Tests

There is no formal test runner; the game logic is covered by standalone smoke
scripts (29 of them) that exercise the bot handlers and game rules end-to-end
against a throwaway SQLite database. They live outside the repo in the
development scratchpad. `python manage.py check` and the migration check should
both be clean.
