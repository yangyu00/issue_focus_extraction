"""「小信智能体摘要提取」工作流的本地直连实现。

调用结构：
    6 个互不依赖的字段（device / business / cause / branch_actions /
    vendor_actions / symptom_detail）由 xiaoxin.llm.client.chat() 并发调用；
    业务 → MAPPING → 二级分类 → MAPPING → 故障现象串行；
    全部结果经 clean_field() 清洗后，按 OUTPUT_KEYS 顺序组装 8 字段 JSON 字符串返回。

提示词来自 prompts/；返回值由 pipelines.issue_pipeline 的 process_model_output_for_db() 解析。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from xiaoxin.config import FIELD_WORKERS, LLM_MODEL
from xiaoxin.llm.client import chat
from xiaoxin.llm.mappings import MAPPING
from xiaoxin.llm.prompt_loader import load_prompt
from xiaoxin.logging_setup import logger

# 彼此无依赖、可并发的 6 个字段
PARALLEL_FIELDS = (
    "device",
    "business",
    "cause",
    "branch_actions",
    "vendor_actions",
    "symptom_detail",
)

# 输出字典的键顺序
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
    """去换行、去 <think>、去所有空格。"""
    cleaned = re.sub(r"[\r\n]+", "", "" if text is None else str(text))
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).replace(" ", "")
    return cleaned


def clean_map_key(text):
    """对映射查表键做清洗：去 <think>、去空格、去换行。"""
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
    # 离线自检：只渲染提示词，不调用模型
    sample = "标题：宽带无法上网\n描述：光猫亮红灯\n分公司处理意见：已重启光猫\n厂商处理意见：已恢复"
    for name in ("device", "business", "cause", "branch_actions", "vendor_actions",
                 "symptom_detail", "subclass", "symptom"):
        rendered = load_prompt(name, query=sample, categories="示例类别A、示例类别B")
        print(f"[OK] {name}.txt -> {len(rendered)} 字")
    print("映射表条目:", len(MAPPING))
