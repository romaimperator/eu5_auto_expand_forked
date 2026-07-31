#!/usr/bin/env python3
"""Generate the Best Urban Right map mode from vanilla data.

Scores each royal specialization town right per province by how much of its
boosted buildings' input goods the province supplies as raw materials, then
emits the map mode that colors provinces by the best right (with a granted
right striped on a red-to-green scale by how far its fit falls below the best
right here) and the tooltip machinery that ranks every option. Alongside it, one
hidden search map mode per right colors by that right's fit alone (with
already-granted, best-right-here, and other-right-granted stripes), opened
from the search panel and the urban right tooltips; the scripted GUI checks
those tooltip buttons use are emitted too. A reason line at the bottom of
each mode's tooltip names the granted right(s) behind the active stripe.
Every mode additionally gets a hidden _refresh twin the search panel swaps
through and back for an immediate stripe recolor after a tooltip grant, and
the grant-section gui gives the map tooltip a per-right grant button row
(spliced into the location_tooltip_alt redefinition) firing the per-right
grant scripted GUIs emitted into the scripted_guis output.

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
  in_game/gui/custom_cooltip.gui
  in_game/gui/cm_town_right_map_mode_grant_section.gui
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
there.
Each tooltip industry line keeps the goods link on the name and wraps only the
percent in a #TOOLTIP:CUSTOM span binding the hovered location's province. The
span opens a per-good sub-tooltip (containers in custom_cooltip.gui, a
full-file override of the comment-only vanilla registry) where each option
opens with "Building:" and "Using <method>:" lines whose name links carry the
building and production method tooltips, above that option's inputs: green =
raw material present in the province, red = missing, white = an input that is
never a raw material. The containers read the per-group winning-option indices
(cm_trmm_pm_*, multi-option groups only) and raw-input presence flags
(cm_trmm_in_*) stored alongside the coverages through the province-rooted
cm_trmm_pmv_* / inv_* / covv_* readers, so the map mode and tooltips still
only read stored variables plus the raw-material checks on dyes/wine
locations.

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
  - Buildings not buildable at town rank or above (the rural-only villages)
    are excluded entirely: urban rights only exist on towns and cities, so
    their production can never sit behind a granted right.
  - A kept building's location_potential is carried as a per-location gate:
    its options pool separately and only count on locations where the gate
    holds (tar_kiln's woods/forest/jungle-or-lumber gate), evaluated live in
    the coverage readers.

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
OUT_CUSTOM_COOLTIP = os.path.join(
    ROOT_DIR, "in_game", "gui", "custom_cooltip.gui")
OUT_GRANT_SECTION = os.path.join(
    ROOT_DIR, "in_game", "gui", "cm_town_right_map_mode_grant_section.gui")

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

# Rank flags a building must carry at least one of to sit behind a granted
# urban right (rights only exist on towns and cities).
URBAN_RANK_FLAGS = ("town", "city", "megalopolis")
RANK_FLAGS = ("rural_settlement",) + URBAN_RANK_FLAGS

SHARE_DECIMALS = 3

# English sub-tooltip headers for known location gates, keyed by the exact
# normalized gate string; an unmapped gate falls back to GATE_LABEL_FALLBACK
# and the run report warns.
GATE_LABELS = {
    "OR = { vegetation = woods vegetation = forest vegetation = jungle "
    "raw_material ?= goods:lumber }":
        "Needs woods, forest, jungle, or a lumber RGO at the location.",
    "is_produced_in_location_market = goods:sand":
        "Using sand from the local market:",
}
GATE_LABEL_FALLBACK = "Where the building can be placed:"

# Vanilla in_game/gui/custom_cooltip.gui ships comment-only; the override
# carries its header verbatim.
VANILLA_COOLTIP_HEADER = """
# Widgets for the CUSTOM tooltip tag handler.
#
# Syntax in localization:
#   #TOOLTIP:CUSTOM,WidgetName,TYPE1:key1,TYPE2:key2,... Text#!
#
#   WidgetName — must match the `name = "..."` of a container defined in this file.
#   TYPE:key   — resolves one game object into the tooltip data context.
#                Ref types use the integer object ID as key; database types use the script key.
#                Multiple TYPE:key pairs can be combined in a single tag.
#
# Example:
#   Loc key:  MY_KEY: "#TOOLTIP:CUSTOM,MyWidget,COUNTRY=[Country.GetKey],GOODS=[Goods.GetKey] Some Text Here#!"
#   Widget:
#     container = {
#         alwaystransparent = no
#         name = "MyWidget"
#         ContextualTooltipType = {
#             blockoverride "title_text"    { text = "[Country.GetName]" }
#             blockoverride "tooltip_content" {
#                 TooltipTextBlock = {
#                     blockoverride "text" { text = "[Goods.GetName]" }
#                 }
#             }
#         }
#     }
#
"""

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
# Another-royal-right-granted stripe.
SEARCH_OTHER_GRANTED_STRIPE = "rgb { 0 0 0 }"

# Main-mode granted stripe: a red-to-green gradient by how far the granted
# right's fit falls below the best specialization here. Green is the low-miss
# end (factor 0), red the high-miss end (factor 1).
GRANTED_MISS_NONE_STRIPE = "rgb { 60 220 60 }"
GRANTED_MISS_FULL_STRIPE = "rgb { 230 60 50 }"
# Main-mode tie stripes: a two-way tie for best stripes the runner-up right's
# own color; three or more tied stripe this dedicated cyan instead.
MULTI_TIE_STRIPE = "rgb { 0 220 255 }"
# Open-urban-right-slot stripe on both the main and search modes, blue reused
# from the placement finder's existing-governor stripe
# (in_game/gfx/map/map_modes/cm_proximity_finder_map_modes.txt:55).
OPEN_SLOT_STRIPE = "rgb { 0 100 255 }"
# City-or-larger at its town-rights cap with no specialization right granted: a
# recommended right cannot be granted here without revoking an existing one.
FULL_NO_SPEC_STRIPE = "rgb { 170 70 210 }"

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
    """Return ([(building, [slot, ...], gates, rank_flags, ref, obsolete)],
    dangling) where each slot is a list of PM dicts {name, ref, inputs,
    produced, output, gates}, gates is the building's own restriction blocks
    as (kind, inner_text), rank_flags maps declared location-rank flags to
    their values, obsolete is the building type this one replaces (the tier
    chain link) or None, and dangling is the set of
    possible_production_methods names with no external definition."""
    buildings = []
    dangling = set()
    for name, path in iter_db_files(building_types_dir):
        rel = os.path.join(BUILDING_TYPES_SUBDIR, name).replace(os.sep, "/")
        text = read_pdx(path)
        for building, body, b_line in child_blocks(text):
            slots = []
            gates = []
            rank_flags = {}
            obsolete = None
            for key, value in scalar_assignments(body):
                if key in RANK_FLAGS:
                    rank_flags[key] = value
                elif key == "obsolete":
                    obsolete = value
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
                buildings.append(
                    (building, slots, gates, rank_flags, f"{rel}:{b_line}",
                     obsolete))
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


def location_gate(gates):
    """One-line trigger text of the building's location_potential block(s), or
    None. raw_material compares are rewritten null-safe (locations without an
    RGO have an invalid raw_material link)."""
    inners = [" ".join(inner.split()) for kind, inner in gates
              if kind == "location_potential"]
    inners = [text for text in inners if text]
    if not inners:
        return None
    return re.sub(r"\braw_material\s*=\s*", "raw_material ?= ",
                  " ".join(inners))


def filter_buildings(buildings, pm_lock, building_lock, boosted_goods):
    """Drop advance-locked, restriction-gated, or rural-only buildings and
    PMs before any scoring math, so main-slot selection and the worth-using
    threshold only ever see universally available production methods. Returns
    the (building, slots, location_gate, obsolete) list collect_options and
    collect_expand_bases consume plus report lines for every exclusion that
    touches a boosted good."""
    kept = []
    report = []
    for building, slots, gates, rank_flags, ref, obsolete in buildings:
        reason = None
        if rank_flags and not any(
                rank_flags.get(flag) == "yes" for flag in URBAN_RANK_FLAGS):
            reason = "only buildable below town rank"
        if not reason:
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
            kept.append((building, new_slots, location_gate(gates), obsolete))
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
    """Return {good: [option]}, option = {shares {good: share_str}, others
    {good: share_str}, gate, building, pm, comment}. shares holds the
    raw-material input shares that can be locally covered, others the
    remaining inputs' shares for the breakdown rows; dedup and domination use
    raw shares only."""
    options = {good: [] for good in boosted_goods}
    seen = {good: set() for good in boosted_goods}
    for building, slots, gate, _obsolete in buildings:
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
            others = {}
            for input_good, amount in sorted(pm["inputs"].items()):
                if goods_categories.get(input_good) != "raw_material":
                    others[input_good] = fmt_num(amount / total)
                    continue
                shares[input_good] = fmt_num(amount / total)
            if not shares:
                continue
            key = (gate, tuple(sorted(shares.items())))
            if key in seen[good]:
                continue
            seen[good].add(key)
            mix = ", ".join(f"{g} {fmt_amount(a)}"
                            for g, a in sorted(pm["inputs"].items()))
            options[good].append({
                "shares": shares,
                "others": others,
                "gate": gate,
                "building": building,
                "pm": pm["name"],
                "comment": (f"{pm['name']} ({building}), {pm['ref']} - "
                            f"{mix} of {fmt_amount(total)} total input"),
            })
    for good, opts in options.items():
        options[good] = [
            o for o in opts
            if not any((a["gate"] is None or a["gate"] == o["gate"])
                       and dominates(a, o) for a in opts if a is not o)]
    return options


def dominates(a, b):
    """True when option a's input shares are >= option b's for every input.
    Only a valid prune when a is available wherever b is (caller checks the
    location gates)."""
    return all(g in a["shares"] and float(a["shares"][g]) >= float(s)
               for g, s in b["shares"].items())


def collect_expand_bases(buildings, rights):
    """Return {right: [(building, gate)]}: the base tier of each building
    chain producing one of the right's goods, registered for auto-expand by
    the grant-and-expand scripted GUIs. A type qualifies when a worth-using
    main-slot PM produces a boosted good; a base is a qualifying type whose
    obsolete target is not itself in the right's qualifying set."""
    producers = {right: {} for right in ROYAL_RIGHTS}
    obsoletes = {}
    for building, slots, gate, obsolete in buildings:
        obsoletes[building] = obsolete
        main_slot = max(
            slots,
            key=lambda slot: max((pm["output"] or 0.0) for pm in slot))
        best_output = max((pm["output"] or 0.0) for pm in main_slot)
        if best_output <= 0:
            continue
        produced = {pm["produced"] for pm in main_slot
                    if (pm["output"] or 0.0)
                    >= PM_OUTPUT_THRESHOLD * best_output}
        for right in ROYAL_RIGHTS:
            if produced & set(rights[right]["goods"]):
                producers[right][building] = gate
    bases = {}
    for right in ROYAL_RIGHTS:
        types = producers[right]
        bases[right] = sorted(
            (building, gate) for building, gate in types.items()
            if obsoletes.get(building) not in types)
    return bases


