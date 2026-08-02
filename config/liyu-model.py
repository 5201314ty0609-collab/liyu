#!/usr/bin/env python3
"""
鲤鱼 Model Switch — 模型切换工具
独立于 Claude Code 的模型管理能力。

功能：
  list        列出可用模型配置
  current     显示当前模型
  switch      切换到指定模型
  preset      使用预设配置（mimo/claude/openai）
  add         添加新模型配置
  remove      删除模型配置
  export      导出当前配置

Usage:
  python3 liyu-model.py list
  python3 liyu-model.py current
  python3 liyu-model.py switch mimo-v2.5-pro
  python3 liyu-model.py preset mimo
  python3 liyu-model.py add --name gpt-4 --base-url https://api.openai.com/v1 --api-key sk-xxx
"""

from pathlib import Path
import json
import sys

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
MODELS_CONFIG_FILE = 鲤鱼_HOME / "models-config.json"

# 预设配置
PRESETS = {
    "mimo": {
        "name": "MiMo (小米)",
        "description": "小米 MiMo 模型，通过 token-plan-cn 代理",
        "config": {
            "ANTHROPIC_BASE_URL": "https://token-plan-cn.xiaomimimo.com/anthropic",
            "ANTHROPIC_MODEL": "mimo-v2.5-pro",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mimo-v2.5-pro",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "mimo-v2.5-pro",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "mimo-v2.5-pro",
        }
    },
    "mimo-1m": {
        "name": "MiMo 1M (小米大上下文)",
        "description": "小米 MiMo 1M 上下文版本",
        "config": {
            "ANTHROPIC_BASE_URL": "https://token-plan-cn.xiaomimimo.com/anthropic",
            "ANTHROPIC_MODEL": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "mimo-v2.5-pro[1M]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "mimo-v2.5-pro[1M]",
        }
    },
    "deepseek": {
        "name": "DeepSeek V4 Pro",
        "description": "DeepSeek V4 Pro 模型",
        "config": {
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "sk-85267b810030428f956a7b737d6edb66",
            "ANTHROPIC_MODEL": "deepseek-v4-pro",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-pro",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "deepseek-v4-pro",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "deepseek-v4-pro",
        }
    },
    "claude": {
        "name": "Claude (Anthropic 官方)",
        "description": "Anthropic 官方 Claude 模型",
        "config": {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "claude-sonnet-4-6",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "claude-opus-4-8",
        }
    },
    "proxy": {
        "name": "Local Proxy (Token 追踪)",
        "description": "通过本地代理，自动追踪 Token 用量",
        "config": {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8766/anthropic",
        }
    }
}


