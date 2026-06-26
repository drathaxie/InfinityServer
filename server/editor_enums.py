"""Human-readable labels for the enum-style numeric fields the editors expose, lifted verbatim
from the client decomp so a dropdown can show "Sword (6)" instead of a bare 6:
  iType (ItemType), EquipSpots, Elements, Entity.Race, Item.RarityValues, and gender ('M'/'F').
Served to every editor page at /editor/enums.json — one source of truth, shared by items/monsters/
quests/apops. JSON object keys are strings; the editor coerces numeric ones back to ints on save.
"""

ITEM_TYPE = {0: "None", 1: "Item", 2: "Note", 3: "Quest Item", 4: "Resource", 5: "Enhancement",
             6: "Sword", 7: "Dagger", 8: "Axe", 9: "Mace", 10: "Staff", 11: "Wand", 12: "Helm",
             13: "Ring", 14: "Necklace", 15: "Cape", 16: "None", 17: "Belt", 18: "Pet", 19: "Gun",
             20: "Polearm", 21: "Class", 22: "Armor", 23: "House", 24: "Wall Item", 25: "Floor Item",
             26: "Server Use", 27: "Client Use", 28: "Guild", 29: "Building", 30: "Bow", 31: "Whip",
             32: "Gauntlet", 33: "Hand Gun", 34: "Rifle", 35: "Misc", 42: "Elixir",
             43: "Pattern (gem)", 44: "Spellstone"}

EQUIP_SPOT = {0: "(blank)", 1: "None", 2: "Weapon", 3: "Head", 4: "Back", 5: "Pet", 6: "Class",
              7: "Armor", 8: "House", 9: "House Item", 10: "Amulet", 11: "Guild Item"}

ELEMENT = {0: "None", 1: "Fire", 2: "Metal", 3: "Earth", 4: "Water", 5: "Ice", 6: "Darkness",
           7: "Light", 8: "Energy", 9: "Wind"}

RACE = {0: "None", 1: "Human", 2: "Orc", 3: "Dragonkin", 4: "Undead", 5: "Chaos", 6: "Elemental",
        7: "Drakath"}

RARITY = {10: "Unknown", 11: "Common", 12: "Weird", 13: "Awesome", 14: "1% Drop", 15: "5% Drop",
          16: "Boss Drop", 17: "Secret", 18: "Junk", 19: "Impossible", 20: "Artifact", 21: "Broken",
          22: "Dumb", 23: "Crazy", 24: "Expensive", 30: "Rare", 35: "Epic", 40: "Import Item",
          50: "Seasonal Item", 55: "Seasonal Rare", 60: "Event Item", 65: "Event Rare",
          70: "Limited Rare", 75: "Collector's Rare", 80: "Promotional", 90: "Ultra Rare",
          95: "Super Mega Ultra Rare", 100: "Legendary"}

GENDER = {"M": "Male", "F": "Female"}

# shop_items.coins: which currency a shop listing costs.
CURRENCY = {0: "Gold", 1: "Coins (AC)"}

# Quest reward kinds (Quest.rewards is Dictionary<QuestRewardType, List<QuestRewardItem>>).
QUEST_REWARD_TYPE = {
    "Static": "Static — always given",
    "Roll": "Roll — each item rolls its own Rate",
    "Choose": "Choose — player picks one",
    "Random": "Random — one given at random (by Rate)",
}

# Enum name (used by the editor field defs) -> {value: label}. Add to this and reference it from
# any editor's field list to get a dropdown.
ENUMS = {
    "ItemType": ITEM_TYPE,
    "EquipSpot": EQUIP_SPOT,
    "Element": ELEMENT,
    "Rarity": RARITY,
    "Race": RACE,
    "Gender": GENDER,
    "QuestRewardType": QUEST_REWARD_TYPE,
    "Currency": CURRENCY,
}
