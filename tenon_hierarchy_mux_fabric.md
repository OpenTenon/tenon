推荐采用 `8 x 16 x 16` 的三级分层 mux fabric，而不是把 TinyTapeout 的结构直接线性放大成一个巨大的 `1:2048` mux。

```text
                         Top-level controller
                    address[10:0] / selection FSM
                                  |
                  balanced global H-tree / trunk buses
        +------------+------------+------------+------------+
        |            |            |            |            |
      Cluster 0    Cluster 1    ...          Cluster 7
      256 slots    256 slots                  256 slots
        |                                      |
   16 local branches                       16 local branches
        |                                      |
   16 project slots/branch                16 project slots/branch
```

容量为：

```text
8 clusters x 16 branches/cluster x 16 slots/branch = 2048
```

地址可自然编码为：

```text
project_id[10:0] = { cluster_id[2:0], branch_id[3:0], slot_id[3:0] }
```

物理上建议保留图中的左右两侧布局，但改为：

```text
左侧：4 clusters x 16 branches x 16 slots
右侧：4 clusters x 16 branches x 16 slots
```

也就是每个 cluster 是一个 `16 x 16` 的局部 tile 阵列，共 256 个 `1x1` 等效 slot；8 个 cluster 组成 2048 个 slot。

核心原则是：**全局只到 cluster，cluster 内才到 branch，branch 内才到 slot。**

- 顶层 controller 负责选择过程、复位、时钟控制和地址寄存。
- `cluster router` 重复 8 次，承担全局 trunk 与局部 fabric 的边界。
- `mux branch` 重复 128 次，每个 branch 管理 16 个 slot。
- 用户 macro 是 leaf；`2x2`、`4x2` 等大项目在一个 cluster 内占用相邻 slot，禁止跨 cluster 放置。

这样可以复用 TinyTapeout 已验证的“每 branch 16 slot”思路，只增加一个 cluster 层，而不破坏用户 tile 的接口与布局规则。

输入和输出的实现不应完全相同：

```text
外部输入 / clk / rst
  -> H-tree 缓冲分发
  -> cluster gate
  -> branch gate
  -> selected project

用户输出 / uio_out / uio_oe
  -> slot mux
  -> branch mux
  -> cluster mux
  -> root mux
  -> padframe
```

对于双向 GPIO，应继续拆为 `uio_in`、`uio_out`、`uio_oe` 三条逻辑路径。最终只有顶层 root mux 输出的 `uio_oe` 可以控制 pad，不能让多个 cluster 或用户 macro 的三态输出直接并到全局总线上。

选择切换必须由 FSM 原子完成：

```text
disable current project
-> force pad outputs high-Z
-> update cluster/branch/slot address
-> wait for selection to settle
-> optional reset selected project
-> enable new project
```

不要直接让 11 位地址组合译码到全部 2048 个 slot；地址位切换会造成短暂误选，可能让两个项目同时驱动输出。建议在每级采用寄存器化 one-hot enable，并执行 break-before-make。

物理设计上，重点是：

- 全局 spine 改成平衡的 H-tree 或 fat-tree，不能让 8 个 cluster 串接在一条长 spine 上。
- 预留顶层金属给全局时钟、reset、共享 I/O trunk 和电源；用户 macro 禁止使用这些金属层。
- `cluster router` 与 `mux branch` 各自单独 harden，作为重复 GDS/LEF macro 集成；不要把 2048-slot mux flatten 成一个顶层综合网表。
- 对每个 cluster 使用相同的 mux 深度、buffer 级数和时序预算；再通过对称布线减小不同位置的 I/O delay 差异。
- 时钟在 cluster 和 branch 级进行集成时钟门控，避免未选中的 2047 个用户项目持续翻转和消耗动态功耗。

这个结构的最大优点是：从 512 扩到 2048 时，最长互连和高扇出不会随项目数线性恶化；新增容量主要靠复制 256-slot cluster。以后扩展到 8192 项目，也可以继续增加顶层 cluster 层，而不是重写整个 mux。

前提是该模板仍采用 TinyTapeout 的基本模型：**同一时刻只有一个用户项目连接到共享外部 I/O。** 如果目标是多个项目并发运行并各自可访问外部 I/O，则需要 NoC、片上总线、独立 pad 或串行化接口，不再是 mux-based shuttle 架构。