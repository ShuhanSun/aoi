# ITBench 集成修复与验证最终报告

生成时间：2026-10-11T05:03:09-07:00（America/Los_Angeles）
分支：`shuhsun/itbench-only`；最终运行代码：`17b02c4`
范围：ITBench SRE Mitigation，归档场景 1，OpenTelemetry Demo。

## 结论

已完成本轮确定的部署、工具执行、JSON 解析、提交控制和用量记录修复。AOI 27 个测试、ITBench 注入脚本 11 个测试及 Ansible 语法检查全部通过。真实工具链负例、确定性修复正例、故障恢复负例全部通过。

最终已提交代码上的模型自主运行：**未完成目标故障缓解，本地检查失败**。模型主要修改演示应用 agent 的资源和端口配置，没有关闭目标 flood flag。模型返回提交请求与实际缓解成功分别记录。官方 Mitigation verdict 未提供，所有自主运行的 `success` 仍为 `null`；不计算官方成功率。

评估后已从保存的 Helm manifest 恢复 agent 与 load-generator 的环境变量和资源配置，并由操作脚本关闭注入的流量激增 flag。清理后的本地检查通过；**此清理结果不计入 AOI 成功**。

## 已交付修复

| 问题 | 修复与验证 |
|---|---|
| 执行器在非结构化 context 下访问未赋值的 `fix_method` | 初始化默认值；有针对性回归测试。此前提交 `81b2b44`。 |
| 命令嵌入字符串导致 JSON patch 引号错误，heredoc 无法执行 | 新增 `exec_kubectl` 参数数组和受控 stdin；JSON 原样传给 subprocess，不经过 shell。保留简单 `exec_shell`。 |
| 交互式 `kubectl edit` 导致 90 秒超时 | 拒绝 edit、attach、exec、port-forward、proxy；给出 patch/apply/set/rollout 的非交互式使用指导。 |
| 模型 JSON 中非法 `\'` 或其他转义导致解析失败 | 仅修复非法 JSON escape；不接受其他损坏 JSON，不使用 eval。Executor 与 Probe 共用解析函数。 |
| 未修复或未验证就提交 | 客户端要求成功变更后有成功读取；失败提交不会设置 submitted。这个门槛不判断语义修复是否正确。 |
| 平台将错误字典变成字符串后提前结束 | 平台同时识别字符串错误；测试通过实际 execute_action 包装函数，覆盖普通任务提前提交和提交任务两个入口。 |
| AWorld 控制台显示 0 tokens | benchmark 捕获 provider 实际 usage，按 Observer/Probe/Executor/Compressor 归组；总量与逐调用记录可核对。原平台控制台的 agent counters 仍可能显示 0，评测以 task record 为准。 |
| 运行结果缺乏可追溯配置 | 保存模型、temperature、预算、Prometheus URL、代码版本、ITBench dirty 状态及 patch hash、工具输出、token 和估算成本。 |
| 本地判定与官方 verdict 容易混淆 | 独立 scenario-1 检查写入 local_verification，带 official=false；官方 verdict importer 拒绝本地检查 JSON。 |
| ITBench 应用默认未启用 | 按场景启用应用；实际重跑 deploy-applications 通过，不再依赖手动额外变量。 |
| telemetry 镜像/config 不匹配 | 固定可用 collector digest；Jaeger 使用 addresses/auth；统一 span_metrics；初始化 jaeger 数据库。 |
| 注入子任务失败但 Make 返回 0；缺失对象仍执行 first | 注入脚本传播非成功状态；对象缺失条件改为 OR；更新过时测试 mock，11 个上游测试通过。 |

AOI 实现提交：`a54924f`、`5f7005b`、`17b02c4`。外部 ITBench checkout 的修复保持为本地修改，已完整保存为可复现的 [patch](../benchmarks/itbench/patches/itbench-333afa05.patch)，适用基线 `333afa05`。patch SHA-256：`4a25b3e7afffc092e737872564df370bb4ba88454222ec4090c4ba5b5b561376`。已通过 reverse/apply check。

原工作区的 AIOpsLab submodule 指针与 requirements.txt 变化保持为原有未提交修改，未纳入上述提交。测试使用当前实际安装的依赖，未宣称未修改基线可直接复现。

## 环境与固定运行条件

- 独立 cluster：kind-dev，3 个 Ready 节点，Kubernetes 1.34.0；原 kind cluster 的 2 个节点仍 Ready（1.32.1）。
- Docker Desktop 设置 24 GiB，daemon 报告 25,158,770,688 bytes（约 23.43 GiB）。
- Helm 4.3.0；OpenTelemetry Demo chart 0.42.3；Jaeger 2.22.0。
- AOI Python 3.12.12、AWorld 0.2.6、OpenAI SDK 3.28.0、aiohttp 3.9.5；ITBench Ansible 使用 Python 3.13 与 uv 0.12.24。
- 使用 ITBench 生成的受限 kubeconfig，文件权限 0600；不把管理员 kubeconfig交给模型。Prometheus 通过 localhost:19090 的 port-forward 接入。
- 最终运行：gpt-4o-mini、temperature=0、14 轮、context=4096、output budget=5000；事件描述仅包含首页异常流量症状及调查/验证要求，未提供具体故障 flag 名称或 ground truth。
- 场景 ground truth 只用于操作脚本和独立 checker。确定性修复 smoke test 与模型自主 trial 分别记录。

## 测试证据

