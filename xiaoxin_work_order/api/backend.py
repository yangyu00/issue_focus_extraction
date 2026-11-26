"""后端取数接口（只读 HTTP 调用，非数据库）。

响应需满足 code == 200 且 success 为真，result 为工单列表。
字段由 pipelines.issue_pipeline 按 taskOid / lastTime / taskTitle /
fldFaultcontent / fldColumn2 / fldColumn4 读取。
"""
import json

import pandas as pd
import requests

from xiaoxin_work_order.config import BACKEND_API_URL
from xiaoxin_work_order.logging_setup import logger


def fetch_data_from_api(start_time: str, end_time: str) -> pd.DataFrame:
    """调用后端接口取数，失败或空结果统一返回空 DataFrame。"""
    print(f"正在获取数据: {start_time} 至 {end_time}")
    params = {"startTime": start_time, "endTime": end_time}
    try:
        resp = requests.get(BACKEND_API_URL, params=params, timeout=60)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.error(f"接口返回非JSON格式: {resp.text[:100]}")
            return pd.DataFrame()

        if data.get("code") == 200 and data.get("success"):
            result_list = data.get("result", [])
            if not result_list:
                logger.info(f"接口返回数据为空 ({start_time})")
                return pd.DataFrame()
            print(f"成功获取 {len(result_list)} 条数据")
            return pd.DataFrame(result_list)
        logger.error(f"接口业务报错: {data.get('message')}")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"接口请求网络异常: {e}")
        print(f"接口请求异常: {e}")
        return pd.DataFrame()
