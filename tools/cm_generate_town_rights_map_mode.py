#!/usr/bin/env python3
"""Generate the Best Urban Right map mode from vanilla data.

Scores each royal specialization town right per province by how much of its
boosted buildings' input goods the province supplies as raw materials, then
emits the map mode that colors provinces by the best right and the tooltip
machinery that ranks every option. Alongside it, one hidden search map mode
per right colors by that right's fit alone (with already-granted and
best-right-here stripes), opened from the search panel and the urban right
tooltips; the scripted GUI checks those tooltip buttons use are emitted too.

Reads vanilla town_rights (which goods each right boosts, right colors),
building_types (production method slots: inline unique_production_methods plus
possible_production_methods references resolved against
common/production_methods), goods (raw_material vs produced category),
advances (unlock_production_method / unlock_building plus each advance's
potential/allow gates and requires chains), and named_colors. Emits:

  in_game/common/script_values/cm_town_right_map_mode_script_values.txt
  in_game/common/scripted_triggers/cm_town_right_map_mode_triggers.txt
  in_game/common/scripted_effects/cm_town_right_map_mode_effects.txt
  in_game/common/customizable_localization/cm_town_right_map_mode_custom_loc.txt
  in_game/common/scripted_guis/cm_town_right_map_mode_scripted_guis.txt
  in_game/gfx/map/map_modes/cm_town_right_map_mode.txt
  main_menu/localization/english/cm_town_right_map_mode_l_english.yml

The scoring math runs once per lobby: cm_trmm_recompute_all sweeps every
province definition, computes each qualifying definition's industry coverages
definition-wide, stores them on every province slice in the definition (so
ownership splits never divide a province's coverage between owners; variables
on the province_definition itself do not read back), and stores each
location's best right index as a location variable.
A boosted good that is itself a raw material (dyes, wine) gets the RGO averaged
in as one more fully covered producer on the RGO's own location, since the
right's output modifier boosts that RGO too - so scores differ per location
there. The map mode and tooltip only read stored variables plus that one
raw-material check.

Scoring model:
  - A building's main production slot is its slot with the highest output;
    other slots are enhancement slots and are ignored.
  - Worth-using production methods are those in the main slot with output at
    least PM_OUTPUT_THRESHOLD of the slot's best output.
  - A production method's coverage in a province is the sum of its input-amount
    shares whose good is a raw material somewhere in the province; goods that
    can never be raw materials still count in the denominator.
  - A boosted good's coverage is the best coverage among worth-using production
    methods producing it (deduped across building tiers with identical shares).
  - A right's score is the average of its boosted goods' coverages.
  - A boosted good that is itself a raw material gets the RGO averaged in as
    one more fully covered producer on the RGO's own location (the output
    modifier boosts that RGO too), so scores differ per location there.
  - Availability is honored: a production method or building whose own gates
    (potential/allow, location_potential/country_potential) restrict by
    culture, tag, religion, reform, variable, government, or capital
    geography is excluded before any scoring, as is anything unlocked only by
    advances restricted the same way (transitively through requires chains).
    Content unlocked by universal advances (age and requires progression
    only) stays. Exclusions touching a boosted good are printed on each run.

Usage:
    python tools/cm_generate_town_rights_map_mode.py [--game-dir PATH]

The game directory comes from `game_directory` in tools/config.toml, or is
auto-detected from the common Steam install paths when that is blank.
"""

import argparse
import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.toml")

STEAM_GAME_PATHS = [
    os.path.join("C:" + os.sep, "Steam", "steamapps", "common",
                 "Europa Universalis V", "game"),
    os.path.join("C:" + os.sep, "Program Files (x86)", "Steam", "steamapps",
                 "common", "Europa Universalis V", "game"),
    os.path.join("C:" + os.sep, "Program Files", "Steam", "steamapps",
                 "common", "Europa Universalis V", "game"),
]

TOWN_RIGHTS_SUBDIR = os.path.join("in_game", "common", "town_rights")
BUILDING_TYPES_SUBDIR = os.path.join("in_game", "common", "building_types")
PRODUCTION_METHODS_SUBDIR = os.path.join(
    "in_game", "common", "production_methods")
GOODS_SUBDIR = os.path.join("in_game", "common", "goods")
ADVANCES_SUBDIR = os.path.join("in_game", "common", "advances")
NAMED_COLORS_SUBDIRS = [
    os.path.join("main_menu", "common", "named_colors"),
    os.path.join("in_game", "common", "named_colors"),
]

OUT_SCRIPT_VALUES = os.path.join(
    ROOT_DIR, "in_game", "common", "script_values",
    "cm_town_right_map_mode_script_values.txt")
OUT_TRIGGERS = os.path.join(
    ROOT_DIR, "in_game", "common", "scripted_triggers",
    "cm_town_right_map_mode_triggers.txt")
OUT_CUSTOM_LOC = os.path.join(
    ROOT_DIR, "in_game", "common", "customizable_localization",
    "cm_town_right_map_mode_custom_loc.txt")
OUT_MAP_MODE = os.path.join(
    ROOT_DIR, "in_game", "gfx", "map", "map_modes",
    "cm_town_right_map_mode.txt")
OUT_EFFECTS = os.path.join(
    ROOT_DIR, "in_game", "common", "scripted_effects",
    "cm_town_right_map_mode_effects.txt")
OUT_LOC = os.path.join(
    ROOT_DIR, "main_menu", "localization", "english",
    "cm_town_right_map_mode_l_english.yml")
OUT_SCRIPTED_GUIS = os.path.join(
    ROOT_DIR, "in_game", "common", "scripted_guis",
    "cm_town_right_map_mode_scripted_guis.txt")

# The generic royal specialization rights, in vanilla 01_discovery.txt order.
# The order is the tie-break priority for both map color and tooltip ranking.
ROYAL_RIGHTS = [
    "royal_tooling_rights",
    "royal_jewelry_rights",
    "royal_naval_rights",
    "royal_textile_rights",
    "royal_weaponry_rights",
    "royal_book_rights",
    "royal_artisan_rights",
    "royal_brewing_rights",
    "royal_masonry_rights",
]

# A main-slot production method is worth using when its output is at least
# this fraction of the slot's best output (keeps bronze 0.6 vs iron 1.0 tools,
# drops stone 0.25).
PM_OUTPUT_THRESHOLD = 0.5

SHARE_DECIMALS = 3

WATER_COLOR = "hsv { 0.58 0.50 0.52 }"
# Pure black, matching the 0-score floor of cm_location_food_potential
# (in_game/gfx/map/map_modes/cm_food_map_modes.txt:193).
NO_MATCH_COLOR = "rgb { 0 0 0 }"

# Search mode fill lerps from NO_MATCH_COLOR up to the right's own color, so
# the gradient's dark end is the same black as a genuine no-match location.
# The granted stripe reuses the placement finder palette
# (in_game/gfx/map/map_modes/cm_proximity_finder_map_modes.txt:45).
SEARCH_GRANTED_STRIPE = "rgb { 205 206 205 }"
# Best-right-here stripe, gold to stand apart from every right color.
SEARCH_BEST_STRIPE = "rgb { 255 200 60 }"

OUTPUT_MODIFIER = re.compile(r"^local_([a-z0-9_]+)_output_modifier$")
ASSIGN_BLOCK = re.compile(r"([A-Za-z_][A-Za-z0-9_.:]*)\s*=\s*\{")
ASSIGN_SCALAR = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.:]*)\s*=\s*([^\s{}]+)\s*$", re.MULTILINE)

