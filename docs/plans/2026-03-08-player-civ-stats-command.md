# `/player_civ_stats` Slash Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `/player_civ_stats` slash command that shows a player's 5 best and 5 worst civs with win rates.

**Architecture:** CSV data loader module reads `data/player_civ_stats.csv` into memory. Slash command in `commands.py` handles the interaction directly (no `run_slash` — not queue-channel-specific), looks up player nick, formats results as a Discord embed.

**Tech Stack:** nextcord (Embed, SlashOption, Member), csv stdlib, pathlib

---

### Task 1: Create CSV data loader module

**Files:**
- Create: `bot/civ_stats.py`

**Step 1: Write `bot/civ_stats.py`**

```python
import csv
from pathlib import Path

MIN_GAMES = 3
TOP_N = 5

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "player_civ_stats.csv"

# {nick_lower: [{"civ": str, "wins": int, "losses": int, "games": int, "winrate": float}, ...]}
_civ_data = {}


def load():
    """Load player civ stats from CSV into memory."""
    _civ_data.clear()
    with open(_DATA_PATH, newline="") as f:
        for row in csv.DictReader(f):
            nick = row["nick"].lower()
            _civ_data.setdefault(nick, []).append({
                "civ": row["civ"],
                "wins": int(row["wins"]),
                "losses": int(row["losses"]),
                "games": int(row["games"]),
                "winrate": float(row["winrate"]),
            })


def get_player_civs(nick):
    """Return (best, worst, total_qualifying) for a player nick.

    Returns None if player not found.
    best/worst are lists of up to TOP_N civ dicts, sorted by winrate.
    """
    entries = _civ_data.get(nick.lower())
    if not entries:
        return None

    qualified = [e for e in entries if e["games"] >= MIN_GAMES]
    if not qualified:
        return None

    by_wr = sorted(qualified, key=lambda e: (-e["winrate"], -e["games"]))
    total = len(qualified)

    best = by_wr[:TOP_N]
    # Only show worst if there are enough civs that best and worst won't fully overlap
    if total > TOP_N:
        worst = by_wr[-TOP_N:]
        worst.reverse()  # Show lowest winrate first
    else:
        worst = []

    return best, worst, total


# Load on import
load()
```

**Step 2: Verify the module loads correctly**

Run: `cd /home/claude-wukong/better_pubobot && python -c "from bot.civ_stats import get_player_civs; r = get_player_civs('aadvanced'); print(r[0][:2]) if r else print('not found')"`

Expected: prints 2 civ dicts with highest winrates for aadvanced

**Step 3: Commit**

```bash
git add bot/civ_stats.py
git commit -m "feat: add civ stats CSV data loader module"
```

---

### Task 2: Add slash command to commands.py

**Files:**
- Modify: `bot/context/slash/commands.py` (add import + command at end of file, before nick command)

**Step 1: Add the import**

At the top of `bot/context/slash/commands.py`, after the existing imports, add:

```python
from bot.civ_stats import get_player_civs
```

Add after line 13 (`import bot`).

**Step 2: Add the slash command**

Add after the `/leaderboard` command (after line 594), before the admin_rating commands:

```python
@dc.slash_command(name='player_civ_stats', description='Show best and worst civs for a player.', **guild_kwargs)
async def _player_civ_stats(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
):
	from core.utils import get_nick, error_embed
	from nextcord import Embed, Colour

	target = player or interaction.user
	nick = get_nick(target)

	result = get_player_civs(nick)
	if result is None:
		await interaction.response.send_message(
			embed=error_embed(f"No civ stats found for **{nick}**. They may not have enough matched games."),
			ephemeral=True
		)
		return

	best, worst, total = result

	def format_civs(civs):
		lines = []
		for i, c in enumerate(civs, 1):
			pct = f"{c['winrate'] * 100:.1f}%"
			lines.append(f"**{i}.** {c['civ']} — {pct} ({c['wins']}W / {c['losses']}L, {c['games']} games)")
		return "\n".join(lines)

	embed = Embed(title=f"Civ Stats for {nick}", colour=Colour(0x7289DA))
	embed.add_field(name="Best Civs", value=format_civs(best), inline=False)
	if worst:
		embed.add_field(name="Worst Civs", value=format_civs(worst), inline=False)
	embed.set_footer(text=f"{total} civs with 3+ games")

	if target.display_avatar:
		embed.set_thumbnail(url=target.display_avatar.url)

	await interaction.response.send_message(embed=embed)
```

**Step 3: Commit**

```bash
git add bot/context/slash/commands.py
git commit -m "feat: add /player_civ_stats slash command"
```

---

### Task 3: Manual smoke test

**Step 1: Check the bot starts without errors**

Run: `cd /home/claude-wukong/better_pubobot && python -c "from bot.civ_stats import load, get_player_civs; load(); print('OK')"`

Expected: `OK`

**Step 2: Verify edge cases in the data loader**

Run:
```python
python -c "
from bot.civ_stats import get_player_civs
# Test known player
r = get_player_civs('aadvanced')
best, worst, total = r
print(f'Best: {len(best)}, Worst: {len(worst)}, Total qualifying: {total}')
print(f'Best #1: {best[0]}')
# Test unknown player
print(f'Unknown: {get_player_civs(\"nonexistent_player_xyz\")}')
# Test case insensitivity
r2 = get_player_civs('AADVANCED')
print(f'Case insensitive: {r2 is not None}')
"
```

Expected:
- Best/worst counts printed, total qualifying > 5
- Best #1 has highest winrate
- Unknown returns None
- Case insensitive returns True

**Step 3: Final commit (if any fixes needed)**
