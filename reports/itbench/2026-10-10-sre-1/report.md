# ITBench SRE 场景 1：本地运行报告

运行时间：2026-10-10 20:27–20:44 PDT。场景来自 ITBench 的归档 SRE 场景 1，任务类型为 Mitigation。ITBench checkout 固定在 `333afa05c421b4e02a47b6c1b51686d20ce40cdd`。

## 结论

独立的 `kind-dev`（Kubernetes 1.34）集群成功部署了 OpenTelemetry Demo，故障注入任务成功执行。AOI 完成了 3 次运行并均调用 `submit()`，但**没有获得 ITBench 的正式评分**，因此记录中的 `official_success` 均为 `null`，不能把提交次数当成通过次数。

本地核验于 2026-10-11 04:22 PDT 读取到 `loadGeneratorFloodHomepage` 仍为 `ENABLED/on`，且 `flagd` 与 `load-generator` 各有 1 个就绪副本；场景故障没有得到缓解。核验详情见 [local-verification.json](local-verification.json)。这是运行后的本地状态检查，不是 ITBench 官方 verdict。

## 运行指标

| 尝试 | 输入 token | 输出 token | 工具调用 | 耗时（秒） | 估算成本（USD） | 观察到的处理 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 258,712 | 21,444 | 20 | 298.834 | 0.051673 | 关注 Collector 旧错误，误加环境变量；随后已撤销 |
| 2 | 379,876 | 34,581 | 21 | 318.260 | 0.077730 | 将 `load-generator` 缩到 0 后又恢复到 1 |
| 3 | 244,772 | 21,621 | 39 | 321.625 | 0.049688 | 执行器报错已修复，但 Service/Ingress 修改命令均未成功 |
| **合计** | **883,360** | **77,646** | **80** | **938.719** | **0.179091** | **0 次正式评分** |

3 次均使用 `gpt-4o-mini`、temperature `0.7`、上下文上限 4,096 token、输出上限 5,000 token；最大迭代数分别为 8、10、10。未配置 Prometheus URL。每次的事件描述、时间、配置、工具调用及状态、provider 报告的逐次和汇总 token 用量，均保存在 [attempts.json](attempts.json)。导出文件省略工具输出、原始 trace、kubeconfig 路径及凭据；原始文件保留在本机被 Git 忽略的 `res/itbench/`。

成本按 [OpenAI 官方 `gpt-4o-mini` 文本费率](https://developers.openai.com/api/docs/models/gpt-4o-mini)估算：输入 $0.15、输出 $0.60／百万 token。前两次成本在运行后从 provider 报告的 token 补算，第三次由 runner 记录。数据未区分缓存输入，故未应用缓存折扣；这些数字不是实际账单。

## 环境与限制

- 原有 AIOpsLab `kind` 集群未作为运行目标。ITBench 使用单独的 `kind-dev` 集群和受限 agent kubeconfig。Docker 内存设置为 24 GB。
- `kind-dev` 内使用 MetalLB 给 Istio 网关分配地址。采用它是因为 Cloud Provider Kind 的 Docker socket 容器方案被自动审批拒绝，理由是该挂载会给予容器控制整个 Docker 环境的权限。
- ITBench checkout 中有两处**未提交的本地配置修正**：Jaeger 2.22 的 ClickHouse 配置及 Collector 镜像摘要；OpenTelemetry Demo chart 的 `span_metrics` connector 名称。部署时也初始化了 Jaeger 数据库。这些修正不在本仓库的提交中，复现环境需要另行保存或上游修复。
- AOI 执行器在非结构化 `executor_context` 下引用未赋值的 `fix_method`，已由本仓库提交 `81b2b44` 修复，并通过对应输入路径的聚焦调用验证。

## 后续工作

先让 AOI 在受限 Kubernetes 权限内检查流量来源和 `flagd-config`，并修复执行器生成的无效 shell/JSON 命令；之后重新运行场景。只有取得 ITBench 官方 verdict，才能报告正式成功率。
