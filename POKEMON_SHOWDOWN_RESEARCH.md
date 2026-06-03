# Pokemon Showdown Battle-Sim Research

**Date**: April 28, 2026  
**Objective**: Understand headless battle initialization, team data structures, request/action formats, and protocol for AI integration

## Table of Contents
1. [Key Files in the Showdown Repository](#1-key-files-in-the-showdown-repository)
2. [How to Initialize a Headless Battle](#2-how-to-initialize-a-headless-battle)
3. [Team Format and Structure](#3-team-format-and-structure)
4. [Request JSON Schema](#4-request-json-schema)
5. [Action Format Expected Each Turn](#5-action-format-expected-each-turn)
6. [Training Data Mapping](#6-training-data-mapping-to-actions)
7. [Parsing Winner and Battle Results](#7-parsing-winner-and-battle-results)
8. [Setup and Build Instructions](#8-setup-and-build-instructions)
9. [Protocol Events and Message Flow](#9-protocol-events-and-message-flow)
10. [Doubles-Specific Implementation](#10-doubles-specific-implementation)
11. [Example: Running a Headless Battle](#11-example-running-a-headless-battle)

---

## 1. Key Files in the Showdown Repository

### Repository Location
- **Main**: https://github.com/smogon/pokemon-showdown (TypeScript)
- **Key Docs**: PROTOCOL.md, sim/SIM-PROTOCOL.md, sim/SIMULATOR.md, sim/TEAMS.md, COMMANDLINE.md, ARCHITECTURE.md

### Core Battle Simulator Files
| File | Purpose |
|------|---------|
| `sim/battle.ts` | Main Battle class - controls game logic, turn execution |
| `sim/battle-stream.ts` | BattleStream - ObjectReadWriteStream for stdin/stdout IO |
| `sim/player.ts` | Player/side management - tracks team, active Pokémon, state |
| `sim/request-handler.ts` | Generates choice requests for each turn |
| `sim/teams.ts` | Team parsing, validation, format conversion (packed/JSON) |
| `sim/dex.ts` | Pokédex - Pokémon data, moves, abilities, items |

### Server Integration Files
| File | Purpose |
|------|---------|
| `server/rooms.ts` | Battle room management on live server |
| `server/sockets.ts` | WebSocket/SockJS connection handling |
| `lib/streams.ts` | Stream base classes and utilities |

### Testing/Examples
| File | Purpose |
|------|---------|
| `test/common.js` | Test utilities (TestTools.createBattle) |
| `test/TESTS.md` | Guide to writing battle tests |
| `test/sim/` | 1000+ tests showing battle usage patterns |

---

## 2. How to Initialize a Headless Battle

### Option A: JavaScript/TypeScript (npm package)

**Installation:**
```bash
npm install pokemon-showdown
```

**Basic Battle Setup:**
```javascript
const Sim = require('pokemon-showdown');
const stream = new Sim.BattleStream();

// Listen for output messages
(async () => {
    for await (const output of stream) {
        console.log(output);
        // Parse battle events, send choices, etc.
    }
})();

// Initialize battle
stream.write(`>start {"formatid":"gen9ou"}`);
stream.write(`>player p1 {"name":"Alice","team":"PACKED_TEAM_HERE"}`);
stream.write(`>player p2 {"name":"Bob","team":"PACKED_TEAM_HERE"}`);
```

**Output Types Received:**
- `update\n` - Messages for all players (battle events)
- `sideupdate\nPLAYERID\n` - Private messages (choice requests)
- `end\n` - Battle end with metadata

### Option B: Command Line (subprocess)

**Usage:**
```bash
echo '>start {"formatid":"gen9randombattle"}
>player p1 {"name":"Alice"}
>player p2 {"name":"Bob"}
>p1 move 1
>p2 move 1
' | ./pokemon-showdown simulate-battle
```

**Integration from Python:**
```python
import subprocess
import json

proc = subprocess.Popen(
    ['./pokemon-showdown', 'simulate-battle'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# Write commands
proc.stdin.write('>start {"formatid":"gen9ou"}\n')
proc.stdin.write('>player p1 {"name":"Alice","team":"..."}\n')
proc.stdin.write('>player p2 {"name":"Bob","team":"..."}\n')
proc.stdin.flush()

# Read responses
for line in proc.stdout:
    if 'request' in line:
        # Handle choice request
        pass
```

### Battle Start Sequence

1. **`>start` command** - Initialize battle with format
   - `formatid` (required): Format like "gen9ou", "gen9randombattle", "gen9doubles"
   - `seed` (optional): [4 integers] for reproducible RNG
   - `p1`, `p2` (optional): Player data inline

2. **`>player` commands** - Set player info (if not in >start)
   - `name`: Player name
   - `team`: Team in packed or JSON format
   - `avatar`: Avatar ID (optional)

3. **Battle begins automatically** once both players have teams

---

## 3. Team Format and Structure

Pokemon Showdown uses three team formats. **You'll usually work with Packed format for transmission and JSON for code.**

### Format 1: JSON (for programming)

**Structure:**
```json
[
  {
    "name": "Pikachu",
    "species": "Pikachu",
    "gender": "M",
    "item": "Leftovers",
    "ability": "Static",
    "evs": {"hp": 252, "atk": 0, "def": 0, "spa": 4, "spd": 0, "spe": 252},
    "nature": "Timid",
    "ivs": {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
    "moves": ["Thunderbolt", "Quick Attack", "Iron Tail", "Thunder Wave"]
  }
]
```

**Fields:**
- `name`: Nickname (optional, defaults to species)
- `species`: Pokémon species
- `gender`: "M", "F", or "" (genderless)
- `item`: Held item ID (or empty)
- `ability`: Ability name or "0"/"1"/"H" (slot)
- `evs`/`ivs`: Stats (hp/atk/def/spa/spd/spe)
- `nature`: Nature ("Timid", "Modest", etc.)
- `moves`: 1-4 move names

### Format 2: Packed (for transmission/storage)

**Example:**
```
Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||
```

**Structure:**
```
NICKNAME|SPECIES|ITEM|ABILITY|MOVES|NATURE|EVS|GENDER|IVS|SHINY|LEVEL|HAPPINESS,POKEBALL,HIDDENPOWERTYPE,GIGANTAMAX,DYNAMAXLEVEL,TERATYPE
```

**Rules:**
- `SPECIES` blank if same as `NICKNAME`
- `ABILITY` can be "0", "1", "H" or ability name
- `MOVES` comma-separated IDs (lowercase, no spaces)
- `NATURE` blank = Serious nature
- `EVs`/`IVs` comma-separated (hp,atk,def,spa,spd,spe)
- Trailing fields can be omitted if blank

**Full Example (6 Pokémon team):**
```
Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||]
Charizard||charcoal|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||]
Venusaur||lifeorb|chlorophyll|sludgebomb,gigadrain,hiddenpowerfire,synthesis|Modest|4,,,252,,252|F|||||]
Blastoise||assaultvest|torrent|hydropump,darkpulse,flashcannon|Modest|4,,,252,,252|M|||||]
Alakazam||lifeorb|magicguard|psychic,focusblast|Timid|4,,,252,,252|||||]
Gengar||lifeorb|levitate|shadowball,focusblast,hiddenpowerfire|Timid|4,,,252,,252|||||]
```

### Format 3: Export (human-readable)

```
Pikachu @ Leftovers
Ability: Static
EVs: 252 HP / 4 SpA / 252 Spe
Timid Nature
IVs: 30 SpA / 30 SpD
- Thunderbolt
- Quick Attack
- Iron Tail
- Thunder Wave
```

### Converting Between Formats

**In JavaScript/TypeScript (sim/teams.ts):**
```javascript
const {Teams} = require('pokemon-showdown');

// Packed → JSON
const json = Teams.unpack(packedTeam);

// JSON → Packed
const packed = Teams.pack(jsonTeam);

// Any format → JSON
const json2 = Teams.import(anyFormatString);

// JSON → Export
const exportFormat = Teams.export(jsonTeam);
```

**From Python/Command Line:**
```bash
# Packed → JSON
echo "Pikachu||leftovers|..." | ./pokemon-showdown json-team

# JSON → Packed
cat team.json | ./pokemon-showdown pack-team

# Any → Export
cat team.txt | ./pokemon-showdown export-team
```

---

## 4. Request JSON Schema

The server sends a **choice request** for each turn via `|request|` message. This tells the AI what actions are available.

### Complete Example

```json
{
  "active": [
    {
      "moves": [
        {
          "move": "Thunderbolt",
          "id": "thunderbolt",
          "pp": 24,
          "maxpp": 24,
          "target": "normal",
          "disabled": false
        },
        {
          "move": "Quick Attack",
          "id": "quickattack",
          "pp": 30,
          "maxpp": 30,
          "target": "normal",
          "disabled": false
        },
        {
          "move": "Iron Tail",
          "id": "irontail",
          "pp": 25,
          "maxpp": 25,
          "target": "normal",
          "disabled": false
        },
        {
          "move": "Thunder Wave",
          "id": "thunderwave",
          "pp": 20,
          "maxpp": 20,
          "target": "normal",
          "disabled": false
        }
      ]
    }
  ],
  "side": {
    "name": "Alice",
    "id": "p1",
    "pokemon": [
      {
        "ident": "p1:Pikachu",
        "details": "Pikachu, L50, M",
        "condition": "100/100",
        "active": true,
        "stats": {
          "atk": 106,
          "def": 131,
          "spa": 139,
          "spd": 230,
          "spe": 189
        },
        "moves": ["thunderbolt", "quickattack", "irontail", "thunderwave"],
        "baseAbility": "static",
        "item": "leftovers",
        "pokeball": "pokeball",
        "ability": "static"
      },
      {
        "ident": "p1:Charizard",
        "details": "Charizard, L50, M",
        "condition": "95/100",
        "active": false,
        "stats": {
          "atk": 139,
          "def": 131,
          "spa": 168,
          "spd": 141,
          "spe": 144
        },
        "moves": ["flamethrower", "aircutter", "dragonclaw", "roost"],
        "baseAbility": "blaze",
        "item": "charcoal",
        "pokeball": "pokeball",
        "ability": "blaze"
      }
    ]
  },
  "rqid": 3
}
```

### Field Explanations

**`active` array:**
- One object per active Pokémon (1 in singles, 2 in doubles, 3 in triples)
- `moves[]` - Available moves

**Move Object Fields:**
| Field | Meaning |
|-------|---------|
| `move` | Display name ("Thunderbolt") |
| `id` | Internal ID ("thunderbolt") - **use this for actions** |
| `pp` | Current power points |
| `maxpp` | Maximum power points |
| `target` | Move target type (see below) |
| `disabled` | true if unusable (paralyzed, Taunt, etc.) |

**Target Types:**
- `"normal"` - Single target (opponent or ally)
- `"self"` - User (Roost, Swords Dance)
- `"adjacentAlly"` - Adjacent ally (Helping Hand in doubles)
- `"allySide"` - Entire ally side (Reflect, Tailwind)
- `"foeSide"` - Entire opponent side (Spikes, Stealth Rock)
- `"allAdjacent"` - All adjacent Pokémon (Earthquake in doubles)

**`side.pokemon` array:**
- Your entire team (6 Pokémon maximum)

**Pokémon Object Fields:**
| Field | Meaning |
|-------|---------|
| `ident` | Unique ID like "p1:Pikachu" |
| `details` | Species, level, gender: "Pikachu, L50, M" |
| `condition` | "CURRENT/MAX" (own) or "/100" (opponent) |
| `active` | true if on field now |
| `stats` | In-battle stats (after boosts) |
| `moves` | Array of move IDs this Pokémon has |
| `baseAbility` | Pokémon's base ability |
| `item` | Held item ID |
| `ability` | Current ability (can differ if swapped) |

**`rqid`:**
- Request ID - send this back with your choice for undo protection
- Format: `>p1 move 1|RQID` (when using `/choose` on server)

---

## 5. Action Format Expected Each Turn

Send actions back to the battle simulator with the `>PLAYER CHOICE` format.

### Singles Actions

| Action | Format | Example |
|--------|--------|---------|
| Use move | `move SLOT` or `move NAME` | `move 1` or `move Thunderbolt` |
| With Mega | `move SLOT mega` | `move 1 mega` |
| With Z-Move | `move SLOT zmove` | `move 1 zmove` |
| With Dynamax | `move SLOT max` | `move 1 max` |
| Switch | `switch SLOT` or `switch NAME` | `switch 2` or `switch Charizard` |
| Auto-choose | `default` | `default` |

**Full Example - Singles Turn:**
```
>p1 move 1
>p2 switch 3
```

### Doubles Actions (Simultaneous)

Format: **comma-separated actions for each Pokémon** (left to right in your team)

**Examples:**
```
>p1 move 1 -1, move 2 1
>p2 move 1, switch 2
>p1 move Thunderbolt 1, move Helping Hand -1
```

**Structure:**
```
>p1 ACTION1, ACTION2[, ACTION3]
```

**Each ACTION:**
- `move SLOT [TARGET]` - Use move slot (1-4)
- `move SLOT mega [TARGET]` - Mega Evolve + move
- `switch SLOT` - Switch to slot (skip field Pokémon)
- `pass` - Do nothing (used if Pokémon fainted, optional)

**Targeting (Doubles/Triples):**
- `-1`, `-2`, `-3`: Your allies
  - `-1` = adjacent ally to this Pokémon
  - `-2` = farthest ally
  - `-3` = unused in doubles
- `+1`, `+2`, `+3`: Opponent slots (opposite direction)
  - `+1` = opponent on the right (from their view)
  - `+2` = opponent on the left
- `1`, `2`, `3`: Default target (varies by move type)

**Position Reference (Doubles, your perspective):**
```
p2a (opp right)    p2b (opp left)
      
p1a (your left)    p1b (your right)
```

**Triples Example:**
```
>p1 move 1 +1, move 2 -1, move 3
```
- Left Pokémon: move 1 at opponent slot 1
- Middle Pokémon: move 2 at ally slot -1 (left)
- Right Pokémon: move 3 (default target)

### Team Preview (start of battle)

If format uses team preview:
```
>p1 team 213456
```
- Reorder your team (1-6 map to positions)
- Example: `team 213456` = swap first two, keep rest

### Error Handling

If you send an **invalid action**:
```
|error|[Invalid choice] MESSAGE
|request|REQUEST_JSON
```

The battle sends a new request object to choose from. Common errors:
- Move with 0 PP
- Trapping effect prevents switching
- Paralyzed Pokémon can't move
- Move disabled by Disable/Imprison

---

## 6. Training Data Mapping to Actions

### Extracting Available Actions from Battle State

When parsing logs or current request, build:

```python
available_actions = {
    "moves": [
        {
            "id": "thunderbolt",
            "slot": 1,
            "pp": 24,
            "target_type": "normal"
        },
        {
            "id": "quickattack",
            "slot": 2,
            "pp": 30,
            "target_type": "normal"
        }
    ],
    "switches": [
        {
            "name": "Charizard",
            "slot": 2,
            "hp_percent": 95
        }
    ]
}
```

### Parsing Actions from Log Events

**From `|move|` message:**
```
|move|p1a:Pikachu|Thunderbolt|p2a:Charizard
```
→ Extract: Move ID = "thunderbolt", Player = p1, Active Pokémon = p1a

**To find move slot:**
- Look up "Thunderbolt" in active Pokémon's moves
- Map to slot 1-4 in that Pokémon's moveset
- Action: `move 1`

**From `|switch|` message:**
```
|switch|p1a:Charizard|Charizard, L50, M|95/100
```
→ Extract: Switched-in species = Charizard, HP = 95%

**To find switch slot:**
- Track team composition throughout game
- Find Charizard in team
- Map to team slot (1-6)
- Action: `switch 2` (if Charizard is slot 2)

### State Representation for ML

**Minimal state for AI input:**
```python
state = {
    "my_active": {
        "species": "Pikachu",
        "level": 50,
        "hp_percent": 100,
        "status": None,
        "moves": ["thunderbolt", "quickattack", "irontail", "thunderwave"],
        "stats": {
            "atk": 106, "def": 131, "spa": 139, "spd": 230, "spe": 189
        }
    },
    "my_team": [
        {"species": "Pikachu", "hp_percent": 100, "active": True},
        {"species": "Charizard", "hp_percent": 95, "active": False},
        # ... 4 more
    ],
    "opponent_active": {
        "species": "Unknown" or "Charizard",  # depends on preview
        "hp_percent": 100,
        "level": 50,
        "moves": [],  # opponent moves not always known
        "status": None
    },
    "field": {
        "weather": None,
        "terrain": None,
        "side_conditions": {
            "p1": [],
            "p2": []
        }
    },
    "turn": 1
}
```

### Action Representation for ML

```python
action = {
    "type": "move" | "switch",
    "move_slot": 1,      # for type=="move"
    "move_id": "thunderbolt",
    "switch_slot": 2,    # for type=="switch"
    "target": 1,         # for doubles (1 or -1, +1, +2, etc.)
}
```

### Training Example (from logs)

```python
training_example = {
    "state": state,  # from above
    "available_actions": available_actions,  # valid moves/switches
    "taken_action": action,  # what the player actually did
    "outcome": 0.5 | 1.0 | 0.0,  # loss | win | draw
}
```

---

## 7. Parsing Winner and Battle Results

### From Protocol Messages

**During battle:**
```
|win|Alice
```
→ Player "Alice" won

```
|tie|
```
→ Battle ended in draw

### From End Message (most detailed)

When battle ends, receive:
```
end
{"winner":"p1","seed":[...],"turns":15,...}
```

**Parse this JSON to get:**
```python
result = {
    "winner": "p1",  # "p1" or "p2", null for tie
    "loser": "p2",
    "turns": 15,
    "seed": [array, of, 4, ints],
    "p1": {
        "name": "Alice",
        "rating": 1600,      # if rated
        "ratingend": 1650,
        "team": [...]        # final team state
    },
    "p2": {
        "name": "Bob",
        "rating": 1550,
        "ratingend": 1500,
        "team": [...]
    }
}
```

### From VGC Replay Logs

Standard format ends with one of:
```
|win|USERNAME
|tie|
```

Then timestamp (if available):
```
|t:|1619827200
```

### Determining Outcome for ML

```python
def get_outcome(winner, my_player_id):
    if winner is None:
        return 0.5  # draw
    elif winner == my_player_id:
        return 1.0  # win
    else:
        return 0.0  # loss
```

---

## 8. Setup and Build Instructions

### Building from Source

**Prerequisites:**
- Node.js (v14+)
- npm

**Setup:**
```bash
# Clone
git clone https://github.com/smogon/pokemon-showdown.git
cd pokemon-showdown

# Install dependencies
npm install

# Build (compiles TypeScript in src/sim/ and src/server/ to dist/)
./build
# On Windows: node build

# Rebuild (if modified)
./build --force
```

**Verify build:**
```bash
./pokemon-showdown help
```

### Running as Server

```bash
./pokemon-showdown start 8000
```

Then connect to: `ws://localhost:8000/showdown/websocket`

### Running Command-Line Simulator

```bash
./pokemon-showdown simulate-battle
```

Then write battle commands to stdin, read output from stdout.

### Using as npm Package

**Install:**
```bash
npm install pokemon-showdown
```

**Use in code:**
```javascript
const Sim = require('pokemon-showdown');

// Battle Stream
const stream = new Sim.BattleStream();

// Teams utilities
const {Teams} = Sim;
const team = Teams.unpack(packedTeam);

// Dex/Pokémon data
const {Dex} = Sim;
const pikachu = Dex.species.get('Pikachu');
const thunderbolt = Dex.moves.get('Thunderbolt');

// Direct Battle (no stream)
const battle = new Sim.Battle({
    formatid: 'gen9ou',
    p1: {name: 'Alice', team: teamData},
    p2: {name: 'Bob', team: teamData}
});

battle.makeChoices('move 1', 'move 1');
console.log(battle.getDebugLog());
```

### Troubleshooting

**"Command not found: ./pokemon-showdown":**
- Run `./build` first to compile

**Port already in use:**
- Use different port: `./pokemon-showdown start 8001`
- Or kill process: `lsof -ti:8000 | xargs kill -9`

**TypeScript errors after git pull:**
- Run `./build --force`

---

## 9. Protocol Events and Message Flow

### Battle Initialization Sequence

When a battle starts, you receive these events in order:

```
|init|battle
|title|Alice vs Bob
|player|p1|Alice|1|1400
|player|p2|Bob|2|1350
|teamsize|p1|6
|teamsize|p2|6
|gametype|singles
|gen|9
|tier|[Gen 9] OU
|rule|Species Clause: Limit one of each Pokémon
|rule|Nickname Clause: Prevent battles with multiple Pokémon with the same nickname
|rule|Evasion Moves Clause: Evasion moves are banned
|rule|Evasion Abilities Clause: Evasion abilities are banned
|rule|OHKO Clause: OHKO moves are banned
|rule|Moody Clause: Moody is banned
|rule|Endless Battle Clause: Prevent infinite battles
|rule|HPPercentage Mod: HP is shown in percentages
|clearpoke
|poke|p1|Pikachu, L50, M|item
|poke|p1|Charizard, L50, M|item
|poke|p1|Venusaur, L50, F|item
|poke|p1|Blastoise, L50, M|item
|poke|p1|Alakazam, L50|item
|poke|p1|Gengar, L50|item
|poke|p2|Dragonite, L50, F|item
|poke|p2|Tyranitar, L50, F|item
|poke|p2|Gyarados, L50, M|item
|poke|p2|Salamence, L50, M|item
|poke|p2|Garchomp, L50, F|item
|poke|p2|Metagross, L50|item
|teampreview
|start
```

### During Battle - Each Turn

```
|request|{"active":[{"moves":[...]}],"side":{...},"rqid":1}
[AI waits for choice, then sends: >p1 move 1]
[AI waits for choice, then sends: >p2 move 2]

|
|-damage|p1a:Pikachu|85/100
|-damage|p2a:Dragonite|90/100
|-heal|p1a:Pikachu|90/100|[from] Leftovers
|turn|1

|request|{"active":[...],"side":{...},"rqid":2}
>p1 move 2
>p2 switch 2
|switch|p2a:Salamence|Salamence, L50, M|100/100
|-damage|p2a:Salamence|85/100
|turn|2
```

### Battle End

```
|win|Alice
|
end
{"winner":"p1","seed":[...],"turns":15,"p1":{...},"p2":{...}}
```

---

## 10. Doubles-Specific Implementation

### Team Preview in Doubles

```json
{
  "side": {
    "pokemon": [
      {"details": "Pikachu, L50, M"},
      {"details": "Charizard, L50, M"},
      ...
    ]
  }
}
```

First 2-4 Pokémon can be previewed. Full team has 6.

**Response (reorder for competitive advantage):**
```
>p1 team 213456
```

### Request Format (Doubles)

```json
{
  "active": [
    {
      "moves": [
        {"move": "Thunderbolt", "id": "thunderbolt", "target": "normal"},
        {"move": "Quick Attack", "id": "quickattack", "target": "normal"},
        {"move": "Iron Tail", "id": "irontail", "target": "normal"},
        {"move": "Thunder Wave", "id": "thunderwave", "target": "allAdjacent"}
      ]
    },
    {
      "moves": [
        {"move": "Flamethrower", "id": "flamethrower", "target": "normal"},
        {"move": "Air Cutter", "id": "aircutter", "target": "allAdjacent"},
        {"move": "Dragon Claw", "id": "dragonclaw", "target": "normal"},
        {"move": "Roost", "id": "roost", "target": "self"}
      ]
    }
  ],
  "side": {
    "pokemon": [
      {"ident": "p1a:Pikachu", "active": true, ...},
      {"ident": "p1b:Charizard", "active": true, ...},
      {"ident": "p1c:Venusaur", "active": false, ...},
      ...
    ]
  }
}
```

### Choices (Doubles)

**Position Layout (from p1's perspective):**
```
p2b (opp left)    p2a (opp right)
      ↑                ↑
      
p1a (your left)    p1b (your right)
      ↓                ↓
```

**Actions Format:**
```
>p1 ACTION1, ACTION2
```

**Examples:**

1. **Both use moves:**
   ```
   >p1 move 1 -1, move 2 +1
   ```
   - p1a (Pikachu): move 1 at p1b (ally) → Thunder Wave
   - p1b (Charizard): move 2 at p2a (foe right) → Flamethrower

2. **Move + Switch:**
   ```
   >p1 move 1 +1, switch 3
   ```
   - p1a: move 1 at opponent
   - p1b: switch to slot 3

3. **Both switch:**
   ```
   >p1 switch 3, switch 4
   ```

4. **With Mega Evo:**
   ```
   >p1 move 1 mega +2, move 2 +1
   ```

5. **With targeting ambiguity (named move):**
   ```
   >p1 move Thunderbolt -1, move Helping Hand -1
   ```

### Triples

```
>p1 ACTION1, ACTION2, ACTION3
```

- 3 Pokémon active simultaneously
- Positions: p1a (left), p1b (center), p1c (right)
- Targeting: ±1, ±2, ±3 for different range

---

## 11. Example: Running a Headless Battle

### Python Example (Subprocess)

```python
import subprocess
import json
import sys

def run_battle():
    # Start simulator
    proc = subprocess.Popen(
        ['./pokemon-showdown', 'simulate-battle'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    # Teams (packed format)
    team_p1 = "Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||"
    team_p2 = "Charizard||charcoal|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||"
    
    # Initialize
    proc.stdin.write('>start {"formatid":"gen9ou"}\n')
    proc.stdin.write(f'>player p1 {{"name":"Alice","team":"{team_p1}"}}\n')
    proc.stdin.write(f'>player p2 {{"name":"Bob","team":"{team_p2}"}}\n')
    proc.stdin.flush()
    
    turn = 0
    while True:
        line = proc.stdout.readline().strip()
        
        if not line:
            continue
        
        if line.startswith('sideupdate'):
            # Request message
            proc.stdout.readline()  # player ID
            request_json = proc.stdout.readline().strip()
            
            try:
                request = json.loads(request_json)
            except:
                continue
            
            turn += 1
            print(f"Turn {turn} - Received request")
            
            # Make random choice (demo)
            if turn % 2 == 0:
                choice = "move 1"
            else:
                choice = "move 2"
            
            player = 'p1' if turn % 2 == 1 else 'p2'
            proc.stdin.write(f'>{player} {choice}\n')
            proc.stdin.flush()
            print(f"Sent: {player} {choice}")
        
        elif line == 'end':
            # Battle end
            metadata = proc.stdout.readline().strip()
            try:
                result = json.loads(metadata)
                print(f"\nBattle ended!")
                print(f"Winner: {result.get('winner', 'tie')}")
                print(f"Turns: {result.get('turns')}")
            except:
                pass
            break
        
        elif line.startswith('update'):
            # Battle event
            events = proc.stdout.readline().strip()
            print(f"Events: {events[:80]}...")

if __name__ == '__main__':
    run_battle()
```

### JavaScript Example (npm package)

```javascript
const Sim = require('pokemon-showdown');

async function runBattle() {
    const stream = new Sim.BattleStream();
    
    const teams = {
        p1: "Pikachu||leftovers|static|thunderbolt,quickattack|Timid|252,,,4,,252|M|||||",
        p2: "Charizard||charcoal|blaze|flamethrower,aircutter|Modest|4,,,252,,252|M|||||"
    };
    
    let turn = 0;
    
    // Read output
    const output = (async () => {
        for await (const message of stream) {
            console.log(message);
            
            if (message.includes('|request|')) {
                const reqStart = message.indexOf('{');
                const request = JSON.parse(message.slice(reqStart));
                
                turn++;
                console.log(`Turn ${turn}`);
                
                // Simple AI
                const choice = turn % 2 === 0 ? "move 1" : "move 2";
                const player = turn % 2 === 1 ? "p1" : "p2";
                
                stream.write(`>${player} ${choice}\n`);
            }
            
            if (message.includes('|win|')) {
                process.exit(0);
            }
        }
    })();
    
    // Initialize
    stream.write(`>start {"formatid":"gen9ou"}\n`);
    stream.write(`>player p1 {"name":"Alice","team":"${teams.p1}"}\n`);
    stream.write(`>player p2 {"name":"Bob","team":"${teams.p2}"}\n`);
}

runBattle();
```

---

## Summary

### Quick Reference

| Concept | Format/Details |
|---------|----------------|
| **Start Battle** | `>start {"formatid":"gen9ou"}` + player commands |
| **Team Format** | Packed: `Pikachu\|\|leftovers\|static\|...` |
| **Make Choice** | `>p1 move 1` or `>p1 switch 2` |
| **Request JSON** | Sent on `\|request\|` - contains available moves/switches |
| **Battle End** | `\|win\|PLAYER` or `\|tie\|` then `end` with metadata |
| **Doubles** | Actions comma-separated: `move 1 +1, move 2 -1` |
| **Target Numbers** | `-1`, `-2` = allies; `+1`, `+2` = opponents |

### Key Takeaways for AI Integration

1. **Use Request JSON** - It contains exactly what's available each turn
2. **Map to Actions** - Convert available moves/switches to move slots (1-4) or switch slots (1-6)
3. **Handle Doubles** - Comma-separate actions for each Pokémon
4. **Parse Events** - Extract state from `|move|`, `|switch|`, `|-damage|` messages
5. **Track Outcome** - `|win|` or `|tie|` at end, or parse `end` metadata
6. **Use Packed Teams** - Required for transmission; convert to JSON for internal use

---

**End of Research Document**
