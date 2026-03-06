#!/usr/bin/env ruby
# frozen_string_literal: true

require "pathname"
require "bigdecimal"

GAME_ROOT_DEFAULT = Pathname.new("/Volumes/Europa Universalis V/game")
REPO_ROOT_DEFAULT = Pathname.new(__dir__).parent
TRIGGER_OUTPUT_PATH_DEFAULT = REPO_ROOT_DEFAULT.join("in_game/common/scripted_triggers/lsq_can_afford_building.txt")
SCRIPT_VALUES_OUTPUT_PATH_DEFAULT = REPO_ROOT_DEFAULT.join("in_game/common/script_values/lsq_construction_cost_adjustments_script_values.txt")
RGO_SCRIPT_VALUES_OUTPUT_PATH_DEFAULT = REPO_ROOT_DEFAULT.join("in_game/common/script_values/lsq_rgo_construction_cost_adjustments_script_values.txt")
RGO_TYPES = %w[mining farming hunting gathering forestry].freeze

IDENTIFIER_RE = /[A-Za-z0-9_:\.\-]+/
NUMERIC_RE = /\A-?\d+(?:\.\d+)?\z/

def read_text(path)
  File.read(path, encoding: "bom|utf-8")
rescue Errno::ENOENT
  warn "Missing file: #{path}"
  ""
end

