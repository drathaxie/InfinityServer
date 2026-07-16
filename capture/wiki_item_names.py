#!/usr/bin/env python3
r"""
Match placeholder item names in data/items.json against public AQW Wiki pages.

This is intentionally a review-first tool. AQW Wiki does not expose the same neat
item API that Infinity's live socket does, so the workflow is:

  1. Crawl/cache wiki HTML slowly and resumably.
  2. Parse likely item pages into a local JSONL index.
  3. Score local items against wiki titles/descriptions using the cleaned bundle
     filename, prefab name, bundle name, and current item name.
  4. Write a JSONL review report.
  5. Only edit data/items.json when --apply is explicitly passed.

Examples:
    # Build or refresh a small wiki index from default AQW Wiki item-category seeds.
    python capture/wiki_item_names.py --crawl --crawl-only --max-pages 5000 --delay 1.0

    # Match only from pages already cached/indexed.
    python capture/wiki_item_names.py --match-only --limit 200

    # Review high-confidence changes for a few ids.
    python capture/wiki_item_names.py --match-only --item-ids 900034,959144,101009

    # Apply only very confident name fixes; descriptions are only filled if blank.
    python capture/wiki_item_names.py --match-only --apply --apply-score 0.92

Output:
    capture/harvest/aqw_wiki_pages/*.html
    capture/harvest/aqw_wiki_items.jsonl
    capture/harvest/aqw_wiki_item_matches.jsonl
"""
import argparse
import html
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from html.parser import HTMLParser


ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_ITEMS = ROOT / "data" / "items.json"
HARVEST = pathlib.Path(__file__).resolve().parent / "harvest"
PAGE_CACHE = HARVEST / "aqw_wiki_pages"
WIKI_INDEX = HARVEST / "aqw_wiki_items.jsonl"
MATCH_REPORT = HARVEST / "aqw_wiki_item_matches.jsonl"

WIKI_BASE = "https://aqwwiki.wikidot.com"
DEFAULT_SEEDS = [
    "/items",
    "/weapons",
    "/armors",
    "/classes",
    "/helmets-hoods",
    "/capes-back-items",
    "/pets",
    "/battle-pets",
    "/houses",
    "/floor-items",
    "/wall-items",
    "/misc-items",
    "/use-items",
    "/swords",
    "/axes",
    "/daggers",
    "/maces",
    "/polearms",
    "/staves",
    "/guns",
    "/bows",
]

ITEM_LABELS = (
    "location:",
    "price:",
    "sellback:",
    "rarity:",
    "description:",
    "notes:",
    "level:",
    "enhancement:",
    "damage:",
)

STOP_WORDS = {
    "a", "an", "and", "are", "armor", "armors", "cape", "capes", "class", "dagger",
    "daggers", "flooritem", "flooritems", "for", "go", "hair", "helm", "helms",
    "house", "item", "items", "locks", "mace", "maces", "morph", "of", "pet",
    "robe", "skin", "slot", "slots", "sword", "swords", "the", "to", "unity3d",
    "weapon", "weapons", "with",
}

SUFFIX_RE = re.compile(
    r"(?i)(?:_?(?:weapon(?:slots|go)?|capeSlots|houseItemGO|skin|armorSlots|"
    r"hair|helm|locks|morph|pet|npc|go|slots))+$"
)


class PageTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.links = []
        self._in_title = False
        self._in_h1 = False
        self._skip = 0
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag in ("br", "p", "li", "tr", "div", "h2", "h3"):
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if not data or self._skip:
            return
        if self._in_title:
            self.title += data
        if self._in_h1:
            self.h1 += data
        self._text.append(data)

    @property
    def text(self):
        return normalize_space(" ".join(self._text))


def normalize_space(value):
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def wiki_url(path_or_url):
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return urllib.parse.urljoin(WIKI_BASE + "/", path_or_url.lstrip("/"))


def cache_name(url):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/") or "index"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", path)
    return PAGE_CACHE / f"{slug}.html"


def fetch(url, delay):
    PAGE_CACHE.mkdir(parents=True, exist_ok=True)
    dest = cache_name(url)
    if dest.exists() and dest.stat().st_size > 0:
        return dest.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": "InfinityServer wiki item matcher/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read().decode("utf-8", "replace")
    dest.write_text(data, encoding="utf-8")
    if delay:
        time.sleep(delay)
    return data


