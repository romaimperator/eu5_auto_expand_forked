#!/usr/bin/env ruby
# frozen_string_literal: true

require "pathname"
require "set"
require "erb"
require "bigdecimal"

GAME_ROOT_DEFAULT = Pathname.new("/Volumes/Europa Universalis V/game")
REPO_ROOT_DEFAULT = Pathname.new(__dir__).parent
AUTO_BUILD_TRIGGERS_PATH_DEFAULT = REPO_ROOT_DEFAULT.join("in_game/common/scripted_triggers/lsq_auto_build_triggers.txt")
TEMPLATE_ROOT_DEFAULT = REPO_ROOT_DEFAULT.join("in_game")

IDENTIFIER_RE = /[A-Za-z0-9_:\.\-]+/
NUMERIC_RE = /\A-?\d+(?:\.\d+)?\z/
RGO_TYPES = %w[mining farming hunting gathering forestry].freeze

class BuildingPopMapping
  attr_reader :building_type, :pop_type

  def initialize(building_type:, pop_type:)
    @building_type = building_type
    @pop_type = pop_type
  end
end

class BuildingTypeMetadata
  attr_reader :building_type, :pop_type, :construction_demand, :max_levels

  def initialize(building_type:, pop_type:, construction_demand:, max_levels:, produces_goods:, fort:)
    @building_type = building_type
    @pop_type = pop_type
    @construction_demand = construction_demand
    @max_levels = max_levels
    @produces_goods = produces_goods
    @fort = fort
  end

  def produces_goods?
    if @building_type == "ablaq_palace"
      return true
    else
      @produces_goods
    end
  end

  def fort?
    @fort
  end

  def is_multi_level?
    # !max_levels.nil? && max_levels != "1"
    if @building_type == "city_of_victory"
      false
    else
      max_levels != "1"
    end
  end

  def one_per_location?
    !is_multi_level?
  end

  def non_production_multi_level?
    !produces_goods? && is_multi_level?
  end

  def production_building?
    produces_goods? && is_multi_level?
  end
end

module TemplateHelpers
  module_function

  def sorted_building_pop_mappings(building_to_pop_type)
    building_to_pop_type.keys.sort.map do |building_type|
      BuildingPopMapping.new(
        building_type: building_type,
        pop_type: building_to_pop_type[building_type]
      )
    end
  end

  def sorted_building_type_metadata(building_type_metadata)
    building_type_metadata.keys.sort.map do |building_type|
      building_type_metadata[building_type]
    end
  end

  def sorted_goods_pairs(goods)
    goods.sort_by { |good, _amount| good }
  end

  def goods_total_or_one(goods)
    sum = goods.values.reduce(BigDecimal("0")) { |acc, amount| acc + BigDecimal(amount) }
    sum = BigDecimal("1") if sum.zero?
    sum
  end

  def decimal_to_s(value)
    value.to_s("F")
  end

  def construction_goods
    ["books", "clay", "cloth", "copper", "fine_cloth", "firearms", "glass", "goods_gold", "horses", "jewelry", "leather", "liquor", "livestock", "lumber", "marble", "masonry", "naval_supplies", "paper", "salt", "sand", "silver", "steel", "stone", "tin", "tools", "weaponry", "wheat", "wool"]
  end
end

class TemplateContext
  include TemplateHelpers

  attr_reader :game_root, :building_to_pop_type, :building_ids, :resolved_building_goods, :rgo_type_to_good, :building_type_metadata, :obsolete_to_upgrade

  def initialize(game_root:, building_to_pop_type:, building_ids:, resolved_building_goods:, rgo_type_to_good:, building_type_metadata:, obsolete_to_upgrade:)
    @game_root = game_root
    @building_to_pop_type = building_to_pop_type
    @building_ids = building_ids
    @resolved_building_goods = resolved_building_goods
    @rgo_type_to_good = rgo_type_to_good
    @building_type_metadata = building_type_metadata
    @obsolete_to_upgrade = obsolete_to_upgrade
  end

  # Returns the building_type that obsoletes +building_type+ (i.e. newer building whose obsolete = building_type), or nil.
  def get_upgrade_building_type(building_type)
    @obsolete_to_upgrade[building_type]
  end

  def building_pop_mappings
    sorted_building_pop_mappings(building_to_pop_type)
  end

  def building_metadata
    sorted_building_type_metadata(building_type_metadata)
  end

  def empty?
    building_to_pop_type.empty?
  end

  def template_binding
    binding
  end

  def rgo_types
    RGO_TYPES
  end
end

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