def group_options(opts):
    """Group a good's options by location gate: [(gate, [option, ...])], the
    ungated group first so it keeps the plain variable names."""
    groups = []
    index = {}
    for option in opts:
        gate = option["gate"]
        if gate not in index:
            index[gate] = len(groups)
            groups.append((gate, []))
        groups[index[gate]][1].append(option)
    groups.sort(key=lambda item: item[0] is not None)
    return groups


def gate_comment(opts):
    """Reader comment naming the gated group's building(s)."""
    return "/".join(sorted({o["building"] for o in opts})) + " location_potential."


def chip_permille(share):
    """Integer permille key for a share string ("0.667" -> 667)."""
    return int(round(float(share) * 1000))


def chip_pct(permille):
    """Display percent for a permille key, matching the |%1 format."""
    return f"{permille / 10:.1f}%"


def row_key(kind, input_good, permille):
    """Shared loc key of one sub-tooltip input row: kind y (present), n
    (missing), o (never a raw material)."""
    return f"cm_trmm_row_{kind}_{input_good}_{permille}"


def gate_label(gate):
    """English sub-tooltip header for a location gate."""
    return GATE_LABELS.get(gate, GATE_LABEL_FALLBACK)


def why_groups(options, good):
    """Sub-tooltip structure for a good: [(gi, gate, label_key, first_k,
    multi_option, [(k, building, pm, [(kind, input, permille), ...]), ...])].
    label_key is None only when the good has a single ungated group; row kind
    is raw or other."""
    groups = group_options(options[good])
    multi_group = len(groups) > 1
    out = []
    k = 0
    for gi, (gate, opts) in enumerate(groups, start=1):
        suffix = "" if gi == 1 else f"_g{gi}"
        if multi_group or gate is not None:
            label_key = f"cm_trmm_why_grp_{good}{suffix}"
        else:
            label_key = None
        first_k = k + 1
        rows_opts = []
        for option in opts:
            k += 1
            rows = []
            for input_good, share in option["shares"].items():
                rows.append(("raw", input_good, chip_permille(share)))
            for input_good, share in option["others"].items():
                rows.append(("other", input_good, chip_permille(share)))
            rows_opts.append((k, option["building"], option["pm"], rows))
        out.append((gi, gate, label_key, first_k, len(opts) > 1, rows_opts))
    return out


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


def emit_script_values(rights, options, aliases, boosted_goods, self_goods,
                       relevant):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Province-definition-scoped option values consumed by\n"
        "# cm_trmm_recompute_province_definition (each is one worth-using production\n"
        "# method's locally-available input share), plus the location-scoped readers of\n"
        "# the stored province variables that the map mode tooltip machinery uses.\n"
        "# Options from a building with a location_potential pool separately and only\n"
        "# count on locations where that gate holds.\n"
        "# Readers are only evaluated on locations the recompute pass marked\n"
        "# (has_variable cm_trmm_best_idx).\n")

    for good in boosted_goods:
        k = 0
        for _gate, opts in group_options(options[good]):
            for option in opts:
                k += 1
                lines.append(f"# {option['comment']}")
                lines.append(f"cm_trmm_opt_{good}_{k} = {{")
                lines.append("\tvalue = 0")
                for input_good, share in option["shares"].items():
                    lines.append("\tif = {")
                    lines.append(
                        f"\t\tlimit = {{ {province_check(input_good)} }}")
                    lines.append(f"\t\tadd = {share}")
                    lines.append("\t}")
                lines.append("}")
        lines.append("")

    for good in boosted_goods:
        groups = group_options(options[good])
        for gi, (_gate, opts) in enumerate(groups, start=1):
            if gi == 1:
                continue
            lines.append(f"cm_trmm_covp_{good}_g{gi} = {{")
            lines.append("\tvalue = 0")
            lines.append(
                f"\tprovince = {{ add = var:cm_trmm_cov_{good}_g{gi} }}")
            lines.append("}")
        lines.append(f"cm_trmm_cov_{good} = {{")
        lines.append("\tvalue = 0")
        first_gate, first_opts = groups[0]
        if first_gate is None:
            lines.append(f"\tprovince = {{ add = var:cm_trmm_cov_{good} }}")
        else:
            lines.append(f"\t# {gate_comment(first_opts)}")
            lines.append("\tif = {")
            lines.append(f"\t\tlimit = {{ {first_gate} }}")
            lines.append(f"\t\tprovince = {{ add = var:cm_trmm_cov_{good} }}")
            lines.append("\t}")
        for gi, (gate, opts) in enumerate(groups, start=1):
            if gi == 1:
                continue
            lines.append(f"\t# {gate_comment(opts)}")
            lines.append("\tmin = {")
            lines.append("\t\tvalue = 0")
            lines.append("\t\tif = {")
            lines.append(f"\t\t\tlimit = {{ {gate} }}")
            lines.append(f"\t\t\tadd = cm_trmm_covp_{good}_g{gi}")
            lines.append("\t\t}")
            lines.append("\t}")
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

    lines.append(
        "# Right-score readers safe on unmarked locations (the grant row renders\n"
        "# there).")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(f"cm_trmm_rightv_{alias} = {{")
        lines.append("\tvalue = 0")
        lines.append("\tif = {")
        lines.append("\t\tlimit = { has_variable = cm_trmm_best_idx }")
        lines.append(f"\t\tadd = cm_trmm_right_{alias}")
        lines.append("\t}")
        lines.append("}")
    lines.append("")

    lines.append(
        "# The granted royal right's fit versus the best specialization here:\n"
        "# the best score among granted rights, and the shortfall (0-1) the\n"
        "# granted stripe shades from green to red.")
    lines.append("cm_trmm_granted_best = {")
    lines.append("\tvalue = 0")
    for right in ROYAL_RIGHTS:
        lines.append("\tmin = {")
        lines.append("\t\tvalue = 0")
        lines.append("\t\tif = {")
        lines.append(
            f"\t\t\tlimit = {{ has_town_rights = town_rights_type:{right} }}")
        lines.append(f"\t\t\tadd = cm_trmm_right_{aliases[right]}")
        lines.append("\t\t}")
        lines.append("\t}")
    lines.append("}")
    lines.append("cm_trmm_granted_miss = {")
    lines.append("\tvalue = var:cm_trmm_best_mil")
    lines.append("\tmultiply = 0.001")
    lines.append("\tsubtract = cm_trmm_granted_best")
    lines.append("\tmin = 0")
    lines.append("\tmax = 1")
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
    lines.append("")

    lines.append(
        "# Province-rooted readers for the industry breakdown sub-tooltips\n"
        "# (custom_cooltip.gui containers, which bind the hovered location's province).\n"
        "# The winning-option readers default to the group's first option where the\n"
        "# stored index is absent.")
    for good in boosted_goods:
        for gi, _gate, _label, first_k, multi_option, _opts in why_groups(
                options, good):
            if not multi_option:
                continue
            suffix = "" if gi == 1 else f"_g{gi}"
            lines.append(f"cm_trmm_pmv_{good}{suffix} = {{")
            lines.append(f"\tvalue = {first_k}")
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ has_variable = cm_trmm_pm_{good}{suffix} }}")
            lines.append(f"\t\tadd = var:cm_trmm_pm_{good}{suffix}")
            lines.append(f"\t\tsubtract = {first_k}")
            lines.append("\t}")
            lines.append("}")
    for input_good in relevant:
        lines.append(f"cm_trmm_inv_{input_good} = {{")
        lines.append("\tvalue = 0")
        lines.append("\tif = {")
        lines.append(f"\t\tlimit = {{ has_variable = cm_trmm_in_{input_good} }}")
        lines.append("\t\tadd = 1")
        lines.append("\t}")
        lines.append("}")
    for good in boosted_goods:
        structure = why_groups(options, good)
        if len(structure) == 1:
            continue
        for gi, _gate, _label, _first_k, _multi, _opts in structure:
            suffix = "" if gi == 1 else f"_g{gi}"
            lines.append(f"cm_trmm_covv_{good}{suffix} = {{")
            lines.append("\tvalue = 0")
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ has_variable = cm_trmm_cov_{good}{suffix} }}")
            lines.append(f"\t\tadd = var:cm_trmm_cov_{good}{suffix}")
            lines.append("\t}")
            lines.append("}")

    lines.append("")
    lines.append("# Grant row ordering: how many rights beat this one on score, ties")
    lines.append("# broken by list order (0 = leftmost button). Root is the location.")
    for pos_a, right_a in enumerate(ROYAL_RIGHTS):
        alias_a = aliases[right_a]
        lines.append(f"cm_trmm_grant_slot_{alias_a} = {{")
        lines.append("\tvalue = 0")
        for pos_b, right_b in enumerate(ROYAL_RIGHTS):
            if right_b == right_a:
                continue
            op = ">=" if pos_b < pos_a else ">"
            lines.append("\tif = {")
            lines.append(
                f"\t\tlimit = {{ cm_trmm_rightv_{aliases[right_b]} {op} "
                f"{{ value = cm_trmm_rightv_{alias_a} }} }}")
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