| 验证 | 结果 | 证据 |
|---|---|---|
| AOI unittest | 27/27 通过 | [unit-tests.log](../res/itbench/runtime/unit-tests.log) |
| ITBench injection unittest | 11/11 通过 | [upstream-tests.log](../res/itbench/runtime/upstream-tests.log) |
| Ansible syntax | 通过 | [ansible-syntax.log](../res/itbench/runtime/ansible-syntax.log) |
| scenario 应用启用及部署 | 通过，ok=43 failed=0 | [app-restore.log](../res/itbench/runtime/app-restore.log) |
| 活跃故障负例 → 修复正例 → 恢复故障负例 | false → true → false，符合预期 | [tool-smoke.json](../res/itbench/runtime/tool-smoke.json) |
| 评估后的环境清理 | 本地检查通过 | [environment-cleanup.json](../res/itbench/runtime/environment-cleanup.json) |
| 最终模型运行 | 未完成目标故障缓解，本地检查失败 | [committed-aoi-run.log](../res/itbench/runtime/committed-aoi-run.log) |
| 编译、CLI help、git diff --check、patch check | 通过 | 运行时检查；命令见下方 |

验证命令：

```sh
.venv/bin/python -m unittest discover -s tests -v
/Users/shuhsun/PersonalProjects/ITBench/.venv/bin/python -m unittest discover -s /Users/shuhsun/PersonalProjects/ITBench/scenarios/sre/scripts/unittests -v
.venv/bin/python -m benchmarks.itbench.verify --kubeconfig res/itbench/runtime/kubeconfig --output res/itbench/runtime/local-check.json
.venv/bin/python -m benchmarks.itbench.summary res/itbench --output res/itbench/summary.json
git diff --check
```

## Task-level 自主运行结果

前三次为此前探索运行，事件文本和预算不一致；第四次发现平台错误包装契约问题，第五次在修复后的已提交代码上验证。不能将这五次作为统一设置的 benchmark 成功率样本。前三次当时人工检查目标 flag 未关闭，但未保存自动 local_verification，因此表中不补造历史评分。

| 次数 | 任务记录 | 轮数预算 / temperature | 秒 | Tool calls | Total tokens | 估算成本 USD | submitted | 自动本地检查 | 官方 success |
|---|---|---|---:|---:|---:|---:|---|---|---|
| 1 | [sre-1-20261011T032712238407Z.json](../res/itbench/sre-1-20261011T032712238407Z.json) | 8 / 0.7 | 298.834 | 20 | 280,156 | $0.051673 | 是 | 未保存自动检查 | null |
| 2 | [sre-1-20261011T033310066945Z.json](../res/itbench/sre-1-20261011T033310066945Z.json) | 10 / 0.7 | 318.260 | 21 | 414,457 | $0.077730 | 是 | 未保存自动检查 | null |
| 3 | [sre-1-20261011T033927430442Z.json](../res/itbench/sre-1-20261011T033927430442Z.json) | 10 / 0.7 | 321.625 | 39 | 266,393 | $0.049688 | 是 | 未保存自动检查 | null |
| 4 | [sre-1-20261011T112535828027Z.json](../res/itbench/sre-1-20261011T112535828027Z.json) | 14 / 0.0 | 225.944 | 26 | 366,297 | $0.065606 | 否 | 失败 | null |
| 5 | [sre-1-20261011T113458949807Z.json](../res/itbench/sre-1-20261011T113458949807Z.json) | 14 / 0.0 | 432.056 | 21 | 262,879 | $0.047330 | 是 | 失败 | null |

合计：1,590,182 tokens，估算 $0.292027；官方 verified attempts=0，success_rate=null。

费用按 [OpenAI GPT-4o Mini 官方文本费率](https://developers.openai.com/api/docs/models/gpt-4o-mini)估算：输入 $0.15／百万 tokens、输出 $0.60／百万 tokens。未应用 cached-input 折扣，不等同于实际账单，不包含 Docker 本地资源成本。

最终 trial 的 provider-reported 用量：

| 代理 | Input tokens | Output tokens | Total tokens |
|---|---:|---:|---:|
| observer | 119,616 | 11,135 | 130,751 |
| probe | 84,904 | 2,260 | 87,164 |
| compressor | 3,260 | 2,807 | 6,067 |
| executor | 37,547 | 1,350 | 38,897 |
| total | 245,327 | 17,552 | 262,879 |

## 判定范围与限制

本地 checker 检查首页 flood flag 是否禁用或默认变体为 0，并检查 flagd 和 load-generator 至少一个副本在当前 generation 就绪。它能识别“仅缩容 load-generator 到 0”的伪修复；不能证明全部业务指标恢复，也不是官方判定。

公开的 [ITBench-Evaluations](https://github.com/itbench-hub/ITBench-Evaluations) 当前提供 SRE RCA 的 LLM judge 指标。没有将该 RCA 分数转换成此归档 live Mitigation 场景的成功分数。外部正式 verdict 导入已保留并测试；本次没有获得正式 Mitigation verdict。

最后一次运行的具体结论、执行命令及独立检查保存在 task record 中。工具能执行修复且 verifier 能识别正反例，并不意味着 gpt-4o-mini 能稳定找到和解除故障。本轮没有通过改写 ground truth、给模型答案或把人工清理计作成功来提高分数。

## 当前状态与复现

应用配置已恢复，注入 flag 已由操作脚本切回 off，清理检查通过；原有 AIOpsLab cluster 保持 Ready。Prometheus 的临时 port-forward 已停止。重测前需重新启动 port-forward、通过 ITBench 注入故障，并在凭据过期时重新执行 enable-agent-access。

复现与参数说明见 [ITBench 运行指南](itbench.md)。所有原始记录和运行日志位于 res/itbench；[summary.json](../res/itbench/summary.json)只用正式 verdict 计算官方成功率。[evidence manifest](../res/itbench/runtime/evidence-manifest.json)保存证据文件 SHA-256。
