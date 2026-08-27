# DevPilot Phase 5 前端设计原型说明

> 文档用途：可直接交给 Figma AI、MasterGo AI、即时设计 AI、v0、Lovable、Galileo 等界面设计或原型生成工具。
>
> 设计范围：DevPilot Web 控制台的 Dashboard、Task Detail、可靠事件 Timeline、Diff、验证报告、风险审批、人工介入、恢复和 ChangeRequest。
>
> 目标平台：Desktop Web 优先，Tablet 和 Mobile 自适应。
>
> 设计基线：与 `frontend/vue3/` 已实现的信息架构和安全交互保持一致。

---

## 1. 产品概述

DevPilot 是一个面向软件研发任务的 Agent Engineering Platform。用户提交代码修复或改造需求后，系统在隔离工作区中依次完成 Planning、Diagnosis、Patch Generation、Risk Assessment、Approval、Verification 和 Review。

前端不是普通聊天机器人界面，而是一个强调可控性、审计性和安全边界的工程任务控制台。核心体验是：

- 快速判断任务是否健康、运行到哪个节点、是否需要人工处理。
- 清晰查看 Plan、Patch Diff、验证结果、资源预算和事件审计链。
- 对高风险操作进行明确、可追溯、绑定具体对象的人工决策。
- 网络中断或页面重载后，可根据持久事件游标恢复 Timeline。
- 展示实际任务模型，避免服务默认模型和真实执行模型产生认知偏差。

### 1.1 目标用户

- 开发工程师：提交任务、查看 Patch 和验证结果。
- Tech Lead / Reviewer：审批高风险变更，判断是否继续执行。
- 平台管理员：观察任务运行、预算、失败和人工介入情况。

### 1.2 设计关键词

```text
Engineering Control Room
Reliable / Auditable / Calm / Precise / Technical
Dark Console / High Information Density / Strong Status Hierarchy
```

### 1.3 非目标

- 不设计成聊天软件或通用 AI 对话产品。
- 不展示模型隐藏思维链，仅展示可审计的阶段摘要。
- 不使用聊天文本触发批准、拒绝、取消、回滚或恢复。
- 不使用高饱和霓虹、大面积玻璃拟态或夸张动画干扰工程信息。
- 暂不设计 Light Mode。

---

## 2. 页面与路由

| 页面 | 路由 | 主要用途 |
|---|---|---|
| Dashboard | `/` | 查看指标、筛选任务、创建任务、进入任务详情 |
| Task Detail | `/tasks/{task_id}` | 查看单个任务的控制状态、执行证据和人工操作 |

Task Detail 内使用标签页切换：

1. 总览 Overview
2. 时间线 Timeline
3. 代码变更 Diff
4. 验证报告 Verification

全局弹窗：

- ChangeRequest 弹窗
- Recovery 恢复点弹窗
- Access Token 会话凭证面板

---

## 3. 全局视觉方向

### 3.1 品牌气质

界面应像“软件交付控制室”，而不是传统后台模板。深蓝黑背景、轻微网格、青绿色主强调色，表达可靠、实时和工程感。风险、失败与成功使用克制的功能色。

视觉层级：

```text
页面背景：深蓝黑 + 极弱工程网格
一级容器：深蓝灰面板
二级容器：更亮的深蓝灰
主操作：青绿色
等待审批：琥珀色
失败/危险操作：珊瑚红
成功：绿色
运行中：蓝色
```

### 3.2 颜色 Token

| Token | 色值 | 用途 |
|---|---:|---|
| Background | `#09111D` | 页面背景 |
| Surface | `#101C2C` | 卡片、面板 |
| Surface Raised | `#152438` | 弹窗、悬浮层、强调面板 |
| Input Background | `#0A1522` | 输入框、代码区域 |
| Primary Text | `#DCE9F5` | 主文本 |
| Muted Text | `#8295A9` | 次级信息 |
| Border | `rgba(150,180,208,0.16)` | 分隔线和默认边框 |
| Accent | `#38D2BD` | 主按钮、焦点、当前项 |
| Accent Deep | `#16A895` | 进度条、渐变起点 |
| Success | `#5DDE98` | 完成、通过、在线 |
| Warning | `#FFBD59` | 待审批、需注意 |
| Danger | `#FF6C78` | 失败、拒绝、取消、危险操作 |
| Running Blue | `#65BFFC` | 执行中 |