def emit_effects(options, aliases, boosted_goods, relevant, expand_bases,
                 rgo_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Once-per-lobby precompute: computes each qualifying province definition's\n"
        "# industry coverages definition-wide, stores them on every province slice in\n"
        "# the definition along with the winning option indices and raw-input presence\n"
        "# flags the tooltip breakdown chips read, and stores each location's best\n"
        "# right index as a location variable, so the map mode and tooltip only read\n"
        "# stored values. Variables stored on the province_definition itself do not\n"
        "# read back, so everything is staged in locals and written to every slice.\n")
    lines.append("# Province definition scope.")
    lines.append("cm_trmm_recompute_province_definition = {")
    lines.append("\tif = {")
    lines.append("\t\tlimit = { cm_trmm_province_definition_has_any_input = yes }")
    lines.append(
        "\t\t# First-best scan per group: coverage plus the winning option's index\n"
        "\t\t# for the breakdown chips. Options are never negative and ties keep the\n"
        "\t\t# earliest option.")
    for good in boosted_goods:
        k = 0
        for gi, (_gate, opts) in enumerate(group_options(options[good]),
                                           start=1):
            suffix = "" if gi == 1 else f"_g{gi}"
            k += 1
            lines.append("\t\tset_local_variable = {")
            lines.append(f"\t\t\tname = cm_trmm_l_cov_{good}{suffix}")
            lines.append(f"\t\t\tvalue = cm_trmm_opt_{good}_{k}")
            lines.append("\t\t}")
            if len(opts) == 1:
                continue
            lines.append("\t\tset_local_variable = {")
            lines.append(f"\t\t\tname = cm_trmm_l_pm_{good}{suffix}")
            lines.append(f"\t\t\tvalue = {k}")
            lines.append("\t\t}")
            for _ in opts[1:]:
                k += 1
                lines.append("\t\tset_local_variable = {")
                lines.append("\t\t\tname = cm_trmm_l_opt")
                lines.append(f"\t\t\tvalue = cm_trmm_opt_{good}_{k}")
                lines.append("\t\t}")
                lines.append("\t\tif = {")
                lines.append(
                    "\t\t\tlimit = { local_var:cm_trmm_l_opt > "
                    f"local_var:cm_trmm_l_cov_{good}{suffix} }}")
                lines.append("\t\t\tset_local_variable = {")
                lines.append(f"\t\t\t\tname = cm_trmm_l_cov_{good}{suffix}")
                lines.append("\t\t\t\tvalue = local_var:cm_trmm_l_opt")
                lines.append("\t\t\t}")
                lines.append("\t\t\tset_local_variable = {")
                lines.append(f"\t\t\t\tname = cm_trmm_l_pm_{good}{suffix}")
                lines.append(f"\t\t\t\tvalue = {k}")
                lines.append("\t\t\t}")
                lines.append("\t\t}")
    for input_good in relevant:
        lines.append("\t\tset_local_variable = {")
        lines.append(f"\t\t\tname = cm_trmm_l_in_{input_good}")
        lines.append("\t\t\tvalue = {")
        lines.append("\t\t\t\tvalue = 0")
        lines.append("\t\t\t\tif = {")
        lines.append(f"\t\t\t\t\tlimit = {{ {province_check(input_good)} }}")
        lines.append("\t\t\t\t\tadd = 1")
        lines.append("\t\t\t\t}")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
    lines.append("\t\tevery_province_in_province_definition = {")
    for good in boosted_goods:
        for gi, (_gate, _opts) in enumerate(group_options(options[good]),
                                            start=1):
            suffix = "" if gi == 1 else f"_g{gi}"
            lines.append("\t\t\tset_variable = {")
            lines.append(f"\t\t\t\tname = cm_trmm_cov_{good}{suffix}")
            lines.append(
                f"\t\t\t\tvalue = local_var:cm_trmm_l_cov_{good}{suffix}")
            lines.append("\t\t\t}")
    for good in boosted_goods:
        for gi, (_gate, opts) in enumerate(group_options(options[good]),
                                           start=1):
            if len(opts) == 1:
                continue
            suffix = "" if gi == 1 else f"_g{gi}"
            lines.append("\t\t\tset_variable = {")
            lines.append(f"\t\t\t\tname = cm_trmm_pm_{good}{suffix}")
            lines.append(
                f"\t\t\t\tvalue = local_var:cm_trmm_l_pm_{good}{suffix}")
            lines.append("\t\t\t}")
    lines.append("\t\t\t# Absence marks a raw input missing.")
    for input_good in relevant:
        lines.append("\t\t\tif = {")
        lines.append(
            f"\t\t\t\tlimit = {{ local_var:cm_trmm_l_in_{input_good} = 1 }}")
        lines.append("\t\t\t\tset_variable = {")
        lines.append(f"\t\t\t\t\tname = cm_trmm_in_{input_good}")
        lines.append("\t\t\t\t\tvalue = 1")
        lines.append("\t\t\t\t}")
        lines.append("\t\t\t}")
        lines.append("\t\t\telse_if = {")
        lines.append(
            f"\t\t\t\tlimit = {{ has_variable = cm_trmm_in_{input_good} }}")
        lines.append(f"\t\t\t\tremove_variable = cm_trmm_in_{input_good}")
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
    lines.append("\t\t\t\t# The encoding's score half: round(best score * 1000).")
    lines.append("\t\t\t\tset_variable = {")
    lines.append("\t\t\t\t\tname = cm_trmm_best_mil")
    lines.append("\t\t\t\t\tvalue = {")
    lines.append("\t\t\t\t\t\tvalue = local_var:cm_trmm_enc")
    lines.append("\t\t\t\t\t\tsubtract = var:cm_trmm_best_idx")
    lines.append("\t\t\t\t\t\tdivide = 10")
    lines.append("\t\t\t\t\t}")
    lines.append("\t\t\t\t}")
    lines.append(
        "\t\t\t\t# Tie data for the stripes: 0 = no tie, 1-8 = the runner-up right of a\n"
        "\t\t\t\t# two-way tie for best, 10 = three or more tied. An exact-tied earlier\n"
        "\t\t\t\t# right would have won the encoding, so only later rights can be partners.")
    lines.append("\t\t\t\tset_variable = {")
    lines.append("\t\t\t\t\tname = cm_trmm_tie_idx")
    lines.append("\t\t\t\t\tvalue = 0")
    lines.append("\t\t\t\t}")
    lines.append("\t\t\t\tif = {")
    lines.append("\t\t\t\t\t# enc below 10 = best score rounds to 0.")
    lines.append("\t\t\t\t\tlimit = { local_var:cm_trmm_enc >= 10 }")
    n = len(ROYAL_RIGHTS)
    for pos, right in enumerate(ROYAL_RIGHTS[:-1]):
        partners = ROYAL_RIGHTS[pos + 1:]
        kw = "if" if pos == 0 else "else_if"
        lines.append(f"\t\t\t\t\t{kw} = {{")
        lines.append(
            f"\t\t\t\t\t\tlimit = {{ var:cm_trmm_best_idx = {n - pos} }}")
        inner = "if"
        if len(partners) > 1:
            lines.append("\t\t\t\t\t\tif = {")
            lines.append(
                "\t\t\t\t\t\t\tlimit = { "
                f"cm_trmm_tie_count_{aliases[right]} >= 2 }}")
            lines.append("\t\t\t\t\t\t\tset_variable = {")
            lines.append("\t\t\t\t\t\t\t\tname = cm_trmm_tie_idx")
            lines.append("\t\t\t\t\t\t\t\tvalue = 10")
            lines.append("\t\t\t\t\t\t\t}")
            lines.append("\t\t\t\t\t\t}")
            inner = "else_if"
        for partner in partners:
            lines.append(f"\t\t\t\t\t\t{inner} = {{")
            lines.append(
                "\t\t\t\t\t\t\tlimit = { "
                f"{tied_trigger(right, partner, aliases)} }}")
            lines.append("\t\t\t\t\t\t\tset_variable = {")
            lines.append("\t\t\t\t\t\t\t\tname = cm_trmm_tie_idx")
            lines.append(
                f"\t\t\t\t\t\t\t\tvalue = {n - ROYAL_RIGHTS.index(partner)}")
            lines.append("\t\t\t\t\t\t\t}")
            lines.append("\t\t\t\t\t\t}")
            inner = "else_if"
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
    lines.append("\t\t\tevery_location_in_province = {")
    lines.append("\t\t\t\tlimit = { has_variable = cm_trmm_best_mil }")
    lines.append("\t\t\t\tremove_variable = cm_trmm_best_mil")
    lines.append("\t\t\t}")
    lines.append("\t\t\tevery_location_in_province = {")
    lines.append("\t\t\t\tlimit = { has_variable = cm_trmm_tie_idx }")
    lines.append("\t\t\t\tremove_variable = cm_trmm_tie_idx")
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
    lines.append("")
    lines.append(
        "# Turns on auto-expand for the industries a right boosts, and for its raw\n"
        "# goods where the location produces them. Each industry registers its chain's\n"
        "# base tier; the obsolete-registration migration resolves it to the current\n"
        "# tier. Root is the location, expects scope:cm_country.")
    for right in ROYAL_RIGHTS:
        lines.append(f"cm_trmm_enable_expands_{aliases[right]} = {{")
        for building, gate in expand_bases[right]:
            register = [
                f"building_type:{building} = "
                "{ save_scope_as = cm_building_type }",
                "cm_enable_auto_expand_for_building_type = yes"]
            if gate:
                lines.append("\tif = {")
                lines.append(f"\t\tlimit = {{ {gate} }}")
                lines.extend(f"\t\t{line}" for line in register)
                lines.append("\t}")
            else:
                lines.extend(f"\t{line}" for line in register)
        if rgo_goods[right]:
            compares = [f"raw_material ?= goods:{good}"
                        for good in rgo_goods[right]]
            joined = (compares[0] if len(compares) == 1
                      else f"OR = {{ {' '.join(compares)} }}")
            lines.append("\tif = {")
            lines.append(f"\t\tlimit = {{ {joined} }}")
            lines.append("\t\tcm_enable_auto_expand_for_rgo = yes")
            lines.append("\t}")
        lines.append("}")
    return "\n".join(lines) + "\n"


