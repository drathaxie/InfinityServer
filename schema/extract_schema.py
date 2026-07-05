#!/usr/bin/env python3
"""
Build a cmd -> {fields} schema map for every Request/Response type the AQWI
client defines. Source of truth is the decompiled Assembly-CSharp project
(docs/decomp). We parse each Request*/Response*.cs class for its serialized
members and the [JsonProperty]/Cmd wire name.

Output: schema/schema.json  { "responses": {cmd: {...}}, "requests": {cmd: {...}} }
"""
import re, json, pathlib

DECOMP = pathlib.Path(__file__).resolve().parent.parent / "docs" / "decomp"
OUT = pathlib.Path(__file__).resolve().parent / "schema.json"

# public/serialized field or auto-property lines, capturing optional [JsonProperty("wire")]
JSONPROP = re.compile(r'\[JsonProperty\("([^"]+)"\)\]')
FIELD = re.compile(
    r'^\s*public\s+(?!class|enum|struct|override|static\s+\w+\s+\w+\s*\()'
    r'([A-Za-z0-9_<>,\[\]\.\?]+)\s+([A-Za-z_]\w*)\s*(?:;|\{\s*get;)')
CMD_LITERAL = re.compile(r'Cmd\s*=\s*"([^"]+)"')

def parse_class(path: pathlib.Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    cmd = None
    m = CMD_LITERAL.search(text)
    if m:
        cmd = m.group(1)
    fields = {}
    pending_wire = None
    for line in text.splitlines():
        pm = JSONPROP.search(line)
        if pm:
            pending_wire = pm.group(1)
            continue
        fm = FIELD.match(line)
        if fm:
            ftype, fname = fm.group(1), fm.group(2)
            if fname in ("Cmd",):
                pending_wire = None
                continue
            wire = pending_wire or fname
            fields[wire] = ftype
            pending_wire = None
        else:
            if line.strip() and not line.strip().startswith("//"):
                pending_wire = None
    return cmd, fields

def main():
    responses, requests = {}, {}
    unresolved = []
    for cs in sorted(DECOMP.glob("*.cs")):
        name = cs.stem
        if not (name.startswith("Response") or name.startswith("Request")):
            continue
        if name in ("Response", "Request", "ResponseTypes"):
            continue
        cmd, fields = parse_class(cs)
        key = cmd if cmd else name  # fall back to type name when no literal Cmd
        entry = {"type": name, "cmd": cmd, "fields": fields}
        if name.startswith("Response"):
            responses[key] = entry
        else:
            requests[key] = entry
        if cmd is None:
            unresolved.append(name)

    out = {"responses": responses, "requests": requests}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"responses={len(responses)} requests={len(requests)}")
    print(f"types with no literal Cmd= (keyed by typename): {len(unresolved)}")
    print("examples:", ", ".join(sorted(responses)[:12]))

if __name__ == "__main__":
    main()
