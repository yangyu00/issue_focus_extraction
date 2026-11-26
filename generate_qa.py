'''
提取知识
从数据库表 xxa_bomc_qa_non_vendor 中读取故障工单数据，经过过滤和清洗后，调用大模型（run_model）聚类生成标准问答对（QA Knowledge），并将结果保存为 Excel 文件。
现有代码将run_model注释，运行该代码只负责只负责从 MySQL 读取并过滤故障工单数据，然后去 GBase 查询对应工单的详情，按 symptom 分组，并将 GBase 结果保存成 Excel 文件
'''
# !/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re

import json_repair
import pandas as pd
import pymysql
from sqlalchemy import create_engine
from utils import logger, setup_logging
from config import GENERATE_QA_INFO_LOG_NAME, GENERATE_QA_ERROR_LOG_NAME
from model import run_model
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from gbase_excel_query import query_gbase_by_task_oids

# 配置日志
setup_logging(GENERATE_QA_INFO_LOG_NAME, GENERATE_QA_ERROR_LOG_NAME)

# ================= 配置区域 =================
DB_CONFIG = {
    "host": "172.21.224.71",
    # "host": "134.84.22.233",
    "port": 6306,
    "user": "root",
    "password": "Nttslgkpt*2024",
    "database": "ntt_xiaoxin_agent",
    "charset": "utf8mb4"
}

# 数据库连接 URL
db_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
engine = create_engine(db_url, pool_recycle=3600, pool_pre_ping=True)


def convert_actions(json_str):
    # 处理空值或非字符串的情况
    if pd.isna(json_str) or not isinstance(json_str, str):
        return ""

    try:
        # 解析 JSON 字符串
        # 注意：如果你的字符串里是单引号List [{'a':1}]，请看下方的"注意事项"
        data_list = json.loads(json_str)

        output_parts = []
        for item in data_list:
            # 使用 .get() 防止某个字典缺少 key 报错
            # 并强制转为 str，防止 value 是数字导致 join 报错
            op = str(item.get("operation", ""))
            res = str(item.get("result", ""))
            sug = str(item.get("suggestion", ""))

            output_parts.extend([op, res, sug])

        # 用 " :: " 连接所有元素
        return " :: ".join(output_parts)

    except (json.JSONDecodeError, TypeError):
        # 如果解析失败，返回空字符串或原始值
        return ""


