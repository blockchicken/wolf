# Headless Battle Runner

This module provides a Python wrapper for running Pokemon Showdown battles headlessly with AI decision-makers.

## Quick Start

### 1. Build Pokemon Showdown Locally

```bash
git clone https://github.com/smogon/pokemon-showdown.git
cd pokemon-showdown
npm install
./build  # (or 'node build' on Windows)
```

### 2. Update Configuration

In `test_headless_battles.py`, update `SHOWDOWN_PATH` to point to your pokemon-showdown directory:

```python
SHOWDOWN_PATH = Path("/path/to/pokemon-showdown")
```

### 3. Run Test Battles

```bash
python test_headless_battles.py
```

This runs 3 random-vs-random doubles battles and reports the results.

---

## Architecture

### `BattleRunner`

Main class that manages subprocess communication with Pokemon Showdown.

**Initialization:**
```python
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

runner = BattleRunner(
    showdown_path="/path/to/pokemon-showdown",
    format_id="gen9vgc2024regg",  # or "gen9ou", "gen9doubles", etc.
    seed=(1234, 5678, 9012, 3456)  # optional for reproducibility
)
```

**Running a Battle:**
```python
result, training_data = runner.run_battle(
    team_p1=packed_team_string,
    team_p2=packed_team_string,
    p1_handler=DecisionHandler,  # Your AI or RandomDecisionHandler
    p2_handler=DecisionHandler,
    p1_name="Alice",
    p2_name="Bob",
    max_turns=1000,
    collect_training_data=False,  # Set True to collect (request, action) pairs
)

print(f"Winner: {result.winner}")  # "p1", "p2", or None
print(f"Turns: {result.turns}")
```

### `DecisionHandler`

Abstract base class for decision-making strategies.

**Interface:**
```python
from abc import ABC, abstractmethod

class MyDecisionHandler(DecisionHandler):
    def choose_action(self, request, side, battle_state):
        """
        Args:
            request: Dict with "active", "side", "forceSwitch", etc.
            side: "p1" or "p2"
            battle_state: Current BattleState object
        
        Returns:
            Action string: "move 1", "switch 2", "move 1 -1, switch 2" (doubles)
        """
        # Your AI logic here
        return "move 1"
```

### Request JSON Schema

The `request` object sent each turn contains:

```python
{
    "active": [
        {
            "moves": [
                {"move": "Thunderbolt", "id": "thunderbolt", "pp": 24, "maxpp": 24, "disabled": False},
                {"move": "Quick Attack", "id": "quickattack", "pp": 30, "maxpp": 30, "disabled": False},
                ...
            ]
        },
        # In doubles, a second active entry
    ],
    "side": {
        "name": "Alice",
        "id": "p1",
        "pokemon": [
            {
                "ident": "p1:Pikachu",
                "details": "Pikachu, L50, M",
                "condition": "100/100",
                "active": True,
                "stats": {"atk": 106, "def": 131, "spa": 139, "spd": 230, "spe": 189},
                "moves": ["thunderbolt", "quickattack", "irontail", "thunderwave"],
                "baseAbility": "static",
                "item": "leftovers",
                "ability": "static"
            },
            # 5 more team members
        ]
    },
    "rqid": 3,
    "forceSwitch": [False],  # True if this Pokémon must switch
    "teamPreview": False,     # True at battle start
    "wait": False             # True if waiting for other player
}
```

**Key Fields:**
- `active[i].moves[]` - Available moves for slot `i` (1-4 in Pokémon names)
- `side.pokemon[j]` - Full team info (HP%, status, moves, stats)
- `forceSwitch[i]` - True if slot `i` must switch
- `teamPreview` - True only at battle start
- `wait` - True if waiting (battle is ending)

### Action Format

**Singles:**
- `move 1`, `move 2`, `move 3`, `move 4` - Use move slot
- `move 1 mega` - Mega Evolve and use move
- `switch 2` - Switch to team slot 2
- `default` - Let AI choose randomly
- `pass` - Do nothing (forced switch with no available switches)

**Doubles (comma-separated):**
- `move 1 -1, move 2 +1` - Left Pokémon uses move 1 on ally slot -1, right uses move 2 on opponent slot +1
- `move 1, switch 2` - Left attacks, right switches
- Targeting:
  - `-1`, `-2`: Ally slots
  - `+1`, `+2`: Opponent slots
  - omit target: default targeting for that move type

---

## Integrating Your Trained Model

### 1. Create a Handler for Your Model

```python
from showdown_ai.battle_runner import DecisionHandler
import torch

class ModelDecisionHandler(DecisionHandler):
    def __init__(self, model_path):
        self.model = torch.load(model_path)
        self.model.eval()

    def choose_action(self, request, side, battle_state):
        # Convert request JSON to your model's input format
        state_tensor = self._request_to_tensor(request)
        
        with torch.no_grad():
            logits = self.model(state_tensor)
        
        # Get available action indices from request
        available_indices = self._get_available_actions(request)
        
        # Pick best available action
        action_idx = self._pick_best_action(logits, available_indices)
        
        # Convert action index back to Showdown format
        return self._action_to_string(action_idx, request)
    
    def _request_to_tensor(self, request):
        # Your feature engineering here
        pass
    
    def _get_available_actions(self, request):
        # Map request to valid action indices
        pass
    
    def _pick_best_action(self, logits, available_indices):
        # Choose best action from available set
        pass
    
    def _action_to_string(self, action_idx, request):
        # Convert back to "move 1", "switch 2", etc.
        pass
```

### 2. Run Battles with Your Model

```python
model_handler = ModelDecisionHandler("/path/to/model.pth")
random_handler = RandomDecisionHandler()

result, _ = runner.run_battle(
    team_p1=team1,
    team_p2=team2,
    p1_handler=model_handler,
    p2_handler=random_handler,
    p1_name="YourAI",
    p2_name="RandomOpponent",
)

print(f"Your AI won!" if result.winner == "p1" else "Loss or tie")
```

---

## Collecting Training Data

Set `collect_training_data=True` to collect (request, action) pairs from battles:

```python
result, training_data = runner.run_battle(
    team_p1=team1,
    team_p2=team2,
    p1_handler=model_handler,
    p2_handler=random_handler,
    collect_training_data=True,
)

# training_data is a list of (side, request_json, action_string) tuples
for side, request, action in training_data:
    print(f"{side}: {action}")
    # Save to file for offline training
```

---

## Debugging

### Enable Logging

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("showdown_ai.battle_runner")
logger.setLevel(logging.DEBUG)
```

### Inspect Request Objects

Add debugging in your `choose_action` method:

```python
def choose_action(self, request, side, battle_state):
    print(f"Turn: {battle_state.turn}")
    print(f"Request: {json.dumps(request, indent=2)}")
    
    # Your logic...
```

---

## Common Issues

### "Pokemon Showdown dist/sim not found"

You need to build Pokemon Showdown locally. See **Quick Start** above.

### "Battle exceeded max_turns"

Battles sometimes loop indefinitely. Increase `max_turns` or investigate your decision handlers.

### Subprocess hangs or crashes

- Check that your action strings are valid (e.g., `move 1` not `move thunderbolt`)
- Verify move slots are in range 1-4
- Ensure switch slots are in range 1-6 and are available

---

## Next Steps

1. **Collect new training data** from headless battles
   - Run AI vs random, AI vs AI, etc.
   - Save (state, action, outcome) tuples

2. **Train a real policy model** on the collected data
   - Use imitation learning on request JSON features
   - Or use reinforcement learning (reward = win/loss)

3. **Evaluate** your model across different teams and formats

4. **Scale up** to tournament simulations or team ranking
