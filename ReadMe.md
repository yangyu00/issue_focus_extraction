工作流：134.84.148.13 dify 小信智能体摘要提取
部署方式：docker
docker镜像打包命令：docker build -t xiaoxin:v1 .
docker容器启动命令:docker run -dit --name xiaoxin_qa -p 8887-8889:8887-8889 xiaoxin_qa:v1

调用后端（魏玉磊）接口：输入是时间，返回taskOid、lastTime、fld_faultcontent（报障内容）、fld_column_2（分公司处理意见）、fld_column_4（厂商处理意见）、fld_question_type_province（省公司确认问题类型），输入时间是lastTime、start_time和end_time中取最新日期

app.py:每天定时运行，小信智能体入库的知识，包含焦点问题、二级分类
app_batch.py：指定时间段批量运行，小信智能体入库的知识，包含焦点问题、二级分类

入库：
business：业务
device:设备
subclass：二级分类
symptom:故障现象
symptom_detail:故障详情
cause:故障原因
branch_actions:分公司操作
vendor_actions:厂商操作
create_time：入库时间
response_time：模型响应耗时
error_msg：失败原因

generate_qa.py:在app.py结果的基础上，生成某段时间的qa知识