对比度要求：正文和操作控件满足 WCAG AA；状态不能只依赖颜色，还必须包含文字或图标。

### 3.3 字体

- UI 字体：`Inter`、`Segoe UI Variable`、`Segoe UI`、sans-serif。
- 工程数据字体：`Cascadia Mono`、`SFMono-Regular`、`Consolas`、monospace。
- 大标题：44–55 px，紧凑字距，700–800。
- 面板标题：17 px，700。
- 正文：12–14 px。
- Eyebrow / ID / Revision / Hash：8–10 px，monospace，增加字距。

### 3.4 间距与圆角

- 4 px 基础栅格。
- 页面最大宽度：1440 px。
- Desktop 页面左右边距：24 px；Mobile：14 px。
- 卡片间距：12–18 px。
- Panel 圆角：12 px。
- Button / Input 圆角：7 px。
- 状态胶囊：20 px 全圆角。
- 顶部导航高度：68 px，滚动时保持吸顶。

### 3.5 图标与动效

- 使用线性图标，线宽 1.5–2 px，避免彩色插画和 Emoji。
- Hover：按钮或卡片向上移动 1 px，边框轻微变亮。
- Loading：24 px 单色圆环。
- 实时在线点允许轻微呼吸动画，周期不小于 1.8 秒。
- 尊重 `prefers-reduced-motion`，关闭非必要位移和循环动画。

---

## 4. 全局框架

### 4.1 Top Bar

固定在页面顶部，高度 68 px。

左侧：

- `DP` 方形品牌标识，青绿色底色。
- 产品名 `DevPilot`。
- 小号等宽副标题 `CONTROL ROOM`。

右侧：

- 环境状态：绿色圆点 + `LOCAL RUNTIME`。
- `访问凭证` 次级文字按钮。

点击访问凭证后，在 Top Bar 下方展开会话凭证面板：

- 标签 `Bearer Token`。
- 密码输入框，占主要宽度。
- `保存` 主按钮。
- 辅助文案：仅保存在当前浏览器会话。

禁止在界面中回显完整 Token。

---

## 5. Dashboard 原型

### 5.1 Desktop 线框

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ DP  DevPilot / CONTROL ROOM                   ● LOCAL RUNTIME  [访问凭证]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ ENGINEERING OPERATIONS                                                   │
│ 任务控制台                                      [+ 创建任务]               │
│ 从计划到验证，持续掌握每一次安全代码变更。                                │
│                                                                              │
│ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐                │
│ │ 任务总数 12│ │ 成功率 82% │ │验证通过 91%│ │平均迭代 1.8│                │
│ └────────────┘ └────────────┘ └────────────┘ └────────────┘                │
│ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐                │
│ │ 待审批 2   │ │人工介入 8% │ │Token 42.8K │ │费用 $0.82  │                │
│ └────────────┘ └────────────┘ └────────────┘ └────────────┘                │
│                                                                              │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ TASK FLEET / 最近任务                                  状态 [全部 ▼]   │ │
│ ├──────────────────────────────────────────────────────────────────────────┤ │
│ │ 修复订单校验失败  task_91…  [待风险审批] approval_gate  qwen…  10:28 →│ │
│ │ 优化缓存失效策略  task_42…  [执行中]     verification   gpt…   10:16 →│ │
│ │ 修复路径越界漏洞  task_18…  [已完成]     review         qwen…  09:55 →│ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 页面头部

