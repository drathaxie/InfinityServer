import json, re, collections
data = json.load(open(r"C:\Users\jesse\OneDrive\Desktop\Projects\InfinityServer-cutscenes\data\cutscenes.json", encoding="utf-8"))
# names + frame counts + completeActions
for cid in sorted(data, key=int):
    cs = json.loads(data[cid])
    print(f"{cid}: '{cs['cutsceneName']}' ID={cs['ID']!r} frames={len(cs['frames'])} idCount={cs['idCount']} boxCount={cs['boxCount']} trackCount={cs['trackCount']} sfxCount={cs['sfxCount']} completeActions={cs['completeActions']}")
# load types
lt = collections.Counter()
for cid, raw in data.items():
    for fr in json.loads(raw)["frames"]:
        for c in fr:
            m = re.match(r"Load\{(.*)\}$", c)
            if m: lt[m.group(1).split("|")[2]] += 1
print("\nLoad types:", dict(lt))
# escaping check: any &lt; in raw?
esc = [cid for cid, raw in data.items() if "&lt;" in raw]
print("cutscenes containing &lt; escaping:", esc)
lit = [cid for cid, raw in data.items() if "<" in json.loads(raw) if False]
# check raw JSON string for literal '<'
lit = [cid for cid, raw in data.items() if "<size" in raw]
print("cutscenes containing literal <size:", lit)