def strip_comments(line)
  line.sub(/#.*/, "")
end

def parse_top_level_blocks(text)
  sanitized = text.each_line.map { |line| strip_comments(line) }.join
  blocks = {}
  i = 0
  n = sanitized.length

  while i < n
    if (match = /\G\s*(#{IDENTIFIER_RE.source})\s*=\s*\{/m.match(sanitized, i))
      block_id = match[1]
      brace_index = match.end(0) - 1
      depth = 1
      j = brace_index + 1

      while j < n && depth.positive?
        ch = sanitized[j]
        depth += 1 if ch == "{"
        depth -= 1 if ch == "}"
        j += 1
      end

      if depth.zero?
        blocks[block_id] = sanitized[(brace_index + 1)...(j - 1)]
        i = j
        next
      end

      warn "Unclosed block for '#{block_id}'"
      break
    end

    i += 1
  end

  blocks
end

def parse_root_assignments(block_text)
  assignments = {}
  depth = 0

  block_text.each_line do |raw_line|
    line = strip_comments(raw_line).strip
    next if line.empty?

    if depth.zero? && (m = /\A(#{IDENTIFIER_RE.source})\s*=\s*([^\{\}\r\n]+?)\s*\z/.match(line))
      assignments[m[1]] = m[2].strip
    end

    depth += line.count("{")
    depth -= line.count("}")
  end

  assignments
end

def parse_building_type_profiles(game_root)
  result = {}
  dir = game_root.join("in_game/common/building_types")

  Dir.glob(dir.join("*.txt").to_s).sort.each do |path|
    blocks = parse_top_level_blocks(read_text(path))
    blocks.each do |building_type, body|
      attrs = parse_root_assignments(body)
      result[building_type] = attrs["construction_demand"]
    end
  end

  result
end

def parse_demand_profiles(game_root)
  result = {}
  demand_paths = [
    game_root.join("in_game/common/goods_demand/building_construction_costs.txt"),
    game_root.join("in_game/common/goods_demand/special_construction_demands.txt")
  ]

  demand_paths.each do |path|
    blocks = parse_top_level_blocks(read_text(path))
    blocks.each do |profile_id, body|
      attrs = parse_root_assignments(body)
      next unless attrs["category"] == "building_construction"

      goods = {}
      attrs.each do |key, value|
        next if key == "category"
        goods[key] = value if NUMERIC_RE.match?(value)
      end

      result[profile_id] = goods
    end
  end

  result
end

def build_trigger_text(building_type, goods)
  lines = []
  lines << "# Root is the location"
  lines << "lsq_can_afford_#{building_type} = {"
  lines << "\tAND = {"

  if goods.empty?
    lines << "\t\talways = yes"
  else
    goods.sort_by { |good, _| good }.each do |good, amount|
      lines << "\t\tgoods:#{good} = { save_temporary_scope_as = good }"
      lines << "\t\tlsq_get_max_required_construction_demand_to_still_allow_building >= #{amount}"
    end
  end

  lines << "\t}"
  lines << "}"
  lines.join("\n")
end

def build_switch_trigger_text(building_ids)
  lines = []
  lines << "# Root is the building type, also expects scope:location"
  lines << "lsq_can_afford_switch_based_on_building_type = {"

  if building_ids.empty?
    lines << "\talways = no"
  else
    building_ids.each_with_index do |building_type, index|
      branch = index.zero? ? "trigger_if" : "trigger_else_if"
      lines << "\t#{branch} = { limit = { $target$ = building_type:#{building_type} }"
      lines << "\t\tlsq_can_afford_#{building_type} = yes"
      lines << "\t}"
    end
    lines << "\ttrigger_else = {"
    lines << "\t\talways = no"
    lines << "\t}"
  end

  lines << "}"
  lines.join("\n")
end

def build_cost_adjustment_script_value_text(building_type, goods)
  lines = []
  lines << "# Expects scopes: location"
  lines << "lsq_get_construction_cost_adjustment_for_#{building_type} = {"
  lines << "\tvalue = 0"

  sum = goods.values.reduce(BigDecimal("0")) { |acc, amount| acc + BigDecimal(amount) }
  sum = BigDecimal("1") if sum.zero?
  goods.sort_by { |good, _| good }.each do |good, amount|
    lines << "\tadd = {"
    lines << "\t\tgoods:#{good} = { save_temporary_scope_as = good }"
    lines << "\t\tvalue = lsq_get_construction_good_adjustment"
    lines << "\t\tmultiply = #{amount}"
    lines << "\t\tdivide = #{sum.to_s("F")}"
    lines << "\t\tmin = -0.33"
    lines << "\t\tmax = 0.33"
    lines << "\t}"
  end

  unless goods.empty?
    lines << "\tdivide = #{goods.size}" # divide by the number of goods to get the average adjustment
  end

  lines << "}"
  lines.join("\n")
end

def build_cost_adjustment_switch_value_text(building_ids)
  lines = []
  lines << "# Expects scopes: building_type, location"
  lines << "lsq_get_construction_cost_adjustment_for_building_type = {"

  if building_ids.empty?
    lines << "\tvalue = 0"
  else
    building_ids.each_with_index do |building_type, index|
      branch = index.zero? ? "if" : "else_if"
      lines << "\t#{branch} = { limit = { scope:building_type = building_type:#{building_type} }"
      lines << "\t\tvalue = lsq_get_construction_cost_adjustment_for_#{building_type}"
      lines << "\t}"
    end
    lines << "\telse = {"
    lines << "\t\tvalue = 0"
    lines << "\t}"
  end

  lines << "}"
  lines.join("\n")
end

def build_rgo_cost_adjustment_script_value_text(rgo_type, good)
  lines = []
  lines << "# Expects scopes: country, location"
  lines << "lsq_get_construction_cost_adjustment_for_rgo_#{rgo_type} = {"
  lines << "\tvalue = 0"
  lines << "\tadd = {"
  lines << "\t\tgoods:#{good} = { save_temporary_scope_as = good }"
  lines << "\t\tvalue = lsq_get_construction_good_adjustment"
  lines << "\t\tmin = -0.33"
  lines << "\t\tmax = 0.33"
  lines << "\t}"
  lines << "}"
  lines.join("\n")
end

def build_rgo_dispatcher_script_value_text(rgo_type_to_good)
  lines = []
  lines << "# Expects scopes: country, location"
  lines << "lsq_get_construction_cost_adjustment_for_rgo = {"

  if rgo_type_to_good.empty?
    lines << "\tvalue = 0"
  else
    first = true
    RGO_TYPES.each do |rgo_type|
      next unless rgo_type_to_good.key?(rgo_type)
      branch = first ? "if" : "else_if"
      first = false
      lines << "\t#{branch} = { limit = { scope:location.raw_material = { goods_method = #{rgo_type} } }"
      lines << "\t\tvalue = lsq_get_construction_cost_adjustment_for_rgo_#{rgo_type}"
      lines << "\t}"
    end
    lines << "\telse = {"
    lines << "\t\tvalue = 0"
    lines << "\t}"
  end

  lines << "}"
  lines.join("\n")
end

def parse_rgo_upgrade_goods(profile_to_goods)
  result = {}
  RGO_TYPES.each do |rgo_type|
    profile_id = "upgrade_rgo_demand_#{rgo_type}"
    goods = profile_to_goods[profile_id]
    if goods.nil?
      warn "RGO type '#{rgo_type}': missing profile '#{profile_id}'."
      next
    end
    if goods.empty?
      warn "RGO type '#{rgo_type}': profile '#{profile_id}' has no goods."
      next
    end
    good_keys = goods.keys.sort
    if good_keys.size > 1
      warn "RGO type '#{rgo_type}': profile '#{profile_id}' has multiple goods, using first: #{good_keys.first}"
    end
    result[rgo_type] = good_keys.first
  end
  result
end

def resolve_building_goods_map(building_to_profile, profile_to_goods, default_profile)
  resolved = {}
  building_to_profile.keys.sort.each do |building_type|
    profile = building_to_profile[building_type] || default_profile
    warn "Building '#{building_type}' has no construction_demand, using '#{default_profile}'." unless building_to_profile[building_type]
    goods = profile_to_goods[profile]
    if goods.nil?
      warn "Building '#{building_type}' references missing profile '#{profile}', falling back to '#{default_profile}'."
      goods = profile_to_goods[default_profile] || {}
    end
    warn "Building '#{building_type}' resolved to empty goods map." if goods.empty?
    resolved[building_type] = goods
  end
  resolved
end

def generate_file(game_root:, trigger_output_path:, script_values_output_path:, rgo_script_values_output_path:)
  building_to_profile = parse_building_type_profiles(game_root)
  profile_to_goods = parse_demand_profiles(game_root)
  rgo_type_to_good = parse_rgo_upgrade_goods(profile_to_goods)

  default_profile = "default_construct_building"
  unless profile_to_goods.key?(default_profile)
    warn "Missing required fallback profile '#{default_profile}' in goods demand files."
  end

  resolved_building_goods = resolve_building_goods_map(building_to_profile, profile_to_goods, default_profile)
  building_ids = resolved_building_goods.keys

  trigger_sections = []
  trigger_sections << "# AUTO-GENERATED FILE. DO NOT EDIT BY HAND."
  trigger_sections << "# Generated by tools/generate_lsq_can_afford_building_triggers.rb"
  trigger_sections << "# Sources:"
  trigger_sections << "# - #{game_root.join('in_game/common/building_types/*.txt')}"
  trigger_sections << "# - #{game_root.join('in_game/common/goods_demand/building_construction_costs.txt')}"
  trigger_sections << "# - #{game_root.join('in_game/common/goods_demand/special_construction_demands.txt')}"
  trigger_sections << ""
  trigger_sections << build_switch_trigger_text(building_ids)
  trigger_sections << ""

  building_ids.each do |building_type|
    trigger_sections << build_trigger_text(building_type, resolved_building_goods[building_type])
    trigger_sections << ""
  end

  script_value_sections = []
  script_value_sections << "# AUTO-GENERATED FILE. DO NOT EDIT BY HAND."
  script_value_sections << "# Generated by tools/generate_lsq_can_afford_building_triggers.rb"
  script_value_sections << "# Sources:"
  script_value_sections << "# - #{game_root.join('in_game/common/building_types/*.txt')}"
  script_value_sections << "# - #{game_root.join('in_game/common/goods_demand/building_construction_costs.txt')}"
  script_value_sections << "# - #{game_root.join('in_game/common/goods_demand/special_construction_demands.txt')}"
  script_value_sections << ""
  script_value_sections << build_cost_adjustment_switch_value_text(building_ids)
  script_value_sections << ""

  building_ids.each do |building_type|
    script_value_sections << build_cost_adjustment_script_value_text(building_type, resolved_building_goods[building_type])
    script_value_sections << ""
  end

  trigger_output_path.dirname.mkpath unless trigger_output_path.dirname.directory?
  File.write(trigger_output_path, "\uFEFF" + trigger_sections.join("\n"))

  script_values_output_path.dirname.mkpath unless script_values_output_path.dirname.directory?
  File.write(script_values_output_path, "\uFEFF" + script_value_sections.join("\n"))

  rgo_sections = []
  rgo_sections << "# AUTO-GENERATED FILE. DO NOT EDIT BY HAND."
  rgo_sections << "# Generated by tools/generate_lsq_can_afford_building_triggers.rb"
  rgo_sections << "# Sources:"
  rgo_sections << "# - #{game_root.join('in_game/common/goods_demand/special_construction_demands.txt')}"
  rgo_sections << ""
  rgo_sections << build_rgo_dispatcher_script_value_text(rgo_type_to_good)
  rgo_sections << ""
  RGO_TYPES.each do |rgo_type|
    next unless rgo_type_to_good.key?(rgo_type)
    rgo_sections << build_rgo_cost_adjustment_script_value_text(rgo_type, rgo_type_to_good[rgo_type])
    rgo_sections << ""
  end

  rgo_script_values_output_path.dirname.mkpath unless rgo_script_values_output_path.dirname.directory?
  File.write(rgo_script_values_output_path, "\uFEFF" + rgo_sections.join("\n"))

  puts "Generated #{trigger_output_path} (#{building_ids.size + 1} triggers, including switch trigger)"
  puts "Generated #{script_values_output_path} (#{building_ids.size + 1} script values, including switch value)"
  puts "Generated #{rgo_script_values_output_path} (#{rgo_type_to_good.size + 1} RGO script values, including dispatcher)"
end

game_root = ARGV[0] ? Pathname.new(ARGV[0]) : GAME_ROOT_DEFAULT
trigger_output_path = ARGV[1] ? Pathname.new(ARGV[1]) : TRIGGER_OUTPUT_PATH_DEFAULT
script_values_output_path = ARGV[2] ? Pathname.new(ARGV[2]) : SCRIPT_VALUES_OUTPUT_PATH_DEFAULT
rgo_script_values_output_path = ARGV[3] ? Pathname.new(ARGV[3]) : RGO_SCRIPT_VALUES_OUTPUT_PATH_DEFAULT

# Usage:
#   ruby tools/generate_lsq_can_afford_building_triggers.rb [GAME_ROOT] [TRIGGER_OUTPUT] [SCRIPT_VALUES_OUTPUT] [RGO_SCRIPT_VALUES_OUTPUT]
#
# Arguments (all optional):
#   GAME_ROOT               Path to EU5 game root containing in_game/common.
#   TRIGGER_OUTPUT           Path for lsq_can_afford_* scripted trigger output.
#   SCRIPT_VALUES_OUTPUT     Path for generated construction cost adjustment script values.
#   RGO_SCRIPT_VALUES_OUTPUT Path for generated RGO construction cost adjustment script values.
#
# Examples:
#   ruby tools/generate_lsq_can_afford_building_triggers.rb
#   ruby tools/generate_lsq_can_afford_building_triggers.rb "/Volumes/Europa Universalis V/game"
#   ruby tools/generate_lsq_can_afford_building_triggers.rb "/Volumes/Europa Universalis V/game" \
#     "/tmp/lsq_can_afford_building.txt" "/tmp/lsq_construction_cost_adjustments_script_values.txt"
generate_file(
  game_root: game_root,
  trigger_output_path: trigger_output_path,
  script_values_output_path: script_values_output_path,
  rgo_script_values_output_path: rgo_script_values_output_path
)