- Eyebrow：`ENGINEERING OPERATIONS`。
- 标题：`任务控制台`。
- 描述：`从计划到验证，持续掌握每一次安全代码变更。`
- 右侧主按钮：`+ 创建任务`。

### 5.3 创建任务展开区

点击 `创建任务` 后，在标题和指标卡之间展开一个 Panel：

| 字段 | 类型 | 占位内容 |
|---|---|---|
| 仓库路径 | Input | `C:\projects\example` |
| 任务需求 | Input / Textarea | `描述需要诊断和修复的问题` |
| Git revision | Input | `HEAD` |
| 模型（可选） | Input / Select | `使用服务默认模型` |
| 操作 | Primary Button | `启动隔离任务` |

加载状态下按钮文字改为 `正在创建…`，按钮不可重复点击。

### 5.4 指标区

Desktop 为 4 列 × 2 行；Tablet 为 2 列；Mobile 为 2 列。

指标卡：

1. 任务总数
2. 成功率
3. 验证通过率
4. 平均迭代次数，并显示平均活跃时间
5. 待审批数量
6. 人工介入率
7. Token 总用量
8. 累计费用，并显示回滚次数

卡片背景应克制，右上角可使用低透明度几何线框作为装饰，不使用完整图表。

### 5.5 任务列表

每行字段：

```text
任务请求摘要 + Task ID
状态 Badge
Current Node
实际执行 Model
更新时间
进入详情箭头
```

整行可点击。Hover 时背景出现极弱青绿色。

状态 Badge 完整集合：

| 状态 | 中文 | 颜色 |
|---|---|---|
| `CREATED` | 已创建 | 蓝色 |
| `RUNNING` | 执行中 | 蓝色 |
| `WAITING_RISK_APPROVAL` | 待风险审批 | 琥珀色 |
| `WAITING_HUMAN_INTERVENTION` | 需人工介入 | 琥珀色 |
| `CANCELLING` | 取消中 | 蓝色 |
| `CANCELLED` | 已取消 | 红色 |
| `COMPLETED` | 已完成 | 绿色 |
| `COMPLETED_NO_CHANGES` | 无需修改 | 绿色 |
| `FAILED` | 失败 | 红色 |
| `POLICY_REJECTED` | 策略拒绝 | 红色 |

### 5.6 Dashboard 状态原型

- Loading：列表区域显示 `正在读取任务状态…`。
- Empty：标题 `还没有任务`，描述创建第一个隔离任务。
- Error：红色弱背景提示条，包含错误文案和 `重试`。
- Filter Empty：明确显示“当前筛选条件下没有任务”，保留筛选器。

---

## 6. Task Detail 原型

### 6.1 Desktop 总览线框

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ DP  DevPilot / CONTROL ROOM                   ● REALTIME       [访问凭证]  │
├──────────────────────────────────────────────────────────────────────────────┤
│ 任务控制台 / task_91…                                                       │
│ [待风险审批] revision 7                                                     │
│ 修复订单校验失败                      [提交变更需求] [恢复操作] [取消任务] │
│ task_91… · Run run_63…                                                       │
│                                                                              │
│ CURRENT NODE       MODEL             PLAN              PAUSE REASON          │
│ approval gate      qwen3.7-flash     v2                RISK_APPROVAL         │
│                                                                              │
│ ┌────────────────── 高风险变更等待审批 ───────────────────────────────────┐ │
│ │ Approval ID / Patch hash / Base revision / Expires                     │ │
│ │ □ 我已核对当前 Patch 与风险信息       [拒绝变更] [批准并继续]          │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│ [总览] [时间线 18] [代码变更] [验证报告]                                  │
│                                                                              │
│ ┌──────────────────────────────────────┐ ┌────────────────────────────────┐ │
│ │ IMMUTABLE PLAN CHAIN / 执行计划  v2 │ │ RESOURCE / 执行预算            │ │
│ │ 1 分析失败测试                      │ │ 迭代       1 / 3  ━━━          │ │
│ │ 2 修改校验逻辑                      │ │ 模型调用   3 / 20 ━━           │ │
│ │ 3 运行回归测试                      │ │ Token    1500 / 100K           │ │
│ └──────────────────────────────────────┘ └────────────────────────────────┘ │
│ ┌──────────────────────────────────────┐ ┌────────────────────────────────┐ │
│ │ DETERMINISTIC RESULT / 验证报告      │ │ DURABLE STREAM / 执行时间线    │ │
│ │ PASS / FAIL、退出码、命令、失败用例 │ │ 018 approval requested  10:28 │ │
│ └──────────────────────────────────────┘ │ 017 risk assessed       10:27 │ │
│                                          └────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 详情头部

