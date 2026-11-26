# -*- coding: utf-8 -*-
"""
文件编码：utf-8
编写时间：2025.04.03
代码编写人：shixinyang
版本：1.0
文件代码说明:Dify接口调用
"""
from requests import post
import time
from utils import logger


def dify_request(query):
    url = 'http://134.84.148.13/v1/chat-messages'
    api_key = 'app-Psu9GYaKueOyi3zkwfj37FaJ'

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    data = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "conversation_id": "",
        "user": "songxiaonan"
    }

    # response = post(url, headers=headers, data=json.dumps(data, ensure_ascii=False))
    # 用 json=data 自动处理 UTF-8 编码
    retries = 0
    while retries < 3:
        try:
            response = post(url, headers=headers, json=data, timeout=(10, 100))
            if response.status_code != 200:
                retries += 1
                time.sleep(1)
                continue

            json_resp = response.json()

            answer = json_resp.get("answer", "")

            json_str = answer.strip().replace('\r', '').replace('\t', '').replace('\n', '')  # 去除str中指定字符

            # try:
            #     result = json.loads(json_str)
            # except json.JSONDecodeError as e:
            #     print("JSON decode error:", e)
            #     print(repr(json_str))

            #     print("Parsed Result:", result)
            #     return result
            if answer:
                return json_str
            else:
                logger.error(f"[Warning] API returned empty answer:{json_resp}")


        except Exception as e:
            print(f"[Retry {retries + 1}] 异常: {e}")
            retries += 1
            time.sleep(1)

    print("达到最大重试次数仍失败")
    return {}


if __name__ == '__main__':
    query = "校园运营"
    dify_request(query)
