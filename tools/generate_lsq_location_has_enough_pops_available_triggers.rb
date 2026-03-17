#!/usr/bin/env ruby
# frozen_string_literal: true

require "pathname"
require "set"
require "erb"

GAME_ROOT_DEFAULT = Pathname.new("/Volumes/Europa Universalis V/game")
REPO_ROOT_DEFAULT = Pathname.new(__dir__).parent
AUTO_BUILD_TRIGGERS_PATH_DEFAULT = REPO_ROOT_DEFAULT.join("in_game/common/scripted_triggers/lsq_auto_build_triggers.txt")
TEMPLATE_ROOT_DEFAULT = REPO_ROOT_DEFAULT.join("in_game")

IDENTIFIER_RE = /[A-Za-z0-9_:\.\-]+/

class BuildingPopMapping
  attr_reader :building_type, :pop_type

  def initialize(building_type:, pop_type:)
    @building_type = building_type
    @pop_type = pop_type
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
end

class TemplateContext
  include TemplateHelpers

  attr_reader :building_to_pop_type

  def initialize(building_to_pop_type)
    @building_to_pop_type = building_to_pop_type
  end

  def building_pop_mappings
    sorted_building_pop_mappings(building_to_pop_type)
  end

  def empty?
    building_to_pop_type.empty?
  end

  def template_binding
    binding
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
  building_to_pop_type = parse_building_to_pop_type(game_root)
  available_pop_checks = parse_available_pop_checks(read_text(auto_build_triggers_path))

  used_pop_types = building_to_pop_type.values.to_set
  missing_pop_checks = used_pop_types - available_pop_checks
  unless missing_pop_checks.empty?
    raise "Missing lsq_auto_build_check_<pop_type>_available_or_will_be for: #{missing_pop_checks.to_a.sort.join(', ')}"
  end

  context = TemplateContext.new(building_to_pop_type)
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
