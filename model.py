from time import sleep, time
import requests
import re
import json  # [新增] 必须引入 json 处理流式返回
from threading import Lock

# URL = "http://134.84.60.31:20005/v1/chat/completions"
# API_KEY = "sk-m-4c062edb8fd5486393f4531cec5f62b1"
# MODEL = "deepseek-r1"

# URL = "http://134.84.255.13:23471/v1/chat/completions"
# API_KEY = "sk-8Sf1bOgJEpKPa6FehorBRQ"
# MODEL = "cxy-deepseek-r1"


URL = "http://134.84.255.13:23471/v1/chat/completions"
API_KEY = "sk-PxwP10Ndr_vJZ-s2sbTE2A"
MODEL = "cxy-deepseek-v3-0324"

# ================= 新增：限流器类 (保持不变) =================
class RateLimiter:
    def __init__(self, max_calls_per_second):
        self.interval = 1.0 / max_calls_per_second
        self.lock = Lock()
        self.next_allowed_time = 0

    def wait(self):
        """线程安全地等待，直到允许发起下一次请求"""
        with self.lock:
            current_time = time()
            if current_time < self.next_allowed_time:
                sleep_time = self.next_allowed_time - current_time
                sleep(sleep_time)
                self.next_allowed_time += self.interval
            else:
                self.next_allowed_time = current_time + self.interval

global_limiter = RateLimiter(max_calls_per_second=2)
# ===============================================

prompt = """
# Role
你是一名资深的企业级知识库架构师和数据清洗专家。你的核心能力是能够从杂乱的运维工单数据中提炼出高价值的“标准问答对（Standard QA Pairs）”。

# Task
请对输入的工单数据（包含：故障现象、详情、原因、措施、结果、建议）进行智能聚类与提炼。
你的目标是构建一个适配**向量检索（Vector Search/RAG）**的知识库。

# Input Data Dimensions
- id: 故障现象 ：：故障详情 ：：故障原因 ：：具体措施1 ：：措施对应结果1 ：：后续建议1 ：：具体措施2 ：：措施对应结果2 ：：后续建议2...

# Critical Rules (核心处理逻辑)

## 1. 聚类原则：根因与方案的双重一致性
* **判断标准**：只有当两个工单的**解决措施**一致时，才能归为一类。
* **区分度要求**：
    * *Case A*：宽带不通 -> 光猫断电 -> 需插电。
    * *Case B*：宽带不通 -> 光缆被挖断 -> 需工程队熔接。
    * *决策*：尽管现象都是“宽带不通”，但解决措施完全不同，**必须拆分为两个不同的QA聚类**。

## 2. Q端优化：面向向量检索的“标准问题”生成
为了提高向量匹配的准确率，生成的 Standard Question 必须满足：
* **结构化描述**：采用 `[业务场景/对象] + [核心报错/异常现象] + [关键特征]` 的结构。
* **语义去噪**：去除“帮忙看下”、“急急急”等情绪词，去除具体的“IP地址、手机号、日期”等高频变化词。    
* **泛化与同义词**：如果工单中包含黑话或缩写，请在标准问题中保留通用的专业术语，确保业务员输入相关关键词时能命中向量空间。
* *Example*：
    * *Bad*: "张三的电脑连不上网了报错1001" (包含无用实体，向量过于稀疏)
    * *Good*: "办公电脑无法连接内网，报错代码1001" (语义稠密，适合检索)

## 3. A端优化：清洗无效知识与标准化回复
生成的 Standard Answer 必须是业务员可以直接发送给客户的“话术”或“操作指引”。
* **🚫 绝对禁令 (无效知识清洗)**：
    * **严禁**保留具体的“联系人姓名”、“手机号”、“工单流水号”、“特定IP地址”。
    * **严禁**出现“联系网管小王”、“已通知李四处理”这类一次性信息。
* **✅ 泛化替换策略**：
    * 将“联系张伟”修改为 -> “联系对应二线支撑团队”或“联系系统管理员”。
    * 将“重启192.168.1.5”修改为 -> “重启故障设备”。
* **内容合成**：答案应综合“故障原因”、“具体措施”和“后续建议”。
    * *格式建议*：【问题原因】+【解决方案/操作步骤】。

# Output Format
请严格按照以下 JSON 格式输出，不要有其他无关内容或解释：
{{
  "clusters": [
    {{
      "standard_question": "生成的标准问题 (需语义清晰)",
      "standard_answer": "生成的标准答案 (清洗了人名电话，综合了原因和措施，业务员可直接复制)",
      "matched_issue_ids": [1, 5, 8]
    }},
    ...
  ]
}}

# Data to Process
{input}
"""