def emit_custom_loc(aliases, boosted_goods, self_goods):
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
        "# Inline RGO chips on the dyes/wine industry lines: shown when the location's\n"
        "# own raw material is the good, whose RGO averages the displayed value up.")
    for good in sorted(self_goods):
        lines.append(f"cm_trmm_rgo_chip_{good} = {{")
        lines.append("\ttype = location")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append(f"\t\t\traw_material ?= goods:{good}")
        lines.append("\t\t}")
        lines.append(f"\t\tlocalization_key = cm_trmm_chip_rgo_{good}")
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
        "# per-right granted-or-blank line the multi-right hover list and the\n"
        "# search-mode stripe-reason line concatenate.")
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

    n = len(ROYAL_RIGHTS)
    lines.append(
        "# Stripe-reason lines for the map mode tooltips' bottom:\n"
        "# cm_trmm_stripe_reason explains the main mode's stripe (the open urban\n"
        "# right slot, the granted right's fit versus the best, a full city with\n"
        "# no specialization granted, or the tied rights),\n"
        "# cm_trmm_search_reason_* the reason for the search modes' stripe.\n"
        "# Blocks mirror the stripe limits, so first match = stripe precedence.")
    lines.append("cm_trmm_stripe_reason = {")
    lines.append("\ttype = location")
    lines.append("\ttext = {")
    lines.append("\t\ttrigger = {")
    lines.append("\t\t\thas_max_town_rights = no")
    lines.append("\t\t}")
    lines.append("\t\tlocalization_key = cm_trmm_reason_open_slot")
    lines.append("\t}")
    for pos, right in enumerate(ROYAL_RIGHTS):
        idx = n - pos
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\thas_variable = cm_trmm_best_idx")
        lines.append(f"\t\t\tvar:cm_trmm_best_idx = {idx}")
        lines.append(f"\t\t\thas_town_rights = town_rights_type:{right}")
        lines.append("\t\t}")
        lines.append(
            f"\t\tlocalization_key = cm_trmm_reason_best_{aliases[right]}")
        lines.append("\t}")
    for right in ROYAL_RIGHTS:
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\thas_variable = cm_trmm_best_idx")
        lines.append(f"\t\t\thas_town_rights = town_rights_type:{right}")
        lines.append("\t\t}")
        lines.append(
            f"\t\tlocalization_key = cm_trmm_reason_missed_{aliases[right]}")
        lines.append("\t}")
    for right in ROYAL_RIGHTS:
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\tNOT = { has_variable = cm_trmm_best_idx }")
        lines.append(f"\t\t\thas_town_rights = town_rights_type:{right}")
        lines.append("\t\t}")
        lines.append(
            f"\t\tlocalization_key = cm_trmm_reason_granted_{aliases[right]}")
        lines.append("\t}")
    lines.append("\ttext = {")
    lines.append("\t\ttrigger = {")
    lines.append("\t\t\thas_max_town_rights = yes")
    lines.append("\t\t\tcm_uright_assigned_count = 0")
    lines.append("\t\t\tOR = {")
    lines.append("\t\t\t\tlocation_rank = location_rank:city")
    lines.append("\t\t\t\tlocation_rank = location_rank:megalopolis")
    lines.append("\t\t\t}")
    lines.append("\t\t}")
    lines.append("\t\tlocalization_key = cm_trmm_reason_full_no_spec")
    lines.append("\t}")
    for pos, right in enumerate(ROYAL_RIGHTS[:-1]):
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\thas_variable = cm_trmm_tie_idx")
        lines.append(f"\t\t\tvar:cm_trmm_best_idx = {n - pos}")
        lines.append("\t\t\tvar:cm_trmm_tie_idx >= 1")
        lines.append("\t\t}")
        lines.append(
            f"\t\tlocalization_key = cm_trmm_reason_tied_{aliases[right]}")
        lines.append("\t}")
    lines.append("\ttext = {")
    lines.append("\t\tlocalization_key = cm_trmm_blank")
    lines.append("\t\tfallback = yes")
    lines.append("\t}")
    lines.append("}")
    for pos, right in enumerate(ROYAL_RIGHTS):
        alias = aliases[right]
        idx = n - pos
        lines.append(f"cm_trmm_search_reason_{alias} = {{")
        lines.append("\ttype = location")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append(f"\t\t\thas_town_rights = town_rights_type:{right}")
        lines.append("\t\t}")
        lines.append("\t\tlocalization_key = cm_trmm_search_granted_line")
        lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\thas_variable = cm_trmm_best_idx")
        lines.append(f"\t\t\tvar:cm_trmm_best_idx = {idx}")
        lines.append("\t\t}")
        lines.append("\t\tlocalization_key = cm_trmm_search_best_line")
        lines.append("\t}")
        if pos != 0:
            lines.append("\ttext = {")
            lines.append("\t\ttrigger = {")
            lines.append("\t\t\thas_variable = cm_trmm_tie_idx")
            lines.append("\t\t\tOR = {")
            lines.append(f"\t\t\t\tvar:cm_trmm_tie_idx = {idx}")
            lines.append("\t\t\t\tAND = {")
            lines.append("\t\t\t\t\tvar:cm_trmm_tie_idx = 10")
            lines.append(f"\t\t\t\t\tcm_trmm_tier_{alias} = 0")
            lines.append("\t\t\t\t}")
            lines.append("\t\t\t}")
            lines.append("\t\t}")
            lines.append(
                "\t\tlocalization_key = cm_trmm_search_tied_best_line")
            lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\tOR = {")
        for other in ROYAL_RIGHTS:
            if other == right:
                continue
            lines.append(
                f"\t\t\t\thas_town_rights = town_rights_type:{other}")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
        lines.append("\t\tlocalization_key = cm_trmm_search_other_line")
        lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\ttrigger = {")
        lines.append("\t\t\thas_variable = cm_trmm_best_idx")
        lines.append(f"\t\t\tcm_trmm_right_{alias} > 0")
        lines.append("\t\t\thas_max_town_rights = no")
        lines.append("\t\t}")
        lines.append("\t\tlocalization_key = cm_trmm_search_open_slot_line")
        lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\tlocalization_key = cm_trmm_blank")
        lines.append("\t\tfallback = yes")
        lines.append("\t}")
        lines.append("}")

    return "\n".join(lines) + "\n"


def emit_custom_cooltip(options, boosted_goods, self_goods):
    """The CUSTOM tooltip container registry: the tag handler resolves
    containers only from this exact file path, so it is a full-file override
    of the comment-only vanilla file."""
    lines = [GENERATED_HEADER + VANILLA_COOLTIP_HEADER.strip("\n")]
    lines.append("")
    lines.append(
        "# CM: industry breakdown sub-tooltips, one container per boosted good,\n"
        "# opened from the #TOOLTIP:CUSTOM spans on the urban-rights tooltip industry\n"
        "# lines with the hovered location's province bound as Province.")
    for good in boosted_goods:
        lines.append("container = {")
        lines.append("\talwaystransparent = no")
        lines.append(f"\tname = \"cm_trmm_why_{good}\"")
        lines.append("\tContextualTooltipType = {")
        lines.append("\t\tblockoverride \"title_text\" {")
        lines.append(f"\t\t\ttext = \"cm_trmm_why_title_{good}\"")
        lines.append("\t\t}")
        lines.append("\t\tblockoverride \"tooltip_content\" {")
        lines.append("\t\t\tvbox = {")
        lines.append("\t\t\t\tlayoutpolicy_horizontal = expanding")
        lines.append("\t\t\t\tmargin = { 10 10 }")
        lines.append("\t\t\t\tignoreinvisible = yes")
        for gi, _gate, label_key, _first_k, multi_option, opts in why_groups(
                options, good):
            suffix = "" if gi == 1 else f"_g{gi}"
            if label_key:
                lines.append("\t\t\t\ttextbox = {")
                lines.append("\t\t\t\t\tusing = tooltip_text_block_template")
                lines.append(f"\t\t\t\t\ttext = \"{label_key}\"")
                lines.append("\t\t\t\t}")
            for k, building, pm, rows in opts:
                lines.append("\t\t\t\tvbox = {")
                if multi_option:
                    lines.append(
                        "\t\t\t\t\tvisible = \"[EqualTo_CFixedPoint("
                        "Province.MakeScope.ScriptValue("
                        f"'cm_trmm_pmv_{good}{suffix}'), "
                        f"'(CFixedPoint){k}')]\"")
                lines.append("\t\t\t\t\tlayoutpolicy_horizontal = expanding")
                lines.append("\t\t\t\t\tignoreinvisible = yes")
                lines.append("\t\t\t\t\ttextbox = {")
                lines.append("\t\t\t\t\t\tusing = tooltip_text_block_template")
                lines.append(
                    f"\t\t\t\t\t\ttext = \"cm_trmm_why_bld_{building}\"")
                lines.append("\t\t\t\t\t}")
                lines.append("\t\t\t\t\ttextbox = {")
                lines.append("\t\t\t\t\t\tusing = tooltip_text_block_template")
                lines.append(f"\t\t\t\t\t\ttext = \"cm_trmm_why_pm_{pm}\"")
                lines.append("\t\t\t\t\t}")
                for kind, input_good, permille in rows:
                    if kind == "raw":
                        present = (
                            "EqualTo_CFixedPoint(Province.MakeScope."
                            f"ScriptValue('cm_trmm_inv_{input_good}'), "
                            "'(CFixedPoint)1')")
                        lines.append("\t\t\t\t\ttextbox = {")
                        lines.append(
                            "\t\t\t\t\t\tusing = tooltip_text_block_template")
                        lines.append(f"\t\t\t\t\t\tvisible = \"[{present}]\"")
                        lines.append(
                            "\t\t\t\t\t\ttext = "
                            f"\"{row_key('y', input_good, permille)}\"")
                        lines.append("\t\t\t\t\t}")
                        lines.append("\t\t\t\t\ttextbox = {")
                        lines.append(
                            "\t\t\t\t\t\tusing = tooltip_text_block_template")
                        lines.append(
                            f"\t\t\t\t\t\tvisible = \"[Not({present})]\"")
                        lines.append(
                            "\t\t\t\t\t\ttext = "
                            f"\"{row_key('n', input_good, permille)}\"")
                        lines.append("\t\t\t\t\t}")
                    else:
                        lines.append("\t\t\t\t\ttextbox = {")
                        lines.append(
                            "\t\t\t\t\t\tusing = tooltip_text_block_template")
                        lines.append(
                            "\t\t\t\t\t\ttext = "
                            f"\"{row_key('o', input_good, permille)}\"")
                        lines.append("\t\t\t\t\t}")
                lines.append("\t\t\t\t}")
        if good in self_goods:
            lines.append("\t\t\t\ttextbox = {")
            lines.append("\t\t\t\t\tusing = tooltip_text_block_template")
            lines.append("\t\t\t\t\ttext = \"cm_trmm_why_rgo_note\"")
            lines.append("\t\t\t\t}")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
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


# Emits a hidden-category mode: the search primaries and every _refresh twin.
def _hidden_mode_lines(name, body):
    lines = [f"{name} = {{"]
    lines.extend(body)
    lines.append(MODE_TAIL_HEAD)
    lines.append("\tcategory = hidden")
    lines.append("\tallow_allocate_hotkey = no")
    lines.append(MODE_TAIL_BLOCKS)
    lines.append("\tcolor_refresh_counters = { Day }")
    lines.append("}")
    return lines


def emit_map_mode(rights, aliases, right_colors):
    n = len(ROYAL_RIGHTS)
    # The mode body, shared verbatim by cm_best_town_right and its _refresh twin.
    body = []
    body.append("\tmap_color = {")
    body.append("\t\tif = {")
    body.append("\t\t\tlimit = { is_land = no }")
    body.append(f"\t\t\tvalue = {WATER_COLOR}")
    body.append("\t\t}")
    body.append("\t\telse_if = {")
    body.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
    body.append(f"\t\t\tvalue = {NO_MATCH_COLOR}")
    body.append("\t\t}")
    for pos, right in enumerate(ROYAL_RIGHTS):
        idx = n - pos
        color, source = right_colors[right]
        body.append("\t\telse_if = {")
        body.append(f"\t\t\tlimit = {{ var:cm_trmm_best_idx = {idx} }}")
        body.append(f"\t\t\t# {right} color: {source}")
        body.append(f"\t\t\tvalue = {color}")
        body.append("\t\t}")
    body.append("\t}")
    body.append("")
    body.append("\tsecondary_map_color = {")
    body.append("\t\tif = {")
    body.append("\t\t\tlimit = {")
    body.append("\t\t\t\tis_land = yes")
    body.append("\t\t\t\thas_max_town_rights = no")
    body.append("\t\t\t}")
    body.append(f"\t\t\tvalue = {OPEN_SLOT_STRIPE}")
    body.append("\t\t}")
    # Granted royal right: red-to-green by how far the granted right's fit falls
    # below the best specialization here.
    body.append("\t\telse_if = {")
    body.append("\t\t\tlimit = {")
    body.append("\t\t\t\tis_land = yes")
    body.append("\t\t\t\thas_variable = cm_trmm_best_idx")
    body.append("\t\t\t\tcm_uright_assigned_count >= 1")
    body.append("\t\t\t}")
    body.append("\t\t\tlerp = {")
    body.append(f"\t\t\t\tmin_color = {GRANTED_MISS_NONE_STRIPE}")
    body.append(f"\t\t\t\tmax_color = {GRANTED_MISS_FULL_STRIPE}")
    body.append("\t\t\t\tfactor = { value = cm_trmm_granted_miss }")
    body.append("\t\t\t}")
    body.append("\t\t}")
    # Granted where nothing scores (unmarked): no fit to miss, so the green end.
    body.append("\t\telse_if = {")
    body.append("\t\t\tlimit = {")
    body.append("\t\t\t\tis_land = yes")
    body.append("\t\t\t\tNOT = { has_variable = cm_trmm_best_idx }")
    body.append("\t\t\t\tcm_uright_assigned_count >= 1")
    body.append("\t\t\t}")
    body.append(f"\t\t\tvalue = {GRANTED_MISS_NONE_STRIPE}")
    body.append("\t\t}")
    # A full city-or-larger with no specialization right granted: nowhere to
    # grant the recommended right without revoking one first.
    body.append("\t\telse_if = {")
    body.append("\t\t\tlimit = {")
    body.append("\t\t\t\tis_land = yes")
    body.append("\t\t\t\thas_max_town_rights = yes")
    body.append("\t\t\t\tcm_uright_assigned_count = 0")
    body.append("\t\t\t\tOR = {")
    body.append("\t\t\t\t\tlocation_rank = location_rank:city")
    body.append("\t\t\t\t\tlocation_rank = location_rank:megalopolis")
    body.append("\t\t\t\t}")
    body.append("\t\t\t}")
    body.append(f"\t\t\tvalue = {FULL_NO_SPEC_STRIPE}")
    body.append("\t\t}")
    body.append("\t\t# cm_trmm_tie_idx: 1-8 = two-way runner-up, 10 = 3+ tied.")
    body.append("\t\telse_if = {")
    body.append("\t\t\tlimit = {")
    body.append("\t\t\t\tis_land = yes")
    body.append("\t\t\t\thas_variable = cm_trmm_tie_idx")
    body.append("\t\t\t\tvar:cm_trmm_tie_idx = 10")
    body.append("\t\t\t}")
    body.append(f"\t\t\tvalue = {MULTI_TIE_STRIPE}")
    body.append("\t\t}")
    for pos, right in enumerate(ROYAL_RIGHTS[1:], start=1):
        idx = n - pos
        color, source = right_colors[right]
        body.append("\t\telse_if = {")
        body.append("\t\t\tlimit = {")
        body.append("\t\t\t\tis_land = yes")
        body.append("\t\t\t\thas_variable = cm_trmm_tie_idx")
        body.append(f"\t\t\t\tvar:cm_trmm_tie_idx = {idx}")
        body.append("\t\t\t}")
        body.append(f"\t\t\t# {right} color: {source}")
        body.append(f"\t\t\tvalue = {color}")
        body.append("\t\t}")
    body.append("\t}")
    body.append("")
    for right in ROYAL_RIGHTS:
        color, _ = right_colors[right]
        body.append("\tlegend_key = {")
        body.append(f"\t\tdesc = \"cm_trmm_legend_{aliases[right]}\"")
        body.append(f"\t\tcolor = {color}")
        body.append("\t}")
    body.append("\tlegend_key = {")
    body.append("\t\tdesc = \"cm_trmm_legend_none\"")
    body.append(f"\t\tcolor = {NO_MATCH_COLOR}")
    body.append("\t}")
    for desc, key_color in (
            ("cm_trmm_legend_granted_best", GRANTED_MISS_NONE_STRIPE),
            ("cm_trmm_legend_granted_poor", GRANTED_MISS_FULL_STRIPE),
            ("cm_trmm_legend_tied_multi", MULTI_TIE_STRIPE),
            ("cm_trmm_legend_open_slot", OPEN_SLOT_STRIPE),
            ("cm_trmm_legend_full_no_spec", FULL_NO_SPEC_STRIPE)):
        body.append("\tlegend_key = {")
        body.append(f"\t\tdesc = \"{desc}\"")
        body.append(f"\t\tcolor = {key_color}")
        body.append("\t}")
    body.append("")
    body.append("\ttooltip_key = {")
    body.append("\t\tif = {")
    body.append("\t\t\tlimit = { is_land = no }")
    body.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_WATER")
    body.append("\t\t}")
    body.append("\t\telse_if = {")
    body.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
    body.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_NONE")
    body.append("\t\t}")
    body.append("\t\telse = {")
    body.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_LAND")
    body.append("\t\t}")
    body.append("\t}")
    lines = [GENERATED_HEADER]
    lines.append("cm_best_town_right = {")
    lines.extend(body)
    lines.append(MODE_TAIL_HEAD)
    lines.append("\tcategory = economy")
    lines.append("\tindex = 1")
    lines.append(MODE_TAIL_BLOCKS)
    lines.append("\t# The Day refresh counter (vanilla in_game/gfx/map/map_modes/")
    lines.append("\t# map_modes.txt:1099) keeps the granted stripes current after grants.")
    lines.append("\tcolor_refresh_counters = { Day }")
    lines.append("}")
    lines.append("")
    lines.append("# Hidden duplicate for the post-grant stripe refresh: setting the same")
    lines.append("# mode is a no-op, so the search panel swaps through the twin and back.")
    lines.extend(_hidden_mode_lines("cm_best_town_right_refresh", body))
    return "\n".join(lines) + "\n"


def emit_search_map_modes(rights, aliases, right_colors):
    n = len(ROYAL_RIGHTS)
    lines = [
        "# Per-right search variants of cm_best_town_right, hidden from the flyout",
        "# (opened from the search panel and the urban right tooltips). The fill lerps",
        "# from the low anchor to the right's own color by its fit score; stripes mark",
        "# already-granted, best-or-tied-for-best-here, other-right-granted, and",
        "# open-urban-right-slot locations. cm_trmm_tie_idx: 1-8 = two-way runner-up,",
        "# 10 = 3+ tied.",
    ]
    for pos, right in enumerate(ROYAL_RIGHTS):
        alias = aliases[right]
        upper = alias.upper()
        idx = n - pos
        color, source = right_colors[right]
        # The mode body, shared verbatim by the search mode and its _refresh twin.
        body = []
        body.append("\tmap_color = {")
        body.append("\t\tif = {")
        body.append("\t\t\tlimit = { is_land = no }")
        body.append(f"\t\t\tvalue = {WATER_COLOR}")
        body.append("\t\t}")
        body.append("\t\telse_if = {")
        body.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
        body.append(f"\t\t\tvalue = {NO_MATCH_COLOR}")
        body.append("\t\t}")
        # Kept after the has_variable branch so the score never reads unset
        # province variables.
        body.append("\t\telse_if = {")
        body.append(f"\t\t\tlimit = {{ cm_trmm_right_{alias} <= 0 }}")
        body.append(f"\t\t\tvalue = {NO_MATCH_COLOR}")
        body.append("\t\t}")
        body.append("\t\telse = {")
        body.append("\t\t\tlerp = {")
        body.append(f"\t\t\t\tmin_color = {NO_MATCH_COLOR}")
        body.append(f"\t\t\t\t# {right} color: {source}")
        body.append(f"\t\t\t\tmax_color = {color}")
        body.append(f"\t\t\t\tfactor = {{ value = cm_trmm_right_{alias} }}")
        body.append("\t\t\t}")
        body.append("\t\t}")
        body.append("\t}")
        body.append("")
        body.append("\tsecondary_map_color = {")
        body.append("\t\tif = {")
        body.append("\t\t\tlimit = {")
        body.append("\t\t\t\tis_land = yes")
        body.append(f"\t\t\t\thas_town_rights = town_rights_type:{right}")
        body.append("\t\t\t}")
        body.append(f"\t\t\tvalue = {SEARCH_GRANTED_STRIPE}")
        body.append("\t\t}")
        body.append("\t\telse_if = {")
        body.append("\t\t\tlimit = {")
        body.append("\t\t\t\tis_land = yes")
        body.append("\t\t\t\thas_variable = cm_trmm_best_idx")
        if pos == 0:
            body.append(f"\t\t\t\tvar:cm_trmm_best_idx = {idx}")
        else:
            body.append("\t\t\t\tOR = {")
            body.append(f"\t\t\t\t\tvar:cm_trmm_best_idx = {idx}")
            body.append(f"\t\t\t\t\tvar:cm_trmm_tie_idx = {idx}")
            body.append("\t\t\t\t\tAND = {")
            body.append("\t\t\t\t\t\tvar:cm_trmm_tie_idx = 10")
            body.append(f"\t\t\t\t\t\tcm_trmm_tier_{alias} = 0")
            body.append("\t\t\t\t\t}")
            body.append("\t\t\t\t}")
        body.append("\t\t\t}")
        body.append(f"\t\t\tvalue = {SEARCH_BEST_STRIPE}")
        body.append("\t\t}")
        body.append("\t\telse_if = {")
        body.append("\t\t\tlimit = {")
        body.append("\t\t\t\tis_land = yes")
        body.append("\t\t\t\thas_any_town_rights = yes")
        body.append("\t\t\t\tOR = {")
        for other in ROYAL_RIGHTS:
            if other == right:
                continue
            body.append(
                f"\t\t\t\t\thas_town_rights = town_rights_type:{other}")
        body.append("\t\t\t\t}")
        body.append("\t\t\t}")
        body.append(f"\t\t\tvalue = {SEARCH_OTHER_GRANTED_STRIPE}")
        body.append("\t\t}")
        body.append("\t\telse_if = {")
        body.append("\t\t\tlimit = {")
        body.append("\t\t\t\tis_land = yes")
        # Gate able-to-grant on the searched right fitting here, so open slots
        # with no coverage for it stay unpainted.
        body.append("\t\t\t\thas_variable = cm_trmm_best_idx")
        body.append(f"\t\t\t\tcm_trmm_right_{alias} > 0")
        body.append("\t\t\t\thas_max_town_rights = no")
        body.append("\t\t\t}")
        body.append(f"\t\t\tvalue = {OPEN_SLOT_STRIPE}")
        body.append("\t\t}")
        body.append("\t}")
        body.append("")
        for desc, key_color in (
                ("cm_trmm_search_legend_100", color),
                ("cm_trmm_search_legend_none", NO_MATCH_COLOR),
                ("cm_trmm_search_legend_granted", SEARCH_GRANTED_STRIPE),
                ("cm_trmm_search_legend_best", SEARCH_BEST_STRIPE),
                ("cm_trmm_search_legend_granted_other",
                 SEARCH_OTHER_GRANTED_STRIPE),
                ("cm_trmm_search_legend_open_slot", OPEN_SLOT_STRIPE)):
            body.append("\tlegend_key = {")
            body.append(f"\t\tdesc = \"{desc}\"")
            body.append(f"\t\tcolor = {key_color}")
            body.append("\t}")
        body.append("")
        body.append("\ttooltip_key = {")
        body.append("\t\tif = {")
        body.append("\t\t\tlimit = { is_land = no }")
        body.append("\t\t\tvalue = MAPMODE_CM_BEST_TOWN_RIGHT_TT_WATER")
        body.append("\t\t}")
        body.append("\t\telse_if = {")
        body.append("\t\t\tlimit = { NOT = { has_variable = cm_trmm_best_idx } }")
        body.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_NONE")
        body.append("\t\t}")
        body.append("\t\telse_if = {")
        body.append(f"\t\t\tlimit = {{ cm_trmm_right_{alias} <= 0 }}")
        body.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_NONE")
        body.append("\t\t}")
        body.append("\t\telse = {")
        body.append(f"\t\t\tvalue = MAPMODE_CM_TRMM_SEARCH_{upper}_TT_LAND")
        body.append("\t\t}")
        body.append("\t}")
        lines.append("")
        lines.extend(_hidden_mode_lines(f"cm_trmm_search_{alias}", body))
        lines.append("")
        lines.extend(_hidden_mode_lines(f"cm_trmm_search_{alias}_refresh", body))
    return "\n".join(lines) + "\n"


def emit_loc(rights, aliases, boosted_goods, options, self_goods):
    def why_line(good, accessor):
        line = (
            f"\\n  @{good}! [ShowGoodsName('{good}')]: "
            f"#TOOLTIP:CUSTOM,cm_trmm_why_{good},"
            f"PROVINCE=[{accessor}.GetProvince.GetID] "
            f"#L [{accessor}.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]#!#!")
        if good in self_goods:
            line += f"[{accessor}.Custom('cm_trmm_rgo_chip_{good}')]"
        return line

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
        f"\\n\\nBest industries:{ind_calls}"
        f"[ROOT.GetLocation.Custom('cm_trmm_stripe_reason')]\"")
    search_cores = {}
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        right_core = (
            f"@{right}! [ShowTownRightsName('{right}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_right_{alias}')|%1]")
        lines.append(f" cm_trmm_tt_line_{alias}: \"\\n{right_core}\"")
        sub_lines = "".join(
            why_line(good, "ROOT.GetLocation")
            for good in rights[right]["goods"])
        lines.append(f" cm_trmm_bd_line_{alias}: \"\\n{right_core}{sub_lines}\"")
        search_cores[alias] = right_core + sub_lines
    for good in boosted_goods:
        lines.append(
            f" cm_trmm_ind_line_{good}: \"\\n@{good}! [ShowGoodsName('{good}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]\"")
    lines.append(" cm_trmm_blank: \"\"")

    for good in boosted_goods:
        lines.append(
            f" cm_trmm_why_title_{good}: "
            f"\"@{good}! [ShowGoodsNameWithNoTooltip('{good}')]\"")
    via_buildings = sorted(
        {building
         for good in boosted_goods
         for _gi, _gate, _label, _fk, _mo, opts in why_groups(options, good)
         for _k, building, _pm, _rows in opts})
    for building in via_buildings:
        lines.append(
            f" cm_trmm_why_bld_{building}: "
            f"\"Building: [ShowBuildingTypeName('{building}')]\"")
    row_strings = {}
    for good in boosted_goods:
        structure = why_groups(options, good)
        for gi, gate, label_key, _fk, _mo, opts in structure:
            if label_key:
                suffix = "" if gi == 1 else f"_g{gi}"
                label = gate_label(gate) if gate else "Base methods:"
                if len(structure) > 1:
                    label += (" [Province.MakeScope.ScriptValue("
                              f"'cm_trmm_covv_{good}{suffix}')|%1]")
                lines.append(f" {label_key}: \"{label}\"")
            for _k, _building, _pm, rows in opts:
                for kind, input_good, permille in rows:
                    pct = chip_pct(permille)
                    name = (f"@{input_good}! "
                            f"[ShowGoodsNameWithNoTooltip('{input_good}')]")
                    if kind == "raw":
                        row_strings[row_key("y", input_good, permille)] = (
                            f"  {name}: {pct} #G in the province#!")
                        row_strings[row_key("n", input_good, permille)] = (
                            f"  {name}: {pct} #R missing#!")
                    else:
                        row_strings[row_key("o", input_good, permille)] = (
                            f"  {name}: {pct} #V from other industries#!")
    for key in sorted(row_strings):
        lines.append(f" {key}: \"{row_strings[key]}\"")
    pm_keys = sorted({option["pm"]
                      for good in boosted_goods
                      for option in options[good]})
    for pm in pm_keys:
        lines.append(
            f" cm_trmm_why_pm_{pm}: "
            f"\"Using [ShowProductionMethodName('{pm}')]:\"")
    for good in sorted(self_goods):
        lines.append(f" cm_trmm_chip_rgo_{good}: \" #G @{good}! RGO here#!\"")
    lines.append(
        " cm_trmm_why_rgo_note: \"Where this good is the location's own RGO, "
        "the RGO counts as one more fully covered source.\"")

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
            why_line(good, "Location")
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
        alias = aliases[right]
        granted_part = f"@{right}! [ShowTownRightsName('{right}')] granted"
        lines.append(
            f" cm_trmm_reason_best_{alias}: "
            f"\"\\n\\n{granted_part} - best urban right for this location\"")
        lines.append(
            f" cm_trmm_reason_granted_{alias}: \"\\n\\n{granted_part}\"")
        lines.append(
            f" cm_trmm_reason_missed_{alias}: "
            f"\"\\n\\n{granted_part} - "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_granted_miss')|%1] "
            f"short of the best fit here\"")
    for pos, right in enumerate(ROYAL_RIGHTS[:-1]):
        alias = aliases[right]
        tied_items = "".join(
            f"[ROOT.GetLocation.Custom('cm_trmm_tie_item_{alias}_{aliases[other]}')]"
            for other in ROYAL_RIGHTS[pos + 1:])
        lines.append(
            f" cm_trmm_reason_tied_{alias}: \"\\n\\nTied with: {tied_items}\"")
    reason_items = "".join(
        f"[ROOT.GetLocation.Custom('cm_uright_assigned_item_{aliases[right]}')]"
        for right in ROYAL_RIGHTS)
    lines.append(
        f" cm_trmm_search_other_line: "
        f"\"\\nOther specializations granted here:{reason_items}\"")

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
        if right == "royal_naval_rights":
            # "Naval Supplies Rights" overflows the search strip covering the banner.
            lines.append(
                f" mapmode_cm_trmm_search_{alias}_name: "
                f"\"Urban Right Search: Naval Rights\"")
        else:
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
            f"best specialization option.\\nBoosted industries: {boosted}"
            f"\\nHover an industry's percentage in the tooltip for its input "
            f"breakdown.\"")
        tie_items = "".join(
            f"[ROOT.GetLocation.Custom('cm_trmm_tie_item_{alias}_{aliases[other]}')]"
            for other in ROYAL_RIGHTS if other != right)
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}_TT_LAND: \"{search_cores[alias]}"
            f"\\nRank among specializations here: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_tierdisp_{alias}')|0] "
            f"of [ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_tier_total')|0]"
            f"[ROOT.GetLocation.Custom('cm_trmm_tie_prefix_{alias}')]{tie_items}"
            f"[ROOT.GetLocation.Custom('cm_trmm_search_reason_{alias}')]\"")
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}_TT_NONE: "
            f"\"[ROOT.GetLocation.GetProvince.GetName] has no [raw_material|e] "
            f"used by the industries @{right}! [ShowTownRightsName('{right}')] "
            f"boosts.[ROOT.GetLocation.Custom('cm_trmm_search_reason_{alias}')]\"")

    # The engine looks up mapmode_<name>_name and MAPMODE_<name> for hidden twins
    # too; alias each to its primary.
    lines.append(
        " mapmode_cm_best_town_right_refresh_name: "
        "\"$mapmode_cm_best_town_right_name$\"")
    lines.append(
        " MAPMODE_CM_BEST_TOWN_RIGHT_REFRESH: "
        "\"$MAPMODE_CM_BEST_TOWN_RIGHT$\"")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        upper = alias.upper()
        lines.append(
            f" mapmode_cm_trmm_search_{alias}_refresh_name: "
            f"\"$mapmode_cm_trmm_search_{alias}_name$\"")
        lines.append(
            f" MAPMODE_CM_TRMM_SEARCH_{upper}_REFRESH: "
            f"\"$MAPMODE_CM_TRMM_SEARCH_{upper}$\"")

    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        lines.append(
            f" cm_trmm_grant_title_{alias}: "
            f"\"Grant [ShowTownRightsName('{right}')]\"")
        lines.append(
            f" cm_trmm_grant_tt_{alias}: "
            f"\"Match: [Location.MakeScope.ScriptValue('cm_trmm_rightv_{alias}')|%1]\"")
        lines.append(
            f" cm_trmm_grant_expand_title_{alias}: "
            f"\"Grant [ShowTownRightsName('{right}')] and enable matching "
            f"auto-expands\"")
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
    lines.append("")
    lines.append(
        "# Map-tooltip grant row scripted GUIs, one per right. Root is the\n"
        "# location; cm_country is the clicking player's country.")
    for right in ROYAL_RIGHTS:
        lines.append(f"cm_trmm_grant_{aliases[right]} = {{")
        lines.append("\tsaved_scopes = { cm_country }")
        lines.append("\tis_shown = {")
        lines.append("\t\texists = owner")
        lines.append("\t\towner = scope:cm_country")
        lines.append(f"\t\tNOT = {{ has_town_rights = town_rights_type:{right} }}")
        lines.append("\t\tNOT = { integration_level = conquered }")
        lines.append("\t}")
        lines.append("\tis_valid = {")
        lines.append(f"\t\tcm_trmm_can_grant_right = {{ RIGHT = {right} }}")
        lines.append("\t}")
        lines.append("\teffect = {")
        lines.append(f"\t\tcm_trmm_grant_right = {{ RIGHT = {right} }}")
        lines.append("\t}")
        lines.append("}")
    lines.append("")
    lines.append(
        "# Right-click grant scripted GUIs: grant plus enable matching\n"
        "# auto-expands. Root is the location; cm_country is the clicking\n"
        "# player's country.")
    for right in ROYAL_RIGHTS:
        lines.append(f"cm_trmm_grant_expand_{aliases[right]} = {{")
        lines.append("\tsaved_scopes = { cm_country }")
        lines.append("\tis_valid = {")
        lines.append(f"\t\tcm_trmm_can_grant_right = {{ RIGHT = {right} }}")
        lines.append("\t}")
        lines.append("\teffect = {")
        lines.append(f"\t\tcm_trmm_grant_right = {{ RIGHT = {right} }}")
        lines.append(f"\t\tcm_trmm_enable_expands_{aliases[right]} = yes")
        lines.append("\t}")
        lines.append("}")
    return "\n".join(lines) + "\n"