# Trigger tokens marking a potential/allow (or building location_potential/
# country_potential) block as restricting content to specific countries,
# cultures, religions, or start positions. Matched against the block's raw
# text: gate conditions can use ?= forms and nest under custom_tooltip, which
# a structural walk would miss. Word boundaries keep culture from hitting
# has_culture_group, tag from hitting has_or_had_tag, continent from hitting
# sub_continent; is_capital_* covers scripted geography triggers like
# is_capital_mesoamerica.
RESTRICTED_TRIGGER = re.compile(
    r"\b(?:dominant_culture|culture_group|culture|has_or_had_tag|tag|"
    r"religion|has_reform|has_variable|country_type|government|"
    r"original_capital|sub_continent|continent|region|area|"
    r"is_capital_[a-z0-9_]+)\b")

GENERATED_HEADER = (
    "# Generated by tools/cm_generate_town_rights_map_mode.py - do not edit by hand.\n"
    "# Regenerate after a game update.\n"
)


def _load_config():
    if tomllib is None:
        return {}
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def resolve_game_dir(cli_game_dir):
    if cli_game_dir:
        if os.path.isdir(cli_game_dir):
            return cli_game_dir
        sys.exit(f"Game directory not found: {cli_game_dir}")

    configured = _load_config().get("game_directory", "")
    if configured and os.path.isdir(configured):
        return configured

    for path in STEAM_GAME_PATHS:
        if os.path.isdir(path):
            return path

    sys.exit("Could not locate EU5 game directory. "
             "Set 'game_directory' in tools/config.toml or pass --game-dir.")


def strip_comments(text):
    """Drop `#` comments while preserving line structure for brace matching."""
    return "\n".join(line.split("#", 1)[0] for line in text.split("\n"))


def find_matching_brace(text, open_idx):
    depth = 0
    in_str = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_str:
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced braces from offset {open_idx}")


def child_blocks(text):
    """Yield (name, inner_text, line) for each `name = { ... }` in `text`.

    `line` is 1-based relative to `text`, which matches the source file when
    `text` came from strip_comments (line structure preserved).
    """
    pos = 0
    while True:
        match = ASSIGN_BLOCK.search(text, pos)
        if not match:
            return
        open_idx = match.end() - 1
        close_idx = find_matching_brace(text, open_idx)
        line = text.count("\n", 0, match.start()) + 1
        yield match.group(1), text[open_idx + 1:close_idx], line
        pos = close_idx + 1


def remove_child_blocks(text):
    """Return `text` with every `name = { ... }` block blanked out."""
    out = []
    pos = 0
    while True:
        match = ASSIGN_BLOCK.search(text, pos)
        if not match:
            out.append(text[pos:])
            return "".join(out)
        close_idx = find_matching_brace(text, match.end() - 1)
        out.append(text[pos:match.start()])
        pos = close_idx + 1


def scalar_assignments(text):
    """Yield (key, value) for simple `key = value` lines outside child blocks."""
    for match in ASSIGN_SCALAR.finditer(remove_child_blocks(text)):
        yield match.group(1), match.group(2)


def restricted_tokens(text):
    """Sorted unique restricted-trigger tokens in a gate block's raw text."""
    return sorted(set(RESTRICTED_TRIGGER.findall(text)))


def read_pdx(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return strip_comments(f.read())


def iter_db_files(directory):
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".txt") or name in ("readme.txt", "__readme.txt"):
            continue
        yield name, os.path.join(directory, name)


def parse_goods(goods_dir):
    categories = {}
    for _, path in iter_db_files(goods_dir):
        for good, body, _ in child_blocks(read_pdx(path)):
            for key, value in scalar_assignments(body):
                if key == "category":
                    categories[good] = value
                    break
    return categories


def parse_named_color_kinds(game_dir):
    """Map color name -> rgb/hsv keyword, from `name = hsv { ... }` forms."""
    kinds = {}
    pattern = re.compile(
        r"^\s*([A-Za-z0-9_]+)\s*=\s*(rgb|hsv|hsv360)\s*\{([^}]*)\}",
        re.MULTILINE)
    for subdir in NAMED_COLORS_SUBDIRS:
        directory = os.path.join(game_dir, subdir)
        if not os.path.isdir(directory):
            continue
        for name, path in iter_db_files(directory):
            text = read_pdx(path)
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                rel = os.path.join(subdir, name).replace(os.sep, "/")
                components = " ".join(match.group(3).split())
                kinds.setdefault(
                    match.group(1),
                    (f"{match.group(2)} {{ {components} }}", rel, line))
    return kinds


COLOR_INLINE = re.compile(
    r"^\s*color\s*=\s*(rgb|hsv|hsv360)\s*\{([^}]*)\}", re.MULTILINE)


def parse_town_rights(town_rights_dir, goods_categories):
    """Return {right: {goods, color_token, color_inline, file, line}}."""
    rights = {}
    for name, path in iter_db_files(town_rights_dir):
        rel = os.path.join(TOWN_RIGHTS_SUBDIR, name).replace(os.sep, "/")
        text = read_pdx(path)
        for right, body, line in child_blocks(text):
            if right not in ROYAL_RIGHTS:
                continue
            boosted = []
            for child, inner, _ in child_blocks(body):
                if child != "location_modifier":
                    continue
                for key, _value in scalar_assignments(inner):
                    match = OUTPUT_MODIFIER.match(key)
                    if match and match.group(1) in goods_categories:
                        boosted.append(match.group(1))
            color_token = None
            for key, value in scalar_assignments(body):
                if key == "color":
                    color_token = value
            inline = COLOR_INLINE.search(body)
            rights[right] = {
                "goods": boosted,
                "color_token": color_token,
                "color_inline": (f"{inline.group(1)} {{ {inline.group(2).strip()} }}"
                                 if inline else None),
                "file": rel,
                "line": line,
            }
    return rights


def parse_pm_body(pm_body, goods_categories):
    """Return {inputs {good: amount}, produced, output, gates} from a PM block
    body; gates is the PM's own potential/allow blocks as (kind, inner_text)."""
    produced = None
    output = None
    inputs = {}
    gates = []
    for child, inner, _ in child_blocks(pm_body):
        if child in ("potential", "allow"):
            gates.append((child, inner))
    for key, value in scalar_assignments(pm_body):
        if key == "produced":
            produced = value
        elif key == "output":
            try:
                output = float(value)
            except ValueError:
                output = None
        elif key in goods_categories:
            try:
                amount = float(value)
            except ValueError:
                continue
            inputs[key] = inputs.get(key, 0.0) + amount
    return {"inputs": inputs, "produced": produced, "output": output,
            "gates": gates}


def parse_production_methods(game_dir, goods_categories):
    """Return {pm_name: {inputs, produced, output, gates, ref}} from the
    external production method files."""
    methods = {}
    directory = os.path.join(game_dir, PRODUCTION_METHODS_SUBDIR)
    for name, path in iter_db_files(directory):
        rel = os.path.join(PRODUCTION_METHODS_SUBDIR, name).replace(os.sep, "/")
        for pm, pm_body, line in child_blocks(read_pdx(path)):
            data = parse_pm_body(pm_body, goods_categories)
            data["ref"] = f"{rel}:{line}"
            methods[pm] = data
    return methods


def parse_buildings(building_types_dir, goods_categories, external_pms):
    """Return ([(building, [slot, ...], gates, ref)], dangling) where each
    slot is a list of PM dicts {name, ref, inputs, produced, output, gates},
    gates is the building's own restriction blocks as (kind, inner_text), and
    dangling is the set of possible_production_methods names with no external
    definition."""
    buildings = []
    dangling = set()
    for name, path in iter_db_files(building_types_dir):
        rel = os.path.join(BUILDING_TYPES_SUBDIR, name).replace(os.sep, "/")
        text = read_pdx(path)
        for building, body, b_line in child_blocks(text):
            slots = []
            gates = []
            for child, inner, c_line in child_blocks(body):
                if child == "unique_production_methods":
                    slot = []
                    for pm, pm_body, pm_line in child_blocks(inner):
                        data = parse_pm_body(pm_body, goods_categories)
                        data["name"] = pm
                        data["ref"] = f"{rel}:{b_line + c_line + pm_line - 2}"
                        slot.append(data)
                    if slot:
                        slots.append(slot)
                elif child == "possible_production_methods":
                    slot = []
                    for pm in inner.split():
                        if pm not in external_pms:
                            dangling.add(pm)
                            continue
                        data = dict(external_pms[pm])
                        data["name"] = pm
                        slot.append(data)
                    if slot:
                        slots.append(slot)
                elif child in ("potential", "allow", "location_potential",
                               "country_potential"):
                    gates.append((child, inner))
            if slots:
                buildings.append((building, slots, gates, f"{rel}:{b_line}"))
    return buildings, dangling