def parse_page(url, raw_html):
    parser = PageTextParser()
    parser.feed(raw_html)
    title = extract_page_title(raw_html) or normalize_space(parser.h1) or normalize_space(parser.title)
    title = re.sub(r"\s*-\s*AQW Wiki.*$", "", title, flags=re.I).strip()
    title = re.sub(r"\s+\(\d+\)$", "", title).strip()
    text = parser.text
    desc = extract_description(text)
    content_links = extract_content_links(raw_html)
    return {
        "url": url,
        "slug": urllib.parse.urlparse(url).path.strip("/"),
        "title": title,
        "description": desc,
        "text": text[:20000],
        "links": unique_links(content_links + parser.links),
    }


def extract_page_title(raw_html):
    match = re.search(
        r'<div\s+id=["\']page-title["\'][^>]*>(.*?)</div>',
        raw_html,
        flags=re.I | re.S,
    )
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return normalize_space(text)

def extract_content_links(raw_html):
    match = re.search(
        r'<div\s+id=["\']page-content["\'][^>]*>(.*?)(?:<div\s+id=["\']page-info-break["\']|'
        r'<div\s+class=["\']page-tags["\']|</body>)',
        raw_html,
        flags=re.I | re.S,
    )
    if not match:
        return []
    return re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\']', match.group(1), flags=re.I)


def unique_links(links):
    seen = set()
    out = []
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def extract_description(text):
    match = re.search(
        r"(?i)\bdescription:\s*(.*?)(?=\s+(?:notes?|note|location|price|sellback|rarity|"
        r"base damage|also see|thanks to)\b|\s+_[a-z0-9]|\s+Help\s+\|\s+Terms|$)",
        text,
    )
    if not match:
        return ""
    desc = normalize_space(match.group(1))
    desc = re.sub(r"\s+Male\s+Female$", "", desc).strip()
    if desc.lower() in {"*no description*", "no description"}:
        return ""
    if desc.lower().startswith("*no description*") or desc.lower() in {"note:", "note"}:
        return ""
    return desc[:2000]


def likely_item_page(page):
    if not page["title"] or page["title"].lower() in {"items", "weapons", "armors"}:
        return False
    text = page["text"].lower()
    in_items_breadcrumb = "» items »" in text or "items »" in text
    commerce_labels = sum(1 for label in ("price:", "sellback:", "rarity:") if label in text)
    return bool(page["description"]) and (in_items_breadcrumb or commerce_labels >= 2)


