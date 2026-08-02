#!/usr/bin/env python3
"""
鲤鱼 Security Layer — 5层纵深防御系统
吸收自 ECC AgentShield + MUNDO 5层安全防御 + OWASP MCP Top 10

五层防御:
  1. 输入验证层 — 检测恶意输入、prompt injection
  2. 输出净化层 — 检测敏感数据泄露 (12种格式)
  3. 权限边界检查 — 检查工具调用是否超出权限范围
  4. 审计追踪日志 — 记录所有安全事件
  5. 注入防护层 — OWASP MCP Top 10 防护

Usage:
  liyu-security-layer.py scan-input "<text>"
    扫描输入文本，检测 prompt injection

  liyu-security-layer.py scan-output "<text>"
    扫描输出文本，检测敏感数据泄露

  liyu-security-layer.py hook-post
    从 stdin 读 Claude Code JSON hook 输入，扫描输出

  liyu-security-layer.py stats
    查看安全统计

  liyu-security-layer.py reset
    重置所有计数器
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import re
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
SECURITY_STATE_FILE = 鲤鱼_HOME / "security-layer-state.json"
SECURITY_LOG_FILE = 鲤鱼_HOME / "security-audit.jsonl"

# ── OWASP MCP Top 10 防护 ──────────────────────────────────────────────────
# 来源: ECC AgentShield + OWASP MCP Top 10

@dataclass
class SecurityThreat:
    """安全威胁检测结果"""
    threat_type: str     # 威胁类型
    severity: str        # CRITICAL / HIGH / MEDIUM / LOW
    description: str     # 描述
    pattern_matched: str # 匹配到的模式
    recommendation: str  # 建议

# ── Layer 1: 输入验证 — Prompt Injection 检测 ──────────────────────────────

PROMPT_INJECTION_PATTERNS: list[tuple[str, str, str, str]] = [
    # (pattern, threat_type, severity, description)

    # 直接指令注入
    (r'ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|prompts?|rules?)',
     "direct-injection", "CRITICAL", "Direct prompt injection: ignore previous instructions"),

    (r'you\s+are\s+now\s+(?:a|an|the)\s+',
     "role-hijack", "HIGH", "Role hijacking attempt"),

    (r'(?:forget|disregard|override)\s+(?:all\s+)?(?:previous|above|prior|your)\s+(?:instructions?|rules?|constraints?)',
     "instruction-override", "CRITICAL", "Instruction override attempt"),

    (r'(?:system|assistant|user)\s*:\s*',
     "role-injection", "HIGH", "Role tag injection (system:/assistant:/user:)"),

    (r'<\|(?:im_start|im_end|system|user|assistant)\|>',
     "delimiter-injection", "CRITICAL", "Chat delimiter injection"),

    (r'(?:INST|SYS)\]',
     "instruction-tag", "HIGH", "Instruction tag injection"),

    # 代码执行注入
    (r'(?:execute|run|eval|exec)\s+(?:this\s+)?(?:code|command|script|python|javascript|bash)',
     "code-execution", "HIGH", "Code execution injection"),

    (r'(?:import|require|load)\s+(?:os|subprocess|sys|shutil)',
     "module-import", "MEDIUM", "Dangerous module import suggestion"),

    # 数据泄露诱导
    (r'(?:show|reveal|expose|leak|dump|print)\s+(?:all\s+)?(?:your\s+)?(?:instructions?|rules?|prompts?|system\s+prompt)',
     "prompt-extraction", "CRITICAL", "System prompt extraction attempt"),

    (r'(?:what|tell|show)\s+(?:is|are)\s+(?:your|the)\s+(?:original|initial|system)\s+(?:instructions?|prompt)',
     "prompt-extraction", "CRITICAL", "System prompt extraction attempt"),

    # 越狱尝试
    (r'(?:DAN|jailbreak|bypass|circumvent|workaround)\s+(?:mode|prompt|restriction)',
     "jailbreak", "CRITICAL", "Jailbreak attempt detected"),

    (r'(?:pretend|act|behave)\s+(?:as\s+if|like)\s+(?:you\s+)?(?:have\s+)?(?:no|don.t\s+have)\s+(?:restrictions?|limitations?|rules?)',
     "jailbreak", "CRITICAL", "Jailbreak: remove restrictions"),

    # 多语言混淆
    (r'(?:忽略|无视|跳过|忘记)\s*(?:之前|以上|所有|全部)\s*(?:的)?\s*(?:指令|规则|提示|约束)',
     "direct-injection-zh", "CRITICAL", "中文 prompt injection: 忽略指令"),

    (r'(?:告诉我|显示|泄露|暴露)\s*(?:你的|系统)\s*(?:指令|提示|规则|prompt)',
     "prompt-extraction-zh", "CRITICAL", "中文 prompt extraction: 泄露指令"),

    # 编码混淆
    (r'(?:base64|hex|rot13|url)\s*(?:decode|encode|decrypt)\s*(?:and|then|→)?\s*(?:execute|run|eval)',
     "encoded-execution", "HIGH", "Encoded payload execution attempt"),

    # 间接注入 (通过外部数据)
    (r'(?:read|load|fetch|download|import)\s+(?:from\s+)?(?:url|http|ftp|file)\s+.*(?:execute|run|eval)',
     "indirect-injection", "HIGH", "Indirect injection via external data"),
]

# ── Layer 2: 输出净化 — 敏感数据检测 ──────────────────────────────────────

SENSITIVE_DATA_PATTERNS: list[tuple[str, str, str, str]] = [
    # (pattern, data_type, severity, description)

    # API Keys
    (r'(?:sk|pk|api)[_-]?(?:key|secret|token)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{20,})',
     "api-key", "CRITICAL", "API key/secret detected"),

    (r'(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}',
     "aws-access-key", "CRITICAL", "AWS Access Key ID detected"),

    (r'(?:sk|pk)-[A-Za-z0-9]{20,}',
     "stripe-key", "CRITICAL", "Stripe API key detected"),

    (r'ghp_[A-Za-z0-9]{36}',
     "github-token", "CRITICAL", "GitHub Personal Access Token detected"),

    (r'glpat-[A-Za-z0-9\-]{20,}',
     "gitlab-token", "CRITICAL", "GitLab Personal Access Token detected"),

    # JWT Tokens
    (r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',
     "jwt-token", "HIGH", "JWT token detected"),

    # Bearer Tokens
    (r'[Bb]earer\s+[A-Za-z0-9_\-\.]{20,}',
     "bearer-token", "HIGH", "Bearer token detected"),

    # Database Connection Strings
    (r'(?:mysql|postgres|postgresql|mongodb|redis|sqlite):\/\/[^\s]+',
     "db-connection", "CRITICAL", "Database connection string detected"),

    # AWS/GCP/Azure Credentials
    (r'(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*[:=]\s*["\']?([A-Za-z0-9/+=]{40})',
     "aws-secret", "CRITICAL", "AWS Secret Access Key detected"),

    (r'(?:GOOGLE_APPLICATION_CREDENTIALS|GCP_SERVICE_ACCOUNT)\s*[:=]\s*["\']?([^\s"\']+)',
     "gcp-credentials", "CRITICAL", "GCP credentials detected"),

    # Private Keys
    (r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----',
     "private-key", "CRITICAL", "Private key detected"),

    # Passwords
    (r'(?:password|passwd|pwd)\s*[:=]\s*["\']?([^\s"\']{8,})',
     "password", "HIGH", "Password detected"),

    # Session Tokens
    (r'(?:session[_-]?id|session[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{20,})',
     "session-token", "HIGH", "Session token detected"),

    # Credit Card Numbers
    (r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b',
     "credit-card", "CRITICAL", "Credit card number detected"),

    # SSN (US)
    (r'\b\d{3}-\d{2}-\d{4}\b',
     "ssn", "CRITICAL", "US Social Security Number detected"),

    # Email Addresses (context-dependent)
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
     "email", "LOW", "Email address detected"),
]

# ── Layer 3: 权限边界检查 ──────────────────────────────────────────────────

PERMISSION_BOUNDARIES: list[tuple[str, str, str]] = [
    # (pattern, boundary_type, description)

    # 文件系统边界
    (r'(?:read|write|delete|create|modify)\s+(?:\/etc|\/usr|\/var|\/bin|\/sbin|\/boot|\/dev|\/sys|\/proc)',
     "filesystem", "Accessing system directory"),

    (r'(?:read|write|delete|create|modify)\s+(?:~\/\.(?:ssh|aws|gnupg|gcloud))',
     "filesystem", "Accessing sensitive config directory"),

    # 网络边界
    (r'(?:connect|send|post|upload)\s+(?:to\s+)?(?:https?:\/\/[^\s]+)',
     "network", "External network connection"),

    # 进程边界
    (r'(?:execute|run|spawn|fork|kill|signal)\s+(?:process|command|script)',
     "process", "Process execution/manipulation"),

    # 数据库边界
    (r'(?:query|execute|drop|truncate|delete|update|insert|alter)\s+(?:table|database|schema)',
     "database", "Database operation"),
]

# ── Layer 4: 审计追踪 ──────────────────────────────────────────────────────

def log_security_event(event_type: str, severity: str, details: dict) -> None:
    """记录安全事件到审计日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "severity": severity,
        "details": details,
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(SECURITY_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[security-layer] Warning: audit log write failed: {e}", file=sys.stderr)


# ── Layer 5: 注入防护 ──────────────────────────────────────────────────────

def scan_input(text: str) -> list[SecurityThreat]:
    """扫描输入文本，检测 prompt injection (Layer 1 + Layer 5)"""
    threats = []
    text_lower = text.lower()

    for pattern, threat_type, severity, description in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            threats.append(SecurityThreat(
                threat_type=threat_type,
                severity=severity,
                description=description,
                pattern_matched=pattern[:50] + "...",
                recommendation="Block this input and warn user",
            ))

    return threats


def scan_output(text: str) -> list[SecurityThreat]:
    """扫描输出文本，检测敏感数据泄露 (Layer 2)"""
    threats = []

    for pattern, data_type, severity, description in SENSITIVE_DATA_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            threats.append(SecurityThreat(
                threat_type=f"data-leak:{data_type}",
                severity=severity,
                description=f"{description} ({len(matches)} occurrence(s))",
                pattern_matched=pattern[:50] + "...",
                recommendation="Redact sensitive data before output",
            ))

    return threats


def check_permissions(tool_name: str, tool_input: dict) -> list[SecurityThreat]:
    """检查工具调用是否超出权限边界 (Layer 3)"""
    threats = []

    # 构建检查字符串
    check_str = f"{tool_name} {json.dumps(tool_input)}"

    for pattern, boundary_type, description in PERMISSION_BOUNDARIES:
        if re.search(pattern, check_str, re.IGNORECASE):
            threats.append(SecurityThreat(
                threat_type=f"permission:{boundary_type}",
                severity="MEDIUM",
                description=description,
                pattern_matched=pattern[:50] + "...",
                recommendation="Verify user has appropriate permissions",
            ))

    return threats


# ── State Management ──────────────────────────────────────────────────────

def load_state() -> dict:
    """加载安全状态"""
    if SECURITY_STATE_FILE.exists():
        try:
            return json.loads(SECURITY_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_scans": 0,
        "threats_detected": 0,
        "threats_blocked": 0,
        "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "by_type": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state: dict) -> None:
    """持久化安全状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    SECURITY_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def record_threat(state: dict, threat: SecurityThreat) -> None:
    """记录威胁到状态"""
    state["threats_detected"] += 1
    state["by_severity"][threat.severity] = state["by_severity"].get(threat.severity, 0) + 1
    state["by_type"][threat.threat_type] = state["by_type"].get(threat.threat_type, 0) + 1


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "scan-input":
        if len(sys.argv) < 3:
            print("Usage: liyu-security-layer.py scan-input '<text>'", file=sys.stderr)
            sys.exit(1)

        text = sys.argv[2]
        threats = scan_input(text)
        state = load_state()
        state["total_scans"] += 1

        if threats:
            for t in threats:
                record_threat(state, t)
                log_security_event("input-scan", t.severity, {
                    "threat_type": t.threat_type,
                    "description": t.description,
                    "text_preview": text[:100],
                })

            save_state(state)

            print("🚨 INPUT THREATS DETECTED:")
            for t in threats:
                severity_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
                print(f"  {severity_icon.get(t.severity, '⚪')} [{t.severity}] {t.threat_type}: {t.description}")
                print(f"    Recommendation: {t.recommendation}")
            sys.exit(2)
        else:
            save_state(state)
            print("✅ No input threats detected")
            sys.exit(0)

    elif cmd == "scan-output":
        if len(sys.argv) < 3:
            print("Usage: liyu-security-layer.py scan-output '<text>'", file=sys.stderr)
            sys.exit(1)

        text = sys.argv[2]
        threats = scan_output(text)
        state = load_state()
        state["total_scans"] += 1

        if threats:
            for t in threats:
                record_threat(state, t)
                log_security_event("output-scan", t.severity, {
                    "threat_type": t.threat_type,
                    "description": t.description,
                    "text_preview": text[:100],
                })

            save_state(state)

            print("🚨 OUTPUT THREATS DETECTED:")
            for t in threats:
                severity_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
                print(f"  {severity_icon.get(t.severity, '⚪')} [{t.severity}] {t.threat_type}: {t.description}")
                print(f"    Recommendation: {t.recommendation}")
            sys.exit(2)
        else:
            save_state(state)
            print("✅ No output threats detected")
            sys.exit(0)

    elif cmd == "hook-post":
        # Hook mode: read JSON from stdin
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, OSError):
            print(json.dumps({"decision": "allow", "reason": "invalid hook input"}))
            sys.exit(0)

        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        # Scan tool output if available
        output_text = tool_input.get("output", "")
        if output_text:
            threats = scan_output(str(output_text))
            state = load_state()
            state["total_scans"] += 1

            if threats:
                for t in threats:
                    record_threat(state, t)
                    log_security_event("hook-post", t.severity, {
                        "tool": tool_name,
                        "threat_type": t.threat_type,
                        "description": t.description,
                    })

                save_state(state)

                # Find highest severity
                severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
                max_severity = max(threats, key=lambda t: severity_order.get(t.severity, 0))

                output = {
                    "decision": "warn" if max_severity.severity in ["CRITICAL", "HIGH"] else "allow",
                    "reason": f"[鲤鱼 Security Layer] {max_severity.description}",
                    "threats": [{"type": t.threat_type, "severity": t.severity, "desc": t.description} for t in threats],
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": f"⚠️ Security: {len(threats)} threat(s) detected in output",
                    },
                }
                print(json.dumps(output, ensure_ascii=False))
                sys.exit(0)

        save_state(load_state())
        print(json.dumps({"decision": "allow", "reason": "no threats detected"}))
        sys.exit(0)

    elif cmd == "stats":
        state = load_state()
        print("═══ 鲤鱼 Security Layer Statistics ═══")
        print(f"  总计扫描:     {state.get('total_scans', 0)}")
        print(f"  威胁检测:     {state.get('threats_detected', 0)}")
        print(f"  威胁阻断:     {state.get('threats_blocked', 0)}")
        print()
        print("  按严重性:")
        for sev, count in state.get("by_severity", {}).items():
            if count > 0:
                print(f"    {sev}: {count}")
        print()
        if state.get("by_type"):
            print("  按类型:")
            for ttype, count in sorted(state["by_type"].items(), key=lambda x: -x[1])[:10]:
                print(f"    {ttype}: {count}")

    elif cmd == "reset":
        save_state({
            "total_scans": 0,
            "threats_detected": 0,
            "threats_blocked": 0,
            "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "by_type": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Security Layer 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