def parse_advances(game_dir):
    """Return {advance: {gate_tokens, requires, unlock_pms, unlock_buildings,
    ref}}. gate_tokens are the restriction tokens in the advance's
    potential/allow blocks plus any government/country_type scalar gate."""
    advances = {}
    directory = os.path.join(game_dir, ADVANCES_SUBDIR)
    for name, path in iter_db_files(directory):
        rel = os.path.join(ADVANCES_SUBDIR, name).replace(os.sep, "/")
        for adv, body, line in child_blocks(read_pdx(path)):
            tokens = set()
            for child, inner, _ in child_blocks(body):
                if child in ("potential", "allow"):
                    tokens.update(restricted_tokens(inner))
            requires = []
            unlock_pms = []
            unlock_buildings = []
            for key, value in scalar_assignments(body):
                if key == "requires":
                    requires.append(value)
                elif key == "unlock_production_method":
                    unlock_pms.append(value)
                elif key == "unlock_building":
                    unlock_buildings.append(value)
                elif key in ("government", "country_type"):
                    tokens.add(key)
            advances[adv] = {
                "gate_tokens": sorted(tokens),
                "requires": requires,
                "unlock_pms": unlock_pms,
                "unlock_buildings": unlock_buildings,
                "ref": f"{rel}:{line}",
            }
    return advances


def resolve_advance_restrictions(advances):
    """Return ({advance: reason or None}, warnings). An advance is restricted
    by its own gate tokens or, transitively, by any advance in its requires
    chain; reason is the report string. A requires target with no definition
    is warned about and treated as unrestricted, biasing against false
    exclusion."""
    reasons = {}
    warnings = []

    def resolve(name, stack):
        if name in reasons:
            return reasons[name]
        if name in stack:
            return None
        data = advances[name]
        if data["gate_tokens"]:
            reasons[name] = (f"{name} ({data['ref']}) gates on "
                             + ", ".join(data["gate_tokens"]))
            return reasons[name]
        stack.add(name)
        for req in data["requires"]:
            if req not in advances:
                warnings.append(
                    f"requires target {req} not found (from {name})")
                continue
            req_reason = resolve(req, stack)
            if req_reason:
                stack.discard(name)
                reasons[name] = f"{name} requires restricted {req_reason}"
                return reasons[name]
        stack.discard(name)
        reasons[name] = None
        return None

    for name in advances:
        resolve(name, set())
    return reasons, warnings


def compute_advance_locks(advances, restrictions):
    """Return (pm_lock, building_lock): name -> reason for every
    unlock_production_method / unlock_building target whose unlocking
    advances are all restricted. A target at least one unrestricted advance
    unlocks is universally obtainable and absent."""
    pm_lock = {}
    building_lock = {}
    for kind, lock in (("unlock_pms", pm_lock),
                       ("unlock_buildings", building_lock)):
        unlockers = {}
        for adv, data in advances.items():
            for target in data[kind]:
                unlockers.setdefault(target, []).append(adv)
        for target, advs in unlockers.items():
            if all(restrictions[a] for a in advs):
                lock[target] = ("locked behind restricted advance(s): "
                                + "; ".join(restrictions[a] for a in advs))
    return pm_lock, building_lock


def filter_buildings(buildings, pm_lock, building_lock, boosted_goods):
    """Drop advance-locked or restriction-gated buildings and PMs before any
    scoring math, so main-slot selection and the worth-using threshold only
    ever see universally available production methods. Returns the
    (building, slots) list collect_options consumes plus report lines for
    every exclusion that touches a boosted good."""
    kept = []
    report = []
    for building, slots, gates, ref in buildings:
        reason = building_lock.get(building)
        if not reason:
            for kind, inner in gates:
                tokens = restricted_tokens(inner)
                if tokens:
                    reason = f"{kind} gates on {', '.join(tokens)}"
                    break
        if reason:
            boosted = sorted({pm["produced"] for slot in slots for pm in slot
                              if pm["produced"] in boosted_goods})
            if boosted:
                report.append(f"building {building} ({ref}): {reason} "
                              f"[boosted: {', '.join(boosted)}]")
            continue
        new_slots = []
        for slot in slots:
            new_slot = []
            for pm in slot:
                pm_reason = pm_lock.get(pm["name"])
                if not pm_reason:
                    for kind, inner in pm["gates"]:
                        tokens = restricted_tokens(inner)
                        if tokens:
                            pm_reason = (f"own {kind} gates on "
                                         f"{', '.join(tokens)}")
                            break
                if pm_reason:
                    if pm["produced"] in boosted_goods:
                        report.append(f"pm {pm['name']} ({building}, "
                                      f"{pm['ref']}): {pm_reason}")
                    continue
                new_slot.append(pm)
            if new_slot:
                new_slots.append(new_slot)
        if new_slots:
            kept.append((building, new_slots))
    return kept, report


def short_alias(right):
    alias = right
    if alias.startswith("royal_"):
        alias = alias[len("royal_"):]
    if alias.endswith("_rights"):
        alias = alias[:-len("_rights")]
    return alias


def fmt_num(value):
    text = f"{value:.{SHARE_DECIMALS}f}".rstrip("0").rstrip(".")
    return text if text else "0"


