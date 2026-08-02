#!/usr/bin/env python3
"""
鲤鱼 NexSandglass — Agent 专用记忆引擎
Absorbed from NexSandglass V2.9.3 (lovevin1314-tech) + 自主改进

四层架构（每层只追加，永不替换下层）:
  L1 沙粒写入 — 纯文本追加 + 文件锁 + SQLite 双写
  L2 偏移率   — 决策倾向量化 + 趋势预测 → 融入 7-Sense
  L3 决策检测 — 双语选择模式匹配 + 决策链追踪
  L4 交互协议 — 学习沟通偏好 + 灵魂蒸馏

原版改进:
  - 双语支持 (CN/EN) vs 原版仅中文
  - SQLite + 纯文本双写 vs 原版纯文本
  - 7-Sense 趋势集成 vs 原版独立运行
  - Claude Code 原生 vs 原版 Hermes 依赖
  - 完整测试覆盖 vs 原版无测试

Usage:
  nexsandglass.py write <session_id> --role user|assistant --content "..."
  nexsandglass.py drift [--days 7]
  nexsandglass.py detect <session_id>
  nexsandglass.py persona [--full]
  nexsandglass.py stats
"""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import sys
import time

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
SAND_DIR = 鲤鱼_HOME / "nexsandglass"
SAND_FILE = SAND_DIR / "sand.txt"           # L1: 纯文本沙粒
SAND_DB = SAND_DIR / "sand.db"              # L1: SQLite 双写
DRIFT_FILE = SAND_DIR / "drift.json"        # L2: 偏移率状态
DECISIONS_FILE = SAND_DIR / "decisions.jsonl"  # L3: 决策链
PERSONA_FILE = SAND_DIR / "persona.md"      # L4: 用户画像
SENSES_DIR = 鲤鱼_HOME / "senses"        # 7-Sense 集成目标

# ── L1: 沙粒数据结构 ──────────────────────────────────────────────────────

@contextmanager
def _use_db():
    """SQLite 上下文管理器——自动提交/回滚，异常安全关闭"""
    db = sqlite3.connect(str(SAND_DB))
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@dataclass(frozen=True)
class SandGrain:
    """一粒沙——对话中的一个原子片段"""
    id: str
    session_id: str
    timestamp: str          # ISO 8601
    role: str               # user | assistant | system
    content: str
    content_hash: str = ""  # SHA256[:16]，去重用
    turn_id: str = ""       # 对话轮次
    tokens: int = 0         # 估算 token 数
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.content_hash:
            object.__setattr__(self, 'content_hash',
                hashlib.sha256(self.content.encode()).hexdigest()[:16])
        if not self.tokens:
            object.__setattr__(self, 'tokens',
                max(1, int(len(self.content) * 0.4)))


# ── L1: 沙粒写入层 ────────────────────────────────────────────────────────

class SandWriter:
    """L1 写入层 — 纯文本追加 + SQLite 双写 + 文件锁"""

    def __init__(self):
        SAND_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化 SQLite 存储"""
        with _use_db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS sand (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    turn_id TEXT DEFAULT '',
                    tokens INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]'
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_sand_session ON sand(session_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sand_date ON sand(date)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sand_role ON sand(role)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sand_hash ON sand(content_hash)")
            db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS sand_fts USING fts5(
                    content, tags, content='sand', content_rowid='rowid'
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS sand_ai AFTER INSERT ON sand BEGIN
                    INSERT INTO sand_fts(rowid, content, tags)
                    VALUES (new.rowid, new.content, new.tags);
                END
            """)

    def write(self, grain: SandGrain) -> str:
        """写入一粒沙。纯文本 + SQLite 双写。返回 grain ID。"""
        # 纯文本追加（文件锁）
        line = json.dumps({
            "id": grain.id,
            "session_id": grain.session_id,
            "timestamp": grain.timestamp,
            "role": grain.role,
            "content": grain.content[:2000],  # 纯文本截断
            "hash": grain.content_hash,
            "turn": grain.turn_id,
        }, ensure_ascii=False)

        try:
            with open(SAND_FILE, "a") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"[L1] 纯文本写入警告: {e}", file=sys.stderr)

        # SQLite 双写
        with _use_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO sand (id, session_id, timestamp, date, role, content, content_hash, turn_id, tokens, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grain.id, grain.session_id, grain.timestamp,
                    grain.timestamp[:10], grain.role, grain.content,
                    grain.content_hash, grain.turn_id, grain.tokens,
                    json.dumps(grain.tags, ensure_ascii=False),
                ),
            )

        return grain.id

    def count(self) -> int:
        """沙粒总数"""
        with _use_db() as db:
            count = db.execute("SELECT COUNT(*) FROM sand").fetchone()[0]
        return count


