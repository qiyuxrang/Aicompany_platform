"""LangGraph orchestration for the product document workflow.

The database remains the durable source of truth.  LangGraph owns routing while
``DocumentTask.checkpoint`` records every entered node, so an expired worker can
rebuild graph state from the task and continue through the existing idempotent
revision/artifact services.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import NotRequired, TypedDict

from django.db import transaction
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from .product_models import DocumentTask


class ProductWorkflowState(TypedDict):
    task_id: str
    fence: int
    attempt_id: str
    action: NotRequired[str]
    node: NotRequired[str]


def invoke_structured_model(call: Callable[[object], object], request, parser: Callable[[object], object]):
    """Execute a gateway call and its strict parser as a LangChain runnable."""
    chain = RunnableLambda(call).with_config(run_name="product_model_gateway") | RunnableLambda(
        parser
    ).with_config(run_name="product_structured_output")
    return chain.invoke(request)


def _record(state: ProductWorkflowState, node: str) -> ProductWorkflowState:
    """Persist a compact, serializable graph checkpoint under the task lease."""
    with transaction.atomic():
        task = DocumentTask.objects.select_for_update().get(pk=state["task_id"])
        if task.state != DocumentTask.State.RUNNING or task.fence != state["fence"]:
            return {"node": node}
        task.checkpoint = {
            **task.checkpoint,
            "workflow_graph": {
                "engine": "langgraph",
                "thread_id": str(task.pk),
                "attempt_id": state["attempt_id"],
                "fence": state["fence"],
                "action": state.get("action", task.pending_action),
                "node": node,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        task.save(update_fields=["checkpoint", "updated_at"])
    return {"node": node}


def run_product_workflow(
    task_id,
    fence: int,
    attempt_id,
    *,
    prepare_blueprint_knowledge: Callable[[object, int], None],
    execute_action: Callable[[object, int, object], None],
):
    """Run one leased action through the graph.

    Human review is a durable boundary: the blueprint node finishes in
    ``WAITING_REVIEW``.  An approval/revision API call queues the next action,
    and a later worker invocation reconstructs the graph from database state.
    """

    def dispatch(state: ProductWorkflowState):
        task = DocumentTask.objects.only("pending_action").get(pk=state["task_id"])
        update = {"action": task.pending_action}
        _record({**state, **update}, "dispatch")
        return update

    def route(state: ProductWorkflowState):
        if state["action"] in {"blueprint", "knowledge"}:
            return "knowledge"
        if state["action"] == "generate_outputs":
            return "deliverables"
        return "legacy_action"

    def knowledge(state: ProductWorkflowState):
        from .product_worker import _analysis_progress

        _record(state, "knowledge")
        _analysis_progress(state['task_id'], state['fence'], 'knowledge', 'running', detail='正在核对知识库授权并检索项目相关资料')
        result = prepare_blueprint_knowledge(state["task_id"], state["fence"])
        used = isinstance(result, dict) and result.get('ragflow_used') is True
        source_count = result.get('source_count', 0) if isinstance(result, dict) else 0
        _analysis_progress(state['task_id'], state['fence'], 'knowledge', 'completed' if used else 'skipped',
                           source_count=source_count,
                           detail=f'授权知识库检索完成，获得 {source_count} 项来源' if used else '资料直生成预览：未执行知识库检索，仅使用项目上传资料')
        if used and source_count == 0:
            from .product_service import ProductError

            _analysis_progress(state['task_id'], state['fence'], 'web_search', 'blocked',
                               detail='知识库未命中；联网搜索服务尚未接入，已暂停生成。请补充知识库资料后重新检索。')
            raise ProductError('web_search_unconfigured', '知识库未命中且联网搜索服务尚未接入，请补充知识库资料后重新检索。', 409)
        _analysis_progress(state['task_id'], state['fence'], 'web_search', 'skipped',
                           detail='本次知识库已有检索来源，未触发联网搜索' if used else '资料直生成预览：未执行联网搜索')
        return {"node": "knowledge"}

    def blueprint(state: ProductWorkflowState):
        _record(state, "blueprint")
        execute_action(state["task_id"], state["fence"], state["attempt_id"])
        return {"node": "blueprint"}

    def after_knowledge(state: ProductWorkflowState):
        return "knowledge_complete" if state["action"] == "knowledge" else "blueprint"

    def knowledge_complete(state: ProductWorkflowState):
        _record(state, "knowledge_complete")
        execute_action(state["task_id"], state["fence"], state["attempt_id"])
        return {"node": "knowledge_complete"}

    def deliverables(state: ProductWorkflowState):
        _record(state, "deliverables")
        execute_action(state["task_id"], state["fence"], state["attempt_id"])
        return {"node": "deliverables"}

    def legacy_action(state: ProductWorkflowState):
        _record(state, "legacy_action")
        execute_action(state["task_id"], state["fence"], state["attempt_id"])
        return {"node": "legacy_action"}

    builder = StateGraph(ProductWorkflowState)
    builder.add_node("dispatch", dispatch)
    builder.add_node("knowledge", knowledge)
    builder.add_node("blueprint", blueprint)
    builder.add_node("knowledge_complete", knowledge_complete)
    builder.add_node("deliverables", deliverables)
    builder.add_node("legacy_action", legacy_action)
    builder.add_edge(START, "dispatch")
    builder.add_conditional_edges(
        "dispatch",
        route,
        {
            "knowledge": "knowledge",
            "deliverables": "deliverables",
            "legacy_action": "legacy_action",
        },
    )
    builder.add_conditional_edges(
        "knowledge",
        after_knowledge,
        {"knowledge_complete": "knowledge_complete", "blueprint": "blueprint"},
    )
    builder.add_edge("blueprint", END)
    builder.add_edge("knowledge_complete", END)
    builder.add_edge("deliverables", END)
    builder.add_edge("legacy_action", END)
    graph = builder.compile()
    return graph.invoke({
        "task_id": str(task_id),
        "fence": fence,
        "attempt_id": str(attempt_id),
    })
