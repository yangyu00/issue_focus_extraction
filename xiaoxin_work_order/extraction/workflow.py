"""「小信工单摘要提取」工作流的本地直连实现。

调用结构：
    6 个互不依赖的字段由 xiaoxin_work_order.llm.client.chat() 并发调用；
    业务 → MAPPING → 二级分类 → MAPPING → 故障现象串行；
    全部结果经 clean_field() 清洗后，按 OUTPUT_KEYS 顺序组装 8 字段 JSON 字符串返回。

字段清单、提示词文件名、并发/串行划分的唯一来源是 ``xiaoxin_work_order/fields.py``。
提示词来自 prompts/；返回值由 pipelines.issue_pipeline 的 parse_model_output() 解析。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from xiaoxin_work_order.common import strip_think
from xiaoxin_work_order.config import FIELD_WORKERS, LLM_MODEL
from xiaoxin_work_order.fields import (
    DEPENDENT_FIELDS,
    FIELDS,
    OUTPUT_KEYS,
    PARALLEL_FIELDS,
    PROMPT_NAMES,
)
from xiaoxin_work_order.llm.client import chat
from xiaoxin_work_order.llm.mappings import MAPPING
from xiaoxin_work_order.llm.prompt_loader import load_prompt
from xiaoxin_work_order.logging_setup import logger


def clean_field(text):
    """去 <think>、去换行与所有空格（去 think 与模型客户端共用同一实现）。"""
    return strip_think(text).replace(" ", "").replace("\n", "").replace("\r", "")


def clean_map_key(text):
    """对映射查表键做清洗：去 <think>、去空格、去换行（与 clean_field 同源）。"""
    return clean_field(text)


def _call(field, query, categories=""):
    prompt = load_prompt(PROMPT_NAMES[field], query=query, categories=categories)
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

    # 依赖链：业务 → MAPPING → 二级分类 → MAPPING → 故障现象
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
    """供 pipelines.issue_pipeline 调用：返回 (raw_json, error_msg)。"""
    raw_json, errors = extract_issue(query)
    error_msg = "; ".join(errors)[:1024] if errors else ""
    return raw_json, error_msg


if __name__ == "__main__":
    # 离线自检：只渲染提示词，不调用模型；字段清单直接来自 fields.py
    sample = "标题：宽带无法上网\n描述：光猫亮红灯\n分公司处理意见：已重启光猫\n厂商处理意见：已恢复"
    for field in FIELDS:
        rendered = load_prompt(
            PROMPT_NAMES[field], query=sample, categories="示例类别A、示例类别B"
        )
        print(f"[OK] prompts/{PROMPT_NAMES[field]}.txt -> {len(rendered)} 字")
    print("并行字段:", PARALLEL_FIELDS)
    print("串行字段:", DEPENDENT_FIELDS)
    print("映射表条目:", len(MAPPING))