class ModelManager:
    """模型管理器"""

    def __init__(self):
        self.settings = self._load_settings()
        self.models_config = self._load_models_config()

    def _load_settings(self) -> dict:
        """加载 Claude Code 设置"""
        if SETTINGS_FILE.exists():
            try:
                return json.loads(SETTINGS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _load_models_config(self) -> dict:
        """加载 鲤鱼 模型配置"""
        if MODELS_CONFIG_FILE.exists():
            try:
                return json.loads(MODELS_CONFIG_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"models": {}, "presets": PRESETS}

    def _save_settings(self):
        """保存 Claude Code 设置"""
        SETTINGS_FILE.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2))

    def _save_models_config(self):
        """保存 鲤鱼 模型配置"""
        MODELS_CONFIG_FILE.write_text(json.dumps(self.models_config, ensure_ascii=False, indent=2))

    def list_models(self) -> List[dict]:
        """列出所有可用模型"""
        models = []
        env = self.settings.get("env", {})

        # 当前使用的模型
        current = env.get("ANTHROPIC_MODEL", "")
        models.append({
            "name": current,
            "status": "current",
            "source": "settings.json"
        })

        # 预设模型
        for preset_id, preset in PRESETS.items():
            if preset["config"].get("ANTHROPIC_MODEL") != current:
                models.append({
                    "name": preset["config"].get("ANTHROPIC_MODEL", ""),
                    "status": "preset",
                    "preset_id": preset_id,
                    "source": preset["name"]
                })

        # 自定义模型
        for model_id, model_config in self.models_config.get("models", {}).items():
            if model_config.get("model") != current:
                models.append({
                    "name": model_config.get("model", ""),
                    "status": "custom",
                    "model_id": model_id,
                    "source": "custom"
                })

        return models

    def get_current(self) -> dict:
        """获取当前模型配置"""
        env = self.settings.get("env", {})
        return {
            "model": env.get("ANTHROPIC_MODEL", ""),
            "base_url": env.get("ANTHROPIC_BASE_URL", ""),
            "haiku": env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", ""),
            "sonnet": env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", ""),
            "opus": env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", ""),
            "effort_level": env.get("CLAUDE_CODE_EFFORT_LEVEL", "default")
        }

    def switch_model(self, model_name: str) -> dict:
        """切换到指定模型"""
        # 检查是否是预设
        for preset_id, preset in PRESETS.items():
            if preset["config"].get("ANTHROPIC_MODEL") == model_name:
                return self.apply_preset(preset_id)

        # 检查是否是自定义模型
        for model_id, model_config in self.models_config.get("models", {}).items():
            if model_config.get("model") == model_name:
                return self._apply_custom_model(model_config)

        # 直接更新模型名称
        env = self.settings.get("env", {})
        env["ANTHROPIC_MODEL"] = model_name
        self.settings["env"] = env
        self._save_settings()

        return {
            "status": "ok",
            "action": "switch",
            "model": model_name,
            "message": f"已切换到 {model_name}"
        }

    def apply_preset(self, preset_id: str) -> dict:
        """应用预设配置"""
        if preset_id not in PRESETS:
            return {"status": "error", "message": f"未知预设: {preset_id}"}

        preset = PRESETS[preset_id]
        env = self.settings.get("env", {})

        # 先清除所有 ANTHROPIC_* 模型相关 key，防止旧预设残留
        keys_to_remove = [k for k in env if k.startswith("ANTHROPIC_DEFAULT_")]
        for k in keys_to_remove:
            del env[k]

        # 更新环境变量
        for key, value in preset["config"].items():
            env[key] = value

        self.settings["env"] = env
        self._save_settings()

        return {
            "status": "ok",
            "action": "apply_preset",
            "preset": preset_id,
            "name": preset["name"],
            "model": preset["config"].get("ANTHROPIC_MODEL", ""),
            "message": f"已应用预设: {preset['name']}"
        }

    def _apply_custom_model(self, model_config: dict) -> dict:
        """应用自定义模型配置"""
        env = self.settings.get("env", {})

        # 更新环境变量
        for key, value in model_config.items():
            if key.startswith("ANTHROPIC_") or key == "CLAUDE_CODE_EFFORT_LEVEL":
                env[key] = value

        self.settings["env"] = env
        self._save_settings()

        return {
            "status": "ok",
            "action": "apply_custom",
            "model": model_config.get("model", ""),
            "message": f"已切换到自定义模型: {model_config.get('model', '')}"
        }

    def add_model(self, name: str, base_url: str, api_key: str = "",
                  model: str = "", **kwargs) -> dict:
        """添加新模型配置"""
        models = self.models_config.get("models", {})

        # 生成模型 ID
        model_id = name.lower().replace(" ", "-")

        # 构建配置
        config = {
            "name": name,
            "base_url": base_url,
            "model": model or name,
            "added_at": __import__("datetime").datetime.now().isoformat()
        }

        if api_key:
            config["api_key"] = api_key

        # 添加其他参数
        for key, value in kwargs.items():
            if value:
                config[key] = value

        models[model_id] = config
        self.models_config["models"] = models
        self._save_models_config()

        return {
            "status": "ok",
            "action": "add_model",
            "model_id": model_id,
            "name": name,
            "message": f"已添加模型: {name}"
        }

    def remove_model(self, model_id: str) -> dict:
        """删除模型配置"""
        models = self.models_config.get("models", {})

        if model_id not in models:
            return {"status": "error", "message": f"未知模型: {model_id}"}

        del models[model_id]
        self.models_config["models"] = models
        self._save_models_config()

        return {
            "status": "ok",
            "action": "remove_model",
            "model_id": model_id,
            "message": f"已删除模型: {model_id}"
        }

    def export_config(self) -> dict:
        """导出当前配置"""
        return {
            "settings": self.settings,
            "models_config": self.models_config,
            "presets": PRESETS
        }

    def import_config(self, config: dict) -> dict:
        """导入配置"""
        if "settings" in config:
            self.settings = config["settings"]
            self._save_settings()

        if "models_config" in config:
            self.models_config = config["models_config"]
            self._save_models_config()

        return {
            "status": "ok",
            "action": "import_config",
            "message": "配置已导入"
        }


