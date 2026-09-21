"""Database-free execution of portable experiment and Run packages.

Heavy simulation and model imports stay behind lazy attributes so package
inspection, control, and Replay never load the model SDK stack.
"""

__all__ = [
    "GameObjectInteractionSystem",
    "export_checkpoint_artifact",
    "export_run_artifact",
    "execute_skill_trial",
    "FileMemoryStream",
    "FileRunControl",
    "LoadedExperiment",
    "RunService",
    "FileRunSupervisor",
    "execute_run_directory",
    "load_experiment_directory",
]


def __getattr__(name: str):
    if name == "FileRunControl":
        from generative_agents.ga_runtime.lifecycle.control import FileRunControl

        return FileRunControl
    if name == "FileMemoryStream":
        from generative_agents.ga_runtime.memory.stream import FileMemoryStream

        return FileMemoryStream
    if name in {"LoadedExperiment", "load_experiment_directory"}:
        from generative_agents.ga_runtime.lifecycle.package import LoadedExperiment
        from generative_agents.ga_runtime.lifecycle.package import load_experiment_directory

        return {
            "LoadedExperiment": LoadedExperiment,
            "load_experiment_directory": load_experiment_directory,
        }[name]
    if name == "RunService":
        from generative_agents.ga_runtime.lifecycle.service import RunService

        return RunService
    if name == "FileRunSupervisor":
        from generative_agents.ga_runtime.supervision.processes import FileRunSupervisor

        return FileRunSupervisor
    if name == "execute_run_directory":
        from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory

        return execute_run_directory
    if name == "execute_skill_trial":
        from generative_agents.ga_runtime.skills.trial import execute_skill_trial
        return execute_skill_trial
    if name == "export_run_artifact":
        from generative_agents.ga_runtime.storage.exports import export_run_artifact
        return export_run_artifact
    if name == "export_checkpoint_artifact":
        from generative_agents.ga_runtime.storage.exports import export_checkpoint_artifact
        return export_checkpoint_artifact
    if name == "GameObjectInteractionSystem":
        from generative_agents.ga_runtime.engine.objects import GameObjectInteractionSystem
        return GameObjectInteractionSystem
    raise AttributeError(name)
