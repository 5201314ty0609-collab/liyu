#!/usr/bin/env python3
"""
鲤鱼 Decorators — 装饰器注册模式
吸收自 PrefectHQ/fastmcp 的装饰器即注册模式

核心理念：
  - 一个装饰器同时完成"标记"和"注册"两件事
  - 从类型注解自动生成 JSON Schema
  - 从 docstring 自动提取描述

Usage:
  from liyu_decorators import liyu_skill, liyu_middleware

  @liyu_skill(tags={"memory", "retrieval"})
  def query_knowledge(query: str, limit: int = 10) -> dict:
      '''Query the knowledge base

      Args:
          query: Search query string
          limit: Maximum results to return
      '''
      ...
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Set
import inspect
import json
import re

# ── 技能元数据 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SkillMeta:
    """技能元数据"""
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Set[str] = field(default_factory=set)
    version: str = "1.0.0"
    enabled: bool = True
    timeout: Optional[float] = None

# ── 中间件元数据 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MiddlewareMeta:
    """中间件元数据"""
    name: Optional[str] = None
    priority: int = 0
    enabled: bool = True

# ── 装饰器工厂 ──────────────────────────────────────────────────────────

def liyu_skill(
    name: Optional[str] = None,
    tags: Optional[Set[str]] = None,
    version: str = "1.0.0",
    enabled: bool = True,
    timeout: Optional[float] = None,
) -> Callable:
    """技能装饰器

    Args:
        name: 技能名称（默认使用函数名）
        tags: 技能标签
        version: 技能版本
        enabled: 是否启用
        timeout: 超时时间（秒）

    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        # 从函数名推断名称
        skill_name = name or func.__name__.replace('_', '-')

        # 从 docstring 提取描述
        description = extract_description(func)

        # 创建元数据
        meta = SkillMeta(
            name=skill_name,
            description=description,
            tags=tags or set(),
            version=version,
            enabled=enabled,
            timeout=timeout,
        )

        # 附加到函数
        func.__liyu_skill__ = meta

        # 生成参数 schema
        func.__liyu_schema__ = generate_schema(func)

        return func

    return decorator


def liyu_middleware(
    name: Optional[str] = None,
    priority: int = 0,
    enabled: bool = True,
) -> Callable:
    """中间件装饰器

    Args:
        name: 中间件名称（默认使用类名）
        priority: 优先级（越小越先执行）
        enabled: 是否启用

    Returns:
        装饰器函数
    """
    def decorator(cls: type) -> type:
        # 从类名推断名称
        middleware_name = name or cls.__name__

        # 创建元数据
        meta = MiddlewareMeta(
            name=middleware_name,
            priority=priority,
            enabled=enabled,
        )

        # 附加到类
        cls.__liyu_middleware__ = meta

        return cls

    return decorator

# ── Docstring 解析 ──────────────────────────────────────────────────────

def extract_description(func: Callable) -> str:
    """从 docstring 提取描述

    支持 Google/NumPy/Sphinx 格式
    """
    docstring = inspect.getdoc(func)
    if not docstring:
        return ""

    # 取第一行作为描述
    lines = docstring.strip().split('\n')
    return lines[0].strip() if lines else ""


def extract_param_descriptions(func: Callable) -> Dict[str, str]:
    """从 docstring 提取参数描述

    支持 Google 格式:
        Args:
            param1: Description
            param2: Description
    """
    docstring = inspect.getdoc(func)
    if not docstring:
        return {}

    descriptions = {}
    in_args_section = False

    for line in docstring.split('\n'):
        line = line.strip()

        # 检查 Args: 部分
        if line.lower().startswith('args:'):
            in_args_section = True
            continue

        # 检查其他部分
        if line and not line.startswith(' ') and line.endswith(':'):
            in_args_section = False
            continue

        # 提取参数描述
        if in_args_section and ':' in line:
            param, desc = line.split(':', 1)
            param = param.strip()
            desc = desc.strip()
            if param and desc:
                descriptions[param] = desc

    return descriptions

# ── Schema 生成 ──────────────────────────────────────────────────────────

def generate_schema(func: Callable) -> Dict[str, Any]:
    """从函数签名生成 JSON Schema

    Args:
        func: 目标函数

    Returns:
        JSON Schema 字典
    """
    sig = inspect.signature(func)
    param_descriptions = extract_param_descriptions(func)

    properties = {}
    required = []

    for name, param in sig.parameters.items():
        # 跳过 self 和 cls
        if name in ('self', 'cls'):
            continue

        # 获取类型
        param_type = param.annotation
        if param_type == inspect.Parameter.empty:
            param_type = "string"

        # 转换为 JSON Schema 类型
        json_type = python_type_to_json_type(param_type)

        # 构建属性
        prop = {"type": json_type}

        # 添加描述
        if name in param_descriptions:
            prop["description"] = param_descriptions[name]

        # 添加默认值
        if param.default != inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(name)

        properties[name] = prop

    schema = {
        "type": "object",
        "properties": properties,
    }

    if required:
        schema["required"] = required

    return schema