def main():
    if len(sys.argv) < 2:
        print("用法: python3 liyu-model.py <command> [args]")
        print()
        print("命令:")
        print("  list              列出可用模型")
        print("  current           显示当前模型")
        print("  switch <model>    切换到指定模型")
        print("  preset <id>       使用预设配置")
        print("  add               添加新模型")
        print("  remove <id>       删除模型")
        print("  export            导出配置")
        print("  import <file>     导入配置")
        print()
        print("预设:")
        for pid, p in PRESETS.items():
            print(f"  {pid:12} - {p['name']}")
        return

    command = sys.argv[1]
    manager = ModelManager()

    if command == "list":
        models = manager.list_models()
        print("可用模型:")
        for m in models:
            status_icon = "●" if m["status"] == "current" else "○"
            print(f"  {status_icon} {m['name']} ({m['source']})")

    elif command == "current":
        current = manager.get_current()
        print("当前配置:")
        print(f"  模型: {current['model']}")
        print(f"  代理: {current['base_url']}")
        print(f"  Haiku: {current['haiku']}")
        print(f"  Sonnet: {current['sonnet']}")
        print(f"  Opus: {current['opus']}")
        print(f"  Effort: {current['effort_level']}")

    elif command == "switch":
        if len(sys.argv) < 3:
            print("用法: python3 liyu-model.py switch <model>")
            return
        model = sys.argv[2]
        result = manager.switch_model(model)
        print(result.get("message", result.get("error", "未知错误")))

    elif command == "preset":
        if len(sys.argv) < 3:
            print("用法: python3 liyu-model.py preset <id>")
            print()
            print("可用预设:")
            for pid, p in PRESETS.items():
                print(f"  {pid:12} - {p['name']}: {p['description']}")
            return
        preset_id = sys.argv[2]
        result = manager.apply_preset(preset_id)
        print(result.get("message", result.get("error", "未知错误")))

    elif command == "add":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--name", required=True)
        parser.add_argument("--base-url", required=True)
        parser.add_argument("--api-key", default="")
        parser.add_argument("--model", default="")
        args = parser.parse_args(sys.argv[2:])
        result = manager.add_model(args.name, args.base_url, args.api_key, args.model)
        print(result.get("message", result.get("error", "未知错误")))

    elif command == "remove":
        if len(sys.argv) < 3:
            print("用法: python3 liyu-model.py remove <model_id>")
            return
        model_id = sys.argv[2]
        result = manager.remove_model(model_id)
        print(result.get("message", result.get("error", "未知错误")))

    elif command == "export":
        config = manager.export_config()
        print(json.dumps(config, ensure_ascii=False, indent=2))

    elif command == "import":
        if len(sys.argv) < 3:
            print("用法: python3 liyu-model.py import <file>")
            return
        file_path = sys.argv[2]
        try:
            with open(file_path) as f:
                config = json.load(f)
            result = manager.import_config(config)
            print(result.get("message", result.get("error", "未知错误")))
        except Exception as e:
            print(f"导入失败: {e}")

    else:
        print(f"未知命令: {command}")
        print("使用 'python3 liyu-model.py' 查看帮助")


if __name__ == "__main__":
    main()
