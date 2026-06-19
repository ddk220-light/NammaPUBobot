# Replay Quiz — Question Categories (spec v1)

Catalog of quiz questions derived from replay stats, attributed to leaderboard
identities. Built on the verified extraction pipeline (`utils/replay_quiz/`).
This is a living list — categories and metrics expand over time.

## Global conventions (apply to every metric unless noted)

- **Aggregation:** default unit is **per-player average across their matches**
  ("X per match"). Each metric also has a **single-game record** variant
  ("most X in one game"). Both are useful; averages are the headline.
- **Qualification:** a player needs a **minimum number of games** to appear in an
  average leaderboard (default **≥3**, tunable) so one lucky game doesn't win.
- **Direction:** most metrics support **most / least** (or **fastest / slowest**,
  **earliest / latest**). "Least/lowest" variants are deliberately **broader**
  (aggregate level, e.g. "fewest military before Imperial" — not per-unit-type).
- **Age threshold = the CLICK.** "before Feudal/Castle/Imperial" means game-time
  **< the player's age-up click time** in that game (RESEARCH of the age tech).
  "Fastest to Feudal" = the click time.
- **Null vs zero handling (important):**
  - **Counts** (villagers, military, buildings): **zero is a valid value** —
    included. "Made 0 military before Feudal" counts as 0 and is itself a stat.
  - **Timing** (age-up speed, tech "earliest to click", time-to-first-TC):
    **exclude games where it never happened** (never clicked the tech / never
    reached that age). Only rank players who actually have a time.
  - **"Before age X" when the player never reached X:** count their whole-game
    total (they spent the entire game below X). *[confirm — alternative is to
    exclude such games]*
- **Attribution:** per leaderboard identity (replay `profile_id` → nick, 98%
  covered; one-off guests shown by aoe2 name).
- **Counts are queue-clicks** (units/buildings commanded, an upper bound) — fine
  for "most/fastest/earliest" aggregates, not exact build counts.

---

## Category 1 — Villager creation (economy)

| ID | Metric | Directions |
|---|---|---|
| C1.1 | Villagers created per match | most / least |
| C1.2 | Villagers created **before clicking Feudal** | most / least |
| C1.3 | Villagers created **before clicking Castle** | most / least |
| C1.4 | Villagers created **before clicking Imperial** | most / least |

## Category 2 — Age-up & build speed

| ID | Metric | Directions |
|---|---|---|
| C2.1 | Time to click **Feudal** | fastest / slowest |
| C2.2 | Time to click **Castle** | fastest / slowest |
| C2.3 | Time to click **Imperial** | fastest / slowest |
| C2.4 | Time to build **first Town Center** | fastest / slowest |

## Category 3 — Buildings created (per match)

| ID | Metric | Directions |
|---|---|---|
| C3.1 | **Town Centers** built | most / least |
| C3.2 | **Military buildings** built (total: barracks+range+stable+castle) | most / least |
| C3.3 | **Barracks** built | most / least |
| C3.4 | **Archery Ranges** built | most / least |
| C3.5 | **Stables** built | most / least |
| C3.6 | **Castles** built | most / least |

## Category 4 — Military production

Two axes: a **time threshold** (before Feudal / before Castle / before Imperial)
and a **unit filter** (any military, or a specific unit/line).

| ID | Metric | Directions |
|---|---|---|
| C4.1 | Military units **before Feudal** (any) | most / least |
| C4.2 | Military units **before Castle** (any) | most / least |
| C4.3 | Military units **before Imperial** (any) | most / least |
| C4.4 | **By unit type, before Feudal:** scouts, militia-line (men-at-arms) | most |
| C4.5 | **By unit type, before Castle:** archers, skirmishers, scouts, spearmen, militia-line | most |
| C4.6 | **By unit type, before Imperial:** knights, camels, crossbowmen | most |
| C4.7 | **Unique units** (from the Castle) created — total | most |
| C4.8 | **Flags / extremes:** "made any units before Feudal" (who), "**never** made military before Feudal/Castle", **lowest** military before Imperial (broad) | yes/no, least |

> Note: pre-Feudal options are mostly scouts / militia-line (other unit types
> aren't trainable yet); the spec lists each unit under the earliest age it can
> appear.

## Category 5 — Technology timing ("earliest to click")

Only games where the player **actually researched** the tech count (timing rule).

| ID | Group | Example techs (earliest / latest to click) |
|---|---|---|
| C5.1 | **Economy** | Loom, Wheelbarrow, Hand Cart, Heavy Plow, Horse Collar, Double-Bit Axe / Bow Saw / Two-Man Saw, Gold/Stone Mining, Caravan, Coinage/Banking |
| C5.2 | **Blacksmith — attack** | Forging, Iron Casting, Blast Furnace, Fletching, Bodkin Arrow, Bracer |
| C5.3 | **Blacksmith — armor** | Scale/Chain/Plate Barding (cav), Scale/Chain/Plate Mail (inf), Padded/Leather/Ring Archer Armor |
| C5.4 | **Key power techs** | Bloodlines, Ballistics, Husbandry, Chemistry |
| C5.5 | **Unit upgrades** | knight-line, archer-line, etc. (earliest to click) |

---

## Terms I want to confirm (speech-to-text)

- "allergists" → **Villagers** (assumed)
- "heavy flow research" → **Heavy Plow** (assumed)
- "double attacks" → ? (Double-Bit Axe? a blacksmith attack upgrade?) — **confirm**
- "militia line, minotaurs" → **militia line / Men-at-Arms** (assumed; "minotaurs"?) — **confirm**
- "number of excommunications created" (in the pre-Castle military list) → ? not a
  unit; **Spearmen? Eagle Scouts?** — **confirm**

## Open decisions

1. **Average vs single-game record** as the headline framing (you stressed
   averages; some — "most villagers in one game" — are natural records). Plan:
   ship both, lead with averages.
2. **Minimum games** to qualify for an average leaderboard (default 3?).
3. **"Before age X" when a player never reached X** — count whole-game total, or
   exclude that game? (default: count whole-game total).
4. **Castle/Imperial click vs completion time** — using the **click** (matches
   your wording). OK?

---

## Data mapping (what's ready vs needs a small extension)

| Category | Status | Source |
|---|---|---|
| C1 villagers (+ before-age splits) | extension | `DE_QUEUE` Villager × `amount`, split by age-click time |
| C2 age speed | ✅ ready | age-click time (RESEARCH age tech) |
| C2.4 first TC time | ✅ ready | first `BUILD` Town Center / first villager-queue time |
| C3 buildings | extension | `BUILD` action by building name |
| C4 military (+ type/age splits) | extension | `DE_QUEUE` by unit name, classified, vs age-click time |
| C4.7 unique units | extension | `DE_QUEUE` of castle unique-unit names |
| C5 tech timing | extension | `RESEARCH` first-click per tech (null-excluded) |

All "extensions" are straightforward additions to `extract.py` — the underlying
events (research, queue, build, age-ups, per-player attribution) are already
parsed and verified. Each metric becomes one row: `(metric_id, scope, leaderboard
of identity → value)`.
