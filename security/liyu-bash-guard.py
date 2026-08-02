#!/usr/bin/env python3
"""
鲤鱼 Bash Safety Gate — AST-Aware Command Classification
Absorbed from Kintsugi (github.com/arrowassassin/kintsugi) v0.x architecture.

Kintsugi 吸收要点:
  - Two-pass classification: tokenizer pass + AST-aware unwrapping
  - Three-tier verdict: SAFE / CAUTION / DANGER (mirrors Kintsugi's Safe/Catastrophic/Ambiguous)
  - Deterministic, LLM-free — rules written by humans, never model guessing
  - Wrap-depth unwrapping: strip sudo, bash -c, find -exec, xargs to reach real payload
  - Fail toward caution — unparseable commands default to CAUTION, never SAFE

鲤鱼 适配:
  - 纯 Python regex 实现，无需 Rust toolchain
  - 集成 Nociception (pain sense): 重复危险尝试自动升级 CAUTION→DANGER
  - Three-tier: SAFE (exit 0), CAUTION (exit 0 + stderr warning), DANGER (exit 2 block)
  - 专注 coding agent 最可能触发的危险模式

Usage:
  liyu-bash-guard.py check "<bash command>"
    分类单个命令，打印 verdict + 原因

  liyu-bash-guard.py hook-pre
    从 stdin 读 Claude Code JSON hook 输入，输出决策 JSON
    Exit 0: allow (SAFE/CAUTION), Exit 2: block (DANGER)

  liyu-bash-guard.py stats
    查看防护统计

  liyu-bash-guard.py reset
    重置所有计数器和状态
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
STATE_FILE = 鲤鱼_HOME / "bash-guard-state.json"
HISTORY_FILE = 鲤鱼_HOME / "bash-guard-history.jsonl"

# ── Constants ──────────────────────────────────────────────────────────────
MAX_WRAP_DEPTH = 6  # 最大解包深度 (Kintsugi uses 8)

# ── Verdict Enum ───────────────────────────────────────────────────────────
# 严重度: SAFE=0, CAUTION=1, DANGER=2
SEVERITY = {"SAFE": 0, "CAUTION": 1, "DANGER": 2}

# ── DANGER Signals ─────────────────────────────────────────────────────────
# 匹配到任何一个 = 直接 DANGER (block)
DANGER_SIGNALS: list[tuple[str, str, str]] = [
    # --- 数据毁灭: rm ---
    (r'\brm\s+.*-(?:-[rf]|[rf][rf]|[rf]-[rf])\b.*/(?:etc|usr|var|bin|sbin|boot|dev|home|root|sys|opt|tmp)\b',
     "rm-recursive-system", "rm -rf targeting system directory"),

    (r'\brm\s+.*-(?:-[rf]|[rf][rf]|[rf]-[rf])\b\s+(?:~|/|/\*)\b',
     "rm-recursive-root", "rm -rf targeting root or home"),

    (r'\brm\s+.*-(?:-[rf]|[rf][rf]|[rf]-[rf])\b\s+\$(?!\()',
     "rm-recursive-variable", "rm -rf with unbounded variable target"),

    # --- 数据毁灭: 磁盘操作 ---
    (r'(?:\bdd\b.*\bof\s*=\s*/dev/(?:sd[a-z]+|nvme\d+n\d+|hd[a-z]+|vd[a-z]+|disk\d+|mmcblk\d+))',
     "dd-block-device", "dd writing to block device"),

    (r'\bmkfs\.\S+|mke2fs\b|\.\./', '', ''),  # 伪造路径兜底——触发下面更精确的
    (r'\b(?:mkfs\.\S+|mke2fs|mkdosfs|mkntfs|newfs)\b',
     "mkfs", "filesystem creation (destroys existing data)"),

    (r'\b(?:fdisk|parted|sgdisk|gdisk|gparted)\b',
     "disk-partitioning", "disk partitioning tool"),

    (r'\b(?:wipefs|shred|blkdiscard)\b',
     "disk-wipe", "disk wiping / secure erase"),

    # --- 权限: chmod/chown 递归 ---
    (r'\bchmod\s+.*-(?:-R|R)\s+777\b',
     "chmod-777-recursive", "chmod -R 777 (world-writable recursive)"),

    (r'\bchmod\s+.*-(?:-R|R)\s+.*/(?:etc|usr|var|bin|sbin|boot|dev|sys|opt)\b',
     "chmod-recursive-system", "chmod -R on system directory"),

    (r'\bchown\s+.*-(?:-R|R)\b\s+.*/(?:etc|usr|var|bin|sbin|boot|dev|sys|opt)\b',
     "chown-recursive-system", "chown -R on system directory"),

    # --- Fork 炸弹 ---
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:',
     "fork-bomb", "fork bomb pattern :(){ :|:& };:"),

    (r'\b(?:perl|python\d*|ruby)\s+.*\bfork\b.*\bwhile\b.*\btrue\b',
     "fork-bomb-script", "fork bomb via scripting language"),

    # --- 重定向到块设备 ---
    (r'>\s*/dev/(?:sd[a-z]+|nvme\d+n\d+|hd[a-z]+|vd[a-z]+|disk\d+|mmcblk\d+)',
     "redirect-block-device", "redirect output to block device"),

    (r'>>\s*/dev/(?:sd[a-z]+|nvme\d+n\d+|hd[a-z]+|vd[a-z]+|disk\d+|mmcblk\d+)',
     "append-block-device", "append to block device"),

    # --- 管道到 shell (远程代码执行) ---
    (r'(?:curl|wget|fetch)\s+.*\|\s*(?:ba|da|z|fi|tc|c|k)?sh\b',
     "curl-pipe-sh", "curl/wget piped to shell interpreter"),

    (r'\b(?:base64|base32|xxd)\s+.*\|\s*(?:ba|da|z|fi|tc|c|k)?sh\b',
     "decode-pipe-sh", "base64 decode piped to shell"),

    # --- Git 破坏性操作 ---
    (r'\bgit\s+push\s+.*(?:-f|--force|--force-with-lease|--mirror|--delete)\b',
     "git-force-push", "git force push (destroys remote history)"),

    (r'\bgit\s+reset\s+--hard\b',
     "git-reset-hard", "git reset --hard (destroys local changes)"),

    (r'\bgit\s+clean\s+.*(?:-f|--force)\b',
     "git-clean-force", "git clean -f (deletes untracked files)"),

    (r'\bgit\s+branch\s+-D\b',
     "git-branch-delete-force", "git branch -D (force-delete branch)"),

    # --- 敏感文件读取 ---
    (r'\b(?:cat|head|tail|less|more|read|open|view)\s+.*(?:~|/root|/home)/\.(?:ssh|aws|gnupg|gcloud|azure|config)/',
     "read-secret-dir", "reading sensitive config directory contents"),

    (r'\b(?:cat|head|tail|less|more)\s+.*(?:id_rsa|id_ed25519|id_ecdsa|authorized_keys)',
     "read-ssh-key", "reading SSH private key or authorized_keys"),

    # --- find -delete (silent mass deletion) ---
    (r'\bfind\b.*-delete\b',
     "find-delete", "find with -delete flag (mass file deletion)"),

    # --- 重定向覆写敏感文件 ---
    (r'>\s*(?:~|/root|/home).*\.(?:env|pem|key|p12|pfx)\b',
     "clobber-secret-file", "redirect overwriting secret/credential file"),

    # --- 基础设施破坏 ---
    (r'\bterraform\s+destroy\b',
     "terraform-destroy", "terraform destroy (destroys infrastructure)"),

    (r'\btofu\s+destroy\b',
     "opentofu-destroy", "OpenTofu destroy"),

    (r'\bkubectl\s+delete\b',
     "kubectl-delete", "kubectl delete (destroys k8s resources)"),

    (r'\bkubectl\s+drain\b',
     "kubectl-drain", "kubectl drain (evicts all pods from node)"),

    (r'\bhelm\s+(?:delete|uninstall)\b',
     "helm-delete", "helm delete/uninstall"),

    (r'\bdocker\s+system\s+prune\b',
     "docker-system-prune", "docker system prune (deletes all unused data)"),

    (r'\bdocker\s+volume\s+(?:rm|prune)\b',
     "docker-volume-rm", "docker volume rm/prune"),

    # --- SQL 破坏 ---
    (r'\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|PROCEDURE|TRIGGER)\b',
     "sql-drop", "SQL DROP statement"),

    (r'\bTRUNCATE\s+(?:TABLE\s+)?',
     "sql-truncate", "SQL TRUNCATE statement"),

    (r'\bDELETE\s+FROM\b',
     "sql-delete", "SQL DELETE FROM statement"),

    # --- 系统级危险: sudo ---
    (r'\bsudo\s+su\b',
     "sudo-su", "sudo su (elevate to root shell)"),

    (r'\bsudo\s+.*\brm\s+.*-(?:-[rf]|[rf][rf])\b',
     "sudo-rm", "sudo rm with force/recursive flags"),

    (r'\bsudo\s+.*\bchmod\s+.*777\b',
     "sudo-chmod-777", "sudo chmod 777"),

    # --- 内核参数修改 ---
    (r'\b(?:sysctl|echo)\s+.*>\s*/proc/sys/',
     "modify-kernel-param", "modifying kernel parameters via /proc/sys"),

    (r'>\s*/etc/(?:sysctl\.conf|sysctl\.d/|fstab|passwd|shadow|group|sudoers|hosts)\b',
     "overwrite-system-config", "redirect overwriting critical system config"),
]

# ── CAUTION Signals ────────────────────────────────────────────────────────
# 匹配到 = CAUTION (warning, not blocking)
CAUTION_SIGNALS: list[tuple[str, str, str]] = [
    # --- 文件删除 (非系统目录) ---
    (r'\brm\s+.*-(?:-[rf]|[rf][rf])\b',
     "rm-recursive", "rm with recursive/force flags"),

    (r'\brmdir\b',
     "rmdir", "rmdir (removes empty directories)"),

    # --- 权限变更 ---
    (r'\bchmod\s+777\b',
     "chmod-777", "chmod 777 (world-writable)"),

    (r'\bchmod\s+.*-(?:-R|R)\b',
     "chmod-recursive", "chmod -R (recursive permission change)"),

    (r'\bchown\s+.*-(?:-R|R)\b',
     "chown-recursive", "chown -R (recursive ownership change)"),

    # --- 磁盘操作 (非特定设备) ---
    (r'\bdd\b.*\bof\s*=',
     "dd-write", "dd writing to output file"),

    # --- Git 中等风险 ---
    (r'\bgit\s+reset\b(?!\s+--)',
     "git-reset", "git reset (may lose staged changes)"),

    (r'\bgit\s+checkout\s+--?\s',
     "git-checkout-discard", "git checkout -- (discard file changes)"),

    (r'\bgit\s+stash\s+drop\b',
     "git-stash-drop", "git stash drop (deletes stashed changes)"),

    # --- 系统服务操作 ---
    (r'\b(?:systemctl|service)\s+(?:stop|disable|mask)\b',
     "service-stop", "stopping/disabling system service"),

    (r'\b(?:systemctl|service)\s+restart\b',
     "service-restart", "restarting system service"),

    # --- 全局包安装 ---
    (r'\b(?:npm|yarn|pnpm|pip\d*|pipx)\s+(?:install|add)\s+(?:-g|--global)\b',
     "global-install", "global package installation"),

    (r'\b(?:gem|cargo|brew|apt-get|apt|yum|dnf|pacman|zypper)\s+install\b',
     "system-pkg-install", "system-level package installation"),

    # --- Docker 删除 ---
    (r'\bdocker\s+(?:rm|rmi|container\s+rm|image\s+rm)\b',
     "docker-rm", "docker container/image removal"),

    (r'\bdocker\s+(?:compose\s+down|stack\s+rm)\b',
     "docker-compose-down", "docker compose down"),

    # --- 环境变量注入 ---
    (r'\bexport\s+\w+\s*=\s*.*\$\(.*curl',
     "export-curl-subshell", "export with curl command substitution (possible exfiltration)"),

    # --- 网络监听/暴露 ---
    (r'\b(?:nc|ncat|netcat)\s+.*-(?:l|e)\b',
     "netcat-listen-exec", "netcat in listen or exec mode"),

    # --- 解密操作 ---
    (r'\bopenssl\s+.*-(?:d|decrypt)\b',
     "openssl-decrypt", "openssl decryption operation"),

    # --- crontab 修改 ---
    (r'\bcrontab\s+',
     "crontab-edit", "modifying crontab entries"),

    # --- iptables/nftables 修改 ---
    (r'\b(?:iptables|nft|nftables)\s+.*-(?:A|I|D|F|X|P)\b',
     "firewall-modify", "modifying firewall rules"),

    # --- mv 到系统目录 ---
    (r'\bmv\b.*/(?:etc|usr/local/bin|usr/bin|usr/sbin)\b',
     "mv-to-system", "moving files to system directory"),

    # --- cp 到系统目录 ---
    (r'\bcp\b.*/(?:etc|usr/local/bin|usr/bin|usr/sbin)\b',
     "cp-to-system", "copying files to system directory"),
]

# ── SAFE Signals ───────────────────────────────────────────────────────────
# 明确安全: 只读操作。匹配到直接返回 SAFE（不继续检查其他信号）。
# 注意: 必须先检查 DANGER/CAUTION，SAFE 是兜底快速路径。
SAFE_SIGNALS: list[tuple[str, str]] = [
    # 文件浏览/查看
    (r'^\s*(?:ls|ll|dir|vdir)\b', "list directory"),
    (r'^\s*(?:cat|head|tail|less|more)\s+(?!.*/(?:\.ssh|\.aws|\.gnupg|\.gcloud)/)', "read file"),
    (r'^\s*(?:file|stat)\b', "file info"),
    (r'^\s*(?:pwd|which|type|whereis|whence)\b', "path info"),
    (r'^\s*(?:echo|printf)\s+["\']?\w', "echo text"),

    # 搜索
    (r'^\s*(?:find|grep|rg|ag)\s+(?!.*-exec|.*-delete)', "search files"),
    (r'^\s*(?:locate|mlocate|mdfind)\b', "locate files"),

    # Git 只读
    (r'^\s*git\s+(?:status|log|diff|show|branch\b(?!\s+-D)|remote\s+-v|tag|stash\s+list)\b', "git read-only"),
    (r'^\s*git\s+(?:blame|grep|rev-parse|rev-list|describe|ls-files|ls-tree)\b', "git read-only"),

    # 版本信息
    (r'^\s*(?:git|node|npm|python\d*|rustc|cargo|go|java|ruby|perl|php)\s+(?:-v|--version|version)\b', "version check"),

    # 进程/系统信息 (只读)
    (r'^\s*(?:ps|top|htop|uptime|uname|hostname|whoami|id|groups)\b', "system info"),
    (r'^\s*(?:df|du|free|vm_stat|swapon)\b', "disk/memory info"),
    (r'^\s*(?:ifconfig|ip\s+addr|ip\s+link\s+show|netstat)\b', "network info"),
    (r'^\s*(?:env|printenv)\b(?!.*\bexport\b)', "env vars"),

    # 包管理只读
    (r'^\s*(?:npm|yarn|pnpm)\s+(?:list|ls|info|view|outdated|why)\b', "pkg info"),
    (r'^\s*(?:pip\d*)\s+(?:list|show|freeze|check)\b', "pip info"),
    (r'^\s*(?:brew|apt|apt-get)\s+(?:list|info|search)\b', "pkg search"),
    (r'^\s*(?:cargo)\s+(?:search|tree)\b', "cargo info"),

    # Docker 只读
    (r'^\s*docker\s+(?:ps|images|logs|inspect|stats|info|version)\b', "docker read-only"),
    (r'^\s*docker\s+compose\s+(?:ps|logs|config|images)\b', "docker compose read-only"),

    # kubectl 只读
    (r'^\s*kubectl\s+(?:get|describe|logs|top|explain|api-resources|api-versions|cluster-info)\b', "kubectl read-only"),

    # 构建/测试
    (r'^\s*(?:make|ninja|cmake)\b(?!.*\binstall\b)', "build tool"),
    (r'^\s*(?:cargo\s+(?:build|test|check|run|clippy|fmt))\b', "cargo build"),
    (r'^\s*(?:npm|yarn|pnpm)\s+(?:run|test|build|start|dev)\b', "node build"),
    (r'^\s*(?:go\s+(?:build|test|run|vet|fmt|mod\s+tidy))\b', "go build"),

    # 格式化/检查
    (r'^\s*(?:prettier|eslint|ruff|black|isort|mypy|pyright|flake8|pylint)\b', "linter/formatter"),

    # 测试框架
    (r'^\s*(?:pytest|jest|vitest|mocha|ava|junit|gotest)\b', "test runner"),
    (r'^\s*(?:rspec|cucumber|playwright\s+test|cypress)\b', "test runner"),

    # 压缩/解压 (非破坏性)
    (r'^\s*(?:tar\s+(?:tzf|tf|tvf)|unzip\s+-l|zipinfo)\b', "archive list"),
    (r'^\s*(?:gzip\s+-l|bzip2\s+-t|xz\s+-l)\b', "archive info"),

    # gh CLI 只读
    (r'^\s*gh\s+(?:pr\s+(?:view|list|status|diff|checks)|issue\s+(?:view|list|status)|repo\s+view|search|api\s+get)\b', "gh read-only"),

    # curl/wget (without pipe to sh)
    (r'^\s*curl\s+.*-(?:I|head|-o\s+\S+)\b(?!.*\|\s*sh\b)', "curl download/info"),
    (r'^\s*wget\s+.*-(?:--spider|--server-response|-O\s+\S+)\b(?!.*\|\s*sh\b)', "wget download/spider"),

    # 编辑器
    (r'^\s*(?:vim|nvim|nano|emacs|code)\s+\S+\.\w+', "editor"),
]


@dataclass
class Verdict:
    """分类结果"""
    tier: str          # SAFE | CAUTION | DANGER
    code: str          # 信号代码 (e.g. "rm-recursive-system")
    reason: str        # 人类可读原因
    severity: int = 0  # 0=SAFE, 1=CAUTION, 2=DANGER

    def __post_init__(self):
        self.severity = SEVERITY.get(self.tier, 0)


# ── Command Preprocessing (inspired by Kintsugi's wrap-depth unwrapping) ────

def strip_prefixes(cmd: str) -> str:
    """剥离前缀，到达实际命令 (Kintsugi's effective_argv).

    处理: sudo, doas, env, nice, nohup, setsid, stdbuf, command, exec, timeout,
          VAR=value assignments, 以及路径前缀。
    """
    prefixes = [
        r'sudo\s+(?:-[AbEeHnPSsUu]+(?:\s+\S+)?\s+)?',
        r'doas\s+',
        r'env\s+(?:-[iIvV]+(?:\s+\S+)?\s+)?',
        r'nice\s+(?:-n\s+\S+\s+)?',
        r'nohup\s+',
        r'setsid\s+(?:-[fw]+(?:\s+\S+)?\s+)?',
        r'stdbuf\s+(?:-[ioe]\s+\S+\s+)+',
        r'command\s+',
        r'exec\s+(?:-[acl]+(?:\s+\S+)?\s+)?',
        r'timeout\s+(?:\S+\s+)?',
        r'chroot\s+\S+\s+',
        r'runuser\s+(?:-\S+\s+)?\S+\s+',
        r'su\s+(?:-\S+\s+)?\S+\s+-c\s+',
        r'systemd-run\s+(?:--\S+(?:\s*=\s*\S+)?\s+)*',
    ]

    original = cmd
    for _ in range(MAX_WRAP_DEPTH):
        changed = False
        for prefix in prefixes:
            new_cmd = re.sub(r'^' + prefix, '', cmd.strip(), count=1)
            if new_cmd != cmd:
                cmd = new_cmd
                changed = True
                break
        if not changed:
            break

    # Strip VAR=value assignments at the start
    while re.match(r'^\s*\w+=\S+\s+', cmd):
        cmd = re.sub(r'^\s*\w+=\S+\s+', '', cmd, count=1)

    return cmd


def unwrap_bash_c(cmd: str) -> str:
    """解包 bash -c '...' / sh -c '...' (Kintsugi's wrapper unwrapping).

    递归解包直到达到 MAX_WRAP_DEPTH 或无法再解包。
    """
    for _ in range(MAX_WRAP_DEPTH):
        changed = False
        # Pattern: bash -c 'command' or sh -c "command"
        m = re.match(r'^\s*(?:ba|da|z|fi|tc|c|k)?sh\s+-c\s+["\'](.+?)["\'](?:\s.*)?$', cmd)
        if m:
            inner = m.group(1)
            # Also check for wrapped: bash -c "bash -c '...'"
            if inner != cmd:
                cmd = inner
                changed = True

        if not changed:
            break

    return cmd


def unwrap_find_exec(cmd: str) -> str:
    """解包 find ... -exec <command> \\; (Kintsugi's wrapper unwrapping).

    提取 -exec 后的命令部分用于安全检查。
    返回原始命令和执行命令的拼接（两者都检查）。
    """
    # find ... -exec <cmd> <args> \; or find ... -exec <cmd> <args> +
    # 提取所有 -exec / -execdir 的命令部分
    exec_cmds = re.findall(r'-(?:exec|execdir)\s+(.+?)(?:\s*(?:\\;|\+|;))', cmd)
    if exec_cmds:
        return cmd + ' ' + ' '.join(exec_cmds)
    return cmd


def unwrap_xargs(cmd: str) -> str:
    """解包 xargs <command>"""
    m = re.match(r'^\s*xargs\s+(?:-[A-Za-z0-9]+\s+)*(.+)$', cmd)
    if m:
        rest = m.group(1)
        if rest and not rest.startswith('-'):
            return cmd + ' ' + rest
    return cmd


def unwrap_subshell(cmd: str) -> str:
    """提取子 shell 中的命令 ($(...), `...`, (cmd; cmd)) 用于检查"""
    # Command substitution: $(...) and backticks
    dollar_sub = re.findall(r'\$\((.*?)\)', cmd)
    backtick_sub = re.findall(r'`([^`]+)`', cmd)
    # Subshell: (cmd1; cmd2)
    paren_sub = re.findall(r'\(\s*([^()]+?)\s*\)', cmd)

    extracted = ' '.join(dollar_sub + backtick_sub + paren_sub)
    if extracted.strip():
        return cmd + ' ' + extracted
    return cmd


def preprocess(cmd: str) -> str:
    """完整的命令预处理管道 (Kintsugi's two-pass 中的预处理阶段).

    返回展开后的字符串，后续 pattern 匹配会同时覆盖原始命令和展开结果。
    """
    processed = cmd.strip()
    processed = strip_prefixes(processed)
    processed = unwrap_bash_c(processed)
    processed = unwrap_find_exec(processed)
    processed = unwrap_xargs(processed)
    processed = unwrap_subshell(processed)
    return processed


# ── Classification Engine ───────────────────────────────────────────────────

def classify(cmd: str) -> Verdict:
    """对 Bash 命令进行分类 (Kintsugi's tokenizer pass).

    Two-pass 检查:
      1. 先检查原始命令
      2. 再检查预处理（展开）后的命令
      3. 取更严重的 verdict

    SAFE signals 只在原始命令上检查（预处理可能错误地让危险命令看起来安全）。
    """
    original = cmd.strip()
    expanded = preprocess(original)

    # Pass 1: Check original command
    verdict1 = _check_signals(original)

    # Pass 2: Check expanded command (unwrap wrappers, subshells, etc.)
    if expanded != original:
        verdict2 = _check_signals(expanded)
        # Take the more severe verdict (Kintsugi's "worst wins" pattern)
        if SEVERITY[verdict2.tier] > SEVERITY[verdict1.tier]:
            return verdict2
        # If same tier but different signal, prefer the more specific one
        if verdict2.tier == verdict1.tier and verdict2.code != verdict1.code:
            return verdict2 if len(verdict2.reason) > len(verdict1.reason) else verdict1

    return verdict1


def _check_signals(cmd: str) -> Verdict:
    """检查命令文本中的所有信号，返回最严重的 verdict。"""
    cmd_lower = cmd.lower()

    # 1. DANGER signals — 立即阻断
    for pattern, code, reason in DANGER_SIGNALS:
        if re.search(pattern, cmd):
            return Verdict("DANGER", code, reason)

    # 2. CAUTION signals — 警告
    for pattern, code, reason in CAUTION_SIGNALS:
        if re.search(pattern, cmd):
            return Verdict("CAUTION", code, reason)

    # 3. SAFE signals — 明确安全
    for pattern, reason in SAFE_SIGNALS:
        if re.match(pattern, cmd):
            return Verdict("SAFE", "known-safe", reason)

    # 4. No signal matched — default CAUTION (Kintsugi: "fail toward caution")
    return Verdict("CAUTION", "unknown", "Unrecognized command pattern — exercise caution")


# ── Nociception Integration (Pain Sense) ────────────────────────────────────

def load_nociception_state() -> dict:
    """加载痛觉状态（危险尝试记忆）"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "danger_attempts": {},     # {"cmd_hash": count}
        "escalations": [],         # [{"cmd_hash": ..., "from": "CAUTION", "to": "DANGER", "timestamp": ...}]
        "total_blocked": 0,
        "total_warned": 0,
        "total_allowed": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_nociception_state(state: dict) -> None:
    """持久化痛觉状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_hash(cmd: str) -> str:
    """计算命令的稳定哈希（用于重复检测）"""
    import hashlib
    normalized = re.sub(r'\s+', ' ', cmd.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def check_with_nociception(cmd: str) -> Verdict:
    """分类 + 痛觉升级。

    如果相同的 CAUTION 命令被重复尝试 ≥2 次，自动升级为 DANGER。
    这是 鲤鱼 的 Nociception (pain sense) 集成:
      第1次危险尝试 = 痛 (CAUTION)
      第2次同样尝试 = 剧痛 (DANGER, block)
    """
    verdict = classify(cmd)
    state = load_nociception_state()
    ch = cmd_hash(cmd)

    # Nociception escalation: CAUTION -> DANGER on repeat
    if verdict.tier == "CAUTION":
        state["danger_attempts"][ch] = state["danger_attempts"].get(ch, 0) + 1
        attempts = state["danger_attempts"][ch]

        if attempts >= 2:
            state.setdefault("escalations", []).append({
                "cmd_hash": ch,
                "from_tier": "CAUTION",
                "to_tier": "DANGER",
                "attempts": attempts,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            save_nociception_state(state)
            return Verdict(
                "DANGER",
                f"{verdict.code}-nociception",
                f"[痛觉升级] 该危险命令已尝试 {attempts} 次: {verdict.reason}",
            )

        save_nociception_state(state)
        return verdict

    # DANGER: always record and block
    if verdict.tier == "DANGER":
        state["danger_attempts"][ch] = state["danger_attempts"].get(ch, 0) + 1
        save_nociception_state(state)
        return verdict

    # SAFE: increment allowed count
    state["total_allowed"] += 1
    save_nociception_state(state)
    return verdict


# ── History Logging ─────────────────────────────────────────────────────────

def log_history(cmd: str, verdict: Verdict, nociception_state: dict) -> None:
    """记录到历史日志 (best-effort)"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command_hash": cmd_hash(cmd),
        "command_preview": cmd[:200],
        "tier": verdict.tier,
        "code": verdict.code,
        "reason": verdict.reason,
    }
    try:
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[bash-guard] Warning: history write failed: {e}", file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────────────────────

def format_verdict_output(verdict: Verdict, cmd: str) -> dict:
    """格式化为 Claude Code hook 兼容的 JSON 输出"""
    icon = {"SAFE": "✅", "CAUTION": "⚠️", "DANGER": "🚫"}
    return {
        "decision": verdict.tier.lower(),
        "code": verdict.code,
        "reason": verdict.reason,
        "icon": icon.get(verdict.tier, "?"),
        "command_preview": cmd[:120],
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny" if verdict.tier == "DANGER" else "allow",
            "permissionDecisionReason": f"[鲤鱼 Bash Guard] {icon.get(verdict.tier, '?')} {verdict.reason}",
        },
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "check":
        # liyu-bash-guard.py check "<bash command>"
        if len(sys.argv) < 3:
            print("Usage: liyu-bash-guard.py check '<command>'", file=sys.stderr)
            sys.exit(1)
        bash_cmd = sys.argv[2]
        verdict = check_with_nociception(bash_cmd)
        state = load_nociception_state()
        log_history(bash_cmd, verdict, state)

        output = format_verdict_output(verdict, bash_cmd)
        print(json.dumps(output, ensure_ascii=False, indent=2))

        if verdict.tier == "DANGER":
            print(f"\n🚫 BLOCKED: {verdict.reason}", file=sys.stderr)
            sys.exit(2)
        elif verdict.tier == "CAUTION":
            print(f"\n⚠️  WARNING: {verdict.reason}", file=sys.stderr)
            sys.exit(0)
        else:
            sys.exit(0)

    elif cmd == "hook-pre":
        # Hook mode: read JSON from stdin (same protocol as tool-guard.py)
        # Claude Code sends: {"tool_name": "Bash", "tool_input": {"command": "..."}}
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, OSError):
            output = {
                "decision": "allow",
                "reason": "bash-guard: invalid hook input, allowing",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            }
            print(json.dumps(output, ensure_ascii=False))
            sys.exit(0)

        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
        bash_cmd = tool_input.get("command", "")

        # Only act on Bash tool calls
        if tool_name != "Bash" or not bash_cmd:
            output = {
                "decision": "allow",
                "reason": "bash-guard: not a Bash call, skipping",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            }
            print(json.dumps(output, ensure_ascii=False))
            sys.exit(0)

        verdict = check_with_nociception(bash_cmd)
        state = load_nociception_state()
        log_history(bash_cmd, verdict, state)

        # Update counters
        if verdict.tier == "DANGER":
            state["total_blocked"] = state.get("total_blocked", 0) + 1
        elif verdict.tier == "CAUTION":
            state["total_warned"] = state.get("total_warned", 0) + 1
        else:
            state["total_allowed"] = state.get("total_allowed", 0) + 1
        save_nociception_state(state)

        output = format_verdict_output(verdict, bash_cmd)
        print(json.dumps(output, ensure_ascii=False))

        if verdict.tier == "DANGER":
            # Exit 2 = block the tool call
            sys.exit(2)
        else:
            sys.exit(0)

    elif cmd == "stats":
        state = load_nociception_state()
        print("═══ 鲤鱼 Bash Guard Statistics ═══")
        print(f"  总计允许 (SAFE):    {state.get('total_allowed', 0)}")
        print(f"  总计警告 (CAUTION): {state.get('total_warned', 0)}")
        print(f"  总计阻断 (DANGER):  {state.get('total_blocked', 0)}")
        print(f"  痛觉升级次数:      {len(state.get('escalations', []))}")
        print()
        if state.get("danger_attempts"):
            print("  危险尝试记录:")
            for h, count in sorted(state["danger_attempts"].items(), key=lambda x: -x[1])[:10]:
                print(f"    {h[:12]} → {count} 次")
        if state.get("escalations"):
            print("\n  痛觉升级记录 (CAUTION→DANGER):")
            for e in state["escalations"][-5:]:
                print(f"    {e['cmd_hash'][:12]} at {e['timestamp'][:19]} — {e['attempts']} attempts")

    elif cmd == "reset":
        save_nociception_state({
            "danger_attempts": {},
            "escalations": [],
            "total_blocked": 0,
            "total_warned": 0,
            "total_allowed": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Bash Guard 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