- 面包屑：`任务控制台 / {task_id}`。
- 状态 Badge + `revision {state_revision}`。
- H1：原始任务请求；没有摘要时显示紧凑 Task ID。
- 副信息：完整 Task ID + 紧凑 Run ID。
- 右侧操作：`提交变更需求`、`恢复操作`、`取消任务`。

操作显示规则：

- `恢复操作` 仅在存在 RecoveryPoint 时显示。
- `取消任务` 仅在非终态显示。
- 终态任务仍可查看全部历史，但不能原地重新运行。

### 6.3 Run 信息条

四列等宽：

1. Current Node
2. Model：必须显示任务实际固化的模型，例如 `qwen3.7-flash`
3. Plan：例如 `v2`
4. Pause Reason：无暂停时显示 `—`

所有 ID、版本和 Node 使用等宽字体。

### 6.4 总览布局

Desktop 使用约 `68% / 32%` 双栏：

- 左栏：Plan Panel、Verification Panel。
- 右栏：Budget Panel、最近 6 条 Timeline。

Tablet 和 Mobile 改为单栏，顺序为：审批/人工介入 → Plan → Budget → Verification → Timeline。

---

## 7. 核心面板设计

### 7.1 Plan Panel

标题：`IMMUTABLE PLAN CHAIN / 执行计划`，右上角显示版本胶囊 `v2`。

内容：

- Plan 摘要。
- 有序任务列表，序号使用青绿色圆形线框。
- 可展开的 `验收条件与风险`。
- Footer 显示创建时间和 Content Hash 前 12 位。

如果展示历史版本：旧版本使用 `SUPERSEDED` 灰色标签，活动版本使用 `ACTIVE` 青绿色标签。不可把旧版本做成可编辑内容。

### 7.2 Budget Panel

展示有界进度条：

- 迭代
- Plan 修订
- 模型调用
- 工具调用
- Token

底部展示 `累计费用 USD 0.0130` 和可选上限。进度接近 80% 时可使用 Warning 色；达到上限时使用 Danger 色，但不通过前端自行扩大预算。

### 7.3 Verification Panel

标题：`DETERMINISTIC RESULT / 验证报告`。

- `PASS` 使用绿色标签。
- `FAIL` 使用红色标签。
- 展示摘要、Exit Code、失败用例数和实际命令。
- 失败用例使用等宽字体列表。
- 明确提示：结果来自退出码和结构化测试报告，不由 LLM 主观决定。

### 7.4 Diff Panel

- 顶部展示 Patch Hash 前 10 位。
- 文件标签横向滚动，首项为 `全部`。
- 使用 Unified Diff 样式和等宽字体。
- 新增行：绿色弱背景。
- 删除行：红色弱背景。
- Hunk：蓝色弱背景。
- 左侧固定行号区域。
- 不对源码做 Markdown 或 HTML 富文本渲染。

### 7.5 Timeline Panel

标题：`DURABLE EVENT STREAM / 执行时间线`。

实时连接状态：

| 状态 | 文案 | 颜色 |
|---|---|---|
| Connecting | 正在连接 | Warning |
| Connected | 实时同步 | Success |
| Recovering | 游标补拉 | Warning |
| Closed | 已断开 | Muted / Danger |

