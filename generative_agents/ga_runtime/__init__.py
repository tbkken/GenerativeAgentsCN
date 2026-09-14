"""Database-free execution of portable experiment and Run packages.

Heavy simulation and model imports stay behind lazy attributes so package
inspection, control, and Replay never load the model SDK stack.
"""

__all__ = [
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
        from .control import FileRunControl

        return FileRunControl
    if name == "FileMemoryStream":
        from .memory import FileMemoryStream

        return FileMemoryStream
    if name in {"LoadedExperiment", "load_experiment_directory"}:
        from .package import LoadedExperiment, load_experiment_directory

        return {
            "LoadedExperiment": LoadedExperiment,
            "load_experiment_directory": load_experiment_directory,
        }[name]
    if name == "RunService":
        from .service import RunService

        return RunService
    if name == "FileRunSupervisor":
        from .supervisor import FileRunSupervisor

        return FileRunSupervisor
    if name == "execute_run_directory":
        from .executor import execute_run_directory

        return execute_run_directory
    raise AttributeError(name)
