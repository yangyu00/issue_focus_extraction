#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Callable, Iterable
import pandas as pd
import re
import json
from model import run_model
from dify import dify_request

from tqdm.auto import tqdm
tqdm.pandas()

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ---------- 读取 ----------
def read_sheet_columns_as_rows(
    xls_path: str,
    sheet: str | int,
    columns: List[str] | str
) -> pd.DataFrame:
    df = pd.read_excel(
        xls_path,
        sheet_name=sheet,
        dtype=object,
        engine="xlrd",      # 读 .xls
        usecols=columns
    )
    return df


# ---------- 工具 ----------
def _to_text(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if s.lower() == "nan" or s == "" or s == "无无":
        return "无"
    return s

def dedup_device_value(value: str) -> str:
    """去除类似 '宽带宽带'、'安防安防' 这种重复字符串"""
    if not isinstance(value, str):
        return value

    # 长度必须是偶数，否则根本不可能是精确重复
    if len(value) % 2 != 0:
        return value

    half = len(value) // 2
    left, right = value[:half], value[half:]

    return left if left == right else value

def ensure_columns(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df

def build_labeled_input(row: pd.Series, required_cols: List[str]) -> str:
    """将 required_cols 拼为：列名：值（空则写‘无’），并以换行连接。"""
    parts: List[str] = []
    for col in required_cols:
        val = _to_text(row.get(col, ""))
        if not val:
            val = "无"
        parts.append(f"{col}：{val}")
    joined = "\n".join(parts).strip()
    # 规整多余空行（通常不会出现，这里防御性处理）
    return re.sub(r"\n{3,}", "\n\n", joined)

# 避免重复调用模型（比如同样的输入文本多次出现时），并且在多线程并发下保证线程安全。
class SummarizerCache:
    def __init__(self):
        self._cache: Dict[str, str] = {}
        # _lock：线程锁，用于防止多线程同时修改 _cache 时出错（保证线程安全）。
        self._lock = threading.Lock()

    def summarize(self, text: str) -> str:
        with self._lock:
            cached = self._cache.get(text)
        if cached is not None:
            return cached
        # out = run_model(text)
        out = dify_request(text)
        tqdm.write("=== 本次模型输入 ===")
        tqdm.write(text)
        tqdm.write("=== 模型输出 ===")
        tqdm.write(str(out))
        with self._lock:
            self._cache[text] = out
        return out


def parallel_map_texts(
    texts: Iterable[str],
    func: Callable[[str], str],
    max_workers: int = 8
) -> List[str]:
    texts = list(texts)
    n = len(texts)
    results: List[str] = ["" for _ in range(n)]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        # futures 是一个字典：key 是任务对象，value 是原文本在列表中的索引。
        futures = {ex.submit(func, txt): i for i, txt in enumerate(texts)}
        for fut in tqdm(as_completed(futures), total=n, desc="并发调用模型"):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                tqdm.write(f"[警告] 第 {i} 条处理失败：{e}")
                results[i] = ""
    return results


# ---------- 新增：解析 run_model 的 JSON 字符串 ----------
def _safe_json_loads(s: str) -> Dict[str, Any]:
    """run_model() 返回字符串；尽量解析为 dict，失败则返回空 dict。"""
    if not isinstance(s, str) or not s.strip():
        return {}
    # 去除可能的围栏或多余空白
    s2 = s.strip()
    # 有些模型可能返回 ```json ... ```，做容错清理
    s2 = re.sub(r"^```(?:json)?\s*|\s*```$", "", s2, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s2)
    except Exception:
        return {}

def _actions_to_str(val: Any) -> str:
    """
    分公司处理 / 厂商处理：若是列表对象则转为紧凑 JSON 字符串；其他类型直接转文本。
    你也可以改为更人类友好的拼接，但为保持“字段值”的一致性，这里选择 JSON 字符串。
    """
    try:
        if isinstance(val, (list, dict)):
            return json.dumps(val, ensure_ascii=False)
        return _to_text(val)
    except Exception:
        return _to_text(val)

def extract_cn_fields_from_model_output(model_out: str) -> Dict[str, str]:
    """
    从 run_model 的输出（字符串 JSON）中提取：
    设备(device)->设备
    症状(symptom)->故障现象
    原因(cause)->故障原因
    分公司处理(branch_actions)->分公司处理
    厂商处理(vendor_actions)->厂商处理
    """
    data = _safe_json_loads(model_out)

    device = dedup_device_value(_to_text(data.get("device", "")))
    business = _to_text(data.get("business", ""))
    symptom = _to_text(data.get("symptom", ""))
    symtom_detail = _to_text(data.get("symptom_detail", ""))
    cause = _to_text(data.get("cause", ""))
    branch = _actions_to_str(data.get("branch_actions", ""))
    vendor = _actions_to_str(data.get("vendor_actions", ""))

    return {
        "设备": device,
        "业务": business,
        "故障现象": symptom,
        "故障提示": symtom_detail,
        "故障原因": cause,
        "分公司处理": branch,
        "厂商处理": vendor,
    }


# ---------- 单个 sheet：按行拼接四列 -> 一次模型调用 ----------
def build_df_for_sheet(
    input_xls: str,
    sheet: str | int,
    columns_to_read: List[str] | str,
    required_cols: List[str],
    cache: SummarizerCache,
    max_workers: int = 8,
) -> pd.DataFrame:
    df = read_sheet_columns_as_rows(input_xls, sheet, columns_to_read)

    # 确保四个必需列存在（缺的补空列）
    df = ensure_columns(df, required_cols)

    # 额外：确保“发起地市”列存在（源表没有则置空；不会参与模型输入）
    df = ensure_columns(df, ["发起地市"])

    # 生成一次性输入（列名：值 的拼接），每行对应一次 run_model
    tqdm.write(f">> [{sheet}] 生成统一模型输入…")
    # 对 df里的每一行 r，调用 build_labeled_input(r, required_cols)，把函数的返回值收集起来组成一个新的 Series（列），并显示执行进度。
    model_inputs = df.progress_apply(lambda r: build_labeled_input(r, required_cols), axis=1)

    tqdm.write(f">> [{sheet}] 并发调用模型（每行一次）…")
    df["模型输出"] = parallel_map_texts(model_inputs.tolist(), cache.summarize, max_workers=max_workers)

    # 解析模型输出为中文字段列
    tqdm.write(f">> [{sheet}] 解析模型输出字段…")
    parsed_cols = df["模型输出"].progress_apply(extract_cn_fields_from_model_output)
    # 将解析结果（字典）展开为列
    parsed_df = pd.DataFrame(list(parsed_cols))

    # 输出列顺序：发起地市、"报障类别", "报障类型", "二级分类"、标题、描述、分公司处理意见、厂商处理意见、设备、故障现象、故障原因、分公司处理、厂商处理、SheetName
    out_df = pd.DataFrame({
        "发起地市": df["发起地市"],
        "报障类别": df["报障类别"],
        "报障类型": df["报障类型"],
        "二级分类": df["二级分类"],
        "标题": df["标题"],
        "描述": df["描述"],
        "分公司处理意见": df["分公司处理意见"],
        "厂商处理意见": df.get("厂商处理意见", ""),  # 某些表可能缺列
    })

    # 合并解析出的 5 个中文字段
    for col in ["设备", "业务", "故障现象", "故障提示", "故障原因", "分公司处理", "厂商处理"]:
        out_df[col] = parsed_df.get(col, "")

    # ★ 新增：如果是【分公司处理的工单】sheet，则将“厂商处理”固定为指定内容
    if str(sheet) == "分公司处理的工单":
        fixed_vendor_actions = json.dumps(
            [{"operation": "无", "result": "无", "suggestion": "无"}],
            ensure_ascii=False
        )
        out_df["厂商处理"] = fixed_vendor_actions

    # 附带 SheetName
    out_df["SheetName"] = str(sheet)

    return out_df


# ---------- 主函数：多 sheet 合并到一个 CSV ----------
def main():
    # 统一输入 .xls
    input_xls = "./data/202507小信报障单明细.xls"

    # 四个必需列（会始终拼接；缺失或空值时写“无”）
    required_cols = ["标题", "描述", "分公司处理意见", "厂商处理意见"]

    # 两个 sheet 的配置（按需读取的列，可少于 required_cols；缺列会在 ensure_columns 中补空）
    sheets_cfg = [
        {
            "sheet": "提交给厂商的工单",
            "columns_to_read": ["标题", "描述", "分公司处理意见", "厂商处理意见", "发起地市", "报障类别", "报障类型", "二级分类"],
        },
        {
            "sheet": "分公司处理的工单",
            "columns_to_read": ["标题", "描述", "分公司处理意见", "发起地市", "报障类别", "报障类型", "二级分类"],  # 此表缺少“厂商处理意见”
        },
    ]

    # 并发与输出
    max_workers = 32
    merged_output_csv = "./output_merged.csv"

    # 共享缓存，跨 sheet 去重（相同输入文本只调用一次模型）
    cache = SummarizerCache()

    # 逐 sheet 生成，再合并
    dfs = []
    for cfg in sheets_cfg:
        out_df = build_df_for_sheet(
            input_xls=input_xls,
            sheet=cfg["sheet"],
            columns_to_read=cfg["columns_to_read"],
            required_cols=required_cols,
            cache=cache,
            max_workers=max_workers,
        )
        dfs.append(out_df)

    # 将多个 DataFrame 按行纵向合并为一个新 DataFrame，并自动重新编号行索引。
    merged = pd.concat(dfs, ignore_index=True)

    # 严格保证列顺序（如果未来改动，下面这行是最终导出的顺序来源）
    final_cols = ["发起地市", "报障类别", "报障类型", "二级分类", "标题", "描述", "分公司处理意见", "厂商处理意见", "设备", "业务", "故障现象", "故障提示", "故障原因", "分公司处理", "厂商处理", "SheetName"]
    for c in final_cols:
        if c not in merged.columns:
            merged[c] = ""
    merged = merged[final_cols]

    # 写出一次
    tqdm.write(f">> 写出合并 CSV：{merged_output_csv}")
    merged.to_csv(merged_output_csv, index=False, encoding="utf-8-sig")
    print(f"✅ 已生成合并文件：{merged_output_csv}，共 {len(merged)} 行")


if __name__ == "__main__":
    main()