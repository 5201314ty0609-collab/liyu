#!/usr/bin/env python3
"""
鲤鱼 Cost Core — Data models, session parsing, waste detection, health calculation.

Internal module used by liyu-cost-tracker.py. Not invoked directly.

Reads Claude Code session transcripts to extract token usage, categorize costs,
detect waste patterns, and compute health grades. Inspired by CodeBurn architecture.

v1.0.0 — 2026-06-17
"""

import json
import os
import glob
from datetime import datetime, timezone
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


# ─── Constants ───────────────────────────────────────────────────────────────

HOME = os.path.expanduser("~")
鲤鱼_DIR = os.path.join(HOME, ".claude", "liyu")
SESSION_DIR = os.path.join(HOME, ".claude", "projects", "-Users-holyty")
RULES_DIR = os.path.join(HOME, ".claude", "rules")
AGENTS_DIR = os.path.join(HOME, ".claude", "agents")
SKILLS_DIR = os.path.join(HOME, ".claude", "skills")
CLAUDE_MD = os.path.join(HOME, ".claude", "CLAUDE.md")
SETTINGS_FILE = os.path.join(HOME, ".claude", "settings.json")

# Context window size for overhead proportionality (mimo-v2.5-pro = 1M)
DEFAULT_CONTEXT_WINDOW = 1_000_000


