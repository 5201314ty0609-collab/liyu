#!/usr/bin/env python3
"""Analyze configuration overhead for waste detection."""
import os, json

home = os.path.expanduser('~')

results = {}

# CLAUDE.md
claude_md = os.path.join(home, '.claude/CLAUDE.md')
if os.path.exists(claude_md):
    size = os.path.getsize(claude_md)
    lines = len(open(claude_md).readlines())
    results['CLAUDE.md'] = {'size': size, 'lines': lines, 'est_tokens': size // 4}
    print(f"CLAUDE.md: {size:,} bytes, {lines} lines, ~{size//4:,} tokens")

# Rules directory
rules_dir = os.path.join(home, '.claude/rules')
total_rules = 0
rule_files_list = []
for root, dirs, files in os.walk(rules_dir):
    for f in files:
        if f.endswith('.md'):
            path = os.path.join(root, f)
            size = os.path.getsize(path)
            total_rules += size
            rel = path.replace(rules_dir + '/', '')
            rule_files_list.append((rel, size))
rule_files_list.sort(key=lambda x: -x[1])
results['rules'] = {'count': len(rule_files_list), 'total_size': total_rules, 'est_tokens': total_rules // 4}

print(f"\nRules: {len(rule_files_list)} files, {total_rules:,} bytes, ~{total_rules//4:,} tokens")
print("Largest rule files:")
for name, size in rule_files_list[:10]:
    print(f"  {name}: {size:,} bytes (~{size//4:,} tokens)")

# Agents
agents_dir = os.path.join(home, '.claude/agents')
if os.path.exists(agents_dir):
    total_agents = 0
    agent_count = 0
    for root, dirs, files in os.walk(agents_dir):
        for f in files:
            path = os.path.join(root, f)
            total_agents += os.path.getsize(path)
            agent_count += 1
    results['agents'] = {'count': agent_count, 'total_size': total_agents, 'est_tokens': total_agents // 4}
    print(f"\nAgents: {agent_count} files, {total_agents:,} bytes, ~{total_agents//4:,} tokens")

# Skills
skills_dir = os.path.join(home, '.claude/skills')
if os.path.exists(skills_dir):
    total_skills = 0
    skill_count = 0
    for root, dirs, files in os.walk(skills_dir):
        for f in files:
            if f.endswith('.md'):
                path = os.path.join(root, f)
                total_skills += os.path.getsize(path)
                skill_count += 1
    results['skills'] = {'count': skill_count, 'total_size': total_skills, 'est_tokens': total_skills // 4}
    print(f"\nSkills: {skill_count} skill files, {total_skills:,} bytes, ~{total_skills//4:,} tokens")

# MCP configs
for mcp_path in ['~/.claude/.mcp.json', '~/.claude/mcp.json', '~/.claude/mcp-config.json',
                 '~/.claude/claude.json', '~/.claude/settings.json', '~/.claude/settings.local.json']:
    p = os.path.expanduser(mcp_path)
    if os.path.exists(p):
        size = os.path.getsize(p)
        results[os.path.basename(p)] = {'size': size, 'est_tokens': size // 4}
        print(f"\n{os.path.basename(p)}: {size:,} bytes, ~{size//4:,} tokens")

# 鲤鱼 directory
liyu_dir = os.path.join(home, '.claude/liyu')
liyu_size = 0
for root, dirs, files in os.walk(liyu_dir):
    for f in files:
        p = os.path.join(root, f)
        try:
            liyu_size += os.path.getsize(p)
        except:
            pass
results['liyu_dir'] = {'total_size': liyu_size}
print(f"\n鲤鱼 directory: {liyu_size:,} bytes total")

# Total overhead estimate
grand_total = sum(v.get('est_tokens', 0) for v in results.values())
print(f"\n=== GRAND TOTAL CONFIG OVERHEAD ===")
print(f"Estimated tokens loaded every session: ~{grand_total:,}")

# Specific waste findings
print(f"\n=== POTENTIAL WASTE ===")
session_overhead = results.get('CLAUDE.md', {}).get('est_tokens', 0)
print(f"CLAUDE.md per session: ~{session_overhead:,} tokens")
print(f"Rules per session: ~{results.get('rules', {}).get('est_tokens', 0):,} tokens")
if 'skills' in results:
    print(f"Skills listing: ~{results['skills']['est_tokens']:,} tokens ({results['skills']['count']} skills)")
