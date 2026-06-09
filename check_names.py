import json
data = json.loads(open("data/pikalytics/gen9championsvgc2026regma-1500-2026-05.json", encoding="utf-8").read())
pokes = data["pokemon"]

# Confirm teammates available in list for non-#1
for name in ["Incineroar", "Kingambit", "Garchomp"]:
    p = pokes.get(name, {})
    tm = p.get("teammates", {})
    print(f"{name}: {len(tm)} teammates, top3={list(tm.items())[:3]}")
