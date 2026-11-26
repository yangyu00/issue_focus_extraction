#!/usr/bin/env python3
"""离线回归测试：本地起一个伪造的 OpenAI 兼容服务，验证整条直连调用链。

不访问真实 MySQL / GBase / 后端接口 / 模型服务。

    python tests/smoke_test.py              # 非流式
    python tests/smoke_test.py --stream     # 流式 SSE
"""
import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RECORDED = []


def fake_answer(prompt):
    RECORDED.append(prompt)
    if "设备提取助手" in prompt:
        return "<think>内部思考</think>光猫 光猫"  # 验证 clean_field 去 think / 空格
    if "信息类型识别助手" in prompt:
        return "宽带业务"
    if "不可新增或自行扩展" in prompt and "故障类别" in prompt:
        return "宽带办理与维护"                   # 二级分类
    if "优先在以下预定义的类别" in prompt:
        return "宽带无法使用"                     # 故障现象
    if "造成问题的直接或间接原因" in prompt:
        return "光猫断电"
    if "故障报错或提示" in prompt:
        return "报错1001"
    if "严禁从" in prompt and "厂商处理意见" in prompt:
        return '[{"operation": "重启光猫", "result": "已恢复", "suggestion": "无"}]'
    if "省公司" in prompt and "厂商需要执行的操作" in prompt:
        return '[{"operation": "更换端口", "result": "业务恢复", "suggestion": "观察"}]'
    if "企业级知识库架构师" in prompt:
        return '```json\n{"clusters": [{"standard_question": "宽带无法上网怎么办", "standard_answer": "重启光猫并检查线路", "matched_issue_ids": [1, 2]}]}\n```'
    return "未知提示词"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        content = fake_answer(body["messages"][0]["content"])
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (content[:3], content[3:]):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            data = json.dumps(
                {"choices": [{"message": {"content": content}}]}, ensure_ascii=False
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stream", action="store_true", help="用流式 SSE 模式验证")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # 必须在 import 业务模块前设置，config.py 的 .env 优先级低于显式环境变量
    os.environ.update({
        "LLM_URL": f"http://127.0.0.1:{port}/v1/chat/completions",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
        "QA_LLM_MODEL": "test-model",
        "LLM_QPS": "1000",
        "LLM_MAX_RETRIES": "1",
        "LLM_TIMEOUT": "10",
        "LLM_STREAM": "true" if args.stream else "false",
        "POST_CALL_SLEEP": "0",
        "FIELD_WORKERS": "6",
    })

    import json_repair
    from xiaoxin.extraction import workflow as issue_workflow
    from xiaoxin.pipelines import issue_pipeline as app
    from xiaoxin.pipelines import qa_pipeline as generate_qa

    sample = "标题：宽带无法上网\n描述：光猫亮红灯\n分公司处理意见：已重启光猫\n厂商处理意见：已更换端口"

    raw, errors = issue_workflow.run_extraction(sample)
    data = json.loads(raw)
    expected_keys = ["device", "business", "subclass", "symptom", "symptom_detail",
                     "cause", "branch_actions", "vendor_actions"]
    assert list(data.keys()) == expected_keys, data.keys()
    assert not errors, errors
    assert data["device"] == "光猫光猫", data["device"]
    assert data["business"] == "宽带业务"
    assert data["subclass"] == "宽带办理与维护"
    assert data["symptom"] == "宽带无法使用"
    assert " " not in data["branch_actions"]
    assert json.loads(data["branch_actions"])[0]["operation"] == "重启光猫"
    assert "<think>" not in raw
    # 断言 MAPPING 把候选类别注入到了下游提示词里
    assert any("宽带办理与维护、宽带计费、宽带上网、宽带带宽" in p and "不可新增或自行扩展" in p
               for p in RECORDED), "映射节点1未生效"
    assert any("宽带开通/取消、宽带竣工" in p and "优先在以下预定义的类别" in p
               for p in RECORDED), "映射节点2未生效"
    print("[OK] issue_workflow：8 字段 + 映射链 + 清洗规则")

    qa_result = generate_qa.call_qa_model("1: 宽带不通 :: 光猫断电 :: 断电 :: 插电 :: 恢复")
    assert json_repair.loads(qa_result)["clusters"][0]["standard_question"] == "宽带无法上网怎么办"
    print("[OK] generate_qa：QA 聚类提示词与结果解析")

    res = app.safe_run_model(sample)
    assert set(res.keys()) == {"raw_json", "response_time", "error_msg"}
    assert res["error_msg"] == ""
    assert json.loads(res["raw_json"])["symptom"] == "宽带无法使用"
    assert res["response_time"] >= 0
    print(f"[OK] app.safe_run_model：{res['response_time']} ms")

    server.shutdown()
    print(f"ALL_TESTS_PASSED (stream={'true' if args.stream else 'false'})")


if __name__ == "__main__":
    main()
