#!/usr/bin/env python3
"""Generate the Best Urban Right map mode from vanilla data.

Scores each royal specialization town right per province by how much of its
boosted buildings' input goods the province supplies as raw materials, then
emits the map mode that colors provinces by the best right and the tooltip
machinery that ranks every option.

Reads vanilla town_rights (which goods each right boosts, right colors),
building_types (production methods: inputs, produced good, output), goods
(raw_material vs produced category), and named_colors. Emits:

  in_game/common/script_values/cm_town_right_map_mode_script_values.txt
  in_game/common/scripted_triggers/cm_town_right_map_mode_triggers.txt
  in_game/common/scripted_effects/cm_town_right_map_mode_effects.txt
  in_game/common/customizable_localization/cm_town_right_map_mode_custom_loc.txt
  in_game/gfx/map/map_modes/cm_town_right_map_mode.txt
  main_menu/localization/english/cm_town_right_map_mode_l_english.yml

The scoring math runs once per lobby: cm_trmm_recompute_all sweeps every
province, stores each qualifying province's industry coverages as province
variables, and stores each location's best right index as a location variable.
A boosted good that is itself a raw material (dyes, wine) gets the RGO averaged
in as one more fully covered producer on the RGO's own location, since the
right's output modifier boosts that RGO too - so scores differ per location
there. The map mode and tooltip only read stored variables plus that one
raw-material check.

Scoring model:
  - A building's main production slot is its unique_production_methods block
    with the highest output; other blocks are enhancement slots and are ignored.
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
GOODS_SUBDIR = os.path.join("in_game", "common", "goods")
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
NO_MATCH_COLOR = "rgb { 90 90 90 }"

OUTPUT_MODIFIER = re.compile(r"^local_([a-z0-9_]+)_output_modifier$")
ASSIGN_BLOCK = re.compile(r"([A-Za-z_][A-Za-z0-9_.:]*)\s*=\s*\{")
ASSIGN_SCALAR = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.:]*)\s*=\s*([^\s{}]+)\s*$", re.MULTILINE)

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


def parse_buildings(building_types_dir, goods_categories):
    """Return [(building, file, line, [slot, ...])] where each slot is a list
    of PM dicts: {name, line, inputs {good: amount}, produced, output}."""
    buildings = []
    for name, path in iter_db_files(building_types_dir):
        rel = os.path.join(BUILDING_TYPES_SUBDIR, name).replace(os.sep, "/")
        text = read_pdx(path)
        for building, body, b_line in child_blocks(text):
            slots = []
            for child, inner, c_line in child_blocks(body):
                if child != "unique_production_methods":
                    continue
                slot = []
                for pm, pm_body, pm_line in child_blocks(inner):
                    produced = None
                    output = None
                    inputs = {}
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
                    slot.append({
                        "name": pm,
                        "line": b_line + c_line + pm_line - 2,
                        "inputs": inputs,
                        "produced": produced,
                        "output": output,
                    })
                if slot:
                    slots.append(slot)
            if slots:
                buildings.append((building, rel, b_line, slots))
    return buildings


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
    for building, rel, _b_line, slots in buildings:
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
                "comment": (f"{pm['name']} ({building}), {rel}:{pm['line']} - "
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
    return f"any_location_in_province = {{ raw_material = goods:{good} }}"


def emit_script_values(rights, options, aliases, boosted_goods, self_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Province-scoped option values consumed by cm_trmm_recompute_province (each\n"
        "# is one worth-using production method's locally-available input share), plus\n"
        "# the location-scoped readers of the stored province variables that the map\n"
        "# mode tooltip machinery uses. Readers are only evaluated on locations the\n"
        "# recompute pass marked (has_variable cm_trmm_best_idx).\n")

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

    return "\n".join(lines) + "\n"


def emit_triggers(relevant_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Province trigger. True when the province produces any raw material consumed\n"
        "# by a worth-using production method of a building a royal specialization town\n"
        "# right boosts. Gates the recompute pass.")
    lines.append("cm_trmm_province_has_any_input = {")
    lines.append("\tany_location_in_province = {")
    lines.append("\t\tOR = {")
    for good in relevant_goods:
        lines.append(f"\t\t\traw_material = goods:{good}")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_effects(options, aliases, boosted_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Once-per-lobby precompute: stores every qualifying province's industry\n"
        "# coverages as province variables and each of its locations' best right index\n"
        "# as a location variable, so the map mode and tooltip only read stored values.\n")
    lines.append("# Province scope.")
    lines.append("cm_trmm_recompute_province = {")
    lines.append("\tif = {")
    lines.append("\t\tlimit = { cm_trmm_province_has_any_input = yes }")
    for good in boosted_goods:
        lines.append("\t\tset_variable = {")
        lines.append(f"\t\t\tname = cm_trmm_cov_{good}")
        lines.append("\t\t\tvalue = {")
        lines.append("\t\t\t\tvalue = 0")
        for k in range(1, len(options[good]) + 1):
            lines.append(f"\t\t\t\tmin = cm_trmm_opt_{good}_{k}")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
    lines.append(
        "\t\t# Best right per location, encoded as round(score * 1000) * 10 + index so\n"
        "\t\t# the chained min = (raise-to-at-least) running maximum keeps both the score\n"
        "\t\t# and which right holds it. Higher index wins score ties, so index order is\n"
        "\t\t# the reverse of the priority order.")
    lines.append("\t\tevery_location_in_province = {")
    lines.append("\t\t\tset_local_variable = {")
    lines.append("\t\t\t\tname = cm_trmm_enc")
    lines.append("\t\t\t\tvalue = {")
    lines.append("\t\t\t\t\tvalue = 0")
    for right in ROYAL_RIGHTS:
        lines.append(f"\t\t\t\t\tmin = cm_trmm_enc_{aliases[right]}")
    lines.append("\t\t\t\t}")
    lines.append("\t\t\t}")
    lines.append("\t\t\tset_variable = {")
    lines.append("\t\t\t\tname = cm_trmm_best_idx")
    lines.append("\t\t\t\tvalue = {")
    lines.append("\t\t\t\t\tvalue = local_var:cm_trmm_enc")
    lines.append("\t\t\t\t\tmodulo = 10")
    lines.append("\t\t\t\t}")
    lines.append("\t\t\t}")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("\telse = {")
    lines.append("\t\tevery_location_in_province = {")
    lines.append("\t\t\tlimit = { has_variable = cm_trmm_best_idx }")
    lines.append("\t\t\tremove_variable = cm_trmm_best_idx")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("}")
    lines.append("")
    lines.append("cm_trmm_recompute_all = {")
    lines.append("\tevery_province_definition = {")
    lines.append("\t\tevery_province_in_province_definition = {")
    lines.append("\t\t\tcm_trmm_recompute_province = yes")
    lines.append("\t\t}")
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_custom_loc(aliases, boosted_goods):
    lines = [GENERATED_HEADER]
    lines.append(
        "# Slot entries resolve rank k to a line, or to nothing: cm_trmm_slot_* for the\n"
        "# ranked rights list, cm_trmm_bd_slot_* for the same ranking with per-industry\n"
        "# sub-lines, cm_trmm_ind_slot_* for the industries ranked by coverage.")
    for prefix, line_prefix in (("cm_trmm_slot", "cm_trmm_tt_line"),
                                ("cm_trmm_bd_slot", "cm_trmm_bd_line")):
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
    for slot in range(1, len(boosted_goods) + 1):
        lines.append(f"cm_trmm_ind_slot_{slot} = {{")
        lines.append("\ttype = location")
        for good in boosted_goods:
            lines.append("\ttext = {")
            lines.append("\t\ttrigger = {")
            lines.append(f"\t\t\tcm_trmm_ind_rank_{good} = {slot - 1}")
            lines.append(f"\t\t\tcm_trmm_cov_{good} > 0")
            lines.append("\t\t}")
            lines.append(f"\t\tlocalization_key = cm_trmm_ind_line_{good}")
            lines.append("\t}")
        lines.append("\ttext = {")
        lines.append("\t\tlocalization_key = cm_trmm_blank")
        lines.append("\t\tfallback = yes")
        lines.append("\t}")
        lines.append("}")
    return "\n".join(lines) + "\n"


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
    lines.append("""
	small_map_names = raw_material
	medium_map_names = raw_material
	large_map_names = market

	small_tooltip_context = location
	medium_tooltip_context = location
	large_tooltip_context = market

	fill_in_impassable = yes
	enable_snow = no

	flatmap_behaviour = always
	use_fow = no

	category = economy
	index = 1

	map_markers = {
		fort_marker = no
		port_marker = no
		unit_marker = no
		combat_marker = no
		combat_imminent_marker = no
		supply_depot_marker = no
		market_marker = yes
		toll_marker = no
		dynasty_marker = no
		raw_goods_marker = yes
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

	refresh_colors_on_selection_change = no
}""")
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
        " MAPMODE_CM_BEST_TOWN_RIGHT_TT_LAND: \"#T [ROOT.GetLocation.GetProvince.GetName]#!"
        f"\\nSpecialization options, best to worst:{slot_calls}"
        f"\\n\\nBreakdown:{bd_calls}"
        f"\\n\\nBest industries:{ind_calls}\"")
    for right in ROYAL_RIGHTS:
        alias = aliases[right]
        right_line = (
            f"\\n@{right}! [ShowTownRightsName('{right}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_right_{alias}')|%1]")
        lines.append(f" cm_trmm_tt_line_{alias}: \"{right_line}\"")
        sub_lines = "".join(
            f"\\n  @{good}! [ShowGoodsName('{good}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]"
            for good in rights[right]["goods"])
        lines.append(f" cm_trmm_bd_line_{alias}: \"{right_line}{sub_lines}\"")
    for good in boosted_goods:
        lines.append(
            f" cm_trmm_ind_line_{good}: \"\\n@{good}! [ShowGoodsName('{good}')]: "
            f"[ROOT.GetLocation.MakeScope.ScriptValue('cm_trmm_cov_{good}')|%1]\"")
    lines.append(" cm_trmm_blank: \"\"")
    for right in ROYAL_RIGHTS:
        lines.append(
            f" cm_trmm_legend_{aliases[right]}: \"@{right}! [ShowTownRightsName('{right}')]\"")
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

    buildings = parse_buildings(
        os.path.join(game_dir, BUILDING_TYPES_SUBDIR), goods_categories)
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
    write_output(OUT_MAP_MODE, emit_map_mode(rights, aliases, right_colors))
    write_output(OUT_LOC, emit_loc(rights, aliases, boosted_goods))

    for path in (OUT_SCRIPT_VALUES, OUT_TRIGGERS, OUT_EFFECTS, OUT_CUSTOM_LOC,
                 OUT_MAP_MODE, OUT_LOC):
        print(f"Wrote {os.path.relpath(path, ROOT_DIR).replace(os.sep, '/')}")
    for right in ROYAL_RIGHTS:
        goods_list = ", ".join(
            f"{g} ({len(options[g])} option{'s' if len(options[g]) != 1 else ''})"
            for g in rights[right]["goods"])
        print(f"  {right}: {goods_list}")
    print(f"  relevant raw materials: {', '.join(relevant)}")
    print(f"  RGO-self boosted goods: {', '.join(sorted(self_goods))}")


if __name__ == "__main__":
    main()