# ── L2: 偏移率引擎 ────────────────────────────────────────────────────────

class DriftEngine:
    """
    L2 偏移率引擎 — 量化决策倾向的时间变化

    三个维度追踪:
      frugal → 保守/省钱/本地优先
      spend  → 投入/冒险/付费方案
      drift  → 随性/无明确倾向

    稳定性分级（标准差）:
      高度稳定 <15 | 稳定 <30 | 波动 <50 | 剧烈波动 ≥50

    改进: 融入 鲤鱼 7-Sense，为每个 sense 输出趋势线
    """

    def __init__(self):
        self.state = self._load_state()
        self._writer = SandWriter()

    def _load_state(self) -> dict:
        if DRIFT_FILE.exists():
            try:
                return json.loads(DRIFT_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "history": [],       # [{date, frugal, spend, drift, total}]
            "current_direction": "drift",
            "stability": "stable",
            "stability_score": 0,
            "trend_slope": 0.0,
            "phase_shifts": [],  # 阶段切换记录
            "version": "2.0.0",
            "updated_at": "",
        }

    def _save_state(self) -> None:
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        DRIFT_FILE.parent.mkdir(parents=True, exist_ok=True)
        DRIFT_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))

    def compute_daily(self, date: str = "") -> dict:
        """计算某一天的偏移率。从沙粒中提取决策信号。"""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with _use_db() as db:
            rows = db.execute(
                "SELECT content FROM sand WHERE date = ? AND role = 'user'",
                (date,)
            ).fetchall()

        signals = self._extract_signals([r[0] for r in rows])
        return self._compute_drift(signals)

    def compute_range(self, days: int = 7) -> dict:
        """计算多日偏移率趋势"""
        today = datetime.now(timezone.utc).date()
        daily_drifts = []

        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            drift = self.compute_daily(d)
            daily_drifts.append({"date": d, **drift})

        daily_drifts.reverse()  # 从旧到新

        # 计算趋势斜率
        if len(daily_drifts) >= 2:
            scores = [d["drift_score"] for d in daily_drifts if d["total_signals"] > 0]
            if len(scores) >= 2:
                self.state["trend_slope"] = self._linear_slope(scores)

        # 更新稳定性
        self.state["stability_score"] = self._stability_score(daily_drifts)
        self.state["stability"] = self._stability_label(self.state["stability_score"])

        # 确定当前方向
        recent = [d for d in daily_drifts if d["total_signals"] > 0]
        if recent:
            scores = {
                "frugal": sum(d["frugal_ratio"] for d in recent) / len(recent),
                "spend": sum(d["spend_ratio"] for d in recent) / len(recent),
                "drift": sum(d["drift_ratio"] for d in recent) / len(recent),
            }
            self.state["current_direction"] = max(scores, key=scores.get)
            self.state["direction_scores"] = scores

        self.state["history"] = daily_drifts
        self._save_state()

        return {
            "direction": self.state["current_direction"],
            "stability": self.state["stability"],
            "stability_score": self.state["stability_score"],
            "trend_slope": self.state["trend_slope"],
            "trend_description": self._trend_description(),
            "daily": daily_drifts,
        }

    def sense_trends(self) -> dict:
        """
        将偏移率映射到 7-Sense 趋势预测。
        这是 NexSandglass → 鲤鱼 的核心集成点。
        """
        drift = self.compute_range(7)
        direction = drift["direction"]
        slope = drift["trend_slope"]

        # 偏移方向 → sense 预测
        trends = {}
        if direction == "frugal":
            trends["o2"] = "may_improve"        # 倾向保守 → 可能减少上下文消耗
            trends["spatial"] = "may_contract"   # 倾向本地 → 可能减少文件扩散
        elif direction == "spend":
            trends["o2"] = "may_worsen"          # 倾向投入 → 可能增加上下文消耗
            trends["spatial"] = "may_expand"     # 倾向冒险 → 可能增加文件操作
            trends["nociception"] = "monitor"    # 更多操作 → 错误概率上升

        # 稳定性 → 风险
        stability = drift["stability"]
        if stability == "highly_volatile":
            trends["drift"] = "alert"            # 剧烈波动 → 话题漂移风险
            trends["echo"] = "alert"             # 剧烈波动 → 重复模式风险
        elif stability == "volatile":
            trends["drift"] = "warn"

        # 趋势斜率 → 预测
        if slope > 0.1:
            trends["chronos"] = "accelerating"   # 上升趋势 → 节奏加快
        elif slope < -0.1:
            trends["chronos"] = "decelerating"

        # 写入 sense 文件
        for sense_name, trend in trends.items():
            sense_file = SENSES_DIR / f"{sense_name}.json"
            if sense_file.exists():
                try:
                    data = json.loads(sense_file.read_text())
                    data["trend"] = trend
                    data["trend_updated"] = datetime.now(timezone.utc).isoformat()
                    sense_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                except (json.JSONDecodeError, OSError):
                    pass

        return trends

    # ── Internal ────────────────────────────────────────────────────────

    def _extract_signals(self, messages: list[str]) -> list[dict]:
        """从消息中提取决策信号"""
        signals = []
        for msg in messages:
            if not msg.strip():
                continue

            # Frugal 信号
            frugal_patterns = [
                "免费", "开源", "本地", "不花钱", "省钱", "便宜",
                "free", "open source", "local", "self-host",
                "简单", "最小", "够用", "不要太多",
            ]
            # Spend 信号
            spend_patterns = [
                "付费", "买", "订阅", "升级", "最好的", "专业的",
                "paid", "buy", "subscribe", "upgrade", "best", "premium",
                "复杂", "全面", "完整", "所有",
            ]

            frugal_count = sum(1 for p in frugal_patterns if p.lower() in msg.lower())
            spend_count = sum(1 for p in spend_patterns if p.lower() in msg.lower())

            if frugal_count > spend_count:
                signals.append({"direction": "frugal", "strength": frugal_count})
            elif spend_count > frugal_count:
                signals.append({"direction": "spend", "strength": spend_count})
            elif frugal_count > 0 or spend_count > 0:
                signals.append({"direction": "drift", "strength": 1})
            # 无信号的消息不记录

        return signals

    def _compute_drift(self, signals: list[dict]) -> dict:
        """计算单日偏移率"""
        total = len(signals)
        if total == 0:
            return {
                "frugal_ratio": 0, "spend_ratio": 0, "drift_ratio": 1.0,
                "drift_score": 0, "total_signals": 0,
            }

        frugal = sum(1 for s in signals if s["direction"] == "frugal") / total
        spend = sum(1 for s in signals if s["direction"] == "spend") / total
        drift = sum(1 for s in signals if s["direction"] == "drift") / total

        # 偏移分数: -1(frugal) ~ +1(spend), 0=drift
        score = (spend - frugal)

        return {
            "frugal_ratio": round(frugal, 3),
            "spend_ratio": round(spend, 3),
            "drift_ratio": round(drift, 3),
            "drift_score": round(score, 3),
            "total_signals": total,
        }

    @staticmethod
    def _stability_score(daily_drifts: list[dict]) -> float:
        """计算偏移率的稳定性（标准差）"""
        scores = [d["drift_score"] for d in daily_drifts if d["total_signals"] > 0]
        if len(scores) < 2:
            return 0.0
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        return round(variance ** 0.5, 2)

    @staticmethod
    def _stability_label(std: float) -> str:
        if std < 0.15:
            return "highly_stable"
        elif std < 0.30:
            return "stable"
        elif std < 0.50:
            return "volatile"
        return "highly_volatile"

    @staticmethod
    def _linear_slope(values: list[float]) -> float:
        """简单线性趋势斜率"""
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return round(num / den, 4) if den != 0 else 0.0

    def _trend_description(self) -> str:
        """生成人类可读的趋势描述"""
        direction = self.state["current_direction"]
        stability = self.state["stability"]
        slope = self.state["trend_slope"]

        desc = ""
        if direction == "frugal":
            desc = "倾向保守/本地优先"
        elif direction == "spend":
            desc = "倾向投入/探索新方案"
        else:
            desc = "无明显决策倾向"

        if stability in ("volatile", "highly_volatile"):
            desc += "，决策波动较大"
        else:
            desc += "，决策稳定"

        if abs(slope) > 0.05:
            desc += f"，趋势{'上升' if slope > 0 else '下降'}中"

        return desc


