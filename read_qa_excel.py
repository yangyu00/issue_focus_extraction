import pandas as pd
import glob
import os

def read_qa_excel(file_path=None):
    """
    读取并展示 QA 知识库 Excel 文件的内容
    :param file_path: Excel 文件路径，如果为 None 则自动查找最新的 qa_*.xlsx
    """
    if not file_path:
        # 查找当前目录下所有符合命名规则的 excel 文件
        files = glob.glob("qa_*.xlsx")
        if not files:
            print("当前目录下没找到 'qa_*.xlsx' 文件。请先运行 generate_qa_knowledge.py 生成数据。")
            return
        # 按修改时间排序，取最新的一个
        file_path = max(files, key=os.path.getmtime)
        print(f"自动检测到最新的 Excel 文件: {file_path}")
    else:
        print(f"正在读取文件: {file_path}")

    try:
        # 读取 Excel
        df = pd.read_excel(file_path)
        
        print("\n" + "="*30)
        print("       文件基础信息")
        print("="*30)
        print(f"总行数: {len(df)}")
        print(f"列名: {list(df.columns)}")
        # print(df.info()) # info() 会直接打印到控制台，这里用上面的方式更简洁
        
        print("\n" + "="*30)
        print("       数据预览 (前 5 条)")
        print("="*30)
        # 设置 pandas 显示选项，避免列内容过多被折叠
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        pd.set_option('display.max_colwidth', 50) 
        print(df.head().to_string())
        
        print("\n" + "="*30)
        print("       详细内容示例 (第一条)")
        print("="*30)
        if not df.empty:
            first_row = df.iloc[0]
            print(f"[分组 Symptom]: {first_row.get('symptom_group')}")
            print(f"\n[标准问题 Question]:\n{first_row.get('standard_question')}")
            print(f"\n[标准答案 Answer]:\n{first_row.get('standard_answer')}")
            print(f"\n[关联工单 IDs]: {first_row.get('matched_issue_ids')}")
            print(f"\n[向量优化备注]: {first_row.get('vector_optimization_note')}")
        else:
            print("文件中没有数据。")
            
    except Exception as e:
        print(f"读取文件失败: {e}")

if __name__ == "__main__":
    # 示例：可以直接运行，或者指定文件名
    # read_qa_excel("qa_2025-10-01_2025-10-31.xlsx")
    read_qa_excel()
