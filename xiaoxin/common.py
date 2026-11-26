"""跨层共用的纯函数：不做任何 I/O，db / pipelines 均可安全引用。"""
from typing import Any


def to_text(x: Any) -> str:
    """把任意值规整为文本；空值 / nan / 无 统一收敛为「其他」。"""
    if x is None:
        return "其他"
    s = str(x).strip()
    if s == "" or s.lower() == "nan" or s == "无" or s == "无无":
        return "其他"
    return s


def is_valid_content(val) -> bool:
    """厂商处理意见（fldColumn4）是否有实质内容——决定入库分流。"""
    if val is None:
        return False
    s = str(val).strip()
    return not (s == "" or s.lower() == "nan")


def dedup_device_value(value: str) -> str:
    """去除重复字符串（模型偶发把同一设备名输出两遍）。"""
    if not isinstance(value, str):
        return value
    if len(value) < 2 or len(value) % 2 != 0:
        return value
    half = len(value) // 2
    left, right = value[:half], value[half:]
    return left if left == right else value