def process_group(symptom, group, gbase_result_collector: dict):
    """
    处理单个 symptom 组的数据，如果超过 400 条分批处理。
    同时查询 GBase 获取该 symptom 下所有 task_oid 的工单详情，
    结果写入 gbase_result_collector[symptom]。
    """
    qa_list = []
    chunk_size = 400
    total_records = len(group)

    # ---- 查询 GBase：获取该 symptom 下全部 task_oid 的工单详情 ----
    try:
        task_oid_list = group["task_oid"].dropna().astype(str).unique().tolist()
        logger.info(f"symptom='{symptom}'：向 GBase 查询 {len(task_oid_list)} 个 task_oid")
        gbase_records = query_gbase_by_task_oids(task_oid_list)
        if gbase_records:
            gbase_rows = []
            for r in gbase_records:
                gbase_rows.append({
                    "task_oid": r.get("TASK_OID", ""),
                    "报障标题": r.get("TASK_TITLE", ""),
                    "报障内容": r.get("fld_faultcontent", ""),
                    "分公司处理意见": r.get("fld_column_2", ""),
                })
            gbase_result_collector[symptom] = gbase_rows
            logger.info(f"symptom='{symptom}'：GBase 返回 {len(gbase_rows)} 条记录")
        else:
            logger.warning(f"symptom='{symptom}'：GBase 未返回任何记录")
            gbase_result_collector[symptom] = []
    except Exception as e:
        logger.error(f"symptom='{symptom}' GBase 查询失败: {e}", exc_info=True)
        gbase_result_collector[symptom] = []

    try:
        # 分批处理
        for start_idx in range(0, total_records, chunk_size):
            end_idx = min(start_idx + chunk_size, total_records)
            chunk = group.iloc[start_idx:end_idx]
            batch_num = (start_idx // chunk_size) + 1

            # 构造 run_model 的输入
            current_group_lines = []
            for _, row in chunk.iterrows():
                line = f"{row['task_oid']}: {row['symptom']} :: {row['symptom_detail']} :: {row['cause']} :: {row['formatted_actions']}"
                current_group_lines.append(line)

            model_input_str = "\n".join(current_group_lines)

            logger.info(
                f"正在提交处理 symptom='{symptom}' (批次 {batch_num})，包含 {len(chunk)} 条记录 (总 {total_records} 条)")

            # 4. 调用 run_model
            try:
                result = ""
                result = run_model(model_input_str)

                # 5. 解析结果并收集
                # result_json = json.loads(result)
                result_json = json_repair.loads(result)

                if "clusters" in result_json:
                    for cluster in result_json["clusters"]:
                        qa_item = {
                            "symptom": symptom,
                            "standard_question": cluster.get("standard_question", ""),
                            "standard_answer": cluster.get("standard_answer", ""),
                            "matched_issue_ids": str(cluster.get("matched_issue_ids", []))
                        }
                        qa_list.append(qa_item)
            except json.JSONDecodeError as e:
                logger.error("解析模型输出 JSON 失败: {} | symptom='{}' (批次 {}) | 原始内容: {}...", e, symptom,
                             batch_num, result, exc_info=True)
            except Exception as e:
                logger.error(f"调用模型失败 symptom='{symptom}' (批次 {batch_num}): {e}", exc_info=True)

    except Exception as e:
        print(f"处理 symptom='{symptom}' 时发生错误: {e}")
        logger.error(f"处理 symptom='{symptom}' 时发生错误: {e}", exc_info=True)

    return qa_list


def save_to_database(qa_data):
    """
    将生成的QA数据直接保存到 xxa_city_qa_knowledge 表中
    """
    # 1. 数据库连接配置 (请根据实际情况修改)
    connection = pymysql.connect(**DB_CONFIG)

    # 2. 固定字段定义
    fixed_values = {
        'knowledge_source': '工单提取',
        'work_id': 'system',
        'name': '知识管理平台',
        'city': '999',
        'city_text': '全省',
        'task_oids': "",
    }

    try:
        with connection.cursor() as cursor:
            # 3. 构建 SQL 插入语句
            # 假设表结构中包含自增主键，这里只列出需要插入的字段
            sql = """
                INSERT INTO xxa_city_qa_knowledge
                (question, answer,
                 knowledge_source, work_id, name, city, city_text, task_oids)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s)
            """

            # 4. 准备数据列表
            data_to_insert = []
            for item in qa_data:
                row = (
                    item['standard_question'],
                    item['standard_answer'],
                    fixed_values['knowledge_source'],
                    fixed_values['work_id'],
                    fixed_values['name'],
                    fixed_values['city'],
                    fixed_values['city_text'],
                    item["matched_issue_ids"].lstrip('[').rstrip(']').replace(" ", "")
                )
                data_to_insert.append(row)

            # 5. 执行批量插入
            cursor.executemany(sql, data_to_insert)

        # 6. 提交事务
        connection.commit()
        print(f"成功插入 {len(data_to_insert)} 条数据到 xxa_city_qa_knowledge 表。")

    except Exception as e:
        print(f"数据库操作出错: {e}")
        connection.rollback()
    finally:
        connection.close()


def process_non_vendor_data(start_time, end_time):
    """
    读取 xxa_bomc_qa_non_vendor 表数据并应用过滤规则
    :param start_time: 开始日期 (YYYY-MM-DD)
    :param end_time: 结束日期 (YYYY-MM-DD)
    """
    try:
        table_name = "xxa_bomc_qa_non_vendor"
        logger.info(f"开始读取表: {table_name}, 时间范围: {start_time} 至 {end_time}")

        # 构造时间范围
        start_dt = f"{start_time} 00:00:00"
        end_dt = f"{end_time} 23:59:59"

        # SQL 查询：增加时间过滤
        # LIMIT 10: 仅取前10条数据进行测试
        #  sql = f"SELECT * FROM {table_name} WHERE last_time >= '{start_dt}' AND last_time <= '{end_dt}' LIMIT 10"
        sql = f"SELECT * FROM {table_name} WHERE last_time >= '{start_dt}' AND last_time <= '{end_dt}'"
        df = pd.read_sql(sql, engine)

        if df.empty:
            logger.warning(f"时间段 {start_time} ~ {end_time} 内无数据。")
            return

        initial_count = len(df)
        logger.info(f"读取到总记录数: {initial_count}")

        # ================= 过滤逻辑 =================
        # 1. 过滤 symptom_detail 列值为 "其他" 的数据
        # 注意：需要处理 None 或 NaN 的情况，虽然 pymysql 读出来通常是 None
        current_criteria = df["symptom_detail"] != "其他"
        df_filtered = df[current_criteria]
        logger.info(f"过滤 symptom_detail='其他' 后剩余: {len(df_filtered)}")

        # 2. 过滤 branch_actions 列值为 '[{"operation":"无","result":"无","suggestion":"无"}]' 的数据
        target_json_str = '[{"operation":"无","result":"无","suggestion":"无"}]'
        # 注意：这里做字符串精确匹配。如果数据库里存的 JSON 格式有微小差异（如空格），可能需要更复杂的解析匹配。
        # 鉴于需求描述很精确，这里先用精确匹配。
        current_criteria_2 = df_filtered["branch_actions"] != target_json_str
        df_filtered = df_filtered[current_criteria_2]

        final_count = len(df_filtered)
        logger.info(f"过滤 branch_actions 特定值后剩余: {final_count}")

        dropped_count = initial_count - final_count
        logger.info(f"共过滤掉 {dropped_count} 条数据。")

        if not df_filtered.empty:
            # 3. 构造 run_model 的输入
            # 格式：task_oid: 【symptom】 :: symptom_detail
            # 检查必要列是否存在
            required_cols = ["task_oid", "symptom", "symptom_detail"]
            missing_cols = [col for col in required_cols if col not in df_filtered.columns]

            if missing_cols:
                logger.error(f"缺少必要列: {missing_cols}，无法构造模型输入。现有列: {list(df_filtered.columns)}")
                # 尝试使用 task_oid 作为 id 的备选方案 (如果 id 缺失)
                if "task_oid" in missing_cols and "task_oid" in df_filtered.columns:
                    logger.warning("尝试使用 'task_oid' 代替 'id'")
                    df_filtered["id"] = df_filtered["task_oid"]
                    missing_cols.remove("id")

                if missing_cols:
                    return

            # 1. 格式化 branch_actions
            df_filtered["formatted_actions"] = df_filtered["branch_actions"].apply(convert_actions)

            # 按 symptom 分组处理
            grouped = df_filtered.groupby('symptom')

            # 打印每个 symptom 的记录数
            symptom_counts = df_filtered['symptom'].value_counts()
            logger.info("=== 各 Symptom 记录数统计 ===")
            print("\n=== 各 Symptom 记录数统计 ===")
            print(symptom_counts.to_string())
            print("==============================\n")

            # 存储所有提取的 QA 对
            all_qa_data = []

            # 用于收集各 symptom 的 GBase 查询结果 {symptom: [row_dict, ...]}
            gbase_result_collector = {}

            # 线程池并发处理
            max_workers = 1  # 设置并发线程数

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_symptom = {
                    executor.submit(process_group, symptom, group, gbase_result_collector): symptom for symptom, group
                    in grouped
                }

                # 获取完成的结果
                for future in as_completed(future_to_symptom):
                    symptom = future_to_symptom[future]
                    try:
                        qa_list = future.result()
                        qa_list = [{
                            "standard_question": "生成的标准问题 1 ",
                            "standard_answer": "生成的标准答案 (清洗了人名电话，综合了原因和措施，业务员可直接复制)",
                            "matched_issue_ids": str([1234, 5546, 87457])
                        }, {
                            "standard_question": "生成的标准问题 2 ",
                            "standard_answer": "生成的标准答案 (清洗了人名电话，综合了原因和措施，业务员可直接复制)",
                            "matched_issue_ids": str([12345, 55465, 87457])
                        }]
                        if qa_list:
                            all_qa_data.extend(qa_list)
                            save_to_database(qa_list)
                            logger.info(f"成功获取 symptom='{symptom}' 的 {len(qa_list)} 条 QA 结果")
                    except Exception as e:
                        logger.error(f"任务执行异常 symptom='{symptom}': {e}", exc_info=True)
            # ---- 保存 GBase 查询结果到 Excel（所有 symptom 合并到一个 sheet） ----
            if gbase_result_collector:
                if not os.path.exists("data"):
                    os.makedirs("data")
                gbase_output_file = os.path.join("data", f"gbase_{start_time}_{end_time}.xlsx")
                all_gbase_rows = []
                for sym, rows in gbase_result_collector.items():
                    for row in rows:
                        row["故障类型"] = sym
                        all_gbase_rows.append(row)
                if all_gbase_rows:
                    df_gbase = pd.DataFrame(all_gbase_rows,
                                            columns=["故障类型", "task_oid", "报障标题", "报障内容", "分公司处理意见"])
                    df_gbase.to_excel(gbase_output_file, index=False)
                logger.info(f"GBase 查询结果已保存到 {gbase_output_file}")
                print(f"GBase 查询结果已保存到 {gbase_output_file}")
            else:
                logger.warning("未获取到任何 GBase 数据，未生成 gbase Excel 文件。")

            # 6. 保存到 Excel
            # if all_qa_data:
            #     # 确保 data 目录存在
            #     if not os.path.exists("data"):
            #         os.makedirs("data")
            #     output_file = os.path.join("data", f"qa_{start_time}_{end_time}.xlsx")
            #     df_result = pd.DataFrame(all_qa_data)
            #     df_result.to_excel(output_file, index=False)
            #     logger.info(f"成功保存 {len(all_qa_data)} 条 QA 对到 {output_file}")
            #     print(f"成功保存 {len(all_qa_data)} 条 QA 对到 {output_file}")
            # else:
            #     logger.warning("未提取到任何有效 QA 数据，未生成 Excel 文件。")

            # 一次存入
            if all_qa_data:
                save_to_database(all_qa_data)

        else:
            logger.warning("过滤后无剩余数据，无需调用模型。")

    except Exception as e:
        logger.error(f"处理数据时出错: {e}", exc_info=True)


if __name__ == "__main__":
    # 示例调用：请根据需要修改日期
    start_date = "2026-04-01"
    end_date = "2026-04-28"
    process_non_vendor_data(start_date, end_date)
