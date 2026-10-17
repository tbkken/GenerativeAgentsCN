"""Execute one Skill in a temporary workspace, with an optional real scene."""
from __future__ import annotations
import json
from pathlib import Path
from uuid import uuid4
from generative_agents.ga_protocol.packages.io import read_json, atomic_write_json, checked_package_path
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_protocol.skills.dependencies import referenced_interfaces
from generative_agents.ga_runtime.engine.context import RunControl, RunPaths
from generative_agents.ga_runtime.models.gateway import create_llm_model
from generative_agents.ga_runtime.models.retry import ModelCallCancelled, check_cancelled
from generative_agents.ga_runtime.models.trace import ModelTraceWriter
from generative_agents.ga_runtime.skills.executor import SkillRuntime
from generative_agents.ga_runtime.skills.trial_scene import build_trial_scene


def execute_skill_trial(directory, *, credentials=None, control=None):
    root = checked_package_path(Path(directory))
    request = read_json(root / "trial.json")
    copied = SkillRegistry(root / "skills")
    name, input_text = request["skill_key"], request["input"]
    decision_root = copied.get(name).llm_type == "decision"
    config, base_url, model = request["config"], request["base_url"], request["model"]
    api_key = (credentials or {}).get(request["credential_env"], "")
    decision_api_key = (credentials or {}).get(request.get("decision_credential_env"), "")
    required = sorted({key for doc in copied.list() for key in referenced_interfaces(doc.body)})
    if required and not request.get("experiment_directory"):
        raise SkillTrialError("SKILL_ITERATION_CONTEXT_REQUIRED",
                              "请选择实验及其中的 Agent 后试运行：" + ", ".join(required))
    control = control if control is not None else RunControl()
    check_cancelled(control)
    paths = RunPaths(root=root / "execution" / str(uuid4()), run_id=uuid4())
    attempt_id = uuid4()
    paths.ensure()
    recorder = ModelTraceWriter(paths, run_id=paths.run_id, attempt_id=attempt_id,
                                attempt_no=1, capture_payloads=True)
    scene, phase = None, "environment"
    try:
        if request.get("experiment_directory"):
            scene = build_trial_scene(root, request, paths=paths, attempt_id=attempt_id,
                                      control=control, recorder=recorder)
        model_config = {
            **config, "base_url": base_url, "model": model, "api_key": api_key,
            "provider": config.get("provider", "vllm"),
            "timeout_seconds": min(float(config.get("timeout_seconds", 90)), 600),
            "retry_attempts": max(1, int(config.get("retry_attempts", 1))),
            "retry_backoff_seconds": max(0, float(config.get("retry_backoff_seconds", 0))),
        }
        decision_config = request.get("decision_config")
        if decision_config is not None:
            decision_config = {**decision_config, "api_key": decision_api_key}
        phase = "skill"
        model_client = None
        if decision_root:
            decision_config = model_config
        else:
            model_client = create_llm_model(model_config, recorder=recorder, control=control)
        runtime = SkillRuntime(
            copied, base_url=base_url, model=model, api_key=api_key,
            interfaces=scene.interfaces if scene else None,
            provider=config.get("provider", "vllm"), timeout=model_config["timeout_seconds"],
            max_hops=12, retry_attempts=model_config["retry_attempts"],
            retry_backoff_seconds=model_config["retry_backoff_seconds"],
            temperature=config.get("temperature", 0.2), max_tokens=config.get("max_tokens", 2048),
            enable_thinking=config.get("enable_thinking", False), model_client=model_client,
            decision_config=decision_config,
            recorder=recorder, control=control,
            agent_key=scene.iteration.agent_key if scene else None, step_no=1 if scene else None,
        )
        context = {}
        if scene:
            context["IterationContext"] = scene.iteration.as_dict()
        result = runtime.run(name, input_text, context=context).as_dict()
        result.update(
            model=model, input_text=input_text, model_usage=_model_usage(recorder),
            execution_source={"registry": "TRIAL_SKILL_CLOSURE", "skill_key": name,
                              "skills": [{"skill_key": doc.name, "content_hash": doc.content_hash}
                                         for doc in copied.list()]},
            environment=({"experiment_id": scene.experiment_id, "source": "EXPERIMENT_INITIAL_STATE",
                          "iteration_context": scene.iteration.as_dict()} if scene else None),
            planned_action=(scene.interfaces.action.as_dict() if scene and scene.interfaces.action else None),
            world_commit="NOT_COMMITTED",
        )
        result["trace"].append({"event": "trial.complete", "world_commit": "NOT_COMMITTED",
                                "planned_action": result["planned_action"],
                                "memory_scope": "ISOLATED_TRIAL" if scene else None})
        atomic_write_json(root / "result.json", result)
        return result
    except BaseException as exc:
        if scene:
            scene.interfaces.discard_action()
            scene.memory.rollback_step(scene.initial_memory)
        if isinstance(exc, ModelCallCancelled):
            raise
        trace = list(getattr(exc, "trace", ()))
        message = str(exc)
        for secret in {api_key, decision_api_key} - {""}:
            message = message.replace(secret, "[REDACTED]")
        if not trace or phase == "environment":
            trace.append({"event": f"trial.{phase}", "status": "FAILED", "input_text": input_text,
                          "error": message})
        atomic_write_json(root / "error.json", {"message": message, "trace": trace})
        if isinstance(exc, SkillTrialError):
            exc.trace = trace
            raise
        if not isinstance(exc, Exception):
            raise
        if any(marker in message.lower() for marker in ("401", "403", "not authenticated", "unauthorized")):
            raise SkillTrialError("SKILL_MODEL_AUTH_FAILED",
                f"模型 {model} 拒绝了身份认证。请在模型中心更新 API Key 后重试。",
                status=502, trace=trace) from exc
        codes = {"environment": "SKILL_EXPERIMENT_INVALID", "skill": "SKILL_MODEL_EXECUTION_FAILED"}
        raise SkillTrialError(codes[phase], f"Skill 试运行失败（模型 {model}）：{message}",
                              status=422 if phase == "environment" else 502, trace=trace) from exc


def _model_usage(recorder):
    """Count this execution's gateway facts, separately from Skill trace events."""
    logical_calls, physical_attempts = set(), set()
    if recorder.path.is_file():
        with recorder.path.open(encoding="utf-8") as records:
            for line in records:
                event = json.loads(line)
                logical_calls.add(event["call_id"])
                if event["event_type"] == "PHYSICAL_START":
                    physical_attempts.add((event["call_id"], event["attempt_no"]))
    return {"logical_calls": len(logical_calls), "physical_attempts": len(physical_attempts)}