def extract_depth1_assignment(block_text, wanted_key)
  depth = 0

  block_text.each_line do |raw_line|
    line = strip_comments(raw_line).strip
    next if line.empty?

    if depth.zero?
      m = /\A(#{IDENTIFIER_RE.source})\s*=\s*([^\{\}\r\n]+?)\s*\z/.match(line)
      return m[2].strip if m && m[1] == wanted_key
    end

    depth += line.count("{")
    depth -= line.count("}")
  end

  nil
end

def normalize_pop_type(raw_pop_type)
  return nil if raw_pop_type.nil? || raw_pop_type.empty?

  raw_pop_type.sub(/\Apop_type:/, "")
end

def block_has_assignment_anywhere?(block_text, wanted_key)
  block_text.each_line.any? do |raw_line|
    line = strip_comments(raw_line).strip
    next false if line.empty?

    /\A#{Regexp.escape(wanted_key)}\s*=\s*[^\{\}\r\n]+/.match?(line)
  end
end

def extract_assignment_in_named_block(block_text, block_name, wanted_key)
  sanitized_lines = block_text.each_line.map { |line| strip_comments(line) }
  depth = 0
  in_block = false
  block_depth = nil

  sanitized_lines.each do |raw_line|
    line = raw_line.strip
    next if line.empty?

    if !in_block && depth.zero? && /\A#{Regexp.escape(block_name)}\s*=\s*\{\s*\z/.match?(line)
      in_block = true
      block_depth = depth + 1
    elsif in_block && depth == block_depth
      if (m = /\A(#{IDENTIFIER_RE.source})\s*=\s*([^\{\}\r\n]+?)\s*\z/.match(line)) && m[1] == wanted_key
        return m[2].strip
      end
    end

    depth += line.count("{")
    depth -= line.count("}")

    if in_block && depth < block_depth
      in_block = false
      block_depth = nil
    end
  end

  nil
end

def extract_root_assignment_rhs(block_text, wanted_key)
  sanitized_lines = block_text.each_line.map { |line| strip_comments(line).strip }
  depth = 0

  i = 0
  while i < sanitized_lines.length
    line = sanitized_lines[i]
    i += 1
    next if line.empty?

    if depth.zero? && (m = /\A(#{IDENTIFIER_RE.source})\s*=\s*(.+)\z/.match(line)) && m[1] == wanted_key
      rhs = m[2].strip
      return rhs unless rhs.start_with?("{")

      # Multi-line RHS like:
      # max_levels = { ... }
      # We parse until braces are balanced.
      brace_balance = rhs.count("{") - rhs.count("}")
      rhs_acc = rhs
      while brace_balance.positive? && i < sanitized_lines.length
        nxt = sanitized_lines[i]
        i += 1
        rhs_acc += "\n#{nxt}"
        brace_balance += nxt.count("{") - nxt.count("}")
      end

      return rhs_acc
    end

    depth += line.count("{")
    depth -= line.count("}")
  end

  nil
end

def max_levels_token_from_rhs(max_levels_rhs)
  return nil if max_levels_rhs.nil? || max_levels_rhs.strip.empty?

  # Take first "value = ..." assignment inside the max_levels block.
  # In the vanilla data this is usually the baseline max level.
  first_value_token = max_levels_rhs.scan(/\bvalue\s*=\s*([^\{\}\r\n]+?)(?=\s*(?:$|\n|if|add|desc|\}))/).flatten.first
  first_value_token = max_levels_rhs.scan(/\bvalue\s*=\s*([^\{\}\r\n]+?)/).flatten.first if first_value_token.nil?

  add_increase = max_levels_rhs.scan(/add\s*=\s*\{[\s\S]*?\bvalue\s*=\s*(-?\d+)\b/i).flatten
  add_increase_any = add_increase.any? { |v| v.to_i > 0 }

  return nil if first_value_token.nil?

  token = first_value_token.strip
  # If baseline is 1 but the max_levels block increases it (add value > 0),
  # treat it as multi-level so it won't be misclassified as one_per_location.
  token == "1" && add_increase_any ? "2" : token
end

def parse_building_to_pop_type(game_root)
  result = {}
  dir = game_root.join("in_game/common/building_types")

  Dir.glob(dir.join("*.txt").to_s).sort.each do |path|
    next if File.basename(path).downcase == "readme.txt"

    blocks = parse_top_level_blocks(read_text(path))
    blocks.each do |building_type, body|
      pop_type = normalize_pop_type(extract_depth1_assignment(body, "pop_type"))
      if pop_type.nil?
        warn "Building '#{building_type}' has no top-level pop_type in #{path}"
        next
      end
      result[building_type] = pop_type
    end
  end

  result
end

def parse_available_pop_checks(trigger_file_text)
  Set.new(trigger_file_text.scan(/lsq_auto_build_check_([A-Za-z0-9_]+)_available_or_will_be\s*=\s*\{/).flatten)
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

# Top-level assignments that may repeat (e.g. multiple obsolete = ... in one building block).
def extract_root_assignments_for_key(block_text, wanted_key)
  values = []
  depth = 0

  block_text.each_line do |raw_line|
    line = strip_comments(raw_line).strip
    next if line.empty?

    if depth.zero? && (m = /\A(#{IDENTIFIER_RE.source})\s*=\s*([^\{\}\r\n]+?)\s*\z/.match(line)) && m[1] == wanted_key
      rhs = m[2].strip
      values << rhs unless rhs.empty?
    end

    depth += line.count("{")
    depth -= line.count("}")
  end

  values
end

def parse_building_type_metadata(game_root)
  result = {}
  dir = game_root.join("in_game/common/building_types")

  Dir.glob(dir.join("*.txt").to_s).sort.each do |path|
    next if File.basename(path).downcase == "readme.txt"

    blocks = parse_top_level_blocks(read_text(path))
    blocks.each do |building_type, body|
      attrs = parse_root_assignments(body)
      pop_type = normalize_pop_type(attrs["pop_type"])
      if pop_type.nil?
        warn "Building '#{building_type}' has no top-level pop_type in #{path}"
        next
      end

      fort_level = extract_assignment_in_named_block(body, "raw_modifier", "fort_level")
      max_levels_rhs = extract_root_assignment_rhs(body, "max_levels")
      max_levels_token = max_levels_token_from_rhs(max_levels_rhs) || attrs["max_levels"]

      result[building_type] = BuildingTypeMetadata.new(
        building_type: building_type,
        pop_type: pop_type,
        construction_demand: attrs["construction_demand"],
        max_levels: max_levels_token,
        produces_goods: block_has_assignment_anywhere?(body, "produced"),
        fort: !fort_level.nil?
      )
    end
  end

  result
end

# For each building block, each obsolete = X line maps X to this building (the upgrade that replaces X).
def parse_obsolete_to_upgrade_map(game_root)
  result = {}
  dir = game_root.join("in_game/common/building_types")

  Dir.glob(dir.join("*.txt").to_s).sort.each do |path|
    next if File.basename(path).downcase == "readme.txt"

    blocks = parse_top_level_blocks(read_text(path))
    blocks.each do |building_type, body|
      extract_root_assignments_for_key(body, "obsolete").each do |obsolete|
        if result.key?(obsolete) && result[obsolete] != building_type
          warn "Multiple buildings obsolete '#{obsolete}': keeping '#{building_type}', was '#{result[obsolete]}' (#{path})"
        end
        result[obsolete] = building_type
      end
    end
  end

  result
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

def erb_render(text, context:)
  ERB.new(text, trim_mode: "-").result(context.template_binding)
end

def each_template_file(template_root)
  Dir.glob(template_root.join("**/*.erb").to_s).sort.each do |path|
    yield Pathname.new(path)
  end
end

def dest_path_for_template_file(template_file)
  Pathname.new(template_file.to_s.delete_suffix(".erb"))
end

def generate_all(game_root:, auto_build_triggers_path:, template_root:)
  obsolete_to_upgrade = parse_obsolete_to_upgrade_map(game_root)
  building_type_metadata = parse_building_type_metadata(game_root)
  building_to_pop_type = building_type_metadata.transform_values(&:pop_type)
  available_pop_checks = parse_available_pop_checks(read_text(auto_build_triggers_path))

  used_pop_types = building_to_pop_type.values.to_set
  missing_pop_checks = used_pop_types - available_pop_checks
  unless missing_pop_checks.empty?
    raise "Missing lsq_auto_build_check_<pop_type>_available_or_will_be for: #{missing_pop_checks.to_a.sort.join(', ')}"
  end

  building_to_profile = parse_building_type_profiles(game_root)
  profile_to_goods = parse_demand_profiles(game_root)
  rgo_type_to_good = parse_rgo_upgrade_goods(profile_to_goods)

  default_profile = "default_construct_building"
  warn "Missing required fallback profile '#{default_profile}' in goods demand files." unless profile_to_goods.key?(default_profile)

  resolved_building_goods = resolve_building_goods_map(building_to_profile, profile_to_goods, default_profile)
  building_ids = resolved_building_goods.keys

  context = TemplateContext.new(
    game_root: game_root,
    building_to_pop_type: building_to_pop_type,
    building_ids: building_ids,
    resolved_building_goods: resolved_building_goods,
    rgo_type_to_good: rgo_type_to_good,
    building_type_metadata: building_type_metadata,
    obsolete_to_upgrade: obsolete_to_upgrade
  )
  generated_count = 0

  each_template_file(template_root) do |template_file|
    dest_path = dest_path_for_template_file(template_file)
    rendered = erb_render(read_text(template_file), context: context)

    dest_path.dirname.mkpath unless dest_path.dirname.directory?
    File.write(dest_path, "\uFEFF" + rendered)
    generated_count += 1
    puts "Generated #{dest_path}"
  end

  puts "Done (#{generated_count} files; #{building_to_pop_type.size} building_type branches)"
end

game_root = ARGV[0] ? Pathname.new(ARGV[0]) : GAME_ROOT_DEFAULT
auto_build_triggers_path = ARGV[1] ? Pathname.new(ARGV[1]) : AUTO_BUILD_TRIGGERS_PATH_DEFAULT
template_root = ARGV[2] ? Pathname.new(ARGV[2]) : TEMPLATE_ROOT_DEFAULT

generate_all(
  game_root: game_root,
  auto_build_triggers_path: auto_build_triggers_path,
  template_root: template_root
)
