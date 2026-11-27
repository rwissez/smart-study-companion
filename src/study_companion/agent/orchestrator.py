"""受控多步骤任务编排（05 §5.2—§5.3）。

本模块负责三件事：

1. **保守拆解**：只对同时包含明确前序读取意图和后续动作意图、且存在顺序
   标记的教师请求做确定性拆解；其余请求原样退回现有单任务路由，不猜测。
2. **结构校验**：子任务最多 6 个，step_key 唯一，依赖必须存在，DAG 无环；
   写意图只能映射为 ``propose``（生成候选，需教师确认），学生请求一律只读。
3. **顺序执行**：按拓扑序逐个执行，逐步报告 pending/running/succeeded/
   failed/skipped；前序步骤失败时跳过依赖它的步骤，并说明未完成原因。

模型只负责在单个步骤内调用白名单工具，不能借子任务扩大范围，也不能直接
发布任何正式方案。真正的执行函数由调用方注入（``run_step``），因此本模块
不依赖数据库，便于用 fake provider/tool 做契约测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from study_companion.agent.expanded_tools import TASK_TOOLS, TEACHER_ONLY
from study_companion.agent.router import route_task
from study_companion.agent.tasks import TASKS, TaskSpec
from study_companion.contracts import DomainError, RouteDecision, RouteRequest

#: 05 §5.2 的硬上限：每个普通对话最多 6 个子任务。
MAX_STEPS = 6

READ = "read"
PROPOSE = "propose"
ALLOWED_OPERATIONS = frozenset({READ, PROPOSE})


# --------------------------------------------------------------------------- #
# 结构
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlanStep:
    """一个受控子任务。

    ``task`` 是执行时使用的现有任务定义；``task_type`` 是对外展示的步骤类别，
    两者在方案候选步骤上不同（``task_type=plan_revision`` 复用 ``study_plan``
    的任务工具集）。``operation`` 只能是只读或候选生成，从不出现直接写。
    """

    step_key: str
    task: TaskSpec
    task_type: str
    operation: str = READ
    depends_on: tuple[str, ...] = ()
    intent_summary: str = ""
    prompt_key: str | None = None

    @property
    def label(self) -> str:
        return self.intent_summary or self.task.label

    @property
    def prompt_task_type(self) -> str:
        return self.prompt_key or self.task.task_type


@dataclass(frozen=True)
class RoutePlan:
    """路由结果：ready 才可执行；denied/unclear 只返回安全说明。"""

    status: str
    steps: tuple[PlanStep, ...] = ()
    source: str = "rule"
    clarification: str | None = None
    explicit_write: bool = False
    router_prompt_version: str | None = None

    @property
    def is_multi_step(self) -> bool:
        return len(self.steps) > 1


@dataclass(frozen=True)
class StepOutcome:
    """``run_step`` 的返回值；失败也可以直接抛 ``DomainError``。"""

    text: str | None = None
    result: Any = None
    error: dict[str, Any] | None = None


@dataclass
class StepResult:
    step: PlanStep
    status: str = "pending"
    text: str | None = None
    result: Any = None
    error: dict[str, Any] | None = None


@dataclass
class PlanExecution:
    results: list[StepResult] = field(default_factory=list)
    text: str = ""
    stopped_reason: str = "model_finished"
    explanation: str = ""

    @property
    def any_failed(self) -> bool:
        return any(item.status in {"failed", "skipped"} for item in self.results)


# --------------------------------------------------------------------------- #
# 结构校验
# --------------------------------------------------------------------------- #


def validate_route_steps(steps: list[PlanStep] | tuple[PlanStep, ...], *, role: str | None = None) -> tuple[PlanStep, ...]:
    """按 05 §5.2 校验子任务结构，任何一项不合法都抛 ``DomainError``。"""

    items = tuple(steps)
    if not items:
        raise DomainError("VALIDATION_ERROR", "路由至少需要一个子任务")
    if len(items) > MAX_STEPS:
        raise DomainError("VALIDATION_ERROR", f"子任务最多 {MAX_STEPS} 个，当前为 {len(items)} 个")

    keys: list[str] = []
    by_key: dict[str, PlanStep] = {}
    for step in items:
        if not isinstance(step.step_key, str) or not step.step_key.strip():
            raise DomainError("VALIDATION_ERROR", "step_key 必须是非空字符串")
        if step.step_key in by_key:
            raise DomainError("VALIDATION_ERROR", f"step_key 重复：{step.step_key}")
        if step.operation not in ALLOWED_OPERATIONS:
            if step.operation == "write":
                raise DomainError("FORBIDDEN", "写操作只能生成候选，不能由 Agent 直接执行")
            raise DomainError("VALIDATION_ERROR", f"未知步骤操作：{step.operation}")
        if step.operation == PROPOSE and role is not None and role != "teacher":
            raise DomainError("FORBIDDEN", "学生请求只读，不能生成或修改正式方案")
        if role is not None and role not in step.task.roles:
            raise DomainError("FORBIDDEN", f"当前角色不能执行步骤 {step.step_key}：{step.task.label}")
        if not isinstance(step.depends_on, tuple) or any(
            not isinstance(dep, str) or not dep for dep in step.depends_on
        ):
            raise DomainError("VALIDATION_ERROR", f"步骤 {step.step_key} 的 depends_on 必须是字符串数组")
        if len(set(step.depends_on)) != len(step.depends_on):
            raise DomainError("VALIDATION_ERROR", f"步骤 {step.step_key} 的依赖重复")
        if step.step_key in step.depends_on:
            raise DomainError("VALIDATION_ERROR", f"步骤 {step.step_key} 不能依赖自己")
        keys.append(step.step_key)
        by_key[step.step_key] = step

    known = set(keys)
    for step in items:
        for dep in step.depends_on:
            if dep not in known:
                raise DomainError("VALIDATION_ERROR", f"步骤 {step.step_key} 依赖了不存在的步骤：{dep}")

    _validate_acyclic(items)
    return items


def _validate_acyclic(steps: tuple[PlanStep, ...]) -> None:
    indegree = {step.step_key: len(step.depends_on) for step in steps}
    children: dict[str, list[str]] = {step.step_key: [] for step in steps}
    for step in steps:
        for dep in step.depends_on:
            children[dep].append(step.step_key)
    ready = [key for key, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(steps):
        raise DomainError("VALIDATION_ERROR", "子任务依赖不能成环")


def build_route_plan(
    steps: list[PlanStep] | tuple[PlanStep, ...],
    *,
    role: str | None = None,
    source: str = "rule",
    explicit_write: bool | None = None,
    router_prompt_version: str | None = None,
) -> RoutePlan:
    items = validate_route_steps(steps, role=role)
    if explicit_write is None:
        explicit_write = any(step.operation == PROPOSE for step in items)
    return RoutePlan(
        status="ready",
        steps=items,
        source=source,
        explicit_write=bool(explicit_write),
        router_prompt_version=router_prompt_version,
    )


def topological_order(steps: list[PlanStep] | tuple[PlanStep, ...]) -> list[PlanStep]:
    """稳定拓扑序：同层保持原声明顺序，便于 SSE 进度可预测。"""

    items = validate_route_steps(steps)
    remaining = list(items)
    done: set[str] = set()
    order: list[PlanStep] = []
    while remaining:
        ready = [step for step in remaining if all(dep in done for dep in step.depends_on)]
        if not ready:  # pragma: no cover - validate_route_steps 已排除环
            raise DomainError("VALIDATION_ERROR", "子任务依赖不能成环")
        for step in ready:
            order.append(step)
            done.add(step.step_key)
        remaining = [step for step in remaining if step.step_key not in done]
    return order


# --------------------------------------------------------------------------- #
# 工具白名单
# --------------------------------------------------------------------------- #


def allowed_step_tools(step: PlanStep, role: str) -> frozenset[str]:
    """该步骤在给定角色下允许注册的工具集合。

    基础工具来自 ``TaskSpec.tools``，扩展工具来自 ``TASK_TOOLS``；教师专属
    工具对学生一律剔除。方案候选步骤（``propose``）只允许教师执行。
    """

    if role not in step.task.roles:
        raise DomainError("FORBIDDEN", f"当前角色不能执行步骤 {step.step_key}")
    if step.operation == PROPOSE and role != "teacher":
        raise DomainError("FORBIDDEN", "学生请求只读，不能生成或修改正式方案")
    names = set(step.task.tools) | set(TASK_TOOLS.get(step.task.task_type, ()))
    if role != "teacher":
        names -= TEACHER_ONLY
    return frozenset(names)


def assert_step_tool_allowed(step: PlanStep, role: str, tool_name: str) -> None:
    if tool_name not in allowed_step_tools(step, role):
        raise DomainError("TOOL_NOT_ALLOWED", f"工具不在步骤 {step.step_key} 的白名单内：{tool_name}")


#: 执行步骤类别 -> 01/05 公共 RouteDecision 的 TaskKey。
_ROUTE_TASK_KEY: dict[str, str] = {
    "student_progress": "learning_progress",
    "observation_review": "learning_progress",
    "student_loss_explanation": "student_analysis",
    "evidence_lookup": "student_analysis",
    "class_analysis": "class_analysis",
    "data_quality": "data_quality",
    "class_data_quality": "data_quality",
    "student_learning_advice": "plan_query",
    "study_plan": "plan_query",
    "plan_revision": "plan_modify",
    "knowledge_lookup": "knowledge_lookup",
    "prerequisite_query": "knowledge_lookup",
    "material_search": "knowledge_lookup",
    "relation_review": "knowledge_lookup",
    "concept_tutoring": "concept_tutoring",
}


def to_route_decision(plan: RoutePlan) -> RouteDecision:
    """把内部执行计划投影为 05 §5.2 的 RouteDecision 契约。

    该投影用于日志、质量统计和前端展示；执行权限仍由内部 PlanStep 与各步骤
    工具白名单决定。``denied`` 在公共契约里表示为 ``out_of_scope``。
    """

    if plan.status in {"denied", "out_of_scope"}:
        status = "out_of_scope"
    elif plan.status == "unclear":
        status = "unclear"
    elif len(plan.steps) == 1 and plan.steps[0].task_type == "out_of_scope":
        status = "out_of_scope"
    else:
        status = "ready"

    requests: list[RouteRequest] = []
    if status == "ready":
        for step in plan.steps:
            task_key = _ROUTE_TASK_KEY.get(step.task_type)
            if task_key is None:
                raise DomainError(
                    "VALIDATION_ERROR", f"步骤类别 {step.task_type} 无法映射到 RouteDecision"
                )
            requests.append(
                RouteRequest(
                    step_key=step.step_key,
                    task_key=task_key,
                    operation=step.operation,
                    depends_on=list(step.depends_on),
                    intent_summary=step.intent_summary or step.task.label,
                )
            )
    return RouteDecision(
        status=status,
        requests=requests,
        clarification=plan.clarification,
        explicit_write=plan.explicit_write,
    )


# --------------------------------------------------------------------------- #
# 规则拆解
# --------------------------------------------------------------------------- #

_SEQUENCE_MARKERS = ("先", "再", "然后", "接着", "之后", "随后", "并")

_PROGRESS_TERMS = (
    "学情", "学习情况", "学习进展", "学习表现", "进展", "最近变化", "近期变化",
    "学习状态", "掌握情况", "表现如何",
)
_PLAN_CANDIDATE_TERMS = (
    "生成方案", "方案候选", "方案修订", "修订方案", "调整方案", "修改方案", "改方案",
    "生成计划", "调整计划", "修改计划", "改计划", "减到", "减至", "减少", "重排",
    "换材料", "优化方案", "下一组", "下次练习", "调整一下", "改一下",
)
_ADVICE_TERMS = ("学习建议", "怎么提高", "如何改进", "下一步怎么学", "给学生建议", "学习办法")
_LOSS_TERMS = ("失分", "丢分", "错在哪里", "哪一步错")
_NEGATION_PATTERNS = (
    "不要修改", "不要调整", "不要改", "不用修改", "不用改", "别改", "不改",
    "不动方案", "只看", "仅看", "不要生成方案",
)
_STUDENT_WRITE_PATTERNS = (
    "删掉", "删除", "修改方案", "修改计划", "修改我的学习计划", "发布方案", "发布计划",
    "帮我改方案", "帮我改计划", "改成", "改到", "减到", "减至", "替换材料", "保存方案",
    "生成方案", "生成计划", "调整方案", "调整计划",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _normalize(text: str) -> str:
    return "".join(text.casefold().split())


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"].strip()
    return ""


def _rule_decompose(text: str, role: str) -> list[PlanStep] | None:
    """只拆解明确的“先读后动作”教师请求；其余返回 ``None`` 交回单任务路由。"""

    if role != "teacher":
        return None
    if _contains_any(text, _NEGATION_PATTERNS):
        return None
    if not _contains_any(text, _SEQUENCE_MARKERS):
        return None

    progress = _contains_any(text, _PROGRESS_TERMS)
    plan_candidate = _contains_any(text, _PLAN_CANDIDATE_TERMS)
    advice = _contains_any(text, _ADVICE_TERMS)
    loss = _contains_any(text, _LOSS_TERMS)

    if progress and plan_candidate:
        return [
            PlanStep(
                step_key="s1",
                task=TASKS["student_progress"],
                task_type="student_progress",
                operation=READ,
                intent_summary="查看学生学情",
            ),
            PlanStep(
                step_key="s2",
                task=TASKS["study_plan"],
                task_type="plan_revision",
                operation=PROPOSE,
                depends_on=("s1",),
                intent_summary="按前序学情生成方案候选（仅候选，不发布）",
                prompt_key="plan_revision",
            ),
        ]
    if progress and advice:
        return [
            PlanStep("s1", TASKS["student_progress"], "student_progress", READ, (), "查看学生学情"),
            PlanStep(
                "s2", TASKS["student_learning_advice"], "student_learning_advice",
                READ, ("s1",), "基于前序学情给出学习建议",
            ),
        ]
    if loss and advice:
        return [
            PlanStep("s1", TASKS["student_loss_explanation"], "student_loss_explanation", READ, (), "解释失分原因"),
            PlanStep(
                "s2", TASKS["student_learning_advice"], "student_learning_advice",
                READ, ("s1",), "基于失分原因给出学习建议",
            ),
        ]
    return None


def plan_route(
    messages: list[dict[str, Any]],
    provider: Any,
    role: str,
    *,
    explicit_task_type: str | None = None,
    router_prompt: tuple[str, str] | None = None,
) -> RoutePlan:
    """先尝试确定性多步骤拆解，失败则退回现有单任务路由。"""

    if explicit_task_type is not None:
        routed = route_task(
            messages, provider, role,
            explicit_task_type=explicit_task_type, router_prompt=router_prompt,
        )
        return _single_plan(routed)

    text = _latest_user_text(messages)
    normalized = _normalize(text)

    if role == "student" and _contains_any(normalized, _STUDENT_WRITE_PATTERNS):
        return RoutePlan(
            status="denied",
            source="rule",
            clarification=(
                "学生对话只能查看本人的学习信息，不能修改、删除或发布正式学习方案。"
                "你可以询问自己的学情、当前方案或作答依据；需要调整方案时请联系老师。"
            ),
        )

    if role == "teacher":
        steps = _rule_decompose(normalized, role)
        if steps:
            return build_route_plan(steps, role=role, source="rule")

    routed = route_task(messages, provider, role, router_prompt=router_prompt)
    return _single_plan(routed)


def _single_plan(routed: Any) -> RoutePlan:
    task = routed.task
    step = PlanStep(
        step_key="s1",
        task=task,
        task_type=task.task_type,
        operation=READ,
        intent_summary=task.label,
    )
    return RoutePlan(
        status="ready",
        steps=(step,),
        source=routed.source,
        explicit_write=False,
        router_prompt_version=routed.router_prompt_version,
    )


# --------------------------------------------------------------------------- #
# 顺序执行
# --------------------------------------------------------------------------- #

RunStep = Callable[[PlanStep, list[StepResult]], StepOutcome | None]
ProgressEvent = Callable[[dict[str, Any]], None]


def execute_plan(
    plan: RoutePlan,
    run_step: RunStep,
    *,
    on_event: ProgressEvent | None = None,
) -> PlanExecution:
    """按依赖顺序执行计划；失败步骤的下游一律 skipped，独立步骤继续。"""

    if plan.status != "ready":
        raise DomainError("VALIDATION_ERROR", "只有 ready 的路由可以执行")
    steps = validate_route_steps(plan.steps)
    results: dict[str, StepResult] = {step.step_key: StepResult(step) for step in steps}

    def emit(step: PlanStep, status: str, message: str = "") -> None:
        if on_event is None:
            return
        try:
            on_event(
                {
                    "type": "step",
                    "step_key": step.step_key,
                    "task_type": step.task_type,
                    "operation": step.operation,
                    "depends_on": list(step.depends_on),
                    "status": status,
                    "label": step.label,
                    "message": message,
                }
            )
        except Exception:  # 进度上报不能中断实际执行
            return

    for step in steps:
        emit(step, "pending")

    for step in topological_order(steps):
        result = results[step.step_key]
        blocked = [dep for dep in step.depends_on if results[dep].status != "succeeded"]
        if blocked:
            result.status = "skipped"
            result.error = {
                "code": "DEPENDENCY_FAILED",
                "message": f"依赖步骤 {', '.join(blocked)} 未成功，已跳过该步骤",
            }
            emit(step, "skipped", result.error["message"])
            continue

        result.status = "running"
        emit(step, "running")
        try:
            outcome = run_step(step, [results[dep] for dep in step.depends_on])
        except DomainError as exc:
            result.status = "failed"
            result.error = {"code": exc.code, "message": exc.message}
        except Exception:  # 不把内部异常文本暴露给前端或模型
            result.status = "failed"
            result.error = {"code": "STEP_FAILED", "message": "步骤执行异常，已停止依赖它的后续步骤"}
        else:
            if outcome is None:
                result.status = "failed"
                result.error = {"code": "STEP_FAILED", "message": "步骤没有返回结果"}
            else:
                result.text = outcome.text
                result.result = outcome.result
                stopped_reason = getattr(outcome.result, "stopped_reason", None) if outcome.result is not None else None
                failed = bool(outcome.error) or (
                    outcome.result is not None
                    and stopped_reason not in {None, "", "model_finished"}
                )
                if failed:
                    result.status = "failed"
                    result.error = outcome.error or {
                        "code": str(stopped_reason or "STEP_FAILED"),
                        "message": "步骤未能完整完成",
                    }
                else:
                    result.status = "succeeded"

        if result.status == "failed":
            emit(step, "failed", (result.error or {}).get("message", ""))
        else:
            emit(step, "succeeded")

    ordered = [results[step.step_key] for step in steps]
    texts = [item.text for item in ordered if item.status == "succeeded" and item.text]
    explanation_parts: list[str] = []
    for item in ordered:
        if item.status == "failed":
            explanation_parts.append(
                f"步骤「{item.step.label}」执行失败：{(item.error or {}).get('message', '未能完成')}。"
            )
        elif item.status == "skipped":
            explanation_parts.append(
                f"步骤「{item.step.label}」因前序步骤未成功而跳过。"
            )
    if explanation_parts:
        explanation_parts.append("以上步骤没有被当作已完成，也没有发布任何方案。")
    stopped_reason = "step_failed" if any(item.status == "failed" for item in ordered) else "model_finished"
    return PlanExecution(
        results=ordered,
        text="\n\n".join(texts).strip(),
        stopped_reason=stopped_reason,
        explanation="".join(explanation_parts).strip(),
    )


__all__ = [
    "ALLOWED_OPERATIONS",
    "MAX_STEPS",
    "PROPOSE",
    "READ",
    "PlanExecution",
    "PlanStep",
    "RoutePlan",
    "StepOutcome",
    "StepResult",
    "allowed_step_tools",
    "assert_step_tool_allowed",
    "build_route_plan",
    "execute_plan",
    "plan_route",
    "to_route_decision",
    "topological_order",
    "validate_route_steps",
]