def fmt_amount(value):
    """Format a vanilla input amount without rounding it away (<= 4 decimals)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def collect_options(buildings, boosted_goods, goods_categories):
    """Return {good: [option]}, option = {shares {good: share_str}, comment}."""
    options = {good: [] for good in boosted_goods}
    seen = {good: set() for good in boosted_goods}
    for building, slots in buildings:
        main_slot = max(
            slots,
            key=lambda slot: max((pm["output"] or 0.0) for pm in slot))
        best_output = max((pm["output"] or 0.0) for pm in main_slot)
        if best_output <= 0:
            continue
        for pm in main_slot:
            good = pm["produced"]
            if good not in options:
                continue
            if pm["output"] is None or not pm["inputs"]:
                continue
            if pm["output"] < PM_OUTPUT_THRESHOLD * best_output:
                continue
            total = sum(pm["inputs"].values())
            shares = {}
            for input_good, amount in sorted(pm["inputs"].items()):
                if goods_categories.get(input_good) != "raw_material":
                    continue
                shares[input_good] = fmt_num(amount / total)
            if not shares:
                continue
            key = tuple(sorted(shares.items()))
            if key in seen[good]:
                continue
            seen[good].add(key)
            mix = ", ".join(f"{g} {fmt_amount(a)}"
                            for g, a in sorted(pm["inputs"].items()))
            options[good].append({
                "shares": shares,
                "comment": (f"{pm['name']} ({building}), {pm['ref']} - "
                            f"{mix} of {fmt_amount(total)} total input"),
            })
    for good, opts in options.items():
        options[good] = [o for o in opts
                         if not any(dominates(a, o) for a in opts if a is not o)]
    return options


def dominates(a, b):
    """True when option a's coverage is >= option b's in every province."""
    return all(g in a["shares"] and float(a["shares"][g]) >= float(s)
               for g, s in b["shares"].items())


def province_check(good):
    # ?= : definitions include water and wasteland locations whose raw_material
    # link is invalid and error-logs on a plain compare.
    return f"any_location_in_province_definition = {{ raw_material ?= goods:{good} }}"


def _diff_var(x, y, aliases):
    """Name of the stored pairwise diff for the pair (x, y) and whether it
    already reads as score(x) - score(y): cm_trmm_diff_a__b is only stored
    for a appearing before b in ROYAL_RIGHTS, so the opposite order reads the
    negated value."""
    px, py = ROYAL_RIGHTS.index(x), ROYAL_RIGHTS.index(y)
    if px < py:
        return f"cm_trmm_diff_{aliases[x]}__{aliases[y]}", True
    return f"cm_trmm_diff_{aliases[y]}__{aliases[x]}", False


def tied_trigger(x, y, aliases):
    """Trigger fragment testing score(x) == score(y)."""
    name, _ = _diff_var(x, y, aliases)
    return f"{name} = 0"


def greater_trigger(x, y, aliases):
    """Trigger fragment testing score(x) > score(y)."""
    name, positive = _diff_var(x, y, aliases)
    return f"{name} {'> 0' if positive else '< 0'}"


def emit_script_values(rights, options, aliases, boosted_goods, self_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Province-definition-scoped option values consumed by\n"
        "# cm_trmm_recompute_province_definition (each is one worth-using production\n"
        "# method's locally-available input share), plus the location-scoped readers of\n"
        "# the stored province variables that the map mode tooltip machinery uses.\n"
        "# Readers are only evaluated on locations the recompute pass marked\n"
        "# (has_variable cm_trmm_best_idx).\n")

    for good in boosted_goods:
        for k, option in enumerate(options[good], start=1):
            lines.append(f"# {option['comment']}")
            lines.append(f"cm_trmm_opt_{good}_{k} = {{")
            lines.append("\tvalue = 0")
            for input_good, share in option["shares"].items():
                lines.append("\tif = {")
                lines.append(f"\t\tlimit = {{ {province_check(input_good)} }}")
                lines.append(f"\t\tadd = {share}")
                lines.append("\t}")
            lines.append("}")
        lines.append("")

    for good in boosted_goods:
        lines.append(f"cm_trmm_cov_{good} = {{")
        lines.append("\tvalue = 0")
        lines.append(f"\tprovince = {{ add = var:cm_trmm_cov_{good} }}")
        if good in self_goods:
            lines.append("\t# The right's output modifier also boosts this raw material's own RGO, so")
            lines.append("\t# on its location the RGO averages in as one more fully covered producer.")
            lines.append("\tif = {")
            lines.append(f"\t\tlimit = {{ raw_material ?= goods:{good} }}")
            lines.append("\t\tadd = 1")
            lines.append("\t\tdivide = 2")
            lines.append("\t}")
        lines.append("}")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        goods = rights[right]["goods"]
        lines.append(f"# {right}: {rights[right]['file']}:{rights[right]['line']}")
        lines.append(f"cm_trmm_right_{alias} = {{")
        lines.append(f"\tvalue = cm_trmm_cov_{goods[0]}")
        for good in goods[1:]:
            lines.append(f"\tadd = cm_trmm_cov_{good}")
        if len(goods) > 1:
            lines.append(f"\tdivide = {len(goods)}")
        lines.append("}")
    lines.append("")

    n = len(ROYAL_RIGHTS)
    for pos, right in enumerate(ROYAL_RIGHTS):
        lines.append(f"cm_trmm_enc_{aliases[right]} = {{")
        lines.append(f"\tvalue = cm_trmm_right_{aliases[right]}")
        lines.append("\tmultiply = 1000")
        lines.append("\tround = yes")
        lines.append("\tmultiply = 10")
        lines.append(f"\tadd = {n - pos}")
        lines.append("}")
    lines.append("")

    lines.append(
        "# Pairwise score differences and per-right ranks (0 = best) for the tooltip\n"
        "# ranking. On a tied score the earlier right in the priority order ranks higher.")
    for i, a in enumerate(ROYAL_RIGHTS):
        for b in ROYAL_RIGHTS[i + 1:]:
            lines.append(f"cm_trmm_diff_{aliases[a]}__{aliases[b]} = {{")
            lines.append(f"\tvalue = cm_trmm_right_{aliases[a]}")
            lines.append(f"\tsubtract = cm_trmm_right_{aliases[b]}")
            lines.append("}")
    for pos, right in enumerate(ROYAL_RIGHTS):
        alias = aliases[right]
        lines.append(f"cm_trmm_rank_{alias} = {{")
        lines.append("\tvalue = 0")
        for earlier in ROYAL_RIGHTS[:pos]:
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ cm_trmm_diff_{aliases[earlier]}__{alias} >= 0 }}")
            lines.append("\t\tadd = 1")
            lines.append("\t}")
        for later in ROYAL_RIGHTS[pos + 1:]:
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ cm_trmm_diff_{alias}__{aliases[later]} < 0 }}")
            lines.append("\t\tadd = 1")
            lines.append("\t}")
        lines.append("}")
    lines.append("")

    lines.append(
        "# Tie-aware dense tier for the search mode tooltips: is_leader marks the\n"
        "# earliest-priority right at each distinct score, tier counts distinct scores\n"
        "# strictly above (0 = best, so tierdisp is 1-based), tier_total is the number\n"
        "# of distinct scores at this location, and tie_count is how many other rights\n"
        "# share this exact score.")
    for pos, right in enumerate(ROYAL_RIGHTS):
        alias = aliases[right]
        earlier = ROYAL_RIGHTS[:pos]
        lines.append(f"cm_trmm_is_leader_{alias} = {{")
        lines.append("\tvalue = 1")
        if earlier:
            lines.append("\tif = {")
            lines.append("\t\tlimit = {")
            lines.append("\t\t\tOR = {")
            for e in earlier:
                lines.append(f"\t\t\t\t{tied_trigger(e, right, aliases)}")
            lines.append("\t\t\t}")
            lines.append("\t\t}")
            lines.append("\t\tvalue = 0")
            lines.append("\t}")
        lines.append("}")
    lines.append("")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(f"cm_trmm_tier_{alias} = {{")
        lines.append("\tvalue = 0")
        for other in ROYAL_RIGHTS:
            if other == right:
                continue
            lines.append("\tif = {")
            lines.append("\t\tlimit = {")
            lines.append(f"\t\t\tcm_trmm_is_leader_{aliases[other]} = 1")
            lines.append(f"\t\t\t{greater_trigger(other, right, aliases)}")
            lines.append("\t\t}")
            lines.append("\t\tadd = 1")
            lines.append("\t}")
        lines.append("}")
        lines.append(f"cm_trmm_tierdisp_{alias} = {{")
        lines.append(f"\tvalue = cm_trmm_tier_{alias}")
        lines.append("\tadd = 1")
        lines.append("}")
    lines.append("")
    lines.append("cm_trmm_tier_total = {")
    lines.append(f"\tvalue = cm_trmm_is_leader_{aliases[ROYAL_RIGHTS[0]]}")
    for right in ROYAL_RIGHTS[1:]:
        lines.append(f"\tadd = cm_trmm_is_leader_{aliases[right]}")
    lines.append("}")
    lines.append("")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(f"cm_trmm_tie_count_{alias} = {{")
        lines.append("\tvalue = 0")
        for other in ROYAL_RIGHTS:
            if other == right:
                continue
            lines.append("\tif = {")
            lines.append(f"\t\tlimit = {{ {tied_trigger(other, right, aliases)} }}")
            lines.append("\t\tadd = 1")
            lines.append("\t}")
        lines.append("}")
    lines.append("")

    lines.append(
        "# Same ranking machinery for the industries themselves, for the best-industries\n"
        "# tooltip list.")
    for i, a in enumerate(boosted_goods):
        for b in boosted_goods[i + 1:]:
            lines.append(f"cm_trmm_ind_diff_{a}__{b} = {{")
            lines.append(f"\tvalue = cm_trmm_cov_{a}")
            lines.append(f"\tsubtract = cm_trmm_cov_{b}")
            lines.append("}")
    for pos, good in enumerate(boosted_goods):
        lines.append(f"cm_trmm_ind_rank_{good} = {{")
        lines.append("\tvalue = 0")
        for earlier in boosted_goods[:pos]:
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ cm_trmm_ind_diff_{earlier}__{good} >= 0 }}")
            lines.append("\t\tadd = 1")
            lines.append("\t}")
        for later in boosted_goods[pos + 1:]:
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ cm_trmm_ind_diff_{good}__{later} < 0 }}")
            lines.append("\t\tadd = 1")
            lines.append("\t}")
        lines.append("}")

    lines.append("")
    lines.append(
        "# Count of royal specialization rights granted at this location. Read live\n"
        "# by the build-location urban-right marker to pick its display state.")
    lines.append("cm_uright_assigned_count = {")
    lines.append("\tvalue = 0")
    for right in ROYAL_RIGHTS:
        lines.append("\tif = {")
        lines.append(f"\t\tlimit = {{ has_town_rights = town_rights_type:{right} }}")
        lines.append("\t\tadd = 1")
        lines.append("\t}")
    lines.append("}")

    return "\n".join(lines) + "\n"


def emit_triggers(relevant_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Province definition trigger. True when the province definition produces any\n"
        "# raw material consumed by a worth-using production method of a building a\n"
        "# royal specialization town right boosts. Gates the recompute pass.")
    lines.append("cm_trmm_province_definition_has_any_input = {")
    lines.append("\tany_location_in_province_definition = {")
    lines.append("\t\tOR = {")
    for good in relevant_goods:
        lines.append(f"\t\t\traw_material ?= goods:{good}")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_effects(options, aliases, boosted_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Once-per-lobby precompute: computes each qualifying province definition's\n"
        "# industry coverages definition-wide, stores them on every province slice in\n"
        "# the definition, and stores each location's best right index as a location\n"
        "# variable, so the map mode and tooltip only read stored values. Variables\n"
        "# stored on the province_definition itself do not read back, so coverage is\n"
        "# staged in locals and written to every slice.\n")
    lines.append("# Province definition scope.")
    lines.append("cm_trmm_recompute_province_definition = {")
    lines.append("\tif = {")
    lines.append("\t\tlimit = { cm_trmm_province_definition_has_any_input = yes }")
    for good in boosted_goods:
        lines.append("\t\tset_local_variable = {")
        lines.append(f"\t\t\tname = cm_trmm_l_cov_{good}")
        lines.append("\t\t\tvalue = {")
        lines.append("\t\t\t\tvalue = 0")
        for k in range(1, len(options[good]) + 1):
            lines.append(f"\t\t\t\tmin = cm_trmm_opt_{good}_{k}")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
    lines.append("\t\tevery_province_in_province_definition = {")
    for good in boosted_goods:
        lines.append("\t\t\tset_variable = {")
        lines.append(f"\t\t\t\tname = cm_trmm_cov_{good}")
        lines.append(f"\t\t\t\tvalue = local_var:cm_trmm_l_cov_{good}")
        lines.append("\t\t\t}")
    lines.append(
        "\t\t\t# Best right per location, encoded as round(score * 1000) * 10 + index so\n"
        "\t\t\t# the chained min = (raise-to-at-least) running maximum keeps both the score\n"
        "\t\t\t# and which right holds it. Higher index wins score ties, so index order is\n"
        "\t\t\t# the reverse of the priority order.")
    lines.append("\t\t\tevery_location_in_province = {")
    lines.append("\t\t\t\tset_local_variable = {")
    lines.append("\t\t\t\t\tname = cm_trmm_enc")
    lines.append("\t\t\t\t\tvalue = {")
    lines.append("\t\t\t\t\t\tvalue = 0")
    for right in ROYAL_RIGHTS:
        lines.append(f"\t\t\t\t\t\tmin = cm_trmm_enc_{aliases[right]}")
    lines.append("\t\t\t\t\t}")
    lines.append("\t\t\t\t}")
    lines.append("\t\t\t\tset_variable = {")
    lines.append("\t\t\t\t\tname = cm_trmm_best_idx")
    lines.append("\t\t\t\t\tvalue = {")
    lines.append("\t\t\t\t\t\tvalue = local_var:cm_trmm_enc")
    lines.append("\t\t\t\t\t\tmodulo = 10")
    lines.append("\t\t\t\t\t}")
    lines.append("\t\t\t\t}")
    lines.append("\t\t\t}")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("\telse = {")
    lines.append("\t\tevery_province_in_province_definition = {")
    lines.append("\t\t\tevery_location_in_province = {")
    lines.append("\t\t\t\tlimit = { has_variable = cm_trmm_best_idx }")
    lines.append("\t\t\t\tremove_variable = cm_trmm_best_idx")
    lines.append("\t\t\t}")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("}")
    lines.append("")
    lines.append("cm_trmm_recompute_all = {")
    lines.append("\tevery_province_definition = {")
    lines.append("\t\tcm_trmm_recompute_province_definition = yes")
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_custom_loc(aliases, boosted_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Slot entries resolve rank k to a line, or to nothing: cm_trmm_slot_* for the\n"
        "# ranked rights list, cm_trmm_bd_slot_* for the same ranking with per-industry\n"
        "# sub-lines, cm_trmm_ind_slot_* for the industries ranked by coverage.\n"
        "# The cm_uright_rank_slot and cm_uright_rank_bd_slot copies are identical but\n"
        "# point at line keys that read the location from the customizable-loc target, so\n"
        "# the build-location marker can reuse the ranking tooltip where the window root\n"
        "# is not the row's location.")
    for prefix, line_prefix in (("cm_trmm_slot", "cm_trmm_tt_line"),
                                ("cm_trmm_bd_slot", "cm_trmm_bd_line"),
                                ("cm_uright_rank_slot", "cm_uright_rank_line"),
                                ("cm_uright_rank_bd_slot", "cm_uright_rank_bd_line")):
        for slot in range(1, len(ROYAL_RIGHTS) + 1):
            lines.append(f"{prefix}_{slot} = {{")
            lines.append("\ttype = location")
            for right in ROYAL_RIGHTS:
                alias = aliases[right]
                lines.append("\ttext = {")
                lines.append("\t\ttrigger = {")
                lines.append(f"\t\t\tcm_trmm_rank_{alias} = {slot - 1}")
                lines.append(f"\t\t\tcm_trmm_right_{alias} > 0")
                lines.append("\t\t}")
                lines.append(f"\t\tlocalization_key = {line_prefix}_{alias}")
                lines.append("\t}")
            lines.append("\ttext = {")
            lines.append("\t\tlocalization_key = cm_trmm_blank")
            lines.append("\t\tfallback = yes")
            lines.append("\t}")
            lines.append("}")
    for ind_prefix, ind_line_prefix in (
            ("cm_trmm_ind_slot", "cm_trmm_ind_line"),):
        for slot in range(1, len(boosted_goods) + 1):
            lines.append(f"{ind_prefix}_{slot} = {{")
            lines.append("\ttype = location")
            for good in boosted_goods:
                lines.append("\ttext = {")
                lines.append("\t\ttrigger = {")
                lines.append(f"\t\t\tcm_trmm_ind_rank_{good} = {slot - 1}")
                lines.append(f"\t\t\tcm_trmm_cov_{good} > 0")
                lines.append("\t\t}")
                lines.append(f"\t\tlocalization_key = {ind_line_prefix}_{good}")
                lines.append("\t}")
            lines.append("\ttext = {")
            lines.append("\t\tlocalization_key = cm_trmm_blank")
            lines.append("\t\tfallback = yes")
            lines.append("\t}")
            lines.append("}")

    lines.append(
        "# Comma-joined \"tied with\" list per right: cm_trmm_tie_prefix_* shows the\n"
        "# shared static label when at least one other right ties, and\n"
        "# cm_trmm_tie_item_<right>_<other> resolves to that other right's name (with a\n"
        "# leading comma unless it is the first tied item in priority order), or nothing.")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(f"cm_trmm_tie_prefix_{alias} = {{")
        lines.append("\ttype = location")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append(f"\t\t\tcm_trmm_tie_count_{alias} > 0")
        lines.append("\t\t}")
        lines.append("\t\tlocalization_key = cm_trmm_tied_with_prefix")
        lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\tlocalization_key = cm_trmm_blank")
        lines.append("\t\tfallback = yes")
        lines.append("\t}")
        lines.append("}")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        for other in ROYAL_RIGHTS:
            if other == right:
                continue
            other_alias = aliases[other]
            other_pos = ROYAL_RIGHTS.index(other)
            earlier_others = [o for o in ROYAL_RIGHTS[:other_pos] if o != right]
            lines.append(f"cm_trmm_tie_item_{alias}_{other_alias} = {{")
            lines.append("\ttype = location")
            lines.append("\ttext = {")
            lines.append("\t\ttrigger = {")
            lines.append(f"\t\t\t{tied_trigger(other, right, aliases)}")
            if earlier_others:
                lines.append("\t\t\tNOT = {")
                lines.append("\t\t\t\tOR = {")
                for e in earlier_others:
                    lines.append(f"\t\t\t\t\t{tied_trigger(e, right, aliases)}")
                lines.append("\t\t\t\t}")
                lines.append("\t\t\t}")
            lines.append("\t\t}")
            lines.append(f"\t\tlocalization_key = cm_trmm_tie_name_only_{other_alias}")
            lines.append("\t}")
            lines.append("\ttext = {")
            lines.append("\t\ttrigger = {")
            lines.append(f"\t\t\t{tied_trigger(other, right, aliases)}")
            lines.append("\t\t}")
            lines.append(f"\t\tlocalization_key = cm_trmm_tie_name_comma_{other_alias}")
            lines.append("\t}")
            lines.append("\ttext = {")
            lines.append("\t\tlocalization_key = cm_trmm_blank")
            lines.append("\t\tfallback = yes")
            lines.append("\t}")
            lines.append("}")

    lines.append(
        "# Build-location urban-right marker icons: cm_uright_rec_icon resolves the\n"
        "# recommended right (rank 0), cm_uright_assigned_icon the single granted royal\n"
        "# right, each to that right's texticon. cm_uright_assigned_item_* is the\n"
        "# per-right granted-or-blank line the multi-right hover list concatenates.")
    for name in ("cm_uright_rec_icon", "cm_uright_assigned_icon"):
        lines.append(f"{name} = {{")
        lines.append("\ttype = location")
        for right in ROYAL_RIGHTS:
            alias = aliases[right]
            lines.append("\ttext = {")
            lines.append("\t\ttrigger = {")
            if name == "cm_uright_rec_icon":
                lines.append(f"\t\t\tcm_trmm_rank_{alias} = 0")
                lines.append(f"\t\t\tcm_trmm_right_{alias} > 0")
            else:
                lines.append(f"\t\t\thas_town_rights = town_rights_type:{right}")
            lines.append("\t\t}")
            lines.append(f"\t\tlocalization_key = cm_uright_icon_line_{alias}")
            lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\tlocalization_key = cm_trmm_blank")
        lines.append("\t\tfallback = yes")
        lines.append("\t}")
        lines.append("}")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(f"cm_uright_assigned_item_{alias} = {{")
        lines.append("\ttype = location")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append(f"\t\t\thas_town_rights = town_rights_type:{right}")
        lines.append("\t\t}")
        lines.append(f"\t\tlocalization_key = cm_uright_assigned_line_{alias}")
        lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\tlocalization_key = cm_trmm_blank")
        lines.append("\t\tfallback = yes")
        lines.append("\t}")
        lines.append("}")

    return "\n".join(lines) + "\n"


# Trailing mode fields shared by the best-right and search modes.
MODE_TAIL_HEAD = """
	small_map_names = raw_material
	medium_map_names = raw_material
	large_map_names = raw_material

	small_tooltip_context = location
	medium_tooltip_context = location
	large_tooltip_context = location

	fill_in_impassable = yes
	enable_snow = no

	flatmap_behaviour = always
	use_fow = no
