# -*- coding: utf-8 -*-
"""本地提示词加载。

所有提示词以 .txt 形式维护在 prompts/ 目录下，模板里用 {{name}} 占位，
运行时由 load_prompt(name, ...) 注入。改提示词只需改文件，不用改代码。
"""
import os
import re

from config import PROMPTS_DIR

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")


def load_prompt(name, **kwargs):
    """读取 prompts/<name>.txt 并替换 {{key}} 占位符。

    未提供的占位符会直接报错，避免把模板变量原样发给模型。
    """
    path = os.path.join(PROMPTS_DIR, f"{name}.txt")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"提示词文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", "" if value is None else str(value))

    left = _PLACEHOLDER.findall(text)
    if left:
        raise ValueError(f"提示词 {name} 仍有未注入的占位符: {left}")
    return text


def list_prompts():
    return sorted(f[:-4] for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt"))
