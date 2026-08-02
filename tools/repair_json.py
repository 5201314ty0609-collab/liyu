#!/usr/bin/env python3
"""
鲤鱼 JSON Repair Module — 借鉴 MUNDO v2.2.7 的 repair_json 模式。

提供鲁棒的 JSON 解析，处理 LLM 输出的格式不正确 JSON。
"""

import json
import re
from typing import Any, Optional


def repair_json(text: str) -> Optional[str]:
    """
    尝试修复格式不正确的 JSON 字符串。
    
    修复策略：
    1. 转义裸换行符
    2. 修复未转义的引号
    3. 正则提取 key-value 对
    4. 移除尾随逗号
    
    Args:
        text: 可能包含格式错误的 JSON 字符串
        
    Returns:
        修复后的 JSON 字符串，或 None 如果无法修复
    """
    if not text or not isinstance(text, str):
        return None
    
    # 移除首尾空白
    text = text.strip()
    
    # 如果已经是有效 JSON，直接返回
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    
    # 策略 1: 转义裸换行符
    repaired = _escape_newlines(text)
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass
    
    # 策略 2: 移除尾随逗号
    repaired = _remove_trailing_commas(repaired)
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass
    
    # 策略 3: 修复未转义的引号
    repaired = _escape_quotes(repaired)
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass
    
    # 策略 4: 正则提取 key-value 对
    repaired = _extract_key_values(text)
    if repaired:
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            pass
    
    return None


def _escape_newlines(text: str) -> str:
    """转义字符串值中的裸换行符。"""
    # 匹配字符串值中的裸换行符
    # 这是一个简化的实现，可能不适用于所有情况
    result = []
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text):
        if escape_next:
            result.append(char)
            escape_next = False
            continue
        
        if char == '\\':
            result.append(char)
            escape_next = True
            continue
        
        if char == '"':
            in_string = not in_string
            result.append(char)
            continue
        
        if in_string and char == '\n':
            result.append('\\n')
            continue
        
        if in_string and char == '\r':
            result.append('\\r')
            continue
        
        result.append(char)
    
    return ''.join(result)


def _remove_trailing_commas(text: str) -> str:
    """移除对象和数组中的尾随逗号。"""
    # 移除 } 或 ] 前的逗号
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _escape_quotes(text: str) -> str:
    """修复字符串值中未转义的引号。"""
    # 这是一个简化的实现
    # 匹配 "key": "value" 模式，修复 value 中的未转义引号
    def fix_quotes(match):
        key = match.group(1)
        value = match.group(2)
        # 转义 value 中的未转义引号
        fixed_value = value.replace('"', '\\"')
        return f'"{key}": "{fixed_value}"'
    
    # 匹配 "key": "value" 模式
    pattern = r'"([^"]+)"\s*:\s*"([^"]*)"'
    return re.sub(pattern, fix_quotes, text)


def _extract_key_values(text: str) -> Optional[str]:
    """从格式错误的文本中提取 key-value 对。"""
    # 尝试匹配 "key": "value" 或 "key": value 模式
    pattern = r'"([^"]+)"\s*:\s*("[^"]*"|[^,}\]]+|true|false|null|\d+\.?\d*)'
    matches = re.findall(pattern, text)
    
    if not matches:
        return None
    
    # 构建 JSON 对象
    result = {}
    for key, value in matches:
        # 尝试解析值
        try:
            if value.startswith('"') and value.endswith('"'):
                result[key] = value[1:-1]
            elif value.lower() == 'true':
                result[key] = True
            elif value.lower() == 'false':
                result[key] = False
            elif value.lower() == 'null':
                result[key] = None
            elif '.' in value:
                result[key] = float(value)
            else:
                result[key] = int(value)
        except (ValueError, AttributeError):
            result[key] = value
    
    return json.dumps(result, ensure_ascii=False)


def safe_json_loads(text: str, default: Any = None) -> Any:
    """
    安全的 JSON 解析，自动尝试修复。
    
    Args:
        text: JSON 字符串
        default: 解析失败时的默认值
        
    Returns:
        解析后的对象，或默认值
    """
    if not text:
        return default
    
    # 首先尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 尝试修复
    repaired = repair_json(text)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
    
    return default


# 测试
if __name__ == "__main__":
    # 测试用例
    test_cases = [
        # 正常 JSON
        '{"key": "value"}',
        # 裸换行符
        '{"key": "line1\nline2"}',
        # 尾随逗号
        '{"key": "value",}',
        # 未转义的引号
        '{"key": "value with "quotes" inside"}',
        # 格式错误的 JSON
        '{key: value}',
    ]
    
    for i, test in enumerate(test_cases, 1):
        print(f"Test {i}: {test[:50]}...")
        result = safe_json_loads(test)
        print(f"  Result: {result}")
        print()
