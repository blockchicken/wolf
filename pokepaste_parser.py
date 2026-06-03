#!/usr/bin/env python3
"""
Parse Pokepaste format and convert to packed team format for Showdown.

Pokepaste format (Champions):
    Starmie @ Starminite
    Ability: Natural Cure
    Level: 50
    EVs: 2 HP / 32 Atk / 32 Spe
    Adamant Nature
    - Agility
    - Aqua Jet
    - Bulk Up
    - Flip Turn

Packed format:
    Name, L50||Item|Ability|Move1,Move2,Move3,Move4|Nature|EVs|Gender|IVs|Happiness|Ball|Species|...
"""

import re
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class Pokemon:
    """A single Pokémon with stats and moves."""
    name: str
    level: int = 50
    item: str = ""
    ability: str = ""
    nature: str = "Serious"
    evs: str = "0,0,0,0,0,0"  # HP,Atk,Def,SpA,SpD,Spe (Stat Points in Champions: 0-32)
    gender: str = ""
    moves: List[str] = None
    shiny: bool = False
    
    def __post_init__(self):
        if self.moves is None:
            self.moves = []
    
    def to_packed(self) -> str:
        """Convert to Showdown packed format.

        Format: NICKNAME|SPECIES|ITEM|ABILITY|MOVES|NATURE|EVS|GENDER|IVS|SHINY|LEVEL|HAPPINESS,...

        For Champions:
        - NICKNAME holds the species name; SPECIES is left blank (Showdown convention)
        - EVs represent Stat Points (0-32 per stat in Champions)
        - IVs are always 31 in Champions and cannot be modified, so leave the field blank
        - Level is specified (50 for Champions flat rules)
        """
        # Showdown convention: put species name as NICKNAME, leave SPECIES blank
        nickname = self.name
        species = ""

        item_id = item_to_id(self.item)
        ability_id = ability_to_id(self.ability)
        moves_ids = ",".join(move_to_id(m) for m in self.moves[:4])

        gender_char = self.gender if self.gender in ["M", "F"] else ""
        shiny_char = "S" if self.shiny else ""
        level_str = str(self.level) if self.level != 100 else ""

        # IVs are always 31 in Champions — leave blank (Showdown treats blank IVs as 31)
        ivs = ""

        packed = f"{nickname}|{species}|{item_id}|{ability_id}|{moves_ids}|{self.nature}|{self.evs}|{gender_char}|{ivs}|{shiny_char}|{level_str}|"
        return packed


def move_to_id(move: str) -> str:
    """Convert move name to move ID (lowercase, no spaces/hyphens)."""
    return move.lower().replace(" ", "").replace("-", "")


def ability_to_id(ability: str) -> str:
    """Convert ability name to ability ID (lowercase, no spaces/hyphens)."""
    return ability.lower().replace(" ", "").replace("-", "")


def item_to_id(item: str) -> str:
    """Convert item name to item ID (lowercase, no spaces/hyphens)."""
    return item.lower().replace(" ", "").replace("-", "")


def parse_pokepaste(text: str) -> List[Pokemon]:
    """Parse Pokepaste format text into list of Pokemon."""
    teams = []
    
    # Split by double newlines or single Pokemon entries
    lines = text.strip().split("\n")
    
    current_pokemon = None
    
    for line in lines:
        line = line.strip()
        
        if not line:
            if current_pokemon:
                teams.append(current_pokemon)
                current_pokemon = None
            continue
        
        # Pokémon name (with or without item)
        if " @ " in line:
            parts = line.split(" @ ")
            current_pokemon = Pokemon(name=parts[0].strip(), item=parts[1].strip() if len(parts) > 1 else "")
        elif not any(k in line for k in ["Ability:", "Level:", "EVs:", "Nature:", "IVs:", "-"]) and current_pokemon is None:
            # Assume it's a Pokémon name if no special keywords
            if line and not line.startswith("-"):
                current_pokemon = Pokemon(name=line.strip())
        
        # Ability
        if line.startswith("Ability:"):
            if current_pokemon:
                current_pokemon.ability = line.replace("Ability:", "").strip()
        
        # Level
        if line.startswith("Level:"):
            if current_pokemon:
                try:
                    current_pokemon.level = int(line.replace("Level:", "").strip())
                except ValueError:
                    current_pokemon.level = 50
        
        # Nature (ends with " Nature")
        if line.endswith(" Nature"):
            if current_pokemon:
                current_pokemon.nature = line.replace(" Nature", "").strip()
        
        # EVs
        if line.startswith("EVs:"):
            if current_pokemon:
                ev_str = line.replace("EVs:", "").strip()
                current_pokemon.evs = parse_evs(ev_str)
        
        # Gender
        if line.startswith("Gender:"):
            if current_pokemon:
                current_pokemon.gender = line.replace("Gender:", "").strip()[0].upper()
        
        # Shiny
        if "Shiny: Yes" in line or "Shiny" in line:
            if current_pokemon:
                current_pokemon.shiny = True
        
        # Moves (start with -)
        if line.startswith("-"):
            if current_pokemon:
                move = line.lstrip("- ").strip()
                if move:
                    current_pokemon.moves.append(move)
    
    # Add last Pokémon
    if current_pokemon:
        teams.append(current_pokemon)
    
    return teams


