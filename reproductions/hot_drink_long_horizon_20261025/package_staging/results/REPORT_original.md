# 长程规划skill对照结果

已完成补跑 3/3；历史完整skill参照3/3。仅移除长程规划文档，其他四skill、规划工具、每次调用前意图及任务配置保持一致。

| 组别 | 完成尝试/计划 | 独立11/11 | 正式收尾且成功 | 平均动作 | 平均失败动作 |
|---|---:|---:|---:|---:|---:|
| with_long_horizon | 3/3 | 3 | 3 | 112.3 | 15.7 |
| without_long_horizon | 3/3 | 0 | 0 | 114.3 | 60.7 |

| 试验 | 目标比例 | 动作 | 失败动作 | 正式收尾 | 录像 |
|---|---:|---:|---:|---|---|
| with_long_horizon_s0 | 1.0 | 116 | 17 | True | [回放](http://10.130.140.115:8769/cohort/with_long_horizon_s0/review_5view.html) |
| with_long_horizon_s11 | 1.0 | 116 | 17 | True | [回放](http://10.130.140.115:8769/cohort/with_long_horizon_s11/review_5view.html) |
| with_long_horizon_s22 | 1.0 | 105 | 13 | True | [回放](http://10.130.140.115:8769/cohort/with_long_horizon_s22/review_5view.html) |
| without_long_horizon_s0 | 0.09090909090909091 | 103 | 64 | False | [回放](http://10.130.140.115:8769/cohort/without_long_horizon_s0/review_5view.html) |
| without_long_horizon_s11 | 0.0 | 120 | 56 | True | [回放](http://10.130.140.115:8769/cohort/without_long_horizon_s11/review_5view.html) |
| without_long_horizon_s22 | 0.0 | 120 | 62 | True | [回放](http://10.130.140.115:8769/cohort/without_long_horizon_s22/review_5view.html) |

失败和提前退出全部保留，独立任务得分与控制器收尾分开报告。每场skill读取、实际bundle、意图检查、视频、状态重放及所有动作轨迹均在原始证据中。重复开关门或导航只列为冗余候选，未经删除反事实不宣称可省动作数。恢复成本以失败延迟与后续动作分别审阅，动作失败率不能代替恢复总成本。

历史配对对照非同时随机双组，主机负载与模型服务等待未受控；三个seed、同一开发布局，不足以证明泛化因果效果。不把高层11目标得分当作原生物理/官方成绩。全部关闭skill的旧单场不纳入本对照。

## 最终证据核验（2026-10-25）

实际补跑三场加载四skill bundle与冻结清单一致，其他skill字节未改；无长程skill读取。机器人接口调用均带公开decision；其他四项只是保持可用，按需读取由模型决定（补跑未读failure-recovery，不能声称四项全部被使用）。原始源码哈希全部通过，36个回放、视频、审计链接HTTP200。

协议偏差必须保留：seed0完成103动作后模型未正式收尾，多出明确的监督器aborted finish，原始全调用对齐失败；seed11除机器人接口外还出现两次对不存在服务器x的read_mcp_resource（均报错）和一次list_mcp_resources（空列表），原始全调用对齐也失败。过滤这些明确的监督器/客户端调用后，三场机器人调用严格对齐，但不覆盖或改写原始失败审计。seed22调用对齐、意图、录像和状态重放均通过，最终目标仍为0/11。

因此：描述性结果支持在这组三个开发seed上保留长程skill，但不能说已严格证明其独立因果效果。未完成的短轨迹不能算效率优势；平均失败动作由15.7增至60.7，约3.87倍。完整skill全部11/11；无长程skill分别1/11、0/11、0/11。所有失败均保留，未自动补抽或替换。详见final_evidence_review.json。