def clean_token_source(value):
    value = (value or "").replace("\\", "/")
    value = value.rsplit("/", 1)[-1]
    value = re.sub(r"\.unity3d$", "", value, flags=re.I)
    value = re.sub(r"^\d+_", "", value)
    value = SUFFIX_RE.sub("", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([A-Za-z])(\d)", r"\1 \2", value)
    value = re.sub(r"[^A-Za-z0-9'&+.-]+", " ", value)
    return normalize_space(value)


def norm_key(value):
    value = clean_token_source(value).lower()
    value = value.replace("&", " and ")
    value = value.replace("dragonslayer", "dragon slayer")
    value = value.replace("shadowslayer", "shadow slayer")
    value = value.replace("doomknight", "doom knight")
    value = value.replace("dragonhelm", "dragon helm")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    tokens = [t for t in value.split() if t and t not in STOP_WORDS]
    return " ".join(tokens)


def token_set(value):
    return set(norm_key(value).split())


def item_sources(item):
    bundle = item.get("Bundle") if isinstance(item.get("Bundle"), dict) else {}
    values = [
        item.get("Filename"),
        item.get("PrefabName"),
        item.get("Name"),
        bundle.get("Name"),
        bundle.get("Filename"),
    ]
    cleaned = []
    for value in values:
        cleaned_value = clean_token_source(str(value or ""))
        if cleaned_value and cleaned_value not in cleaned:
            cleaned.append(cleaned_value)
    return cleaned



def item_kind(item):
    filename = (item.get("Filename") or "").lower()
    equip = int(item.get("EquipSpot") or 0)
    item_type = int(item.get("ItemType") or 0)
    if equip == 3 or item_type == 12 or "/helms/" in filename:
        return "helm"
    if equip == 7 or item_type in (21, 22) or filename.startswith("armors/"):
        return "armor"
    if equip == 4 or item_type == 15 or "/capes/" in filename:
        return "cape"
    if equip == 5 or item_type == 18 or filename.startswith("npcs/"):
        return "pet"
    if equip == 9 or item_type in (24, 25) or "/flooritems/" in filename or "/wallitems/" in filename:
        return "house"
    if equip == 2:
        return "weapon"
    return ""


def page_kind(page):
    text = (page.get("text") or "").lower()
    title_slug = ((page.get("title") or "") + " " + (page.get("slug") or "")).lower()
    if "» items » helmets & hoods »" in text or re.search(r"\b(helm|hood|morph|visage|mask)\b", title_slug):
        return "helm"
    if "» items » armors »" in text or "» items » classes »" in text:
        return "armor"
    if "» items » capes & back items »" in text:
        return "cape"
    if "» items » pets »" in text or "» items » battle pets »" in text:
        return "pet"
    if "» items » houses »" in text or "» items » floor items »" in text or "» items » wall items »" in text:
        return "house"
    if "» items » weapons »" in text:
        return "weapon"
    return ""


def slot_adjustment(item, page):
    expected = item_kind(item)
    actual = page_kind(page)
    if not expected or not actual:
        return 0.0
    if expected == actual:
        return 0.08
    return -0.32


def slugify(value):
    value = clean_token_source(value).lower()
    value = value.replace("&", " and ")
    value = value.replace("dragonslayer", "dragon slayer")
    value = value.replace("shadowslayer", "shadow slayer")
    value = value.replace("doomknight", "doom knight")
    value = value.replace("dragonhelm", "dragon helm")
    value = re.sub(r"\b20\d\d\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    drop = {"unity3d", "slots", "slot", "weapon", "weapongo", "weaponslots", "go", "skin"}
    tokens = [t for t in value.split() if t and t not in drop]
    return "-".join(tokens)

def candidate_slugs(item):
    out = []
    for source in item_sources(item):
        base = slugify(source)
        if not base:
            continue
        variants = [base]
        if base.endswith("-infinity"):
            variants.append(base[:-len("-infinity")])
        else:
            variants.append(base + "-infinity")
        variants.append(base.replace("-helm-", "-"))
        variants.append(base.replace("-armor-", "-"))
        variants.append(base.replace("dragon-slayer", "dragonslayer"))
        variants.append(base.replace("shadow-slayer", "shadowslayer"))
        for variant in variants:
            variant = variant.strip("-")
            if variant and variant not in out:
                out.append(variant)
    return out[:8]


def direct_probe_pages(item, delay):
    pages = []
    seen = set()
    for slug in candidate_slugs(item):
        url = wiki_url(slug)
        try:
            page = parse_page(url, fetch(url, delay=delay))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            continue
        direct_item = "» items »" in (page.get("text") or "").lower()
        if (likely_item_page(page) or direct_item) and page["url"] not in seen:
            pages.append(page)
            seen.add(page["url"])
    return pages
def score_item_to_page(item, page):
    title_key = norm_key(page["title"])
    slug_key = norm_key(page["slug"].replace("-", " "))
    page_keys = [title_key, slug_key]
    page_tokens = token_set(page["title"] + " " + page["slug"].replace("-", " "))
    text_lc = page["text"].lower()

    best = 0.0
    reason = ""
    for source in item_sources(item):
        source_key = norm_key(source)
        if not source_key:
            continue
        source_tokens = set(source_key.split())
        for page_key in page_keys:
            if not page_key:
                continue
            seq = SequenceMatcher(None, source_key, page_key).ratio()
            overlap = len(source_tokens & page_tokens) / max(len(source_tokens | page_tokens), 1)
            contains = 0.0
            if source_key == page_key:
                contains = 1.0
            elif len(source_tokens) > 1 and len(page_tokens) > 1 and (source_key in page_key or page_key in source_key):
                contains = 0.85
            score = max(seq * 0.78 + overlap * 0.22, contains * 0.9)
            raw_source = source.lower()
            if raw_source and raw_source in text_lc:
                score += 0.08
            if len(source_tokens) == 1 and len(page_tokens) > 3:
                score -= 0.18
            score += slot_adjustment(item, page)
            score = max(0.0, min(score, 1.0))
            if score > best:
                best = score
                reason = f"{source!r} ~ {page['title']!r}"
    return best, reason


def crawl(seeds, max_pages, delay):
    queue = [wiki_url(seed) for seed in seeds]
    seen = set()
    pages = []
    while queue and len(seen) < max_pages:
        url = queue.pop(0).split("#", 1)[0]
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc and parsed.netloc != urllib.parse.urlparse(WIKI_BASE).netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        try:
            raw = fetch(url, delay=delay)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[crawl] skip {url}: {exc}", file=sys.stderr)
            continue
        page = parse_page(url, raw)
        if likely_item_page(page):
            pages.append(page)
        if len(seen) % 50 == 0:
            print(f"[crawl] visited {len(seen)} pages, item-like {len(pages)}")
        discovered = []
        for href in page["links"]:
            next_url = urllib.parse.urljoin(url, href).split("#", 1)[0]
            next_parsed = urllib.parse.urlparse(next_url)
            if next_parsed.netloc != parsed.netloc:
                continue
            path = next_parsed.path
            if not path or path.startswith(("/forum", "/system:", "/nav:", "/admin:")):
                continue
            if any(part in path for part in ("/tag/", "/search:", "/category:", "/css:", "/files")):
                continue
            if next_url not in seen and next_url not in queue and next_url not in discovered:
                discovered.append(next_url)
        queue = discovered + queue
    return pages


def write_index(pages):
    HARVEST.mkdir(parents=True, exist_ok=True)
    with WIKI_INDEX.open("w", encoding="utf-8") as out:
        for page in sorted(pages, key=lambda p: p["url"]):
            page = {k: v for k, v in page.items() if k != "links"}
            out.write(json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[index] wrote {len(pages)} wiki item-like pages -> {WIKI_INDEX}")


def read_index():
    if not WIKI_INDEX.exists():
        return []
    pages = []
    with WIKI_INDEX.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                pages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pages


def load_items(path, bundle_catalog=False):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not bundle_catalog:
        return data
    items = {}
    for row in data:
        if not isinstance(row, dict) or row.get("ID") is None:
            continue
        filename = row.get("FileName") or row.get("Filename") or ""
        iid = int(row["ID"])
        items[str(iid)] = {
            "ID": iid,
            "Name": clean_token_source(row.get("Name") or filename),
            "Description": "",
            "Filename": filename,
            "Bundle": {"ID": iid, "Name": row.get("Name"), "Filename": filename},
        }
    return items


def candidate_items(items, item_ids, limit, only_placeholders, skip=0):
    selected = []
    wanted = {str(i) for i in item_ids}
    seen_candidates = 0
    for key, item in sorted(items.items(), key=lambda kv: int(kv[0])):
        if wanted and key not in wanted:
            continue
        if only_placeholders:
            desc = (item.get("Description") or "").strip()
            iid = int(item.get("ID") or key)
            if desc and iid < 900000:
                continue
        seen_candidates += 1
        if seen_candidates <= skip:
            continue
        selected.append((key, item))
        if limit and len(selected) >= limit:
            break
    return selected


def best_match(item, pages, direct_probe=False, probe_delay=0.0):
    best_page = None
    best_score = 0.0
    best_reason = ""
    candidate_pages = list(pages)
    if direct_probe:
        candidate_pages = direct_probe_pages(item, probe_delay) + candidate_pages
    for page in candidate_pages:
        score, reason = score_item_to_page(item, page)
        if score > best_score:
            best_page, best_score, best_reason = page, score, reason
    return best_page, best_score, best_reason


def match_items(items, pages, args):
    rows = []
    selected = candidate_items(items, args.item_ids, args.limit, args.only_placeholders, args.skip)
    base_pages = [] if args.direct_probe_only else pages
    HARVEST.mkdir(parents=True, exist_ok=True)
    report_path = pathlib.Path(args.match_report)
    with report_path.open("w", encoding="utf-8") as out:
        for idx, (key, item) in enumerate(selected, 1):
            page, score, reason = best_match(item, base_pages, args.direct_probe, args.probe_delay)
            if idx % args.progress_every == 0:
                print(f"[match] checked {idx}/{len(selected)} items, rows {len(rows)}")
            if not page:
                continue
            if score < args.min_score and not args.include_low:
                continue
            row = {
                "item_id": int(item.get("ID") or key),
                "score": round(score, 4),
                "current_name": item.get("Name") or "",
                "current_description": item.get("Description") or "",
                "wiki_name": page["title"],
                "wiki_description": page.get("description") or "",
                "wiki_url": page["url"],
                "sources": item_sources(item),
                "reason": reason,
            }
            if args.skip_noop:
                same_name = (row["current_name"] or "").strip().lower() == (row["wiki_name"] or "").strip().lower()
                has_new_desc = bool(row["wiki_description"] and not (row["current_description"] or "").strip())
                if same_name and not has_new_desc:
                    continue
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()
    print(f"[match] wrote {len(rows)} rows -> {report_path}")
    return rows


def apply_matches(items, rows, apply_score, replace_description):
    changed = 0
    for row in rows:
        if row["score"] < apply_score:
            continue
        item = items.get(str(row["item_id"]))
        if not item:
            continue
        before = (item.get("Name") or "", item.get("Description") or "")
        item["Name"] = row["wiki_name"]
        if row["wiki_description"] and (replace_description or not (item.get("Description") or "").strip()):
            item["Description"] = row["wiki_description"]
        after = (item.get("Name") or "", item.get("Description") or "")
        if after != before:
            changed += 1
    if changed:
        backup = DATA_ITEMS.with_suffix(".json.bak")
        if not backup.exists():
            backup.write_text(DATA_ITEMS.read_text(encoding="utf-8"), encoding="utf-8")
        DATA_ITEMS.write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[apply] changed {changed} items in {DATA_ITEMS}")


def self_test():
    item = {
        "ID": 76294,
        "Name": "DmnkHHDFemaleMorph01",
        "Description": "",
        "Filename": "items/helms/59144_DmnkHHDFemaleMorph01.unity3d",
        "PrefabName": "ArmorSlots",
        "Bundle": {"Name": "DmnkHHDFemaleMorph01"},
    }
    page = {
        "url": "https://aqwwiki.wikidot.com/blushing-bandaid-visage",
        "slug": "blushing-bandaid-visage",
        "title": "Blushing Bandaid Visage",
        "description": "You'll risk it all for your love.",
        "text": "Description: You'll risk it all for your love. Location: Foo Price: 150 AC",
    }
    parsed = parse_page(page["url"], "<html><title>Blushing Bandaid Visage - AQW Wiki</title>"
                        "<body>Description: You'll risk it all for your love. Location: Foo "
                        "Price: 150 AC</body></html>")
    assert parsed["title"] == "Blushing Bandaid Visage"
    assert parsed["description"].startswith("You'll risk")
    score, _reason = score_item_to_page(item, page)
    assert 0.0 <= score <= 1.0
    assert "Dmnk HHD Female Morph 01" in item_sources(item)
    print("[self-test] ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crawl", action="store_true", help="fetch wiki pages and rebuild the local index")
    ap.add_argument("--crawl-only", action="store_true", help="crawl/rebuild the wiki index without matching local items")
    ap.add_argument("--match-only", action="store_true", help="skip crawling and use the existing index")
    ap.add_argument("--direct-probe", action="store_true", help="try direct AQW Wiki slug probes for each item before scoring the cached index")
    ap.add_argument("--direct-probe-only", action="store_true", help="score only direct slug probes instead of the cached wiki index")
    ap.add_argument("--probe-delay", type=float, default=0.2, help="seconds between direct-probe wiki fetches")
    ap.add_argument("--progress-every", type=int, default=100, help="print matcher progress every N selected items")
    ap.add_argument("--skip-noop", action="store_true", default=True, help="omit matches that would not change name or fill a blank description")
    ap.add_argument("--include-noop", action="store_false", dest="skip_noop", help="include exact no-op matches in the report")
    ap.add_argument("--seed", action="append", dest="seeds", help="wiki URL/path to seed crawl; repeatable")
    ap.add_argument("--max-pages", type=int, default=2000)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--min-score", type=float, default=0.78)
    ap.add_argument("--apply-score", type=float, default=0.92)
    ap.add_argument("--include-low", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--replace-description", action="store_true")
    ap.add_argument("--only-placeholders", action="store_true", default=True,
                    help="skip already-described real item ids below 900000 (default)")
    ap.add_argument("--all-items", action="store_false", dest="only_placeholders",
                    help="consider every item, including real ids with descriptions")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip", type=int, default=0, help="skip N selected items before matching")
    ap.add_argument("--match-report", default=str(MATCH_REPORT), help="path for JSONL match report")
    ap.add_argument("--item-ids", default="", help="comma-separated item ids")
    ap.add_argument("--items-path", default=str(DATA_ITEMS), help="item JSON path; defaults to data/items.json")
    ap.add_argument("--bundle-catalog", action="store_true", help="treat --items-path as a harvested bundle list instead of item dict")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    args.item_ids = [i.strip() for i in args.item_ids.split(",") if i.strip()]
    if args.self_test:
        self_test()
        return

    seeds = args.seeds or DEFAULT_SEEDS
    if args.crawl:
        pages = crawl(seeds, max_pages=args.max_pages, delay=args.delay)
        write_index(pages)
    else:
        pages = read_index()
    if args.crawl_only:
        print("[match] skipped because --crawl-only was passed")
        return
    if not pages:
        sys.exit(f"no wiki index found; run with --crawl first or create {WIKI_INDEX}")

    items = load_items(pathlib.Path(args.items_path), args.bundle_catalog)
    rows = match_items(items, pages, args)
    if args.apply:
        apply_matches(items, rows, args.apply_score, args.replace_description)
    else:
        print("[apply] dry run; pass --apply to edit data/items.json")


if __name__ == "__main__":
    main()













