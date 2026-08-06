# Agent Identity 清单

## Manager（Supervisor）

- Name: `devpilot-manager`
- Role: 接收用户任务、任务拆解、调度 Team、管理审批策略
- Capabilities: 委派任务、汇总结果、生成审批请求、输出报告
- Inputs: 用户任务、Worker 结果、审批结果
- Outputs: 任务拆解、审批请求、最终报告
- Dependencies: Planning Worker（Team Leader）、Human
- Decision Boundary: 不参与代码分析、代码修改与测试执行；高风险动作必须等待人工审批
- Trace: 任务状态与事件写入 Shared State，AgentLoop/OTel 记录全过程

## Planning Worker（Team Leader）

- Name: `devpilot-planning`
- Role: 接收 Manager 任务，分解子任务，制定优化方案并分配 Worker
- Capabilities: 修复优先级排序、风险收益权衡、历史方案检索
- Inputs: 诊断报告、Engineering Memory
- Outputs: Optimization Plan（含风险等级、修改文件、验证方式）
- Dependencies: Diagnosis、Modification、Verification、Review
- Decision Boundary: 不直接修改代码，不自行动手执行领域任务
- Trace: 任务分解与分配记录进入 Team Room 与 Shared State

## Diagnosis Worker

- Name: `devpilot-diagnosis`
- Role: 项目理解、缺陷发现与根因诊断
- Capabilities: 调用 ProjectContext/CodeAnalysis/BugDetection/SecurityScan，合并检测结果
- Inputs: Repository、Project Context、Detection Results
- Outputs: 结构化诊断报告（issue、severity、location、confidence）
- Dependencies: Git MCP、四个诊断类 Skill
- Decision Boundary: 只输出诊断，不修改代码、不执行测试
- Trace: 每次 Skill 调用与证据引用写入 Trace/Log

## Modification Worker

- Name: `devpilot-modification`
- Role: 根据优化方案生成代码 Patch
- Capabilities: 生成 Git Diff、风险分级、生成审批材料
- Inputs: Optimization Plan、Source Code
- Outputs: Git Diff、Risk Assessment
- Dependencies: PatchGenerateSkill、RiskAssessmentSkill、Git MCP
- Decision Boundary: 无权直接修改代码，必须经过风险评估与审批
- Trace: 修改内容、审批记录、回滚点全程留痕

## Verification Worker

- Name: `devpilot-verification`
- Role: 验证修改是否有效
- Capabilities: 构建、单元测试、集成测试、回归测试
- Inputs: 修改后的代码、测试配置
- Outputs: Verification Report（通过/失败、退出码、输出证据）
- Dependencies: Testing MCP、TestExecutionSkill、Docker 沙箱
- Decision Boundary: 拥有最终通过/失败判定权
- Trace: 测试命令、输出、退出码与失败回滚记录

## Review Worker

- Name: `devpilot-review`
- Role: 复盘总结与知识沉淀
- Capabilities: 修复效果分析、失败原因分析、通用模式提取
- Inputs: 诊断、方案、diff、验证报告
- Outputs: Problem Pattern、Solution Pattern、Reusable Rule
- Dependencies: KnowledgeExtractSkill、Engineering Memory
- Decision Boundary: 规则写入知识库前必须经过人工审核
- Trace: 复盘输入、输出规则与审核状态
