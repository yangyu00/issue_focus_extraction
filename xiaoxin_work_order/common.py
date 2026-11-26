"""跨层共用的纯函数：不做任何 I/O，db / pipelines / cli / tests 均可安全引用。

收敛三类反复出现的口径，避免同一件事在多个模块各写一遍：

1. 空值判据（``BLANK_MARKERS`` / ``is_blank`` / ``to_text`` / ``is_valid_content``）
2. 模型文本清洗（``strip_think``）
3. QA 聚类 id 列表的序列化 / 反序列化（``format_issue_ids`` / ``parse_issue_ids``）
"""
import ast
import re
from typing import Any

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

# 「无实质内容」的统一口径：to_text 与 is_valid_content 共用，避免同一份
# 厂商处理意见在「模型输入归一」和「入库分流」时得出不同结论。
BLANK_MARKERS = frozenset({"", "nan", "none", "无", "无无"})


def is_blank(x: Any) -> bool:
    """空值 / nan /「无」/「无无」是否视为无实质内容（大小写不敏感）。"""
    if x is None:
        return True
    return str(x).strip().lower() in BLANK_MARKERS


def to_text(x: Any) -> str:
    """把任意值规整为文本；空值统一收敛为「其他」。"""
    return "其他" if is_blank(x) else str(x).strip()


def is_valid_content(val) -> bool:
    """厂商处理意见（fldColumn4）是否有实质内容——决定入库分流。

    与 ``to_text`` 共用 ``BLANK_MARKERS``：被归一为「其他」的值在这里同样判为无效。
    """
    return not is_blank(val)


def strip_think(text) -> str:
    """去掉 Qwen3 / DeepSeek-R1 的思维链标签（模型客户端与工作流共用）。"""
    if text is None:
        return ""
    return _THINK_BLOCK.sub("", str(text))


def dedup_device_value(value: Any) -> Any:
    """去除整串翻倍的设备名（模型偶发把同一设备名输出两遍）。

    例：``dedup_device_value("光猫光猫") -> "光猫"``；非字符串或奇数长度原样返回。
    """
    if not isinstance(value, str):
        return value
    if len(value) < 2 or len(value) % 2 != 0:
        return value
    half = len(value) // 2
    left, right = value[:half], value[half:]
    return left if left == right else value


def format_issue_ids(value) -> str:
    """把 matched_issue_ids 统一成「逗号分隔、无空格」的字符串。

    接受 list / tuple / set / 已序列化的字符串（含 ``"[1, 2]"`` 字面量）/ None。
    """
    return ",".join(parse_issue_ids(value))


def parse_issue_ids(value) -> list:
    """解析 matched_issue_ids：支持 list 字面量字符串、逗号分隔字符串、已是 list。"""
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    value = value.strip()
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, (list, tuple, set)):
            return [str(v).strip() for v in parsed if str(v).strip()]
    except (ValueError, SyntaxError):
        pass
    return [v.strip() for v in value.split(",") if v.strip()]