"""

MODE_TAIL_BLOCKS = """
	map_markers = {
		fort_marker = no
		port_marker = no
		unit_marker = no
		combat_marker = no
		combat_imminent_marker = no
		supply_depot_marker = no
		market_marker = no
		toll_marker = no
		dynasty_marker = no
		raw_goods_marker = no
	}

	gradient_parameters = {
		zoom_step = 2

		gradient_alpha_inside = 1
		gradient_alpha_outside = 1
		gradient_width = 0.0375
		gradient_color_mult = 0.9
		edge_width = 0
		edge_sharpness = 0.01
		edge_alpha = 0
		edge_color_mult = 0
		before_lighting_blend = 0.5
		after_lighting_blend = 0.5
	}

	refresh_colors_on_selection_change = no"""


def emit_map_mode(rights, aliases, right_colors):
    n = len(ROYAL_RIGHTS)
    lines = [GENERATED_HEADER]
    lines.append("cm_best_town_right = {")
    lines.append("\tmap_color = {")
    lines.append("\t\tif = {")
    lines.append("\t\t\tlimit = { is_land = no }")
    lines.append(f"\t\t\tvalue = {WATER_COLOR}")
    lines.append("\t\t}")
    lines.append("\t\telse_if = {")
    lines.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
    lines.append(f"\t\t\tvalue = {NO_MATCH_COLOR}")
    lines.append("\t\t}")
    for pos, right in enumerate(ROYAL_RIGHTS):
        idx = n - pos
        color, source = right_colors[right]
        lines.append("\t\telse_if = {")
        lines.append(f"\t\t\tlimit = {{ var:cm_trmm_best_idx = {idx} }}")
        lines.append(f"\t\t\t# {right} color: {source}")
        lines.append(f"\t\t\tvalue = {color}")
        lines.append("\t\t}")
    lines.append("\t}")
    lines.append("")
    for right in ROYAL_RIGHTS:
        color, _ = right_colors[right]
        lines.append("\tlegend_key = {")
        lines.append(f"\t\tdesc = \"cm_trmm_legend_{aliases[right]}\"")
        lines.append(f"\t\tcolor = {color}")
        lines.append("\t}")
    lines.append("\tlegend_key = {")
    lines.append("\t\tdesc = \"cm_trmm_legend_none\"")
    lines.append(f"\t\tcolor = {NO_MATCH_COLOR}")
    lines.append("\t}")
    lines.append("")
    lines.append("\ttooltip_key = {")
    lines.append("\t\tif = {")
    lines.append("\t\t\tlimit = { is_land = no }")
    lines.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_WATER")
    lines.append("\t\t}")
    lines.append("\t\telse_if = {")
    lines.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
    lines.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_NONE")
    lines.append("\t\t}")
    lines.append("\t\telse = {")
    lines.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_LAND")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append(MODE_TAIL_HEAD)
    lines.append("\tcategory = economy")
    lines.append("\tindex = 1")
    lines.append(MODE_TAIL_BLOCKS)
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_search_map_modes(rights, aliases, right_colors):
    n = len(ROYAL_RIGHTS)
    lines = [
        "# Per-right search variants of cm_best_town_right, hidden from the flyout",
        "# (opened from the search panel and the urban right tooltips). The fill lerps",
        "# from the low anchor to the right's own color by its fit score; stripes mark",
        "# already-granted and best-right-here locations. The Day refresh counter",
        "# (vanilla in_game/gfx/map/map_modes/map_modes.txt:1099) keeps the granted",
        "# stripe current after grants.",
    ]
    for pos, right in enumerate(ROYAL_RIGHTS):
        alias = aliases[right]
        upper = alias.upper()
        idx = n - pos
        color, source = right_colors[right]
        lines.append("")
        lines.append(f"cm_trmm_search_{alias} = {{")
        lines.append("\tmap_color = {")
        lines.append("\t\tif = {")
        lines.append("\t\t\tlimit = { is_land = no }")
        lines.append(f"\t\t\tvalue = {WATER_COLOR}")
        lines.append("\t\t}")
        lines.append("\t\telse_if = {")
        lines.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
        lines.append(f"\t\t\tvalue = {NO_MATCH_COLOR}")
        lines.append("\t\t}")
        # Kept after the has_variable branch so the score never reads unset
        # province variables.
        lines.append("\t\telse_if = {")
        lines.append(f"\t\t\tlimit = {{ cm_trmm_right_{alias} <= 0 }}")
        lines.append(f"\t\t\tvalue = {NO_MATCH_COLOR}")
        lines.append("\t\t}")
        lines.append("\t\telse = {")
        lines.append("\t\t\tlerp = {")
        lines.append(f"\t\t\t\tmin_color = {NO_MATCH_COLOR}")
        lines.append(f"\t\t\t\t# {right} color: {source}")
        lines.append(f"\t\t\t\tmax_color = {color}")
        lines.append(f"\t\t\t\tfactor = {{ value = cm_trmm_right_{alias} }}")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
        lines.append("\t}")
        lines.append("")
        lines.append("\tsecondary_map_color = {")
        lines.append("\t\tif = {")
        lines.append("\t\t\tlimit = {")
        lines.append("\t\t\t\tis_land = yes")
        lines.append(f"\t\t\t\thas_town_rights = town_rights_type:{right}")
        lines.append("\t\t\t}")
        lines.append(f"\t\t\tvalue = {SEARCH_GRANTED_STRIPE}")
        lines.append("\t\t}")
        lines.append("\t\telse_if = {")
        lines.append("\t\t\tlimit = {")
        lines.append("\t\t\t\tis_land = yes")
        lines.append("\t\t\t\thas_variable = cm_trmm_best_idx")
        lines.append(f"\t\t\t\tvar:cm_trmm_best_idx = {idx}")
        lines.append("\t\t\t}")
        lines.append(f"\t\t\tvalue = {SEARCH_BEST_STRIPE}")
        lines.append("\t\t}")
        lines.append("\t}")
        lines.append("")
        for desc, key_color in (
                ("cm_trmm_search_legend_100", color),
                ("cm_trmm_search_legend_none", NO_MATCH_COLOR),
                ("cm_trmm_search_legend_granted", SEARCH_GRANTED_STRIPE),
                ("cm_trmm_search_legend_best", SEARCH_BEST_STRIPE)):
            lines.append("\tlegend_key = {")
            lines.append(f"\t\tdesc = \"{desc}\"")
            lines.append(f"\t\tcolor = {key_color}")
            lines.append("\t}")
        lines.append("")
        lines.append("\ttooltip_key = {")
        lines.append("\t\tif = {")
        lines.append("\t\t\tlimit = { is_land = no }")
        lines.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_WATER")
        lines.append("\t\t}")
        lines.append("\t\telse_if = {")
        lines.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
        lines.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_NONE")
        lines.append("\t\t}")
        lines.append("\t\telse_if = {")
        lines.append(f"\t\t\tlimit = {{ cm_trmm_right_{alias} <= 0 }}")
        lines.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_NONE")
        lines.append("\t\t}")
        lines.append("\t\telse_if = {")
        lines.append(f"\t\t\tlimit = {{ has_town_rights = town_rights_type:{right} }}")
        lines.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_GRANTED")
        lines.append("\t\t}")
        lines.append("\t\telse = {")
        lines.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_LAND")
        lines.append("\t\t}")
        lines.append("\t}")
        lines.append(MODE_TAIL_HEAD)
        lines.append("\tcategory = hidden")
        lines.append("\tallow_allocate_hotkey = no")
        lines.append(MODE_TAIL_BLOCKS)
        lines.append("\tcolor_refresh_counters = { Day }")
        lines.append("}")
    return "\n".join(lines) + "\n"


def emit_loc(rights, aliases, boosted_goods):
    slot_calls = "".join(
        f"[ROOT.GetLocation.Custom('cm_trmm_slot_{slot}')]"
        for slot in range(1, len(ROYAL_RIGHTS) + 1))
    bd_calls = "".join(
        f"[ROOT.GetLocation.Custom('cm_trmm_bd_slot_{slot}')]"
        for slot in range(1, len(ROYAL_RIGHTS) + 1))
    ind_calls = "".join(
        f"[ROOT.GetLocation.Custom('cm_trmm_ind_slot_{slot}')]"
        for slot in range(1, len(boosted_goods) + 1))
    lines = ["l_english:"]
    for header_line in GENERATED_HEADER.splitlines():
        lines.append(f" {header_line}")
    lines.append(
        f" MAPMODE_CM_BEST_TOWN_RIGHT_TT_LAND: \"[ROOT.GetLocation.GetName], "
        f"[ROOT.GetLocation.GetProvince.GetName] specialization options:{slot_calls}"
        f"\\n\\nDetails:{bd_calls}"
        f"\\n\\nBest industries:{ind_calls}\"")
    search_cores = {}
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        right_core = (
            f"@{right}! [ShowTownRightsName('{right}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_right_{alias}')|%1]")
        lines.append(f" cm_trmm_tt_line_{alias}: \"\\n{right_core}\"")
        sub_lines = "".join(
            f"\\n  @{good}! [ShowGoodsName('{good}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]"
            for good in rights[right]["goods"])
        lines.append(f" cm_trmm_bd_line_{alias}: \"\\n{right_core}{sub_lines}\"")
        search_cores[alias] = right_core + sub_lines
    for good in boosted_goods:
        lines.append(
            f" cm_trmm_ind_line_{good}: \"\\n@{good}! [ShowGoodsName('{good}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]\"")
    lines.append(" cm_trmm_blank: \"\"")

    # Build-location marker strings. The ranking tooltip is reused here where the
    # window root is not the row's location, so these copies read the location from
    # the customizable-loc target (Location) instead of ROOT.GetLocation.
    u_slot_calls = "".join(
        f"[Location.Custom('cm_uright_rank_slot_{slot}')]"
        for slot in range(1, len(ROYAL_RIGHTS) + 1))
    u_bd_calls = "".join(
        f"[Location.Custom('cm_uright_rank_bd_slot_{slot}')]"
        for slot in range(1, len(ROYAL_RIGHTS) + 1))
    lines.append(
        f" CM_URIGHT_RANKING_TT: \"[Location.GetName], "
        f"[Location.GetProvince.GetName] specialization options:{u_slot_calls}"
        f"\\n\\nDetails:{u_bd_calls}\"")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        u_core = (
            f"@{right}! [ShowTownRightsName('{right}')]: "
            f"[Location.MakeScope.ScriptValue('cm_trmm_right_{alias}')|%1]")
        lines.append(f" cm_uright_rank_line_{alias}: \"\\n{u_core}\"")
        u_sub = "".join(
            f"\\n  @{good}! [ShowGoodsName('{good}')]: "
            f"[Location.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]"
            for good in rights[right]["goods"])
        lines.append(f" cm_uright_rank_bd_line_{alias}: \"\\n{u_core}{u_sub}\"")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(f" cm_uright_icon_line_{alias}: \"@{right}!\"")
        lines.append(
            f" cm_uright_assigned_line_{alias}: "
            f"\"\\n@{right}! [ShowTownRightsName('{right}')]\"")
    assigned_items = "".join(
        f"[Location.Custom('cm_uright_assigned_item_{aliases[right]}')]"
        for right in ROYAL_RIGHTS)
    lines.append(
        f" CM_URIGHT_ASSIGNED_LIST_TT: "
        f"\"#T Assigned Specialization Rights#!{assigned_items}\"")

    for right in ROYAL_RIGHTS:
        lines.append(
            f" cm_trmm_legend_{aliases[right]}: \"@{right}! [ShowTownRightsName('{right}')]\"")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(
            f" cm_trmm_tie_name_only_{alias}: "
            f"\"@{right}! [ShowTownRightsName('{right}')]\"")
        lines.append(
            f" cm_trmm_tie_name_comma_{alias}: "
            f"\", @{right}! [ShowTownRightsName('{right}')]\"")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        upper = alias.upper()
        boosted = ", ".join(
            f"@{good}! [ShowGoodsName('{good}')]"
            for good in rights[right]["goods"])
        lines.append(
            f" mapmode_cm_trmm_search_{alias}_name: "
            f"\"Urban Right Search: [ShowTownRightsName('{right}')]\"")
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}: "
            f"\"#T $mapmode_cm_trmm_search_{alias}_name$#!"
            f"\\nColors each [location|e] by the share of input [goods|e] that the "
            f"buildings boosted by @{right}! [ShowTownRightsName('{right}')] can "
            f"source from the [province|e]'s [rgo|e]s. Light stripes mark "
            f"where the right is already granted; gold stripes mark where it is the "
            f"best specialization option.\\nBoosted industries: {boosted}\"")
        tie_items = "".join(
            f"[ROOT.GetLocation.Custom('cm_trmm_tie_item_{alias}_{aliases[other]}')]"
            for other in ROYAL_RIGHTS if other != right)
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}_TT_LAND: \"{search_cores[alias]}"
            f"\\nRank among specializations here: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_tierdisp_{alias}')|0] "
            f"of [ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_tier_total')|0]"
            f"[ROOT.GetLocation.Custom('cm_trmm_tie_prefix_{alias}')]{tie_items}\"")
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}_TT_GRANTED: "
            f"\"$MAPMODE_CM_TRMM_SEARCH_{upper}_TT_LAND$"
            f"$cm_trmm_search_granted_line$\"")
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}_TT_NONE: "
            f"\"[ROOT.GetLocation.GetProvince.GetName] has no [raw_material|e] "
            f"used by the industries @{right}! [ShowTownRightsName('{right}')] "
            f"boosts.\"")
    return "\n".join(lines) + "\n"


def emit_scripted_guis(aliases):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Right-identity checks for the urban right tooltip search buttons. Root is\n"
        "# a town_rights_type scope pushed from the GUI (TownRightsType.MakeScope).")
    for right in ROYAL_RIGHTS:
        lines.append(f"cm_trmm_is_search_{aliases[right]} = {{")
        lines.append("\tis_shown = {")
        lines.append(f"\t\tthis = town_rights_type:{right}")
        lines.append("\t}")
        lines.append("}")
    lines.append("")
    lines.append("cm_trmm_is_search_any = {")
    lines.append("\tis_shown = {")
    lines.append("\t\tOR = {")
    for right in ROYAL_RIGHTS:
        lines.append(f"\t\t\tthis = town_rights_type:{right}")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_output(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + normalized.encode("utf-8"))


def resolve_right_colors(rights, game_dir):
    kinds = parse_named_color_kinds(game_dir)
    colors = {}
    for right, data in rights.items():
        if data["color_inline"]:
            colors[right] = (data["color_inline"],
                             f"{data['file']}:{data['line']}")
            continue
        token = data["color_token"]
        if token is None:
            sys.exit(f"{right} has no color in {data['file']}")
        if token not in kinds:
            sys.exit(f"Named color {token} for {right} not found in named_colors")
        value, rel, line = kinds[token]
        colors[right] = (value, f"named color {token}, {rel}:{line}")
    return colors


def main():
    parser = argparse.ArgumentParser(
        description="Generate the Best Urban Right map mode from vanilla "
                    "town rights, building production methods, and goods.")
    parser.add_argument(
        "--game-dir", type=str, default=None,
        help="Path to EU5 game directory (overrides config.toml).")
    args = parser.parse_args()

    game_dir = resolve_game_dir(args.game_dir)
    goods_categories = parse_goods(os.path.join(game_dir, GOODS_SUBDIR))
    if not goods_categories:
        sys.exit("No goods parsed")

    rights = parse_town_rights(
        os.path.join(game_dir, TOWN_RIGHTS_SUBDIR), goods_categories)
    missing = [r for r in ROYAL_RIGHTS if r not in rights]
    if missing:
        sys.exit(f"Town rights not found in vanilla: {', '.join(missing)}")
    for right in ROYAL_RIGHTS:
        if not rights[right]["goods"]:
            sys.exit(f"{right} boosts no goods (local_<good>_output_modifier)")

    boosted_goods = []
    for right in ROYAL_RIGHTS:
        for good in rights[right]["goods"]:
            if good not in boosted_goods:
                boosted_goods.append(good)

    external_pms = parse_production_methods(game_dir, goods_categories)
    buildings, dangling = parse_buildings(
        os.path.join(game_dir, BUILDING_TYPES_SUBDIR), goods_categories,
        external_pms)
    advances = parse_advances(game_dir)
    restrictions, requires_warnings = resolve_advance_restrictions(advances)
    pm_lock, building_lock = compute_advance_locks(advances, restrictions)
    buildings, excluded = filter_buildings(
        buildings, pm_lock, building_lock, boosted_goods)
    options = collect_options(buildings, boosted_goods, goods_categories)
    for good in boosted_goods:
        if not options[good]:
            sys.exit(f"No worth-using production methods found for {good}")

    relevant = sorted({g for opts in options.values()
                       for opt in opts for g in opt["shares"]})

    aliases = {right: short_alias(right) for right in ROYAL_RIGHTS}
    if len(set(aliases.values())) != len(aliases):
        sys.exit("Right alias collision")

    self_goods = {good for good in boosted_goods
                  if goods_categories.get(good) == "raw_material"}

    right_colors = resolve_right_colors(rights, game_dir)

    write_output(OUT_SCRIPT_VALUES,
                 emit_script_values(rights, options, aliases, boosted_goods,
                                    self_goods))
    write_output(OUT_TRIGGERS, emit_triggers(relevant))
    write_output(OUT_EFFECTS, emit_effects(options, aliases, boosted_goods))
    write_output(OUT_CUSTOM_LOC, emit_custom_loc(aliases, boosted_goods))
    write_output(OUT_MAP_MODE,
                 emit_map_mode(rights, aliases, right_colors) + "\n"
                 + emit_search_map_modes(rights, aliases, right_colors))
    write_output(OUT_LOC, emit_loc(rights, aliases, boosted_goods))
    write_output(OUT_SCRIPTED_GUIS, emit_scripted_guis(aliases))

    for path in (OUT_SCRIPT_VALUES, OUT_TRIGGERS, OUT_EFFECTS, OUT_CUSTOM_LOC,
                 OUT_MAP_MODE, OUT_LOC, OUT_SCRIPTED_GUIS):
        print(f"Wrote {os.path.relpath(path, ROOT_DIR).replace(os.sep, '/')}")
    for right in ROYAL_RIGHTS:
        goods_list = ", ".join(
            f"{g} ({len(options[g])} option{'s' if len(options[g]) != 1 else ''})"
            for g in rights[right]["goods"])
        print(f"  {right}: {goods_list}")
    print(f"  relevant raw materials: {', '.join(relevant)}")
    print(f"  RGO-self boosted goods: {', '.join(sorted(self_goods))}")
    if excluded:
        print(f"  gated content excluded ({len(excluded)}):")
        for entry in excluded:
            print(f"    {entry}")
    else:
        print("  gated content excluded: none")
    for warning in sorted(set(requires_warnings)):
        print(f"  WARNING: {warning}")
    if dangling:
        rel_pm = PRODUCTION_METHODS_SUBDIR.replace(os.sep, "/")
        print("  WARNING: possible_production_methods entries with no definition "
              f"in {rel_pm}, skipped: {', '.join(sorted(dangling))}")


if __name__ == "__main__":
    main()
