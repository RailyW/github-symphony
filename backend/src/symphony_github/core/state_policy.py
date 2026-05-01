"""GitHub Project 阶段策略上下文。

本模块把 App settings / WORKFLOW.md 中的状态配置转换为 prompt 可直接使用的
结构化对象，避免默认 prompt 或用户 prompt 写死 Todo、In Progress、Done 等阶段名。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .config import SymphonyConfig


# 函数说明：为 Jinja2 prompt 构建状态策略上下文，让 agent 能理解当前 Project 的自定义阶段。
def build_workflow_prompt_context(config: SymphonyConfig) -> Dict[str, Any]:
    tracker = config.tracker
    blocker = config.blocker_policy
    completion = config.completion_policy

    # 逻辑说明：所有列表都复制一份，避免模板渲染或测试代码意外修改运行配置对象。
    context: Dict[str, Any] = {
        "status_field": tracker.status_field,
        "status_options": list(tracker.status_options),
        "active_states": list(tracker.active_states),
        "handoff_states": list(tracker.handoff_states),
        "terminal_states": list(tracker.terminal_states),
        "blocked_states": list(blocker.blocked_states),
        "success_state": completion.success_state,
        "failure_state": completion.failure_state,
        "completion_kind": completion.kind,
        "mark_done_after_successful_turn": completion.mark_done_after_successful_turn,
    }
    context["status_policy_markdown"] = build_status_policy_markdown(context)
    return context


# 函数说明：生成可直接嵌入 prompt 的 Markdown 阶段说明，降低用户自写模板的负担。
def build_status_policy_markdown(context: Dict[str, Any]) -> str:
    lines = [
        "## GitHub Project 阶段策略",
        "",
        f"- Status 字段：`{context['status_field']}`",
        f"- 全部已发现阶段：{_format_states(context['status_options'])}",
        f"- 会被本 App 派发给 agent 的 active 阶段：{_format_states(context['active_states'])}",
        (
            "- 交接阶段（不会继续派发，也不视为终态清理）："
            f"{_format_states(context['handoff_states'])}"
        ),
        f"- 终态阶段：{_format_states(context['terminal_states'])}",
        f"- 依赖阻塞只适用于这些阶段：{_format_states(context['blocked_states'])}",
        f"- 成功 turn 后的目标/交接阶段：`{context['success_state']}`",
    ]

    failure_state = context.get("failure_state")
    if failure_state:
        lines.append(f"- 建议失败/返工阶段：`{failure_state}`")

    completion_kind = context.get("completion_kind")
    if completion_kind == "update_project_status" and context.get(
        "mark_done_after_successful_turn"
    ):
        lines.append("- 完成状态由 App 自动更新；agent 不需要自行修改 Project Status。")
    elif completion_kind == "agent_managed":
        lines.append(
            "- 完成状态由 agent 通过 GitHub 工具自行更新；"
            "请在结束前把任务移出 active 阶段。"
        )
    else:
        lines.append("- App 不自动更新完成状态；如果任务仍在 active 阶段，后续可能继续派发。")

    return "\n".join(lines)


# 函数说明：把状态列表格式化为 Markdown 片段；空列表明确显示“未配置”。
def _format_states(states: List[str]) -> str:
    if not states:
        return "未配置"
    return ", ".join(f"`{state}`" for state in states)
