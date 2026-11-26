# -*- coding: utf-8 -*-
"""「小信智能体摘要提取」工作流的本地直连实现。

原 Dify 工作流（小信智能体摘要提取.yml）结构：
    开始 → 6 个独立 LLM 节点（设备 / 业务 / 故障原因 / 分公司操作 / 厂商操作 / 故障详情）
    业务 → [映射] → 二级分类 LLM → [映射2] → 故障现象 LLM
    全部结果 → [代码执行]（清洗 + 组装 8 字段 JSON）→ 直接回复

现在把两个「映射」代码节点换成 mappings.py 的字典查表，
把 8 个 LLM 节点换成 llm_client.chat() 直连调用，提示词来自 prompts/。
输出仍是原「直接回复」节点的那一串 JSON 字符串，app.py 的解析逻辑无需改动。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import FIELD_WORKERS, LLM_MODEL
from llm_client import chat
from mappings import MAPPING
from prompt_loader import load_prompt
from utils import logger

# 与开始节点直接相连、彼此无依赖的 6 个字段
PARALLEL_FIELDS = (
    "device",
    "business",
    "cause",
    "branch_actions",
    "vendor_actions",
    "symptom_detail",
)

# 与原「代码执行」节点输出字典的键顺序一致
OUTPUT_KEYS = (
    "device",
    "business",
    "subclass",
    "symptom",
    "symptom_detail",
    "cause",
    "branch_actions",
    "vendor_actions",
)


def clean_field(text):
    """复刻 yml「代码执行」节点里的 process()：去换行、去 <think>、去所有空格。"""
    cleaned = re.sub(r"[\r\n]+", "", "" if text is None else str(text))
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).replace(" ", "")
    return cleaned


def clean_map_key(text):
    """复刻 yml「映射 / 映射2」代码节点对 arg1 的清洗。"""
    cleaned = re.sub(r"<think>.*?</think>", "", "" if text is None else str(text), flags=re.DOTALL)
    return cleaned.replace(" ", "").replace("\n", "")


def _call(prompt_name, query, categories=""):
    prompt = load_prompt(prompt_name, query=query, categories=categories)
    return chat(prompt, model=LLM_MODEL)


def extract_issue(query):
    """执行整条工作流。

    :return: (json_str, errors) —— json_str 为 8 字段 JSON 字符串，errors 为各节点失败说明。
    """
    errors = []
    values = {}

    def task(field):
        try:
            return field, _call(field, query), None
        except Exception as e:  # noqa: BLE001 - 单字段失败不阻断整条工单
            return field, "无", f"{field}: {e}"

    workers = max(1, FIELD_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(task, field) for field in PARALLEL_FIELDS]
        for future in as_completed(futures):
            field, value, err = future.result()
            values[field] = value
            if err:
                errors.append(err)

    # 依赖链：业务 → 映射 → 二级分类 → 映射2 → 故障现象
    try:
        categories_for_subclass = MAPPING.get(clean_map_key(values.get("business", "")), "")
        values["subclass"] = _call("subclass", query, categories_for_subclass)
    except Exception as e:  # noqa: BLE001
        values["subclass"] = "无"
        errors.append(f"subclass: {e}")

    try:
        categories_for_symptom = MAPPING.get(clean_map_key(values.get("subclass", "")), "")
        values["symptom"] = _call("symptom", query, categories_for_symptom)
    except Exception as e:  # noqa: BLE001
        values["symptom"] = "无"
        errors.append(f"symptom: {e}")

    data = {key: clean_field(values.get(key, "")) for key in OUTPUT_KEYS}
    if errors:
        logger.warning(f"工作流部分节点失败: {errors}")
    return json.dumps(data, ensure_ascii=False), errors


def run_extraction(query):
    """供 app.py 调用：返回 (raw_json, error_msg)。"""
    raw_json, errors = extract_issue(query)
    error_msg = "; ".join(errors)[:1024] if errors else ""
    return raw_json, error_msg


if __name__ == "__main__":
    # 离线自检：只渲染提示词，不调用模型
    sample = "标题：宽带无法上网\n描述：光猫亮红灯\n分公司处理意见：已重启光猫\n厂商处理意见：已恢复"
    for name in ("device", "business", "cause", "branch_actions", "vendor_actions",
                 "symptom_detail", "subclass", "symptom"):
        rendered = load_prompt(name, query=sample, categories="示例类别A、示例类别B")
        print(f"[OK] {name}.txt -> {len(rendered)} 字")
    print("映射表条目:", len(MAPPING))