单条事件内容：

- Sequence Number，例如 `018`。
- Node Name。
- 发生时间。
- `agent_summary` 或可审计事件摘要。
- Event Type。
- `state_revision`，仅状态事件显示。
- `checkpoint 已确认`、`等待 checkpoint` 或 `独立事件`。
- 紧凑 Event ID。

最新事件位于顶部。Timeline 使用纵向轨道，不使用聊天气泡。

关键语义：

- `state_revision = null`：显示 `独立事件`。
- 有 Revision 且未确认：显示 `等待 checkpoint`。
- 已确认：显示 `checkpoint 已确认`。
- WebSocket 序号跳跃时进入 `游标补拉`，不能直接把缺口后的事件当作连续历史。

### 7.6 Message History

- 只读列表，标题旁显示 `只读，不作为控制命令`。
- 区分 User / Assistant / System，但不使用聊天气泡强化“对话即控制”的错觉。
- 消息中的“批准”“回滚”等文字不能转化为操作按钮或状态变更。

---

## 8. Human-in-the-loop 原型

### 8.1 风险审批面板

这是页面内的高优先级 Warning Panel，而不是普通通知。

必须展示：

- Approval ID
- Patch Hash
- Base Revision
- Expiration Time
- `我已核对当前 Patch 与风险信息` Checkbox
- `拒绝变更` 按钮
- `批准并继续` 按钮

交互约束：

- 未勾选核对 Checkbox 时，批准按钮禁用。
- 拒绝可以直接执行，不依赖核对 Checkbox。
- 过期后两个按钮均禁用，并提示刷新状态。
- HTTP 409 时显示：`任务状态已变化，已为你刷新。请核对最新对象后重试。`
- 冲突后不得自动对新 Patch 重放旧审批操作。

### 8.2 人工介入面板

使用 Danger 弱背景，标题 `自动执行已安全停止`。

展示：

- Failure Summary
- `Category / Error Code`
- Recommended Recovery Action
- `提交修正要求`
- `查看恢复点`

重点表达“系统已停止自动执行，等待人工选择”，不要使用持续旋转的 Loading。

### 8.3 ChangeRequest 弹窗

```text
┌──────────────────────────────────────────────────────┐
│ FORMAL CONTROL REQUEST / 提交变更需求             × │
│ 这是正式控制操作，将进入审计链。                    │
│                                                      │
│ 新的约束或目标                                       │
│ ┌──────────────────────────────────────────────────┐ │
│ │ 清晰描述需要调整的目标、验收条件或限制……       │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ ⚠ 当前 Patch 正在等待审批                           │
│ 接受本次变更将废弃现有 Approval 和 Patch Proposal。 │
│ □ 确认废弃当前待审批 Patch                          │
│                                      [取消] [提交]   │
└──────────────────────────────────────────────────────┘
```

等待审批时必须显示警告区和二次确认 Checkbox。未确认时提交按钮禁用。

### 8.4 Recovery 弹窗

恢复点以单选卡片展示：

- Plan Version
- Created Time
- Repository Snapshot ID
- State Revision

底部操作：

- `取消`
- `回滚代码`：当前 Run 内补偿操作
- `完整恢复`：创建新 Run，保留原 Run 历史

必须勾选：`我理解恢复会使下游 Patch 和验证结果失效`。

完整恢复成功后：

- 页面 Run ID 更新。
- Timeline 清空旧 Run 游标。
- 从新 Run 的 Sequence 0 重新补拉。
- 原 Run 不删除、不覆盖。

### 8.5 取消任务

使用简单确认弹窗：

```text
确认取消此任务？
尚未应用或验证的 Patch 将失效。

[返回] [确认取消]
```

确认取消使用 Danger Button。

---

## 9. 响应式规则

### 9.1 Desktop：≥ 1000 px

- 最大内容宽度 1440 px。
- Dashboard 指标 4 列。
- 任务列表显示全部字段。
- Task Detail 总览为双栏。
- 弹窗宽度约 620 px。

