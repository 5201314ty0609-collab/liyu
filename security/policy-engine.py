#!/usr/bin/env python3
"""
鲤鱼 Policy Engine — Structured rule chains with priority adjudication.
Absorbed from MUNDO v2.0.9 Policy Engine (15 built-in rules, chain evaluation).

Usage:
  policy-engine.py check <action> [--context '{}']   Evaluate action against rules
  policy-engine.py list [--domain X]                  List active rules
  policy-engine.py conflicts                           Show rule conflicts
  policy-engine.py stats                               Rule statistics
  policy-engine.py chains                              Show rule chains
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

鲤鱼_HOME = Path.home() / ".claude/liyu"
RULES_FILE = 鲤鱼_HOME / "policy-rules.json"
POLICY_LOG = 鲤鱼_HOME / "policy-decisions.jsonl"

# ── Built-in Rules ────────────────────────────────────────────────────────

DEFAULT_RULES = {
    "rules": [
        # ▸ CRITICAL — Block destructive actions (enforcement Lv7)
        {
            "id": "pol-block-rm-rf",
            "priority": 100,
            "domain": "security",
            "condition": "action matches 'rm -rf /|git push --force|sudo rm'",
            "action": "block",
            "message": "Destructive system command blocked",
            "chain": "security-gate",
            "chain_order": 1,
        },
        {
            "id": "pol-block-secret-leak",
            "priority": 99,
            "domain": "security",
            "condition": "file_content contains secret patterns (API_KEY, token, password)",
            "action": "block",
            "message": "Potential secret leak detected",
            "chain": "security-gate",
            "chain_order": 2,
        },

        # ▸ HIGH — Warn before execution (enforcement Lv6)
        {
            "id": "pol-warn-system-dir",
            "priority": 80,
            "domain": "security",
            "condition": "target_path starts with /etc|/usr|/System",
            "action": "warn",
            "message": "Writing to system directory requires confirmation",
            "chain": "file-safety",
            "chain_order": 1,
        },
        {
            "id": "pol-warn-ssh-key",
            "priority": 79,
            "domain": "security",
            "condition": "target_path contains .ssh|id_rsa|authorized_keys",
            "action": "warn",
            "message": "SSH key operation detected",
            "chain": "file-safety",
            "chain_order": 2,
        },

        # ▸ MEDIUM — Quality gates
        {
            "id": "pol-quality-file-size",
            "priority": 60,
            "domain": "code-quality",
            "condition": "write_content lines > 800",
            "action": "warn",
            "message": "File exceeds 800-line limit (鲤鱼 coding standard)",
            "chain": "quality-gate",
            "chain_order": 1,
        },
        {
            "id": "pol-quality-nesting",
            "priority": 55,
            "domain": "code-quality",
            "condition": "code has nesting depth > 4",
            "action": "warn",
            "message": "Deep nesting detected (>4 levels)",
            "chain": "quality-gate",
            "chain_order": 2,
        },
        {
            "id": "pol-quality-function-length",
            "priority": 54,
            "domain": "code-quality",
            "condition": "function length > 50 lines",
            "action": "warn",
            "message": "Function exceeds 50-line limit",
            "chain": "quality-gate",
            "chain_order": 3,
        },

        # ▸ Design taste rules
        {
            "id": "pol-taste-em-dash",
            "priority": 70,
            "domain": "design-taste",
            "condition": "output contains '—' (em-dash)",
            "action": "block",
            "message": "Em-dash banned by Taste-Skill Anti-Slop rules",
            "chain": "taste-gate",
            "chain_order": 1,
        },
        {
            "id": "pol-taste-ai-purple",
            "priority": 68,
            "domain": "design-taste",
            "condition": "color value in #7C3AED..#8B5CF6 range",
            "action": "warn",
            "message": "AI-purple detected — use Catppuccin Mocha palette",
            "chain": "taste-gate",
            "chain_order": 2,
        },
        {
            "id": "pol-taste-inter-default",
            "priority": 66,
            "domain": "design-taste",
            "condition": "font-family is 'Inter' without deliberate choice",
            "action": "warn",
            "message": "Inter as default font — deliberate choice required",
            "chain": "taste-gate",
            "chain_order": 3,
        },

        # ▸ Context awareness
        {
            "id": "pol-context-pressure",
            "priority": 85,
            "domain": "context",
            "condition": "context_usage > 70%",
            "action": "warn",
            "message": "Context pressure >70% — compact before proceeding",
            "chain": "context-guard",
            "chain_order": 1,
        },
        {
            "id": "pol-context-critical",
            "priority": 90,
            "domain": "context",
            "condition": "context_usage > 85%",
            "action": "block",
            "message": "Context critical >85% — force compact required",
            "chain": "context-guard",
            "chain_order": 2,
        },

        # ▸ Evolution awareness
        {
            "id": "pol-evolution-stale",
            "priority": 40,
            "domain": "evolution",
            "condition": "days_since_last_evolution > 3",
            "action": "warn",
            "message": "Evolution engine idle >3 days — consider running evolve",
            "chain": "evolution-guard",
            "chain_order": 1,
        },
        {
            "id": "pol-evolution-no-promotions",
            "priority": 35,
            "domain": "evolution",
            "condition": "active_frameworks >= 10 AND validated_frameworks == 0",
            "action": "warn",
            "message": "10+ active frameworks with zero promotions — review accumulation",
            "chain": "evolution-guard",
            "chain_order": 2,
        },
        {
            "id": "pol-sense-cascade",
            "priority": 75,
            "domain": "metacognition",
            "condition": "nociception_errors > 2 in 5 calls",
            "action": "block",
            "message": "Error cascade detected — pause and analyze root cause",
            "chain": "sense-guard",
            "chain_order": 1,
        },
    ],
    "chains": {
        "security-gate": {"description": "Critical security checks (Lv7)", "stop_on_block": True},
        "file-safety": {"description": "File system safety (Lv6)", "stop_on_block": False},
        "quality-gate": {"description": "Code quality standards", "stop_on_block": False},
        "taste-gate": {"description": "Design taste enforcement (Anti-Slop)", "stop_on_block": True},
        "context-guard": {"description": "Context pressure monitoring (O2)", "stop_on_block": True},
        "sense-guard": {"description": "Metacognitive sense alerts", "stop_on_block": True},
        "evolution-guard": {"description": "Evolution health checks", "stop_on_block": False},
    },
    "config": {
        "version": "1.0.0",
        "source": "MUNDO v2.0.9 Policy Engine",
        "default_priority": 50,
        "chain_evaluation": "ordered",
    },
}


# ── Engine ─────────────────────────────────────────────────────────────────

class PolicyEngine:
    def __init__(self):
        self.rules = self._load_rules()
        self.chains = self.rules.get("chains", {})
        self.config = self.rules.get("config", {})

    def _load_rules(self):
        if RULES_FILE.exists():
            try:
                return json.loads(RULES_FILE.read_text())
            except Exception:
                pass
        return dict(DEFAULT_RULES)

    def _save_rules(self):
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.rules["updated_at"] = datetime.now(timezone.utc).isoformat()
        RULES_FILE.write_text(json.dumps(self.rules, ensure_ascii=False, indent=2))

    def save(self):
        self._save_rules()

    def check(self, action: str, context: dict = None) -> dict:
        """Evaluate an action against all applicable rules.
        Returns: {allowed: bool, decisions: [...], summary: str}
        """
        context = context or {}
        decisions = []

        # Find matching rules
        for rule in sorted(self.rules["rules"], key=lambda r: -r["priority"]):
            if self._rule_matches(rule, action, context):
                decision = self._evaluate_rule(rule, action, context)
                decisions.append(decision)

        # Priority adjudication: highest priority block wins
        blocks = [d for d in decisions if d["verdict"] == "block"]
        warns = [d for d in decisions if d["verdict"] == "warn"]

        allowed = len(blocks) == 0

        # Run chain evaluation
        chain_results = self._evaluate_chains(action, context)

        return {
            "allowed": allowed,
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decisions": decisions,
            "blocks": blocks,
            "warns": warns,
            "chain_results": chain_results,
            "summary": "ALLOWED" if allowed else f"BLOCKED by {len(blocks)} rule(s)",
        }

    def _rule_matches(self, rule, action, context):
        """Simple condition matching. The condition is a human-readable string
        that describes what to check. In a full implementation this would be
        a proper expression evaluator."""
        cond = rule.get("condition", "").lower()
        action_lower = action.lower()

        # Parse condition keywords
        if "action matches" in cond:
            patterns = cond.split("action matches ")[1].split("'")
            patterns = [p for p in patterns if p and p != " "]
            if "|".join(patterns):
                for pat in patterns[0].split("|"):
                    if pat.strip() in action_lower:
                        return True

        if "target_path" in cond:
            paths = cond.split("target_path ")[1]
            target = context.get("target_path", "")
            if "starts with" in cond:
                prefixes = paths.replace("starts with ", "").split("|")
                for p in prefixes:
                    if target.startswith(p.strip()):
                        return True
            if "contains" in cond:
                keywords = paths.replace("contains ", "").split("|")
                for k in keywords:
                    if k.strip() in target:
                        return True

        if "file_content" in cond:
            if "secret patterns" in cond:
                content = context.get("file_content", "")
                secret_patterns = ["API_KEY", "token", "password", "secret"]
                if any(p.lower() in content.lower() for p in secret_patterns):
                    return True

        if "write_content lines" in cond:
            lines = context.get("write_content_lines", 0)
            threshold = int(cond.split(">")[1].strip()) if ">" in cond else 0
            if lines > threshold:
                return True

        if "code has nesting depth" in cond:
            depth = context.get("nesting_depth", 0)
            threshold = int(cond.split(">")[1].strip()) if ">" in cond else 0
            if depth > threshold:
                return True

        if "function length" in cond:
            length = context.get("function_length", 0)
            threshold = int(cond.split(">")[1].strip().split()[0]) if ">" in cond else 0
            if length > threshold:
                return True

        if "output contains" in cond:
            needle = cond.split("output contains ")[1].strip().strip("'")
            output = context.get("output", "")
            if needle in output:
                return True

        if "color value" in cond:
            color = context.get("color_value", "")
            if "#7C3AED" <= color <= "#8B5CF6":
                return True

        if "font-family" in cond:
            font = context.get("font_family", "")
            if "inter" in font.lower() and "deliberate" not in context:
                return True

        if "context_usage >" in cond:
            usage = context.get("context_usage", 0)
            threshold = int(cond.split(">")[1].strip().rstrip("%"))
            if usage > threshold:
                return True

        if "days_since_last_evolution" in cond:
            days = context.get("days_since_last_evolution", 0)
            threshold = int(cond.split(">")[1].strip()) if ">" in cond else 0
            if days > threshold:
                return True

        if "active_frameworks >=" in cond and "validated_frameworks ==" in cond:
            active = context.get("active_frameworks", 0)
            validated = context.get("validated_frameworks", 0)
            if active >= 10 and validated == 0:
                return True

        if "nociception_errors" in cond:
            errors = context.get("nociception_errors", 0)
            if errors > 2:
                return True

        return False

    def _evaluate_rule(self, rule, action, context):
        return {
            "rule_id": rule["id"],
            "priority": rule["priority"],
            "domain": rule["domain"],
            "verdict": rule["action"],
            "message": rule["message"],
            "chain": rule.get("chain", ""),
        }

    def _evaluate_chains(self, action, context):
        """Evaluate chains in order. If stop_on_block, halt chain on first block."""
        results = {}
        for chain_name, chain_def in sorted(self.chains.items()):
            chain_rules = sorted(
                [r for r in self.rules["rules"] if r.get("chain") == chain_name],
                key=lambda r: r.get("chain_order", 0)
            )
            chain_result = {"rules_evaluated": 0, "blocks": 0, "warns": 0, "stopped": False}
            for rule in chain_rules:
                if self._rule_matches(rule, action, context):
                    decision = self._evaluate_rule(rule, action, context)
                    chain_result["rules_evaluated"] += 1
                    if decision["verdict"] == "block":
                        chain_result["blocks"] += 1
                        if chain_def.get("stop_on_block"):
                            chain_result["stopped"] = True
                            break
                    elif decision["verdict"] == "warn":
                        chain_result["warns"] += 1
            results[chain_name] = chain_result
        return results

    def list_rules(self, domain: str = None):
        rules = self.rules["rules"]
        if domain:
            rules = [r for r in rules if r["domain"] == domain]
        return sorted(rules, key=lambda r: -r["priority"])

    def stats(self):
        rules = self.rules["rules"]
        domains = {}
        verdicts = {}
        for r in rules:
            domains[r["domain"]] = domains.get(r["domain"], 0) + 1
            verdicts[r["action"]] = verdicts.get(r["action"], 0) + 1
        return {
            "total_rules": len(rules),
            "total_chains": len(self.chains),
            "by_domain": domains,
            "by_verdict": verdicts,
            "highest_priority": max(r["priority"] for r in rules),
        }

    def log_decision(self, result):
        POLICY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(POLICY_LOG, "a") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    engine = PolicyEngine()

    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "check":
        action = sys.argv[2] if len(sys.argv) > 2 else "test"
        ctx = {}
        for i, arg in enumerate(sys.argv):
            if arg == "--context" and i + 1 < len(sys.argv):
                try:
                    ctx = json.loads(sys.argv[i + 1])
                except Exception:
                    pass
        result = engine.check(action, ctx)
        engine.log_decision(result)
        print(f"Action: {action}")
        print(f"Verdict: {result['summary']}")
        if result["decisions"]:
            for d in result["decisions"]:
                icon = {"block": "🚫", "warn": "⚠️"}.get(d["verdict"], "ℹ️")
                print(f"  {icon} [{d['domain']}] P{d['priority']} {d['message']}")

    elif cmd == "list":
        domain = sys.argv[3] if "--domain" in sys.argv and len(sys.argv) > 3 else None
        if domain is None and len(sys.argv) > 2 and not sys.argv[2].startswith("--"):
            domain = sys.argv[2]
        rules = engine.list_rules(domain)
        for r in rules:
            icon = {"block": "🚫", "warn": "⚠️"}.get(r["action"], "ℹ️")
            print(f"  {icon} P{r['priority']:3d} [{r['domain']:15s}] {r['message']}")

    elif cmd == "chains":
        for name, chain in engine.chains.items():
            rules = [r for r in engine.rules["rules"] if r.get("chain") == name]
            print(f"  {name}: {chain['description']} ({len(rules)} rules, stop_on_block={chain['stop_on_block']})")

    elif cmd == "stats":
        s = engine.stats()
        print(f"Rules: {s['total_rules']}")
        print(f"Chains: {s['total_chains']}")
        print(f"By domain: {s['by_domain']}")
        print(f"By verdict: {s['by_verdict']}")

    elif cmd == "init":
        engine.save()
        print(f"Policy rules saved to {RULES_FILE}")


if __name__ == "__main__":
    main()