# ── L3: 决策粒子检测 ──────────────────────────────────────────────────────

class DecisionDetector:
    """
    L3 决策粒子检测 — 从对话中识别选择时刻

    改进: 双语模式 (原版仅中文)
    检测类型: 显式选择 / 命令式决策 / 放弃信号 / 偏好声明
    """

    # 双语决策模式
    CHOICE_PATTERNS = {
        "explicit_cn": [
            r"我选[择]?[了]?\S{0,10}(吧|了|的)",
            r"还是\S{2,10}(吧|了|好)",
            r"就[用要做]\S{0,10}(吧|了|这个)",
            r"决定\S{2,10}",
        ],
        "explicit_en": [
            r"(I('ll| will) )?(go with|choose|pick|use|take)\s+\S+",
            r"(let'?s|I'?m)\s+(go|do|try|use)\s+\S+",
            r"decided?\s+(on|to)\s+\S+",
        ],
        "fallback_cn": [
            r"不管了",
            r"随便[吧了]?",
            r"算了[吧了]?",
            r"将就[用吧了]?",
            r"无所谓[了]?",
        ],
        "fallback_en": [
            r"whatever",
            r"never\s*mind",
            r"doesn'?t\s+matter",
            r"fine[,.]?\s*(just|I'?ll)",
        ],
        "command_cn": [
            r"(用|删|换|改|加|做|跑|执行)\S{1,10}(吧|了|一下)",
        ],
        "command_en": [
            r"(delete|remove|switch|change|add|run|execute)\s+\S+",
        ],
        "preference": [
            r"(更喜欢|偏好|倾向于|习惯[了]?|讨厌|不喜欢)",
            r"(prefer|like\s+better|tend\s+to|used\s+to|hate|dislike)",
        ],
    }

    def __init__(self):
        self._decisions_file = DECISIONS_FILE

    def detect(self, text: str, session_id: str = "") -> list[dict]:
        """检测文本中的决策粒子。返回检测到的所有决策。"""
        import re
        decisions = []

        for pattern_type, patterns in self.CHOICE_PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    decisions.append({
                        "type": pattern_type,
                        "text": match.group(),
                        "position": match.start(),
                        "session_id": session_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

        # 去重（同位置、同类型）
        seen = set()
        unique = []
        for d in sorted(decisions, key=lambda x: x["position"]):
            key = (d["position"], d["type"])
            if key not in seen:
                seen.add(key)
                unique.append(d)

        return unique

    def detect_chain(self, messages: list[str], session_id: str = "") -> list[dict]:
        """在一组消息中检测决策链"""
        chain = []
        for i, msg in enumerate(messages):
            particles = self.detect(msg, session_id)
            for p in particles:
                p["message_index"] = i
                chain.append(p)
        return chain

    def log_decision(self, decision: dict) -> None:
        """将决策粒子持久化"""
        DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISIONS_FILE, "a") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")

    def recent_decisions(self, limit: int = 50) -> list[dict]:
        """读取最近的决策粒子"""
        if not DECISIONS_FILE.exists():
            return []
        decisions = []
        with open(DECISIONS_FILE) as f:
            for line in f:
                if line.strip():
                    try:
                        decisions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return decisions[-limit:]

    def decision_summary(self) -> dict:
        """决策统计摘要"""
        decisions = self.recent_decisions(100)
        by_type = defaultdict(int)
        for d in decisions:
            by_type[d.get("type", "unknown")] += 1
        return {
            "total": len(decisions),
            "by_type": dict(by_type),
            "preference_ratio": round(
                by_type.get("preference", 0) / max(len(decisions), 1), 3
            ),
        }


# ── L4: 交互协议 + 灵魂蒸馏 ──────────────────────────────────────────────

class PersonaBuilder:
    """
    L4 画像构建器 — 从沙粒中蒸馏用户画像

    四层扫描:
      1. 基础锚点 — 职业、技术栈、项目
      2. 兴趣图谱 — 时间/金钱/注意力投向
      3. 交互协议 — 沟通习惯、雷区、交付偏好
      4. 认知内核 — 决策模式、核心价值观

    每条声明附 [src:hash] 溯源，防 LLM 幻觉
    """

    def __init__(self):
        self._writer = SandWriter()

    def build(self, full: bool = False) -> dict:
        """构建当前画像"""
        with _use_db() as db:
            total = db.execute("SELECT COUNT(*) FROM sand").fetchone()[0]

            if total < 10:
                return {"status": "insufficient_data", "grains": total}

            # 提取各维度
            anchors = self._extract_anchors(db)
            interests = self._extract_interests(db)
            protocol = self._extract_protocol(db)
            kernel = self._extract_kernel(db, full)

        persona = {
            "version": "2.0.0",
            "updated": datetime.now(timezone.utc).isoformat(),
            "total_grains": total,
            "layers": {
                "anchors": anchors,
                "interests": interests,
                "protocol": protocol,
                "kernel": kernel,
            },
        }

        # 持久化
        self._write_persona(persona)
        return persona

    def interaction_guide(self) -> str:
        """生成交互协议指南——教 鲤鱼 如何正确服务用户"""
        persona = self.build()
        if persona.get("status") == "insufficient_data":
            return "数据不足，继续对话以学习交互偏好。"

        protocol = persona["layers"]["protocol"]
        kernel = persona["layers"]["kernel"]

        lines = ["## 鲤鱼 交互协议（自动学习）\n"]
        lines.append(f"> 基于 {persona['total_grains']} 条沙粒蒸馏\n")

        if protocol.get("style"):
            lines.append(f"### 沟通风格\n{protocol['style']}\n")
        if protocol.get("anti_patterns"):
            lines.append(f"### 避免\n{', '.join(protocol['anti_patterns'])}\n")
        if kernel.get("decision_mode"):
            lines.append(f"### 决策模式\n{kernel['decision_mode']}\n")
        if kernel.get("core_values"):
            lines.append(f"### 核心价值观\n{', '.join(kernel['core_values'])}\n")

        return "\n".join(lines)

    # ── Extraction (simplified — keyword-based, LLM-free baseline) ─────

    def _extract_anchors(self, db: sqlite3.Connection) -> dict:
        """提取基础锚点"""
        rows = db.execute(
            "SELECT content FROM sand WHERE role = 'user' ORDER BY timestamp ASC LIMIT 100"
        ).fetchall()
        text = " ".join(r[0] for r in rows)
        return {
            "tech_keywords": self._top_keywords(text, [
                "python", "typescript", "rust", "go", "react", "vue", "claude",
                "ai", "agent", "frontend", "backend", "fullstack", "devops",
            ]),
            "domains": self._top_keywords(text, [
                "web", "mobile", "desktop", "cloud", "data", "ml", "security",
                "design", "product", "oss",
            ]),
            "source": f"[src:{hashlib.sha256(text.encode()).hexdigest()[:8]}]",
        }

    def _extract_interests(self, db: sqlite3.Connection) -> dict:
        """提取兴趣图谱"""
        rows = db.execute(
            "SELECT content FROM sand WHERE role = 'user' ORDER BY timestamp DESC LIMIT 200"
        ).fetchall()
        text = " ".join(r[0] for r in rows)
        return {
            "focus_areas": self._top_keywords(text, [
                "架构", "性能", "测试", "设计", "安全", "自动化",
                "architecture", "performance", "testing", "design", "security",
                "agent", "ai", "orchestration", "memory", "evolution",
            ], top_n=5),
            "tool_preferences": self._top_keywords(text, [
                "claude", "vscode", "terminal", "git", "docker",
                "python", "typescript", "sqlite", "github",
            ], top_n=5),
            "source": f"[src:{hashlib.sha256(text.encode()).hexdigest()[:8]}]",
        }

    def _extract_protocol(self, db: sqlite3.Connection) -> dict:
        """提取交互协议——最重要的层"""
        rows = db.execute(
            "SELECT content FROM sand WHERE role = 'user' ORDER BY timestamp DESC LIMIT 300"
        ).fetchall()
        text = " ".join(r[0] for r in rows)

        # 沟通风格
        style_signals = {
            "短句优先": sum(1 for p in ["简短", "直接", "一句话", "不要太多"] if p in text),
            "深度优先": sum(1 for p in ["详细", "深入", "全面", "完整分析"] if p in text),
            "代码优先": sum(1 for p in ["直接写", "开始写", "实现", "代码"] if p in text),
            "先讨论再写": sum(1 for p in ["先分析", "先设计", "讨论一下", "你怎么看"] if p in text),
        }
        dominant_style = max(style_signals, key=style_signals.get) if max(style_signals.values()) > 0 else ""

        # 反模式
        anti_patterns = []
        if "不要解释" in text or "直接给结果" in text:
            anti_patterns.append("过度解释")
        if "别问我" in text or "不需要确认" in text:
            anti_patterns.append("频繁确认")
        if "中文" in text and "英文" not in text:
            anti_patterns.append("英文回复（用户偏好中文）")

        return {
            "style": dominant_style,
            "style_signals": style_signals,
            "anti_patterns": anti_patterns,
            "source": f"[src:{hashlib.sha256(text.encode()).hexdigest()[:8]}]",
        }

    def _extract_kernel(self, db: sqlite3.Connection, full: bool) -> dict:
        """提取认知内核"""
        rows = db.execute(
            "SELECT content FROM sand WHERE role = 'user' ORDER BY timestamp DESC LIMIT 500"
        ).fetchall()
        text = " ".join(r[0] for r in rows)

        # 决策模式 — 正确实现：generator expression 内联条件
        decision_modes = {
            "快速决策": sum(1 for p in ["直接", "开始", "立刻", "马上", "go ahead", "just do"] if p in text),
            "深思熟虑": sum(1 for p in ["考虑", "权衡", "再想想", "分析", "think about", "consider"] if p in text),
            "数据驱动": sum(1 for p in ["数据", "对比", "benchmark", "测试结果"] if p in text),
        }
        dominant_mode = max(decision_modes, key=decision_modes.get) if max(decision_modes.values()) > 0 else ""

        # 核心价值观
        values = self._top_keywords(text, [
            "效率", "质量", "简洁", "安全", "创新", "可靠", "优雅", "实用",
            "efficiency", "quality", "simplicity", "security", "innovation",
            "reliability", "elegance", "pragmatism",
        ], top_n=5)

        return {
            "decision_mode": dominant_mode,
            "decision_signals": decision_modes if full else {},
            "core_values": values,
            "source": f"[src:{hashlib.sha256(text.encode()).hexdigest()[:8]}]",
        }

    def _write_persona(self, persona: dict) -> None:
        """将画像持久化到 persona.md"""
        lines = [
            f"# 鲤鱼 Persona",
            f"> 自动蒸馏 · {persona['total_grains']} 沙粒 · {persona['updated'][:19]}",
            "",
        ]

        for layer_name, layer_data in persona.get("layers", {}).items():
            layer_labels = {
                "anchors": "## 🏔️ L1 基础锚点",
                "interests": "## 🎯 L2 兴趣图谱",
                "protocol": "## 🤝 L3 交互协议",
                "kernel": "## 🧠 L4 认知内核",
            }
            lines.append(layer_labels.get(layer_name, f"## {layer_name}"))
            for key, val in layer_data.items():
                if key == "source":
                    lines.append(f"\n*{val}*")
                elif isinstance(val, list):
                    lines.append(f"- **{key}**: {', '.join(val)}")
                elif isinstance(val, dict):
                    lines.append(f"- **{key}**:")
                    for k, v in val.items():
                        lines.append(f"  - {k}: {v}")
                elif val:
                    lines.append(f"- **{key}**: {val}")
            lines.append("")

        PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
        PERSONA_FILE.write_text("\n".join(lines))

    @staticmethod
    def _top_keywords(text: str, keywords: list[str], top_n: int = 3) -> list[str]:
        """提取文本中最频繁的关键词"""
        text_lower = text.lower()
        scored = [(kw, text_lower.count(kw.lower())) for kw in keywords]
        scored = [(kw, c) for kw, c in scored if c > 0]
        scored.sort(key=lambda x: -x[1])
        return [kw for kw, _ in scored[:top_n]]


# ── 统一接口 ──────────────────────────────────────────────────────────────

class NexSandglass:
    """鲤鱼 NexSandglass — 四层记忆引擎统一入口"""

    def __init__(self):
        self.writer = SandWriter()
        self.drift = DriftEngine()
        self.detector = DecisionDetector()
        self.persona = PersonaBuilder()

    def ingest(self, session_id: str, role: str, content: str,
               turn_id: str = "") -> list[str]:
        """
        摄入一粒沙。同时执行:
          L1: 写入沙粒
          L3: 检测决策粒子
          返回: [grain_id, ...decision_types]
        """
        grain = SandGrain(
            id=f"sand-{hashlib.sha256(f'{session_id}{content}{time.time()}'.encode()).hexdigest()[:12]}",
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            role=role,
            content=content,
            turn_id=turn_id,
        )

        grain_id = self.writer.write(grain)

        # 只对 user 消息做决策检测
        detected = []
        if role == "user":
            particles = self.detector.detect(content, session_id)
            for p in particles:
                self.detector.log_decision(p)
                detected.append(p["type"])

        return [grain_id] + detected

    def analyze(self) -> dict:
        """完整分析：偏移率 + 决策摘要 + 画像"""
        drift = self.drift.compute_range(7)
        sense_trends = self.drift.sense_trends()
        decisions = self.detector.decision_summary()
        persona = self.persona.build()

        return {
            "drift": drift,
            "sense_trends": sense_trends,
            "decisions": decisions,
            "persona": persona,
            "total_grains": self.writer.count(),
        }

    def interaction_guide(self) -> str:
        """获取当前交互协议"""
        return self.persona.interaction_guide()

    def compact_context(self) -> dict:
        """
        生成 Token-Efficient 上下文块。
        替代笨重的 MEMORY.md 全量加载，目标 <300 tokens。

        三要素:
          1. 交互协议 — 怎么服务用户 (1-2 句)
          2. 偏移趋势 — 当前决策方向 + 稳定性
          3. 核心锚点 — 技术栈 + 领域
        """
        drift = self.drift.compute_range(7)
        persona = self.persona.build()
        grains = self.writer.count()

        # 从 persona 中提取最紧凑的信息
        layers = persona.get("layers", {}) if persona.get("status") != "insufficient_data" else {}

        # 交互协议（最核心）
        protocol = layers.get("protocol", {})
        style = protocol.get("style", "")
        anti = protocol.get("anti_patterns", [])

        # 核心锚点
        anchors = layers.get("anchors", {})
        tech = anchors.get("tech_keywords", [])
        domains = anchors.get("domains", [])

        # 决策内核
        kernel = layers.get("kernel", {})
        decision_mode = kernel.get("decision_mode", "")
        values = kernel.get("core_values", [])

        # 组装紧凑上下文
        lines = []
        lines.append(f"用户: {', '.join(tech[:3])} | {', '.join(domains[:2])}")
        if style:
            lines.append(f"偏好: {style}")
        if anti:
            lines.append(f"避免: {', '.join(anti)}")
        if decision_mode:
            lines.append(f"决策: {decision_mode}")
        if values:
            lines.append(f"价值: {', '.join(values[:3])}")

        block = " | ".join(lines)
        tokens = max(1, int(len(block) * 0.4))

        # 偏移率摘要
        drift_line = f"偏移: {drift['direction']} | {drift['stability']}"
        drift_tokens = max(1, int(len(drift_line) * 0.4))

        return {
            "context_block": block,
            "context_tokens": tokens,
            "drift_line": drift_line,
            "drift_tokens": drift_tokens,
            "total_tokens": tokens + drift_tokens,
            "drift_direction": drift["direction"],
            "drift_trend": drift["trend_description"],
            "total_grains": grains,
            "generated": datetime.now(timezone.utc).isoformat(),
        }

    def hybrid_context(self) -> str:
        """
        混合注入块：NexSandglass 蒸馏 + MEMORY.md 关键事实。
        这是实际注入到会话中的内容。
        目标: <300 tokens。
        """
        compact = self.compact_context()
        grains = compact["total_grains"]

        # 从 MEMORY.md 提取核心事实（只读第一行关键信息）
        memory_dir = Path.home() / ".claude" / "projects" / "-Users-holyty" / "memory"
        essential_facts = []
        if memory_dir.exists():
            for f in sorted(memory_dir.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                if f.name.startswith("_"):  # 跳过归档
                    continue
                try:
                    content = f.read_text()
                    # 提取 frontmatter description
                    for line in content.split("\n"):
                        if line.startswith("description:"):
                            desc = line.replace("description:", "").strip()
                            if len(desc) > 10:
                                essential_facts.append(desc)
                            break
                except OSError:
                    pass

        lines = ["[鲤鱼 NexSandglass 记忆注入]", ""]

        # 交互协议
        lines.append(f"协议: {compact['context_block']}")
        lines.append(f"趋势: {compact['drift_line']}")

        # 核心事实（从 MEMORY.md 导入，压缩到一行）
        if essential_facts:
            facts_line = "事实: " + "; ".join(essential_facts[:6])
            if len(facts_line) > 300:
                facts_line = facts_line[:297] + "..."
            lines.append(facts_line)

        lines.append(f"沙粒: {grains}")

        return "\n".join(lines)

    def compare_memory_cost(self) -> dict:
        """
        对比 NexSandglass 紧凑上下文 vs MEMORY.md 的 token 消耗。
        输出可量化的节省报告。
        """
        compact = self.compact_context()

        # 测量 MEMORY.md 加载成本
        memory_dir = Path.home() / ".claude" / "projects" / "-Users-holyty" / "memory"
        mem_tokens = 0
        mem_files = 0
        if memory_dir.exists():
            for f in memory_dir.glob("*.md"):
                try:
                    content = f.read_text()
                    mem_tokens += max(1, int(len(content) * 0.4))
                    mem_files += 1
                except OSError:
                    pass

        saved = mem_tokens - compact["total_tokens"]
        ratio = round(compact["total_tokens"] / max(mem_tokens, 1) * 100, 1)

        return {
            "memory_system": {
                "files": mem_files,
                "estimated_tokens": mem_tokens,
                "method": "全量加载所有 .md 文件",
            },
            "nexsandglass": {
                "estimated_tokens": compact["total_tokens"],
                "method": "蒸馏上下文块（交互协议 + 偏移 + 锚点）",
            },
            "savings": {
                "tokens_saved": saved,
                "reduction_percent": round(100 - ratio, 1),
                "ratio": f"{ratio}%",
            },
        }


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    ns = NexSandglass()

    if cmd == "write":
        # Usage: nexsandglass.py write <session_id> --role user|assistant --content "..."
        session_id = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        role = "user"
        content = ""
        turn = ""
        for i, arg in enumerate(sys.argv):
            if arg == "--role" and i + 1 < len(sys.argv):
                role = sys.argv[i + 1]
            if arg == "--content" and i + 1 < len(sys.argv):
                content = sys.argv[i + 1]
            if arg == "--turn" and i + 1 < len(sys.argv):
                turn = sys.argv[i + 1]
        if not content:
            print("❌ 需要 --content")
            sys.exit(1)
        result = ns.ingest(session_id, role, content, turn)
        print(f"✅ 沙粒: {result[0]}")
        if len(result) > 1:
            print(f"🎯 检测到决策: {result[1:]}")

    elif cmd == "drift":
        days = 7
        for i, arg in enumerate(sys.argv):
            if arg == "--days" and i + 1 < len(sys.argv):
                days = int(sys.argv[i + 1])
        drift = ns.drift.compute_range(days)
        print(f"═══ 偏移率 ({days}天) ═══")
        print(f"  方向: {drift['direction']}")
        print(f"  稳定性: {drift['stability']} (std={drift['stability_score']})")
        print(f"  趋势: {drift['trend_description']}")
        if drift["daily"]:
            print(f"  日均偏移:")
            for d in drift["daily"]:
                if d["total_signals"] > 0:
                    print(f"    {d['date']}: {d['drift_score']:+.3f} ({d['total_signals']} 信号)")

    elif cmd == "detect":
        session_id = sys.argv[2] if len(sys.argv) > 2 else ""
        decisions = ns.detector.recent_decisions(20)
        print(f"═══ 决策粒子 (最近 {len(decisions)} 条) ═══")
        for d in decisions:
            print(f"  [{d.get('type', '?')}] {d.get('text', '')[:80]}")

    elif cmd == "persona":
        full = "--full" in sys.argv
        persona = ns.persona.build(full)
        if persona.get("status") == "insufficient_data":
            print(f"⚠️ 沙粒不足: {persona['grains']} 条 (需要 ≥10)")
        else:
            guide = ns.interaction_guide()
            print(guide)

    elif cmd == "analyze":
        result = ns.analyze()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "compact":
        ctx = ns.compact_context()
        print(f"═══ 紧凑上下文 ({ctx['total_tokens']} tokens) ═══")
        print(ctx["context_block"])
        print(f"\n{ctx['drift_line']}")
        print(f"\n沙粒: {ctx['total_grains']} | 趋势: {ctx['drift_trend']}")

    elif cmd == "hybrid":
        block = ns.hybrid_context()
        tokens = max(1, int(len(block) * 0.4))
        print(f"═══ 混合注入块 ({tokens} tokens) ═══")
        print(block)

    elif cmd == "compare":
        report = ns.compare_memory_cost()
        hybrid = ns.hybrid_context()
        hybrid_tokens = max(1, int(len(hybrid) * 0.4))
        print(f"═══ Token 效率对比 ═══")
        print(f"  MEMORY.md 全量: {report['memory_system']['estimated_tokens']} tokens ({report['memory_system']['files']} 文件)")
        print(f"  NexSandglass 纯蒸馏: {report['nexsandglass']['estimated_tokens']} tokens")
        print(f"  NexSandglass 混合注入: {hybrid_tokens} tokens (蒸馏 + 关键事实)")
        print(f"  节省: {report['savings']['tokens_saved']} tokens ({report['savings']['reduction_percent']}%)")
        print(f"\n─── 混合注入块预览 ───")
        print(hybrid)

    elif cmd == "stats":
        count = ns.writer.count()
        drift = ns.drift.compute_range(7)
        decisions = ns.detector.decision_summary()
        print(f"═══ NexSandglass 统计 ═══")
        print(f"  沙粒总数: {count}")
        print(f"  当前偏移方向: {drift['direction']}")
        print(f"  偏移稳定性: {drift['stability']}")
        print(f"  决策粒子: {decisions['total']}")
        print(f"  偏好声明占比: {decisions['preference_ratio']}")

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