def emit_grant_section(aliases):
    root = ("GuiScope.SetRoot(Location.MakeScope)"
            ".AddScope('cm_country', GetPlayer.MakeScope).End")
    player = "GuiScope.SetRoot(GetPlayer.MakeScope).End"
    loc = "GuiScope.SetRoot(Location.MakeScope).End"
    best_pair = ("Or(GetMapMode('cm_best_town_right').IsActive, "
                 "GetMapMode('cm_best_town_right_refresh').IsActive)")
    # Per-mode "a grant button would show" terms: the searched right's IsShown in
    # its search mode; in the best mode the open slot the section already
    # requires guarantees an ungranted right, so the mode being active is enough.
    shown_terms = [best_pair]
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        shown_terms.append(
            f"And(Or(GetMapMode('cm_trmm_search_{alias}').IsActive, "
            f"GetMapMode('cm_trmm_search_{alias}_refresh').IsActive), "
            f"GetScriptedGui('cm_trmm_grant_{alias}').IsShown({root}))")
    any_shown = (f"Or(Or5({', '.join(shown_terms[:5])}), "
                 f"Or5({', '.join(shown_terms[5:])}))")

    lines = [GENERATED_HEADER]
    lines.append("types cm_town_right_map_mode_grant_types {")
    lines.append("\t# Grant row for the recommended urban rights map tooltip; spliced into the")
    lines.append("\t# location_tooltip_alt redefinition in cm_town_rights_tooltip_types.gui.")
    lines.append("\ttype cm_trmm_grant_section = hbox {")
    lines.append("\t\tspacing = 4")
    lines.append("\t\tmargin = { 10 4 }")
    lines.append(
        f"\t\tvisible = \"[And3(ObjectsEqual(Location.GetOwner, GetPlayer), "
        f"GetScriptedGui('cm_trmm_grant_location_eligible')"
        f".IsShown({loc}), {any_shown})]\"")
    lines.append("")
    for key, negate in (("CM_TRMM_GRANT_LABEL", True),
                        ("CM_TRMM_GRANT_LABEL_FREE", False)):
        free = f"GetScriptedGui('cm_trmm_grant_is_free').IsShown({player})"
        lines.append("\t\ttext_single = {")
        lines.append("\t\t\tautoresize = yes")
        # Stretched to the row height with vcenter so the label lines up with the buttons.
        lines.append("\t\t\tlayoutpolicy_vertical = expanding")
        lines.append("\t\t\talign = left|vcenter")
        lines.append(f"\t\t\ttext = \"{key}\"")
        lines.append(
            f"\t\t\tvisible = \"[{f'Not({free})' if negate else free}]\"")
        lines.append("\t\t}")
    # One copy of each right's button per slot position; the cm_trmm_grant_slot
    # values order the visible buttons best-first.
    for slot in range(len(ROYAL_RIGHTS)):
        for right in ROYAL_RIGHTS:
            alias = aliases[right]
            gui = f"GetScriptedGui('cm_trmm_grant_{alias}')"
            gui_expand = f"GetScriptedGui('cm_trmm_grant_expand_{alias}')"
            search_pair = (f"Or(GetMapMode('cm_trmm_search_{alias}').IsActive, "
                           f"GetMapMode('cm_trmm_search_{alias}_refresh').IsActive)")
            slot_eq = (f"EqualTo_CFixedPoint(Location.MakeScope.ScriptValue("
                       f"'cm_trmm_grant_slot_{alias}'), '(CFixedPoint){slot}')")
            lines.append("")
            lines.append("\t\tbutton_townrights = {")
            lines.append("\t\t\tsize = { 34 30 }")
            lines.append(f"\t\t\ttext = \"cm_uright_icon_line_{alias}\"")
            lines.append(
                f"\t\t\tvisible = \"[And3({slot_eq}, Or({search_pair}, "
                f"{best_pair}), {gui}.IsShown({root}))]\"")
            lines.append(f"\t\t\tenabled = \"[{gui}.IsValid({root})]\"")
            lines.append("")
            lines.append("\t\t\ttooltipwidget = {")
            lines.append("\t\t\t\tContextualTooltipType = {")
            lines.append("\t\t\t\t\tblockoverride \"title_icon\" {")
            lines.append("\t\t\t\t\t\ticon = {")
            lines.append("\t\t\t\t\t\t\tusing = tooltip_title_icon_size")
            lines.append(
                "\t\t\t\t\t\t\ttexture = \"[GetConceptTexture('town_rights')]\"")
            lines.append("\t\t\t\t\t\t}")
            lines.append("\t\t\t\t\t}")
            lines.append("\t\t\t\t\tblockoverride \"concept_link\" {")
            lines.append("\t\t\t\t\t\ttext = \"[town_rights|e]\"")
            lines.append("\t\t\t\t\t}")
            lines.append("\t\t\t\t\tblockoverride \"title_text\" {")
            lines.append(
                f"\t\t\t\t\t\ttext = \"[ShowTownRightsNameWithNoTooltip('{right}')]\"")
            lines.append("\t\t\t\t\t}")
            lines.append("\t\t\t\t\tblockoverride \"tooltip_content\" {")
            lines.append("\t\t\t\t\t\tTooltipStringPairList = {")
            lines.append(f"\t\t\t\t\t\t\ttextcontext = \"cm_trmm_grant_tt_{alias}\"")
            lines.append("\t\t\t\t\t\t}")
            lines.append("\t\t\t\t\t}")
            lines.append("\t\t\t\t}")
            lines.append("\t\t\t}")
            lines.append("")
            lines.append("\t\t\taction_tooltip = {")
            lines.append("\t\t\t\tclick_type = left")
            lines.append("\t\t\t\tclick_mode = single")
            lines.append(f"\t\t\t\ttitle = \"cm_trmm_grant_title_{alias}\"")
            lines.append(
                "\t\t\t\tconditions = \"[AddLocalizationIf("
                "Not(ShowGrantLocationTownRights(Location.Self)), 'TWR_NOT_CAPABLE')]\"")
            lines.append(
                f"\t\t\t\tconditions = \"[AddLocalizationIf(Not(GetScriptedGui("
                f"'cm_trmm_grant_can_afford').IsShown({player})), 'CM_TRMM_GRANT_CANT_AFFORD')]\"")
            lines.append(
                f"\t\t\t\tconditions = \"[AddLocalizationIf(And3("
                f"ShowGrantLocationTownRights(Location.Self), "
                f"GetScriptedGui('cm_trmm_grant_can_afford').IsShown({player}), "
                f"Not({gui}.IsValid({root}))), 'CM_TRMM_GRANT_REQUIREMENTS')]\"")
            lines.append(f"\t\t\t\tenabled = \"[{gui}.IsValid({root})]\"")
            lines.append(f"\t\t\t\ton_action = \"[{gui}.Execute({root})]\"")
            lines.append("\t\t\t}")
            lines.append("")
            lines.append("\t\t\taction_tooltip = {")
            lines.append("\t\t\t\tclick_type = right")
            lines.append("\t\t\t\tclick_mode = single")
            lines.append(
                f"\t\t\t\ttitle = \"cm_trmm_grant_expand_title_{alias}\"")
            lines.append(f"\t\t\t\tenabled = \"[{gui_expand}.IsValid({root})]\"")
            lines.append(
                f"\t\t\t\ton_action = \"[{gui_expand}.Execute({root})]\"")
            lines.append("\t\t\t}")
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
    expand_bases = collect_expand_bases(buildings, rights)
    for right in ROYAL_RIGHTS:
        if not expand_bases[right]:
            sys.exit(f"No auto-expand base buildings found for {right}")

    relevant = sorted({g for opts in options.values()
                       for opt in opts for g in opt["shares"]})

    aliases = {right: short_alias(right) for right in ROYAL_RIGHTS}
    if len(set(aliases.values())) != len(aliases):
        sys.exit("Right alias collision")

    self_goods = {good for good in boosted_goods
                  if goods_categories.get(good) == "raw_material"}
    rgo_goods = {right: sorted(self_goods & set(rights[right]["goods"]))
                 for right in ROYAL_RIGHTS}

    right_colors = resolve_right_colors(rights, game_dir)

    write_output(OUT_SCRIPT_VALUES,
                 emit_script_values(rights, options, aliases, boosted_goods,
                                    self_goods, relevant))
    write_output(OUT_TRIGGERS, emit_triggers(relevant))
    write_output(OUT_EFFECTS,
                 emit_effects(options, aliases, boosted_goods, relevant,
                              expand_bases, rgo_goods))
    write_output(OUT_CUSTOM_LOC,
                 emit_custom_loc(aliases, boosted_goods, self_goods))
    write_output(OUT_CUSTOM_COOLTIP,
                 emit_custom_cooltip(options, boosted_goods, self_goods))
    write_output(OUT_MAP_MODE,
                 emit_map_mode(rights, aliases, right_colors) + "\n"
                 + emit_search_map_modes(rights, aliases, right_colors))
    write_output(OUT_LOC,
                 emit_loc(rights, aliases, boosted_goods, options, self_goods))
    write_output(OUT_SCRIPTED_GUIS, emit_scripted_guis(aliases))
    write_output(OUT_GRANT_SECTION, emit_grant_section(aliases))

    for path in (OUT_SCRIPT_VALUES, OUT_TRIGGERS, OUT_EFFECTS, OUT_CUSTOM_LOC,
                 OUT_CUSTOM_COOLTIP, OUT_MAP_MODE, OUT_LOC, OUT_SCRIPTED_GUIS,
                 OUT_GRANT_SECTION):
        print(f"Wrote {os.path.relpath(path, ROOT_DIR).replace(os.sep, '/')}")
    for right in ROYAL_RIGHTS:
        goods_list = ", ".join(
            f"{g} ({len(options[g])} option{'s' if len(options[g]) != 1 else ''})"
            for g in rights[right]["goods"])
        print(f"  {right}: {goods_list}")
    for right in ROYAL_RIGHTS:
        parts = [building for building, _gate in expand_bases[right]]
        parts += [f"{good} RGO" for good in rgo_goods[right]]
        print(f"  grant-expand {right}: {', '.join(parts)}")
    print(f"  relevant raw materials: {', '.join(relevant)}")
    print(f"  RGO-self boosted goods: {', '.join(sorted(self_goods))}")
    total_rows = 0
    for good in boosted_goods:
        for _gi, _gate, _label, _fk, _mo, opts in why_groups(options, good):
            for _k, _building, _pm, rows in opts:
                total_rows += len(rows)
    print(f"  breakdown sub-tooltips: {len(boosted_goods)} containers, "
          f"{total_rows} input rows")
    for good in boosted_goods:
        for gate, _opts in group_options(options[good]):
            if gate and gate not in GATE_LABELS:
                print(f"  WARNING: no GATE_LABELS entry for {good} gate "
                      f"[{gate}]; using the generic fallback header")
    if excluded:
        print(f"  gated content excluded ({len(excluded)}):")
        for entry in excluded:
            print(f"    {entry}")
    else:
        print("  gated content excluded: none")
    gated = []
    for good in boosted_goods:
        for gate, opts in group_options(options[good]):
            if gate:
                names = "/".join(sorted({o["building"] for o in opts}))
                gated.append(f"{good}: {names} options only count where "
                             f"[{gate}] holds at the location")
    if gated:
        print(f"  location-gated option groups ({len(gated)}):")
        for entry in gated:
            print(f"    {entry}")
    for warning in sorted(set(requires_warnings)):
        print(f"  WARNING: {warning}")
    if dangling:
        rel_pm = PRODUCTION_METHODS_SUBDIR.replace(os.sep, "/")
        print("  WARNING: possible_production_methods entries with no definition "
              f"in {rel_pm}, skipped: {', '.join(sorted(dangling))}")


if __name__ == "__main__":
    main()