def python_type_to_json_type(py_type: Any) -> str:
    """将 Python 类型转换为 JSON Schema 类型

    Args:
        py_type: Python 类型

    Returns:
        JSON Schema 类型字符串
    """
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        List: "array",
        Dict: "object",
    }

    # 处理 typing 模块的类型
    if hasattr(py_type, '__origin__'):
        origin = py_type.__origin__
        if origin is list:
            return "array"
        if origin is dict:
            return "object"

    return type_map.get(py_type, "string")

# ── 技能注册表 ──────────────────────────────────────────────────────────

class SkillRegistry:
    """技能注册表"""

    def __init__(self):
        self._skills: Dict[str, Callable] = {}
        self._metadata: Dict[str, SkillMeta] = {}

    def register(self, func: Callable) -> None:
        """注册技能

        Args:
            func: 技能函数（必须有 __liyu_skill__ 属性）
        """
        if not hasattr(func, '__liyu_skill__'):
            raise ValueError(f"Function {func.__name__} is not a liyu skill")

        meta = func.__liyu_skill__
        name = meta.name

        self._skills[name] = func
        self._metadata[name] = meta

    def get(self, name: str) -> Optional[Callable]:
        """获取技能

        Args:
            name: 技能名称

        Returns:
            技能函数或 None
        """
        return self._skills.get(name)

    def get_meta(self, name: str) -> Optional[SkillMeta]:
        """获取技能元数据

        Args:
            name: 技能名称

        Returns:
            技能元数据或 None
        """
        return self._metadata.get(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有技能

        Returns:
            技能列表
        """
        skills = []
        for name, meta in self._metadata.items():
            skills.append({
                "name": meta.name,
                "description": meta.description,
                "tags": list(meta.tags),
                "version": meta.version,
                "enabled": meta.enabled,
            })
        return skills

    def get_schema(self, name: str) -> Optional[Dict[str, Any]]:
        """获取技能参数 Schema

        Args:
            name: 技能名称

        Returns:
            JSON Schema 或 None
        """
        func = self._skills.get(name)
        if func and hasattr(func, '__liyu_schema__'):
            return func.__liyu_schema__
        return None

# ── 中间件注册表 ──────────────────────────────────────────────────────────

class MiddlewareRegistry:
    """中间件注册表"""

    def __init__(self):
        self._middlewares: Dict[str, type] = {}
        self._metadata: Dict[str, MiddlewareMeta] = {}

    def register(self, cls: type) -> None:
        """注册中间件

        Args:
            cls: 中间件类（必须有 __liyu_middleware__ 属性）
        """
        if not hasattr(cls, '__liyu_middleware__'):
            raise ValueError(f"Class {cls.__name__} is not a liyu middleware")

        meta = cls.__liyu_middleware__
        name = meta.name

        self._middlewares[name] = cls
        self._metadata[name] = meta

    def get(self, name: str) -> Optional[type]:
        """获取中间件

        Args:
            name: 中间件名称

        Returns:
            中间件类或 None
        """
        return self._middlewares.get(name)

    def list_middlewares(self) -> List[Dict[str, Any]]:
        """列出所有中间件

        Returns:
            中间件列表
        """
        middlewares = []
        for name, meta in self._metadata.items():
            middlewares.append({
                "name": meta.name,
                "priority": meta.priority,
                "enabled": meta.enabled,
            })
        return middlewares

    def get_sorted(self) -> List[type]:
        """获取按优先级排序的中间件列表

        Returns:
            排序后的中间件类列表
        """
        sorted_metas = sorted(
            self._metadata.values(),
            key=lambda m: m.priority
        )
        return [
            self._middlewares[m.name]
            for m in sorted_metas
            if m.enabled
        ]

# ── 全局注册表 ──────────────────────────────────────────────────────────

skill_registry = SkillRegistry()
middleware_registry = MiddlewareRegistry()

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    """测试装饰器"""
    # 定义测试技能
    @liyu_skill(tags={"test", "example"})
    def test_skill(query: str, limit: int = 10) -> dict:
        """Test skill for demonstration

        Args:
            query: Search query string
            limit: Maximum results to return
        """
        return {"query": query, "limit": limit}

    # 注册技能
    skill_registry.register(test_skill)

    # 列出技能
    print("=== 技能列表 ===")
    for skill in skill_registry.list_skills():
        print(f"  {skill['name']}: {skill['description']}")
        print(f"    Tags: {skill['tags']}")
        print(f"    Version: {skill['version']}")

    # 获取 schema
    print("\n=== Schema ===")
    schema = skill_registry.get_schema("test-skill")
    print(json.dumps(schema, indent=2))


if __name__ == "__main__":
    main()
