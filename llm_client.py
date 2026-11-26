# -*- coding: utf-8 -*-
"""直连大模型客户端（OpenAI 兼容 /v1/chat/completions）。

替代原 dify.py 的 Dify 工作流请求：不再经过 134.84.148.13 的 Dify 应用，
直接向模型服务发 chat/completions 请求。提示词全部来自 prompts/ 本地文件。
"""
import json
import re
import time
from threading import Lock

import requests

from config import (
    LLM_API_KEY,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_QPS,
    LLM_STREAM,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    LLM_URL,
)
from utils import logger


class RateLimiter:
    """全局线程安全限流：每秒最多 max_calls_per_second 次请求。"""

    def __init__(self, max_calls_per_second):
        self.interval = 1.0 / max_calls_per_second if max_calls_per_second > 0 else 0
        self.lock = Lock()
        self.next_allowed_time = 0.0

    def wait(self):
        if self.interval <= 0:
            return
        with self.lock:
            now = time.time()
            if now < self.next_allowed_time:
                time.sleep(self.next_allowed_time - now)
                self.next_allowed_time += self.interval
            else:
                self.next_allowed_time = now + self.interval


_LIMITER = RateLimiter(LLM_QPS)


def strip_think(text):
    """去掉 Qwen3 / DeepSeek-R1 的思维链标签。"""
    if text is None:
        return ""
    return re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)


def extract_json_block(text):
    """从模型文本里抽出 JSON 对象（供 QA 聚类结果解析）。"""
    cleaned = strip_think(text)
    match = re.search(r"```json\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if match:
        return match.group(1)
    return cleaned.strip()


def _content_from_message(message):
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return message.get("content") or message.get("text") or ""
    return ""


def _request_once(prompt, model, temperature, stream, timeout):
    headers = {
        "Content-Type": "application/json;charset=utf-8",
        "Authorization": f"Bearer {LLM_API_KEY}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "stream": bool(stream),
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}

    _LIMITER.wait()

    if not stream:
        resp = requests.post(LLM_URL, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"模型返回无 choices: {str(data)[:300]}")
        content = _content_from_message(choices[0].get("message"))
        if not content:
            content = _content_from_message(choices[0].get("delta"))
        return content

    pieces = []
    with requests.post(
        LLM_URL, headers=headers, json=payload, timeout=timeout, stream=True
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data:"):
                continue
            data_str = decoded[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices") or []:
                content = _content_from_message(choice.get("delta"))
                if not content:
                    content = _content_from_message(choice.get("message"))
                if content:
                    pieces.append(content)
    return "".join(pieces)


def chat(prompt, *, model=None, temperature=None, stream=None, timeout=None, max_retries=None):
    """发送一条完整提示词（单 user 消息，与 yml 中把输入嵌在提示词里的写法一致），返回清洗后的文本。"""
    model = model or LLM_MODEL
    temperature = LLM_TEMPERATURE if temperature is None else temperature
    stream = LLM_STREAM if stream is None else stream
    timeout = timeout or LLM_TIMEOUT
    max_retries = max_retries or LLM_MAX_RETRIES

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            content = _request_once(prompt, model, temperature, stream, timeout)
            result = strip_think(content).strip()
            if not result:
                raise ValueError("模型返回空内容")
            return result
        except Exception as e:  # noqa: BLE001 - 网络/解析异常统一重试
            last_exc = e
            logger.warning(f"模型请求失败（第 {attempt}/{max_retries} 次）: {e}")
            if attempt < max_retries:
                time.sleep(1)

    raise RuntimeError(f"模型请求在 {max_retries} 次尝试后仍失败，最后错误: {last_exc}")