def run_model(model_input: str) -> str:
    retries, last_exc = 0, None
    user_content = prompt.format(input=model_input)

    headers = {
        "Content-Type": "application/json;charset=utf-8",
        "Authorization": f"Bearer {API_KEY}",
    }
    # 注意：payload 中 stream 必须为 True
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": user_content}],
        "temperature": 0.1,
        "stream": True,
        "stream_options": {"include_usage": True}
    }

    while retries < 3:
        try:
            # 1. 限流等待
            global_limiter.wait()

            # 用于收集完整的返回内容，以便后续做 clean_result 处理和返回
            full_content_accumulator = []

            # 2. 发起流式请求 (参考图片逻辑)
            # stream=True 开启流式模式
            with requests.post(URL, headers=headers, json=payload, timeout=120, stream=True) as resp:
                resp.raise_for_status()
                
                # 逐行读取流
                for line in resp.iter_lines():
                    if line:
                        decoded_line = line.decode("utf-8")
                        # 检查是否以 "data:" 开头 (SSE 格式)
                        if decoded_line.startswith("data:"):
                            # 去掉前缀并去除首尾空白
                            data_str = decoded_line[5:].strip()
                            
                            # 某些 API 会在结束时发送 [DONE]
                            if data_str == "[DONE]":
                                break

                            try:
                                json_data = json.loads(data_str)
                                
                                # 解析 delta 内容
                                if "choices" in json_data and json_data["choices"]:
                                    delta = json_data["choices"][0].get('delta', {})
                                    if "content" in delta:
                                        content = delta["content"]
                                        # 【实时打印】flush=True 确保立即显示
                                        print(content, end="", flush=True)
                                        # 收集起来用于返回值
                                        full_content_accumulator.append(content)
                            
                            except json.JSONDecodeError:
                                pass
            
            # 流结束后打印换行
            print() 

            # 拼接完整结果
            result = "".join(full_content_accumulator)

            if not result:
                raise ValueError(f"未获得文本内容")

            # === 解析逻辑 ===
            # 1. 去除思维链
            clean_result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL)
            
            # 2. 提取 JSON 内容
            # 优先尝试提取 ```json ... ``` 代码块
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', clean_result, re.DOTALL)
            if json_match:
                return json_match.group(1)
            
            # 如果没有代码块，尝试提取第一个 {} 对象
            json_match = re.search(r'(\{.*\})', clean_result, re.DOTALL)
            if json_match:
                return json_match.group(1)

            # 如果都匹配不到，尝试清理空白字符后返回（兜底）
            return clean_result.strip()

        except (requests.RequestException, ValueError, KeyError) as e:
            retries += 1
            last_exc = e
            print(f"\n请求失败: {e}，正在尝试第 {retries} 次重连...")
            sleep(1)

    raise Exception(f"所有请求在 {retries} 次尝试后失败，最后错误: {last_exc}")

if __name__ == '__main__':
    # 测试代码
    model_input = '【度小满】通知；您的预估额168900元已成功到达...'
    # 这里 result 依然会拿到清洗后的 JSON 字符串，但控制台会先看到流式输出
    result = run_model(model_input)
    # print(result) # 测试时如果不想看两遍，这行可以注释掉
