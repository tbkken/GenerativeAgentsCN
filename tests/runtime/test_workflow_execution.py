from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace

from generative_agents.config import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowPort,
    make_builtin_definition,
    make_default_workflows,
)
from generative_agents.config.workflows import WorkflowPosition
from generative_agents.modules.agent import Agent
from generative_agents.runtime.context import (
    SimulationClock,
    WorkflowPromptRepository,
)
from generative_agents.runtime.workflow_engine import WorkflowExecutor


def _node(
    node_id: str,
    kind: str,
    *,
    inputs=(),
    outputs=(),
    **kwargs,
) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        kind=kind,
        title=node_id,
        inputs=[WorkflowPort(name=name) for name in inputs],
        outputs=[WorkflowPort(name=name) for name in outputs],
        position=WorkflowPosition(x=10, y=10),
        **kwargs,
    )


def _edge(source, source_port, target, target_port, branch="always"):
    return WorkflowEdge(
        source_node_id=source,
        source_port=source_port,
        target_node_id=target,
        target_port=target_port,
        branch=branch,
    )


def test_native_executor_runs_ports_code_and_only_the_selected_branch():
    workflow = WorkflowDefinition(
        workflow_key="action",
        title="executable action",
        nodes=[
            _node("start", "start", outputs=("input",)),
            _node(
                "double",
                "code",
                inputs=("input",),
                outputs=("score",),
                script_mode="inline",
                script_source=(
                    "def main(inputs, context):\n"
                    "    return {'score': int(inputs.get('input', 0)) * 2}\n"
                ),
            ),
            _node(
                "choose",
                "selector",
                inputs=("score",),
                outputs=("accepted", "rejected"),
                expression="score >= threshold",
            ),
            _node(
                "accept",
                "llm",
                inputs=("context",),
                outputs=("result",),
                prompt_key="determine_sector",
            ),
            _node(
                "reject",
                "llm",
                inputs=("context",),
                outputs=("result",),
                prompt_key="determine_arena",
            ),
            _node("end", "end", inputs=("result",), outputs=("flow_result",)),
        ],
        edges=[
            _edge("start", "input", "double", "input"),
            _edge("double", "score", "choose", "score"),
            _edge("choose", "accepted", "accept", "context", "true"),
            _edge("choose", "rejected", "reject", "context", "false"),
            _edge("accept", "result", "end", "result"),
            _edge("reject", "result", "end", "result"),
        ],
    )
    calls = []
    traces = []

    def invoke(node, inputs, _context):
        calls.append(node.node_id)
        return inputs["context"] + 1

    result = WorkflowExecutor(
        {"action": workflow}, trace_handler=traces.append
    ).execute(
        "action",
        {"input": 3},
        llm_handler=invoke,
        runtime_context={"threshold": 5, "agent_key": "agent-a"},
    )

    assert result.value == 7
    assert calls == ["accept"]
    assert "double" in result.executed_nodes
    assert "choose" in result.executed_nodes
    assert any(
        item["node_id"] == "reject" and item["status"] == "SKIPPED"
        for item in traces
    )


class _FakePromptResult:
    def _asdict(self):
        return {
            "prompt": "score this event",
            "callback": None,
            "failsafe": 1,
            "response_model": None,
            "retry": 1,
        }


class _FakeLLM:
    def is_available(self):
        return True

    def completion(self, **kwargs):
        callback = kwargs.get("callback")
        return callback(3) if callback else 3


def test_agent_completion_result_is_changed_by_the_published_workflow_graph():
    workflows = make_default_workflows()
    document = workflows["memory"].model_dump(mode="json", exclude_none=False)
    original = next(
        edge
        for edge in document["edges"]
        if edge["source_node_id"] == "prompt_poignancy_event"
    )
    document["edges"].remove(original)
    document["nodes"].append(
        {
            "node_id": "force_safe_score",
            "kind": "code",
            "title": "force safe score",
            "inputs": [{"name": "input", "data_type": "any", "required": True, "description": ""}],
            "outputs": [{"name": "result", "data_type": "any", "required": True, "description": ""}],
            "position": {"x": 20, "y": 20},
            "prompt_key": None,
            "operation": None,
            "script_mode": "inline",
            "script_source": "def main(inputs, context):\n    return {'result': 9}\n",
            "expression": None,
            "state_path": None,
            "subflow_key": None,
            "config": {},
        }
    )
    document["edges"].extend(
        [
            {
                "source_node_id": "prompt_poignancy_event",
                "source_port": original["source_port"],
                "target_node_id": "force_safe_score",
                "target_port": "input",
                "branch": "always",
                "case_value": None,
            },
            {
                "source_node_id": "force_safe_score",
                "source_port": "result",
                "target_node_id": original["target_node_id"],
                "target_port": original["target_port"],
                "branch": original["branch"],
                "case_value": original["case_value"],
            },
        ]
    )
    workflows["memory"] = WorkflowDefinition.model_validate(document)
    definition = make_builtin_definition(key="workflow-runtime", name="Workflow Runtime")
    traces = []
    prompts = WorkflowPromptRepository(
        {key: prompt.content for key, prompt in definition.prompts.items()},
        workflows,
        trace_handler=traces.append,
    )

    agent = Agent.__new__(Agent)
    agent.name = "runtime agent"
    agent.agent_key = "runtime-agent"
    agent.scratch = SimpleNamespace(prompt_poignancy_event=lambda *_args, **_kwargs: _FakePromptResult())
    agent._prompts = prompts
    agent._llm = _FakeLLM()
    agent._models = None
    agent._workflow_state = {}
    agent._workflow_invocation_seq = 0
    agent._clock = SimulationClock(datetime(2026, 8, 12, tzinfo=timezone.utc))
    agent.concepts = []
    agent.logger = logging.LoggerAdapter(logging.getLogger("workflow-test"), {})

    assert agent.completion("poignancy_event", object()) == 9
    assert any(
        item["node_id"] == "force_safe_score" and item["status"] == "SUCCEEDED"
        for item in traces
    )
    assert any(
        item["node_id"] == "prompt_poignancy_event"
        and item["status"] == "SUCCEEDED"
        for item in traces
    )
    assert any(
        item["node_id"] == "route_prompt" and item["status"] == "SUCCEEDED"
        for item in traces
    )
    assert any(
        item["node_id"] == "prompt_poignancy_chat"
        and item["status"] == "SKIPPED"
        for item in traces
    )
    assert traces[-1]["node_id"] == "end"