CHAMPIONS_STAT_MAX = 32  # Maximum stat points per stat in Pokémon Champions


def parse_evs(ev_str: str) -> str:
    """Parse EV/Stat-Point string like '2 HP / 32 Atk / 32 Spe' to '2,0,0,0,0,32'.

    In Pokémon Champions, each stat accepts 0-32 Stat Points (equivalent to 0-252 EVs
    in traditional games). Values above 32 are clamped with a warning.
    """
    evs = [0, 0, 0, 0, 0, 0]

    stat_map = {
        "hp": 0,
        "atk": 1,
        "def": 2,
        "spa": 3,
        "spd": 4,
        "spe": 5,
    }

    parts = ev_str.split("/")
    for part in parts:
        part = part.strip()
        match = re.match(r"(\d+)\s+(\w+)", part)
        if match:
            value = int(match.group(1))
            stat = match.group(2).lower()
            if stat in stat_map:
                if value > CHAMPIONS_STAT_MAX:
                    import warnings
                    warnings.warn(
                        f"Stat point value {value} for {stat.upper()} exceeds Champions max "
                        f"of {CHAMPIONS_STAT_MAX}. Clamping to {CHAMPIONS_STAT_MAX}."
                    )
                    value = CHAMPIONS_STAT_MAX
                evs[stat_map[stat]] = value

    return ",".join(str(e) for e in evs)


def parse_ivs(iv_str: str) -> str:
    """Parse IV string like '31 HP / 0 Atk' to '31,0,31,31,31,31'."""
    # IV order: HP, Atk, Def, SpA, SpD, Spe (all start at 31)
    ivs = [31, 31, 31, 31, 31, 31]
    
    stat_map = {
        "hp": 0,
        "atk": 1,
        "def": 2,
        "spa": 3,
        "spd": 4,
        "spe": 5,
    }
    
    parts = iv_str.split("/")
    for part in parts:
        part = part.strip()
        match = re.match(r"(\d+)\s+(\w+)", part)
        if match:
            value = int(match.group(1))
            stat = match.group(2).lower()
            if stat in stat_map:
                ivs[stat_map[stat]] = value
    
    return ",".join(str(i) for i in ivs)


# Test with the Champions Pokepaste
EXAMPLE_POKEPASTE = """Starmie @ Starminite
Ability: Natural Cure
Level: 50
EVs: 2 HP / 32 Atk / 32 Spe
Adamant Nature
- Agility
- Aqua Jet
- Bulk Up
- Flip Turn

Incineroar @ Assault Vest
Ability: Intimidate
Level: 50
EVs: 252 HP / 4 Atk / 252 SpD
Careful Nature
- Fake Out
- Darkest Lariat
- Stone Edge
- Close Combat

Charizard @ Charizardite Y
Ability: Blaze
Level: 50
EVs: 4 SpA / 252 SpA / 252 Spe
Timid Nature
- Flamethrower
- Air Cutter
- Dragon Claw
- Roost"""


def main():
    print("Pokepaste Parser - Champions Format")
    print("=" * 70)
    
    pokemon_list = parse_pokepaste(EXAMPLE_POKEPASTE)
    
    for i, poke in enumerate(pokemon_list, 1):
        print(f"\nPokémon {i}: {poke.name}")
        print(f"  Level: {poke.level}")
        print(f"  Item: {poke.item}")
        print(f"  Ability: {poke.ability}")
        print(f"  Nature: {poke.nature}")
        print(f"  EVs: {poke.evs}")
        print(f"  Moves: {', '.join(poke.moves)}")
        print(f"\nPacked format:")
        print(f"  {poke.to_packed()}")
    
    # Create full team string
    print("\n" + "=" * 70)
    print("Full team (packed format for Showdown):")
    print("=" * 70)
    
    team_str = "\n".join(poke.to_packed() for poke in pokemon_list)
    print(team_str)


if __name__ == "__main__":
    main()
