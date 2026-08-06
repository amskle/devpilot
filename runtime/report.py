from __future__ import annotations

from shared_state.schema import TaskState


def build_report(task: TaskState) -> str:
    lines = [
        "# DevPilot Infra 运行报告",
        "",
        f"- 任务 ID：`{task.task_id}`",
        f"- 仓库：`{task.repo_path}`",
        f"- 最终状态：`{task.status.value}`",
        "",
        "## 项目上下文",
        "",
    ]
    ctx = task.context.get("data", {})
    lines.append(f"- 项目类型：{ctx.get('project_type', 'unknown')}")
    lines.append(f"- 技术栈：{', '.join(ctx.get('tech_stack', []))}")
    lines.append(f"- 构建工具：{ctx.get('build_tool', 'unknown')}")
    lines.append("")
    lines.append("## 诊断结果")
    lines.append("")
    if task.issues:
        for issue in task.issues:
            lines.append(
                f"- [{issue.get('severity', 'Unknown')}] {issue.get('issue')} "
                f"@ `{issue.get('location', '?')}` (confidence {issue.get('confidence', 0):.2f})"
            )
    else:
        lines.append("- 未发现可处理缺陷。")
    lines.append("")
    lines.append("## 优化方案")
    lines.append("")
    if task.plan:
        lines.append(f"- 计划项：{task.plan.get('summary', '')}")
        lines.append(f"- 可自动修复：{len(task.plan.get('patches', []))} 项")
        lines.append(f"- 仅报告：{len(task.plan.get('report_only', []))} 项")
    lines.append("")
    lines.append("## 修改与审批")
    lines.append("")
    if task.approval:
        lines.append(f"- 审批模式：{task.approval.get('mode', '')}")
        lines.append(f"- 审批结果：{task.approval.get('result', '')}")
    for diff in task.diffs:
        lines.append("")
        lines.append(f"### {diff.get('file', '?')}")
        lines.append("")
        lines.append("```diff")
        lines.append(diff.get("diff", "").strip())
        lines.append("```")
    lines.append("")
    lines.append("## 验证结果")
    lines.append("")
    if task.verification:
        veri = task.verification.get("data", {})
        lines.append(f"- 通过：{veri.get('passed')}")
        lines.append(f"- 退出码：{veri.get('exit_code')}")
        if veri.get("stdout"):
            lines.append("")
            lines.append("```text")
            lines.append(veri["stdout"].strip()[-2000:])
            lines.append("```")
    lines.append("")
    lines.append("## 经验沉淀")
    lines.append("")
    if task.knowledge.get("data"):
        knowledge = task.knowledge["data"]
        lines.append(f"- Problem Pattern：{knowledge.get('problem_pattern')}")
        lines.append(f"- Solution Pattern：{knowledge.get('solution_pattern')}")
        lines.append(f"- Reusable Rule：{knowledge.get('reusable_rule')}")
    lines.append("")
    lines.append("## 执行证据")
    lines.append("")
    for event in task.history:
        lines.append(f"- `{event.get('event')}` by {event.get('agent')}: {event.get('detail')}")
    lines.append("")
    return "\n".join(lines)
