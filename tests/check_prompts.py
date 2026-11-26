#!/usr/bin/env python3
"""校验本地提示词与映射表是否仍与 tests/source_snapshot.json 基线逐字一致。

基线在 tests/source_snapshot.json（零第三方依赖，不需要 PyYAML）。
改过 prompts/ 或 mappings.py 后跑一次；任一不一致退出码为 1。

    python tests/check_prompts.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mappings import MAPPING  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "tests", "source_snapshot.json")
PROMPTS_DIR = os.path.join(ROOT, "prompts")


def first_diff(a, b):
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def main():
    with open(SNAPSHOT, "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    problems = []
    expected_prompts = snapshot["prompts"]

    for name, expected in expected_prompts.items():
        path = os.path.join(PROMPTS_DIR, name + ".txt")
        if not os.path.isfile(path):
            problems.append(f"缺失提示词文件: prompts/{name}.txt")
            continue
        with open(path, "r", encoding="utf-8") as f:
            actual = f.read()
        if actual != expected:
            i = first_diff(actual, expected)
            problems.append(
                f"prompts/{name}.txt 第 {i} 字不一致"
                f"（基线 {len(expected)} 字 / 当前 {len(actual)} 字）"
            )
        else:
            print(f"[OK] prompts/{name}.txt  {len(actual)} 字")

    # 反向检查：新增了但没纳入基线的提示词
    actual_names = {
        f[:-4] for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt")
    }
    extra = sorted(actual_names - set(expected_prompts))
    if extra:
        problems.append(f"新增提示词未纳入基线快照: {extra}")

    if MAPPING != snapshot["mapping"]:
        missing = set(snapshot["mapping"]) - set(MAPPING)
        changed = [k for k in snapshot["mapping"] if k in MAPPING and MAPPING[k] != snapshot["mapping"][k]]
        problems.append(f"mappings.py 与基线不一致（缺失 {sorted(missing)}，值变更 {changed}）")
    else:
        print(f"[OK] mappings.py  {len(MAPPING)} 条映射")

    print("-" * 60)
    if problems:
        for p in problems:
            print("[FAIL]", p)
        print(f"基线来源: {snapshot['source']}")
        print("如属预期变更，请同步更新源文件后重新生成 source_snapshot.json。")
        return 1
    print(f"全部一致：{len(expected_prompts)} 段提示词 + {len(MAPPING)} 条映射")
    return 0


if __name__ == "__main__":
    sys.exit(main())
