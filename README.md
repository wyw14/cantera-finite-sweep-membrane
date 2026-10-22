# 有限扫气的双通道催化膜反应器

D.7 化学与材料。本仓库根目录就是一个 Harbor 题包，保留 instruction.md、task.toml、environment/、tests/、solution/ 五件套。
基于真实开源项目的作者应用扩展，不宣称是上游已报告的缺陷或生产测量记录。

## 运行

已使用 Harbor 0.24.0。需要 Linux/amd64 Docker，并允许题包独立 Verifier 以 root 引导隔离进程；候选计算以 uid 65534 运行，强制启用文件权限保护和 seccomp。支持 Landlock 的内核会额外启用它；不支持时须通过实际读写及网络隔离探测，任何保护未建立都在准备阶段明确失败。

```sh
harbor run -p . -a oracle -n 1 -k 1 --job-name oracle-check
harbor run -p . -a nop -n 1 -k 1 --job-name nop-check
harbor run -p . -a claude-code --ak version=2.1.306 --install-only -n 1 -k 1 --job-name agent-install-check
```

第三条只安装 Agent，不调用模型。真实模型运行使用 Harbor 内置 Claude Code harness，通过进程环境提供模型服务配置与凭据；不要把密钥写入仓库或命令示例。
基础镜像按 digest 固定。运行和测试 wheels 随包提供并校验 SHA256；Agent 安装阶段需要联网。题包不预装或分发 Claude Code 二进制，不固定本机 Docker 子网。

题面给出完整数学/物理模型、接口、边界及数值容差；README 不替代题面。Docker 构建只复制运行源码和运行依赖，不复制 tests/solution。独立 Verifier 在候选隔离进程外执行测试。

## 评测记录与边界

初次发布从已冻结的 v4-003/chemistry 制作副本，去除本机 Agent 预装与固定网段，补齐提交者元数据及 Git 文件模式。
第二轮初审返修保持题面、应用初始源码、研究机理及参考解不变；整理整个测试目录的格式、辅助函数和测试说明，物理条件、数值门槛及 20 个参数化节点保持不变。
第三轮返修保留上述科学工况和公开容差，修复了合法慢表面动力学导致的参考解误拒收。可严格识别的一阶表面状态转移按默认初态的可达闭类与吸收概率求稳态，保留快慢速率；其他表面机理继续使用物理松弛和分支核验。测试加入统一速率缩放、快慢状态连接、零入口反应产物与膜接收物、机理物种换序、可加载但不支持的相模型与超反应数拒收、输入拒收后恢复、完整输出结构和采样边界；独立有限体积对比扩展至内部坐标，并覆盖沿程膜通量变号的供体焓切换。断言在测试函数中直接覆盖完整点阵，每项说明引用公开题面；当前共63个参数化节点。该数字描述覆盖范围，不代替科学正确性或难度证明。当前版本的正式运行与审核证据仍需分别生成。
test.sh 直接执行 pytest，timeout 限制总时间，tee 同时保留控制台和 pytest.log。grade.py 只做准备与结果核验，显式插件记录完整收集及 setup/call/teardown 三阶段；精确节点清单、超时清理、隔离和异常归零检查保留。
Dockerfile 的 FROM 行使用字面量固定镜像和完整 SHA256，不通过变量间接引用；显式采用 Linux/amd64，并在安装原生 wheels 前检查架构。基础镜像摘要及依赖版本未变。AWS ECR 的 Docker Official Images 副本与原 Docker Hub 清单的 SHA256 完全相同。正确的手工构建上下文为 environment/：

```sh
docker build --no-cache --progress=plain -t membrane-review environment/
```

Harbor 官方命令会自动使用该上下文。审核方原始构建错误日志未提供，不能据此归因或宣称复现了同一故障；返修验证日志随交付单独提供。

## 失败诊断

test.sh 在调用 Python 前由受信 shell 原子重置 reward 为 0，也处理旧的只读常规文件；符号链接被替换而不跟随其目标，并拒绝该次运行。完整验收通过后才原子替换为 1，shell 最终退出前将结果封存为 0444。阶段进程组、候选进程登记和总时限用于中断清理；初始化、信号或最终保存异常均保持零分。初始零分不代表 Oracle 的最终结论。
请同时检查 `/logs/verifier/preflight.json`、`grading.json`、`pytest.log` 和 `outcomes.json`。准备、依赖安装、测试收集、候选断言、超时及清理失败会被分别记录；安装失败或内核隔离未建立不计作模型难度证据。

## 历史评测

旧冻结环境的官方 Oracle 为 20/20、NOP 为 0/20；NOP 每项在调用阶段失败，setup/teardown 正常。
旧冻结环境的一次 DeepSeek 运行通过 14/20，reward=0，Agent 耗尽3600秒，Verifier完整完成。API返回模型别名 deepseek-flash，harness为Claude Code2.1.306，effort=max。
这些是旧环境的真实历史结果，不冒称当前返修 commit 已重新调用过模型；初次发布环境的 Oracle/NOP 及保存产物重放、本次返修的验证记录均分别保存。
历史六个失败均来自同一个膜传递率输出的面积/长度换算错误。领域难度证据偏弱，不能据此宣称高难度初审通过。
提交状态为待审核；本仓库不宣称完整64条内部初审通过。参考解由作者出题过程编写，不是独立盲测。

## 来源

Cantera 3.2.0，https://github.com/Cantera/cantera/tree/v3.2.0 。原示例及许可保留在 environment/upstream；研究机理的作者归属见 ATTRIBUTION.md 和机理头部，不将第三方研究机理统一重新许可。
第三方 wheel 保留各自内置许可及原始字节。许可证、研究署名和第三方归属不因本次题目包装而改变。