### 9.2 Tablet：681–999 px

- Dashboard 指标 2 列。
- 任务列表隐藏 Model 和更新时间，保留 Task、Status、Node。
- Task Detail 改为单栏。
- Run 信息条改为 2 × 2。

### 9.3 Mobile：≤ 680 px

- 页面边距 14 px。
- 标题和操作按钮纵向排列。
- 指标保持 2 列，单卡最小高度 113 px。
- 任务列表仅保留 Task 摘要、状态和箭头。
- Run 信息条单列。
- 标签栏可以横向滚动。
- 弹窗接近全宽，底部按钮可换行。
- Diff 和 Timeline 保持横向/纵向独立滚动，不能压缩代码内容。

---

## 10. 可访问性要求

- 所有操作必须可通过键盘完成。
- Focus Ring 使用 Accent 色，不能仅去掉浏览器默认焦点样式。
- Modal 打开后焦点进入 Modal，关闭后回到触发按钮。
- Modal 支持 Escape 关闭；存在未提交文本时需要提示。
- Icon-only Button 必须有可读的 `aria-label`。
- 状态、进度和错误同时使用颜色、文本和图标。
- 动态错误提示使用 `role="alert"`。
- Timeline 新事件不主动抢夺焦点。
- ID 和 Hash 可复制，但默认只显示紧凑格式，Hover 显示完整值。

---

## 11. 原型示例数据

```json
{
  "task_id": "task_91b7f38a8ca640e2",
  "run_id": "run_63a9b8b520e7428d",
  "request": "修复订单创建接口在空优惠码下的校验失败",
  "status": "WAITING_RISK_APPROVAL",
  "state_revision": 7,
  "current_node": "approval_gate",
  "pause_reason": "RISK_APPROVAL",
  "model": "qwen3.7-flash",
  "plan_version": 2,
  "approval": {
    "approval_id": "approval_d9a217a8c0a34c28",
    "patch_hash": "f2e73e782f3a917d23a1c7a4b43a0dcf",
    "base_revision": "8b90dc326b795d50643f35dc",
    "expires_at": "2026-08-28T10:30:00+08:00"
  },
  "budget": {
    "iterations": "1 / 3",
    "plan_revisions": "1 / 2",
    "llm_calls": "3 / 20",
    "tool_calls": "5 / 40",
    "tokens": "1500 / 100000",
    "cost": "USD 0.0130 / 2.0000"
  }
}
```

Timeline 示例：

```text
018 · approval_gate · approval_requested · revision 7 · checkpoint 已确认
017 · risk_assessment · risk_assessed · revision 6 · checkpoint 已确认
016 · patch_generation · patch_proposed · revision 5 · checkpoint 已确认
015 · control · user_message · 独立事件
014 · diagnosis · agent_summary · revision 4 · checkpoint 已确认
```

Plan 示例：

```text
v2 ACTIVE
摘要：根据基线失败结果修正优惠码空值分支，并补充回归测试。

1. 定位订单校验器与失败测试
2. 修正空优惠码的判断逻辑
3. 增加空值和空字符串测试
4. 运行目标测试与完整回归
```

---

## 12. 需要设计工具交付的画板

至少输出以下高保真画板：

1. Desktop / Dashboard / Default
2. Desktop / Dashboard / Create Task Expanded
3. Desktop / Dashboard / Empty + Error States
4. Desktop / Task Detail / Running Overview
5. Desktop / Task Detail / Waiting Risk Approval
6. Desktop / Task Detail / Human Intervention
7. Desktop / Task Detail / Timeline
8. Desktop / Task Detail / Diff
9. Desktop / Task Detail / Verification PASS
10. Desktop / Task Detail / Verification FAIL
11. Desktop / ChangeRequest Modal / Normal
12. Desktop / ChangeRequest Modal / Invalidates Approval
13. Desktop / Recovery Modal
14. Mobile / Dashboard
15. Mobile / Task Detail / Waiting Approval

