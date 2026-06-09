import json
data = json.loads(open("data/pikalytics/gen9championsvgc2026regma-1500-2026-05.json", encoding="utf-8").read())
pokes = data["pokemon"]

for name in ["Maushold", "Basculegion", "Incineroar", "Kingambit"]:
    p = pokes.get(name, {})
    mv = p.get("moves", [])
    it = p.get("items", [])
    ab = p.get("abilities", [])
    print(f"{name}: moves={len(mv)}, items={len(it)}, abilities={len(ab)}, spread={len(p.get('spreads', []))}")
    if mv:
        print(f"  top move: {mv[0]}")

no_moves = [n for n, p in pokes.items() if not p.get("moves")]
print(f"\nNo moves: {len(no_moves)} / {len(pokes)} total")
print(f"First 5 no-move mons: {no_moves[:5]}")
print(f"\nSample Maushold raw keys: {list(pokes.get('Maushold', {}).keys())}")
