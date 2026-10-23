# 有限扫气的双通道催化膜反应器

基于 Cantera 3.2.0 的应用扩展，计算有限扫气条件下的反应、穿膜传递和双侧换热。输入输出和科学定义见 instruction.md。

## 运行

使用 Linux/amd64、Python 3.12。运行依赖及测试依赖的锁定 wheels 随包提供；Docker 构建上下文为 environment/。

```sh
harbor run -p . -a oracle
harbor run -p . -a nop
```

Agent 作答预算为10800秒，运行资源为1 CPU、2 GiB。科学接口调用限60秒，Verifier限300秒。模型服务凭据由外部环境提供。

独立 Verifier 需要 root 引导，并保留 Docker 默认的 SYS_CHROOT、MKNOD、SETUID、SETGID 能力。候选以非特权身份运行，采用文件隔离和 seccomp；Landlock 不可用时进入仅含运行依赖、候选源码和公开输入的 chroot。测试、参考解和评分文件对候选不可读，候选无网络，只能写私有临时目录。隔离无法建立时明确报告环境错误。

## 来源

Cantera： https://github.com/Cantera/cantera/tree/v3.2.0 。官方示例、许可证及研究机理的归属资料保留在 environment/upstream/。研究机理来源与原始论文见其中 ATTRIBUTION.md；第三方 wheels 保留各自许可证。
