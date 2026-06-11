#!/usr/bin/env python3
"""Steal GlorpUI's vanilla-GUI override files that carry cm_ buttons into this mod.

GlorpUI is the authoring baseline for cm_ button placement inside vanilla GUI files.
This mod's own cm_ widget, type, and template definitions are not touched.
"""
import argparse
import sys
from pathlib import Path

# GlorpUI source -> this mod destination, relative to each mod root.
FILE_MAP = {
    "in_game/gui/location_window.gui": "in_game/gui/location_window.gui",
    "in_game/gui/production_lateralview.gui": "in_game/gui/production_lateralview.gui",
    "in_game/gui/glorpUI_build_location_lateralview.gui": "in_game/gui/cm_build_location_lateralview.gui",
    "in_game/gui/glorpUI_food_production_lateralview.gui": "in_game/gui/cm_food_production_lateralview.gui",
    "in_game/gui/expand_raw_goods_lateralview.gui": "in_game/gui/expand_raw_goods_lateralview.gui",
    "in_game/gui/glorpUI_shared_types.gui": "in_game/gui/cm_glorp_synced_types.gui",
    "in_game/common/scripted_guis/glorpui_construction_manager_scripted_gui.txt":
        "in_game/common/scripted_guis/glorpui_construction_manager_scripted_gui.txt",
    "in_game/common/scripted_guis/glorpui_build_location_scripted_gui.txt":
        "in_game/common/scripted_guis/glorpui_build_location_scripted_gui.txt",
    "in_game/common/script_values/glorpui_rgo_script_values.txt":
        "in_game/common/script_values/glorpui_rgo_script_values.txt",
    "main_menu/localization/english/glorpui_shared_l_english.yml":
        "main_menu/localization/english/glorpui_shared_l_english.yml",
}

BOM = b"\xef\xbb\xbf"


def to_bom_lf(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return BOM + text.encode("utf-8")


def main():
    script_dir = Path(__file__).resolve().parent
    cm_root = script_dir.parent
    default_glorp = cm_root.parent / "EU5.Glorp.UI"

    parser = argparse.ArgumentParser(
        description="Steal GlorpUI's GUI override files into this mod."
    )
    parser.add_argument(
        "--glorp-root", type=Path, default=default_glorp,
        help="GlorpUI mod root; defaults to the sibling EU5.Glorp.UI folder",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list the source -> destination mapping without writing",
    )
    args = parser.parse_args()

    glorp_root = args.glorp_root.resolve()
    if not glorp_root.is_dir():
        parser.error(f"GlorpUI not found at '{glorp_root}'. Pass --glorp-root <path>.")

    stolen = 0
    missing = []
    for src_rel, dst_rel in FILE_MAP.items():
        src_path = glorp_root / src_rel
        dst_path = cm_root / dst_rel
        if not src_path.is_file():
            missing.append(src_rel)
            print(f"WARNING missing source: {src_rel}")
            continue
        if args.dry_run:
            print(f"[dry-run] {src_rel} -> {dst_rel}")
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_bytes(to_bom_lf(src_path.read_text(encoding="utf-8-sig")))
            print(f"stole   {src_rel} -> {dst_rel}")
        stolen += 1

    verb = "would steal" if args.dry_run else "stole"
    print(f"\n{verb} {stolen} file(s).")
    if missing:
        print(f"{len(missing)} source(s) missing; nothing stolen for those.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