def _load_skill_overrides() -> Dict[str, str]:
    """Read skillOverrides from settings.json to know which skills are disabled."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
            return data.get("skillOverrides", {})
    except Exception:
        pass
    return {}

# Model pricing per 1M tokens (USD). Update as pricing changes.
MODEL_PRICING = {
    "mimo-v2.5-pro":     {"input": 0.55, "output": 2.19, "cache_read": 0.055, "cache_write": 1.10},
    "deepseek-v4-pro":   {"input": 0.50, "output": 2.00, "cache_read": 0.05,  "cache_write": 1.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 6.00},
    "claude-opus-4-8":   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 30.00},
    "<synthetic>":       {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
}

# Tool categories for attribution
TOOL_CATEGORIES = {
    "coding":      {"Write", "Edit", "NotebookEdit"},
    "reading":     {"Read", "Glob", "Bash"},
    "execution":   {"Bash"},
    "web":         {"WebFetch", "WebSearch"},
    "planning":    {"EnterPlanMode", "ExitPlanMode", "TaskCreate", "TaskUpdate", "TaskOutput"},
    "delegation":  {"Agent", "Skill", "Task", "TaskStop", "Workflow"},
    "interaction": {"AskUserQuestion"},
    "meta":        {"EnterWorktree", "ExitWorktree", "Monitor"},
}

HEALTH_GRADES = {
    "A": (90, 100, "Excellent — minimal waste, high cache efficiency"),
    "B": (75, 89,  "Good — some optimizations available"),
    "C": (55, 74,  "Fair — notable waste patterns"),
    "D": (35, 54,  "Poor — significant waste, optimization recommended"),
    "E": (15, 34,  "Critical — major waste, action needed"),
    "F": (0,  14,  "Severe — emergency optimization required"),
}

O2_THRESHOLDS = {
    "critical": 90,
    "warning":  70,
    "healthy":  50,
}


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class SessionTokens:
    """Token breakdown for a single session."""
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    model: str = "unknown"
    tool_calls: Dict[str, int] = field(default_factory=Counter)
    user_messages: int = 0
    assistant_messages: int = 0
    turn_count: int = 0
    repeated_files: Dict[str, int] = field(default_factory=Counter)
    edit_files: set = field(default_factory=set)
    read_files: set = field(default_factory=set)
    duration_ms: int = 0
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def output_ratio(self) -> float:
        return self.output_tokens / max(self.input_tokens, 1)

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_read_tokens / max(self.input_tokens + self.cache_read_tokens, 1)

    def estimate_cost(self) -> Dict[str, float]:
        """Estimate USD cost using model pricing."""
        pricing = MODEL_PRICING.get(self.model, MODEL_PRICING.get("deepseek-v4-pro", {}))
        if not pricing:
            return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0}
        mil = 1_000_000
        cost = {
            "input": (self.input_tokens / mil) * pricing["input"],
            "output": (self.output_tokens / mil) * pricing["output"],
            "cache_read": (self.cache_read_tokens / mil) * pricing["cache_read"],
            "cache_write": (self.cache_write_tokens / mil) * pricing["cache_write"],
        }
        cost["total"] = sum(cost.values())
        return cost


@dataclass
class ConfigOverhead:
    """Size and token estimate for configuration loaded each session."""
    path: str
    description: str
    size_bytes: int
    line_count: int = 0
    est_tokens: int = 0
    language: str = "all"

    def __post_init__(self):
        if self.est_tokens == 0 and self.size_bytes > 0:
            self.est_tokens = self.size_bytes // 4


@dataclass
class WasteFinding:
    """A detected waste pattern with estimated savings."""
    id: str
    category: str
    severity: str
    description: str
    estimated_token_waste: int
    estimated_cost_waste: float
    fix_command: str
    evidence: str = ""


@dataclass
class CostReport:
    """Aggregated cost report across sessions."""
    sessions: List[SessionTokens]
    config_overheads: List[ConfigOverhead]
    waste_findings: List[WasteFinding]
    health_score: int = 0
    health_grade: str = "?"
    total_sessions: int = 0
    date_range: Tuple[str, str] = ("?", "?")
    report_time: str = ""

    @property
    def total_input(self) -> int:
        return sum(s.input_tokens for s in self.sessions)

    @property
    def total_output(self) -> int:
        return sum(s.output_tokens for s in self.sessions)

    @property
    def total_cache_read(self) -> int:
        return sum(s.cache_read_tokens for s in self.sessions)

    @property
    def total_cost(self) -> float:
        return sum(s.estimate_cost()["total"] for s in self.sessions)

    @property
    def config_overhead_tokens(self) -> int:
        """Total config overhead, using active skill count when available."""
        total = sum(c.est_tokens for c in self.config_overheads)
        # Replace full skill listing with active skills if available
        active = next((c for c in self.config_overheads if "Active skills" in c.description), None)
        if active:
            full = next((c for c in self.config_overheads
                        if "Skill listing" in c.description and "(active" not in c.description), None)
            if full:
                total = total - full.est_tokens + active.est_tokens
        return total


# ─── Session Parser ───────────────────────────────────────────────────────────

def parse_session_transcript(filepath: str) -> Optional[SessionTokens]:
    """Parse a single Claude Code session transcript (.jsonl).

    Extracts: token usage per API call, tool call counts, file access patterns,
    timing data, model identification.
    """
    session_id = os.path.basename(filepath).replace(".jsonl", "")
    st = SessionTokens(session_id=session_id)

    try:
        with open(filepath, "r") as f:
            content = f.read()
    except Exception:
        return None

    for line in content.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = entry.get("type", "")

        # Track timing
        ts = entry.get("timestamp", "")
        if ts:
            if st.start_time is None or ts < st.start_time:
                st.start_time = ts
            if st.end_time is None or ts > st.end_time:
                st.end_time = ts

        # Parse assistant messages with token usage
        if etype == "assistant" and "message" in entry:
            msg = entry["message"]
            usage = msg.get("usage", {})

            if usage.get("input_tokens", 0) > 0 or usage.get("output_tokens", 0) > 0:
                st.input_tokens += usage.get("input_tokens", 0)
                st.output_tokens += usage.get("output_tokens", 0)
                st.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
                st.cache_write_tokens += usage.get("cache_creation_input_tokens", 0)
                st.assistant_messages += 1

                model = msg.get("model", "")
                if model and model not in ("<synthetic>", ""):
                    st.model = model

                for cbit in msg.get("content", []):
                    if cbit.get("type") == "thinking":
                        st.thinking_tokens += len(cbit.get("thinking", "")) // 4

                for cbit in msg.get("content", []):
                    if cbit.get("type") == "tool_use":
                        tool_name = cbit.get("name", "unknown")
                        st.tool_calls[tool_name] += 1

                        inp = cbit.get("input", {})
                        fp = (inp.get("file_path", "") or
                              inp.get("path", "") or
                              inp.get("notebook_path", ""))
                        if fp:
                            if tool_name == "Read":
                                st.read_files.add(fp)
                                st.repeated_files[fp] += 1
                            elif tool_name in ("Write", "Edit", "NotebookEdit"):
                                st.edit_files.add(fp)

        elif etype == "user" and "message" in entry:
            st.user_messages += 1

        elif etype == "system" and entry.get("subtype") == "turn_duration":
            st.duration_ms += entry.get("durationMs", 0)
            st.turn_count += 1

    return st if st.assistant_messages > 0 else None


def parse_all_sessions(session_dir: str = SESSION_DIR) -> List[SessionTokens]:
    """Parse all available session transcripts and return sorted by recency."""
    sessions = []
    pattern = os.path.join(session_dir, "*.jsonl")
    for filepath in sorted(glob.glob(pattern)):
        st = parse_session_transcript(filepath)
        if st and st.input_tokens > 0:
            sessions.append(st)
    sessions.sort(key=lambda s: s.start_time or "", reverse=True)
    return sessions


# ─── Config Overhead Analysis ──────────────────────────────────────────────────

def analyze_config_overhead() -> List[ConfigOverhead]:
    """Measure token overhead from configuration files loaded each session.

    Returns list of ConfigOverhead objects for CLAUDE.md, rules, agents, skills,
    and settings files — everything injected into the system prompt.
    """
    overheads = []

    # CLAUDE.md
    if os.path.exists(CLAUDE_MD):
        size = os.path.getsize(CLAUDE_MD)
        lines = sum(1 for _ in open(CLAUDE_MD))
        overheads.append(ConfigOverhead(
            path=CLAUDE_MD, description="Main system prompt (CLAUDE.md)",
            size_bytes=size, line_count=lines))

    # Rules directory (aggregated + per-language breakdown for waste detection)
    rules_by_lang = defaultdict(lambda: {"size": 0, "count": 0})
    for root, dirnames, filenames in os.walk(RULES_DIR):
        for f in filenames:
            if f.endswith(".md"):
                path = os.path.join(root, f)
                size = os.path.getsize(path)
                rel = os.path.relpath(path, RULES_DIR)
                lang = rel.split("/")[0] if "/" in rel else "root"
                rules_by_lang[lang]["size"] += size
                rules_by_lang[lang]["count"] += 1

    total_rules_size = sum(v["size"] for v in rules_by_lang.values())
    total_rules_count = sum(v["count"] for v in rules_by_lang.values())
    overheads.append(ConfigOverhead(
        path=RULES_DIR, description="Rule files loaded every session",
        size_bytes=total_rules_size, line_count=total_rules_count))

    for lang, info in sorted(rules_by_lang.items(), key=lambda x: -x[1]["size"]):
        overheads.append(ConfigOverhead(
            path=f"{RULES_DIR}/{lang}",
            description=f"Rules: {lang} ({info['count']} files)",
            size_bytes=info["size"], line_count=info["count"], language=lang))

    # Agents directory
    if os.path.exists(AGENTS_DIR):
        total_agents = 0
        count = 0
        for root, dirnames, filenames in os.walk(AGENTS_DIR):
            for f in filenames:
                try:
                    total_agents += os.path.getsize(os.path.join(root, f))
                    count += 1
                except OSError:
                    pass
        overheads.append(ConfigOverhead(
            path=AGENTS_DIR, description="Agent definitions",
            size_bytes=total_agents, line_count=count))

    # Skills directory (exclude overridden skills from overhead calculation)
    if os.path.exists(SKILLS_DIR):
        overrides = _load_skill_overrides()
        total_skills = 0
        total_skills_active = 0
        count = 0
        count_active = 0
        for root, dirnames, filenames in os.walk(SKILLS_DIR):
            for f in filenames:
                if f.endswith(".md"):
                    try:
                        fsize = os.path.getsize(os.path.join(root, f))
                        total_skills += fsize
                        count += 1
                        # Check if this skill is overridden
                        skill_dir = os.path.basename(root)
                        if overrides.get(skill_dir, "on") == "on":
                            total_skills_active += fsize
                            count_active += 1
                    except OSError:
                        pass
        overheads.append(ConfigOverhead(
            path=SKILLS_DIR, description="Skill listing loaded each session",
            size_bytes=total_skills, line_count=count))
        # If there are overrides, also report active skills
        if count_active < count:
            overheads.append(ConfigOverhead(
                path=f"{SKILLS_DIR} (active only)",
                description=f"Active skills ({count_active}/{count} files)",
                size_bytes=total_skills_active, line_count=count_active))

    # Settings files
    for name in ["settings.json", "settings.local.json"]:
        path = os.path.join(HOME, ".claude", name)
        if os.path.exists(path):
            size = os.path.getsize(path)
            overheads.append(ConfigOverhead(path=path, description=name, size_bytes=size))

    return overheads


# ─── Waste Detector ────────────────────────────────────────────────────────────

def detect_waste(sessions: List[SessionTokens],
                 overheads: List[ConfigOverhead]) -> List[WasteFinding]:
    """Detect waste patterns using CodeBurn-inspired deterministic heuristics.

    Seven waste categories:
      1. repeated_context  — files read multiple times across/within sessions
      2. low_ratio         — sessions with very low output/input ratios
      3. bloated_config    — oversized config for unused languages/features
      4. ghost_skills      — skills listed in system prompt but never invoked
      5. ghost_agents      — agents available but rarely spawned
      6. cache_inefficiency— low cache hit rates
      7. tool_imbalance    — over-reliance on a single tool type
    """
    findings = []

    # ── 1. Repeated context ──
    all_repeated = Counter()
    for s in sessions:
        for fpath, count in s.repeated_files.items():
            if count > 1:
                all_repeated[fpath] += count - 1

    if all_repeated:
        total_extra = sum(all_repeated.values())
        top_repeats = all_repeated.most_common(5)
        est_tokens = total_extra * 2000
        findings.append(WasteFinding(
            id="repeated-context",
            category="repeated_context",
            severity="high" if total_extra > 20 else "medium",
            description=(
                f"Files re-read across sessions ({total_extra} extra reads). "
                f"Top: {', '.join(f'{f}({c}x)' for f, c in top_repeats[:5])}"
            ),
            estimated_token_waste=est_tokens,
            estimated_cost_waste=est_tokens / 1_000_000 * 0.50,
            fix_command=(
                "# Consider: cache file contents in session state, "
                "use Grep to find content instead of re-reading"
            ),
            evidence=f"Total extra reads: {total_extra}",
        ))

    # ── 2. Low output/input ratio ──
    terrible = [
        (s.session_id[:8], s.output_ratio, s.input_tokens)
        for s in sessions
        if s.output_ratio < 0.05 and s.input_tokens > 10000
    ]
    if terrible:
        findings.append(WasteFinding(
            id="low-ratio-sessions",
            category="low_ratio",
            severity="high",
            description=(
                f"{len(terrible)} sessions with severe input/output imbalance "
                f"(<5% output ratio). "
                f"Sessions: {', '.join(f'{sid}({r:.3f})' for sid, r, _ in terrible[:5])}"
            ),
            estimated_token_waste=sum(t for _, _, t in terrible) // 2,
            estimated_cost_waste=sum(t for _, _, t in terrible) / 1_000_000 * 0.25,
            fix_command=(
                "# These sessions may have context loops — check for repeated "
                "error patterns, excessive tool retries"
            ),
            evidence=f"Sessions with ratio < 0.05: {len(terrible)}",
        ))

    # ── 3. Bloated config: unused language rules ──
    used_extensions = set()
    for s in sessions:
        for fp in s.edit_files | s.read_files:
            ext = os.path.splitext(fp)[1].lower()
            used_extensions.add(ext)

    lang_map = {
        "typescript": {".ts", ".tsx", ".js", ".jsx"},
        "python":    {".py", ".pyi", ".pyx"},
        "golang":    {".go"},
        "rust":      {".rs"},
        "swift":     {".swift"},
        "dart":      {".dart"},
        "java":      {".java", ".kt"},
        "php":       {".php"},
        "web":       {".html", ".css", ".scss"},
    }
    unused_langs = []
    for lang, exts in lang_map.items():
        if not (used_extensions & exts):
            lang_dir = os.path.join(RULES_DIR, lang)
            if os.path.isdir(lang_dir):
                total = sum(
                    os.path.getsize(os.path.join(lang_dir, f))
                    for f in os.listdir(lang_dir) if f.endswith(".md")
                )
                if total > 0:
                    unused_langs.append((lang, total))

    if unused_langs:
        total_unused = sum(s for _, s in unused_langs)
        findings.append(WasteFinding(
            id="unused-language-rules",
            category="bloated_config",
            severity="medium",
            description=(
                f"Rules for {len(unused_langs)} unused languages loaded every session: "
                f"{', '.join(f'{l}(~{s//4:,}t)' for l, s in unused_langs)}"
            ),
            estimated_token_waste=total_unused // 4,
            estimated_cost_waste=total_unused / 4 / 1_000_000 * 0.50,
            fix_command=(
                "# Remove unused language rules: rm -r "
                + " ".join(os.path.join(RULES_DIR, l) for l, _ in unused_langs)
            ),
            evidence=f"Unused languages: {[l for l, _ in unused_langs]}",
        ))

    # ── 4. Ghost skills (only count non-overridden skills) ──
    overrides = _load_skill_overrides()
    active_skill_count = sum(1 for v in overrides.values() if v == "on") + (
        sum(1 for _ in os.walk(SKILLS_DIR)) - len(overrides)
    ) if os.path.exists(SKILLS_DIR) else 0

    for oh in overheads:
        if "Skill listing" in oh.description and "(active" not in oh.description:
            total_skill_uses = sum(s.tool_calls.get("Skill", 0) for s in sessions)
            # Only flag if active skills are high AND unused
            active_overhead = next(
                (o for o in overheads if "Active skills" in o.description), oh)
            effective_tokens = active_overhead.est_tokens if active_overhead else oh.est_tokens
            effective_files = active_overhead.line_count if active_overhead else oh.line_count

            if total_skill_uses < 3 and effective_tokens > 50000:
                # Downgrade severity if skillOverrides are already in place
                sev = "medium" if len(overrides) >= 20 else "high"
                findings.append(WasteFinding(
                    id="ghost-skills",
                    category="ghost_skills",
                    severity=sev,
                    description=(
                        f"Active skills listing costs ~{effective_tokens:,} tokens/session "
                        f"({effective_files} files). "
                        f"Only {total_skill_uses} skill invocations across "
                        f"{len(sessions)} sessions."
                    ),
                    estimated_token_waste=effective_tokens * len(sessions) // 2,
                    estimated_cost_waste=(
                        effective_tokens * len(sessions) / 2 / 1_000_000 * 0.50
                    ),
                    fix_command=(
                        "# Review ~/.claude/settings.json skillOverrides. "
                        "Mark unused skills as 'user-invocable-only'."
                    ),
                    evidence=f"Active skills: {effective_files} files, Uses: {total_skill_uses}",
                ))
            break

    # ── 5. Ghost agents ──
    total_agent_spawns = sum(s.tool_calls.get("Agent", 0) for s in sessions)
    for oh in overheads:
        if "Agent definitions" in oh.description and oh.est_tokens > 30000:
            if total_agent_spawns < 10:
                findings.append(WasteFinding(
                    id="ghost-agents",
                    category="ghost_agents",
                    severity="medium",
                    description=(
                        f"Agent definitions cost ~{oh.est_tokens:,} tokens/session. "
                        f"Only {total_agent_spawns} agent spawns observed across "
                        f"{len(sessions)} sessions."
                    ),
                    estimated_token_waste=oh.est_tokens * len(sessions) // 3,
                    estimated_cost_waste=(
                        oh.est_tokens * len(sessions) / 3 / 1_000_000 * 0.50
                    ),
                    fix_command=(
                        "# Review agent files in ~/.claude/agents/. "
                        "Remove unused agent definitions or convert to lazy-load format."
                    ),
                    evidence=f"Agents directory: {oh.path}",
                ))
            break

    # ── 6. Cache inefficiency ──
    for s in sessions:
        if s.cache_read_tokens > 0 and s.cache_hit_rate < 0.3 and s.input_tokens > 50000:
            findings.append(WasteFinding(
                id=f"low-cache-{s.session_id[:8]}",
                category="cache_inefficiency",
                severity="medium",
                description=(
                    f"Session {s.session_id[:8]} has low cache hit rate "
                    f"({s.cache_hit_rate:.1%}) with {s.input_tokens:,} input tokens"
                ),
                estimated_token_waste=int(s.input_tokens * 0.3),
                estimated_cost_waste=s.input_tokens * 0.3 / 1_000_000 * 0.50,
                fix_command=(
                    "# Use prompt caching optimization: keep system prompt stable, "
                    "avoid changing early conversation context frequently"
                ),
                evidence=f"Cache hit rate: {s.cache_hit_rate:.2%}",
            ))

    # ── 7. Tool imbalance ──
    for s in sessions:
        total_tools = sum(s.tool_calls.values())
        if total_tools > 50:
            bash_pct = s.tool_calls.get("Bash", 0) / total_tools
            if bash_pct > 0.6:
                bash_count = s.tool_calls.get("Bash", 0)
                read_count = s.tool_calls.get("Read", 0)
                findings.append(WasteFinding(
                    id=f"bash-heavy-{s.session_id[:8]}",
                    category="tool_imbalance",
                    severity="low",
                    description=(
                        f"Session {s.session_id[:8]}: {bash_pct:.0%} of tool calls "
                        f"are Bash ({bash_count}/{total_tools}). "
                        f"Consider using Read/Grep instead of Bash for file inspection."
                    ),
                    estimated_token_waste=int(total_tools * bash_pct * 500),
                    estimated_cost_waste=total_tools * bash_pct * 500 / 1_000_000 * 0.50,
                    fix_command=(
                        "# Prefer Read tool for file inspection. Use Bash only for execution."
                    ),
                    evidence=f"Bash: {bash_count}, Read: {read_count}",
                ))

    return findings


# ─── Health Grade Calculator ───────────────────────────────────────────────────

def calculate_health(sessions: List[SessionTokens],
                     waste_findings: List[WasteFinding],
                     overheads: List[ConfigOverhead]) -> Tuple[int, str]:
    """Calculate health score (0-100) and grade (A-F).

    Seven scoring dimensions (CodeBurn-inspired):
      Cache efficiency   30 pts  — higher cache hit = better
      Output ratio       20 pts  — higher output/input = better
      Tool diversity     15 pts  — more unique tools = better
      Waste penalty     varies   — deductions per finding severity
      Config overhead    15 pts  — leaner config = better
      Read:Edit ratio    10 pts  — lower read/edit ratio = better
      Session balance    10 pts  — even distribution = better
    """
    if not sessions:
        return 0, "F"

    score = 0

    # 1. Cache efficiency (30 pts)
    total_cache = sum(s.cache_read_tokens for s in sessions)
    total_in = sum(s.input_tokens for s in sessions)
    if total_in > 0:
        cache_ratio = total_cache / (total_in + total_cache)
        score += int(min(cache_ratio * 50, 30))

    # 2. Output ratio (20 pts)
    ratios = [s.output_ratio for s in sessions if s.input_tokens > 1000]
    if ratios:
        avg = sum(ratios) / len(ratios)
        score += int(min(avg * 60, 20))

    # 3. Tool diversity (15 pts)
    all_tools = Counter()
    for s in sessions:
        all_tools.update(s.tool_calls)
    score += min(len(all_tools), 15)

    # 4. Waste penalties
    penalties = {"critical": -8, "high": -5, "medium": -2, "low": -1}
    for w in waste_findings:
        score += penalties.get(w.severity, 0)

    # 5. Config overhead (15 pts) — percentage of model context window
    total_overhead = sum(o.est_tokens for o in overheads if "(active" not in o.description)
    # Use active skill overhead if available (replaces full listing)
    active_skill_oh = next((o for o in overheads if "Active skills" in o.description), None)
    if active_skill_oh:
        skill_oh = next((o for o in overheads if "Skill listing" in o.description and "(active" not in o.description), None)
        if skill_oh:
            total_overhead = total_overhead - skill_oh.est_tokens + active_skill_oh.est_tokens

    overhead_pct = total_overhead / DEFAULT_CONTEXT_WINDOW
    if overhead_pct < 0.05:        # <5% → optimal
        score += 15
    elif overhead_pct < 0.10:      # <10% → great
        score += 12
    elif overhead_pct < 0.20:      # <20% → good
        score += 9
    elif overhead_pct < 0.35:      # <35% → fair
        score += 6
    elif overhead_pct < 0.50:      # <50% → needs attention
        score += 3

    # 6. Read:Edit ratio (10 pts)
    total_reads = sum(s.tool_calls.get("Read", 0) for s in sessions)
    total_edits = sum(
        s.tool_calls.get("Edit", 0) + s.tool_calls.get("Write", 0)
        for s in sessions
    )
    if total_edits > 0:
        re_ratio = total_reads / total_edits
        if re_ratio < 3:
            score += 10
        elif re_ratio < 7:
            score += 5
        else:
            score += 2
    else:
        score += 3

    # 7. Session balance (10 pts)
    if len(sessions) > 1:
        sorted_s = sorted(sessions, key=lambda s: s.input_tokens, reverse=True)
        top2 = sum(s.input_tokens for s in sorted_s[:2])
        concentration = top2 / total_in if total_in > 0 else 1
        score += int((1 - concentration) * 10)

    score = max(0, min(100, score))

    # Map score to letter grade
    grade = "F"
    for g, (lo, hi, _) in HEALTH_GRADES.items():
        if lo <= score <= hi:
            grade = g
            break

    return score, grade


# ─── O2 Vitality Integration ───────────────────────────────────────────────────

def o2_check(sessions: List[SessionTokens]) -> Dict[str, Any]:
    """Generate O2 vitality data for precise context pressure tracking.

    Uses real token data from session transcripts instead of estimates.
    Output is JSON-serializable for hook consumption.
    """
    if not sessions:
        return {"status": "no_data", "message": "No session data available"}

    latest = sessions[0]
    recent = sessions[:5]

    avg_input = sum(s.input_tokens for s in recent) / max(len(recent), 1)
    estimated_current = min(latest.input_tokens, avg_input * 3)

    # Context budget: 200K practical limit for 1M context models
    context_budget = 200000
    pressure_pct = min(100, int(estimated_current / context_budget * 100))

    if pressure_pct >= O2_THRESHOLDS["critical"]:
        status = "critical"
        message = f"Context at {pressure_pct}% — FORCE compaction recommended"
    elif pressure_pct >= O2_THRESHOLDS["warning"]:
        status = "warning"
        message = f"Context at {pressure_pct}% — consider compaction"
    else:
        status = "healthy"
        message = f"Context at {pressure_pct}% — healthy"

    turns = latest.turn_count or 1
    growth_rate = latest.input_tokens // max(turns, 1)

    return {
        "status": status,
        "pressure_pct": pressure_pct,
        "message": message,
        "estimated_current_tokens": int(estimated_current),
        "context_budget": context_budget,
        "growth_rate_per_turn": growth_rate,
        "recent_avg_input": int(avg_input),
        "latest_session_id": latest.session_id[:8],
        "latest_model": latest.model,
        "latest_turns": latest.turn_count,
        "latest_tokens": latest.input_tokens,
    }


# ─── PreCompact Hook Data ──────────────────────────────────────────────────────

def pre_compact_check(sessions: List[SessionTokens],
                      overheads: List[ConfigOverhead]) -> Dict[str, Any]:
    """Generate data for PreCompact hook consumption.

    Identifies bloated configs and provides precise recommendations
    on what to trim before context compaction.
    """
    total_overhead = sum(o.est_tokens for o in overheads)
    biggest = sorted(overheads, key=lambda o: o.est_tokens, reverse=True)[:5]
    waste = detect_waste(sessions, overheads)
    critical_waste = [w for w in waste if w.severity in ("critical", "high")]

    warnings = []
    if total_overhead > 150000:
        warnings.append(
            f"Config overhead is {total_overhead:,} tokens — "
            f"this is {total_overhead // 1000}K tokens loaded EVERY session"
        )

    for w in critical_waste[:3]:
        warnings.append(f"[{w.severity.upper()}] {w.description}")

    # Identify bloated language rules
    bloated_langs = [
        o for o in overheads
        if o.language not in ("all", "common", "web", "liyu")
        and o.est_tokens > 2000
    ]
    trim_savings = sum(o.est_tokens for o in bloated_langs)

    return {
        "total_config_overhead": total_overhead,
        "top_contributors": [(o.description, o.est_tokens) for o in biggest],
        "warnings": warnings,
        "potential_savings_by_trimming_unused": trim_savings,
        "bloated_language_rules": [(o.language, o.est_tokens) for o in bloated_langs],
        "recommend_compact": total_overhead > 150000,
    }