同时输出组件集合：

- Buttons：Primary、Secondary、Warning、Danger Ghost、Disabled、Loading。
- Status Badges：全部 10 个 Task Status。
- Input、Select、Textarea、Checkbox、Radio、Focus、Error、Disabled。
- Metric Card、Task Row、Plan Task、Budget Meter、Timeline Event。
- Modal、Inline Alert、Empty State、Loading State。

---

## 13. 可直接粘贴到设计 AI 的提示词

```text
请为 DevPilot 设计一套高保真的响应式 Web 控制台原型。

DevPilot 是软件研发 Agent Engineering Platform，不是聊天机器人。用户通过它提交代码修复任务，查看 Planning、Diagnosis、Patch、Risk Approval、Verification 和 Review 的执行过程，并对高风险 Patch、取消、回滚、恢复和正式需求变更进行明确控制。

视觉风格是深色 Engineering Control Room：深蓝黑背景 #09111D，面板 #101C2C / #152438，主文字 #DCE9F5，辅助文字 #8295A9，主强调色 #38D2BD，成功 #5DDE98，警告 #FFBD59，危险 #FF6C78。使用轻微工程网格背景、克制边框和少量辉光，不使用夸张霓虹、插画或大面积玻璃拟态。UI 字体使用 Inter，Task ID、Run ID、Revision、Hash 和事件序号使用 Cascadia Mono。

设计 Dashboard 和 Task Detail 两类页面。Dashboard 包含固定 Top Bar、任务创建展开表单、8 个指标卡、状态筛选和任务列表。Task Detail 包含状态、Revision、实际执行模型、Plan 版本、暂停原因、风险审批或人工介入区域，以及总览、Timeline、Diff、验证报告四个标签页。总览桌面端采用 68/32 双栏，展示 Plan、Verification、Budget 和最近 Timeline。

风险审批必须展示 Approval ID、Patch Hash、Base Revision、过期时间和核对 Checkbox；未勾选时批准按钮禁用。ChangeRequest 在任务等待审批时必须额外确认废弃当前 Patch。Recovery 必须选择具体 RecoveryPoint 并确认下游结果失效。消息区只读，聊天文字不能触发批准、取消、回滚或恢复。

Timeline 是可靠审计事件流，不使用聊天气泡。每条事件展示 Sequence、Node、Time、Summary、Event Type、State Revision 和 Checkpoint 状态；需要设计“实时同步、正在连接、游标补拉、已断开”四种连接状态。Diff 使用标准 Unified Diff 视觉。

请输出 Desktop 1440 px、Tablet 768 px 和 Mobile 390 px 布局，并建立可复用组件、Auto Layout、颜色变量、文字样式、按钮状态、10 种任务状态 Badge、Loading/Empty/Error/Conflict 状态。界面必须满足 WCAG AA，并尊重 reduced motion。
```

---

## 14. 设计验收清单

- [ ] 用户在 3 秒内能够定位待审批和人工介入任务。
- [ ] Task Detail 明确展示当前 Run、State Revision、Plan Version 和实际模型。
- [ ] 批准操作明确绑定 Approval、Patch 和 Base Revision。
- [ ] 等待审批时，ChangeRequest 包含废弃 Patch 的二次确认。
- [ ] 回滚和完整恢复在视觉与说明上有明确区别。
- [ ] Timeline 能区分独立事件、等待 Checkpoint 和已确认事件。
- [ ] WebSocket 断线时展示恢复状态，而不是错误地显示执行失败。
- [ ] PASS/FAIL 来自确定性验证报告，不出现“AI 判断通过”的表述。
- [ ] 所有危险操作均有清楚后果说明和禁用状态。
- [ ] Desktop、Tablet、Mobile 的信息优先级保持一致。
- [ ] 没有将聊天输入设计为控制命令入口。
- [ ] 没有展示模型隐藏思维链。
