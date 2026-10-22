# 有限扫气的双通道催化膜反应器

D.7 化学与材料。本仓库根目录就是一个 Harbor 题包，保留 instruction.md、task.toml、environment/、tests/、solution/ 五件套。
基于真实开源项目的作者应用扩展，不宣称是上游已报告的缺陷或生产测量记录。

## 运行

已使用 Harbor 0.24.0。需要 Linux/amd64 Docker、支持 Landlock ABI >= 3 的 Linux 内核和允许题包独立 Verifier 以 root 引导隔离进程的环境；候选计算以非特权身份运行。

```sh
harbor run -p . -a oracle -n 1 -k 1 --job-name oracle-check
harbor run -p . -a nop -n 1 -k 1 --job-name nop-check
harbor run -p . -a claude-code --ak version=2.1.306 --install-only -n 1 -k 1 --job-name agent-install-check
```

第三条只安装 Agent，不调用模型。真实模型运行使用 Harbor 内置 Claude Code harness，通过进程环境提供模型服务配置与凭据；不要把密钥写入仓库或命令示例。
基础镜像按 digest 固定。运行和测试 wheels 随包提供并校验 SHA256；Agent 安装阶段需要联网。题包不预装或分发 Claude Code 二进制，不固定本机 Docker 子网。

题面给出完整数学/物理模型、接口、边界及数值容差；README 不替代题面。Docker 构建只复制运行源码和运行依赖，不复制 tests/solution。独立 Verifier 在候选隔离进程外执行测试。

## 评测记录与边界

本包从已冻结的 v4-003/chemistry 制作发布副本，题面、应用初始源码、全部测试和参考解保持字节一致；仅去除本机 Agent 预装与固定网段，补齐提交者元数据及 Git 文件模式。
旧冻结环境的官方 Oracle 为 20/20、NOP 为 0/20；NOP 每项在调用阶段失败，setup/teardown 正常。
旧冻结环境的一次 DeepSeek 运行通过 14/20，reward=0，Agent 耗尽3600秒，Verifier完整完成。API返回模型别名 deepseek-flash，harness为Claude Code2.1.306，effort=max。
这些是旧环境的真实历史结果，不冒称发布commit已重新调用过模型；发布环境另行执行Oracle/NOP及保存产物重放，证据随交付登记提供。
历史六个失败均来自同一个膜传递率输出的面积/长度换算错误。领域难度证据偏弱，不能据此宣称高难度初审通过。
提交状态为待审核；本仓库不宣称完整64条内部初审通过。参考解由作者出题过程编写，不是独立盲测。

## 来源

Cantera 3.2.0，https://github.com/Cantera/cantera/tree/v3.2.0 。原示例及许可保留在 environment/upstream；研究机理的作者归属见 ATTRIBUTION.md 和机理头部，不将第三方研究机理统一重新许可。
第三方 wheel 保留各自内置许可及原始字节。许可证、研究署名和第三方归属不因本次题目包装而改变。
