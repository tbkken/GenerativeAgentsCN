"""Universal command line for Studio-independent experiments and Runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _experiment(args) -> object:
    from generative_agents.ga_protocol.packages.io import open_package
    from generative_agents.ga_protocol.packages.io import seal_directory
    from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
    from generative_agents.ga_protocol.packages.io import verify_integrity
    from generative_agents.ga_protocol.packages.io import write_integrity_manifest

    if args.action == "validate":
        with open_package(Path(args.package)) as root:
            manifest = validate_experiment_directory(root)
            integrity = verify_integrity(root)
            return {
                "valid": True,
                "experiment_id": manifest.experiment.experiment_id,
                "root_sha256": integrity["root_sha256"],
            }
    root = Path(args.directory).resolve()
    write_integrity_manifest(root)
    manifest = validate_experiment_directory(root)
    archive = seal_directory(root, Path(args.archive))
    return {
        "experiment_id": manifest.experiment.experiment_id,
        "archive": str(archive),
    }


def _run(args) -> object:
    if args.action in {"status"}:
        from generative_agents.ga_replay.api import ReplayReader

        with ReplayReader(args.package) as replay:
            return replay.summary()
    from generative_agents.ga_runtime.api import RunService

    service = RunService()
    if args.action == "create":
        root = service.create(args.experiment, args.destination, requested_steps=args.steps)
        return {"run_directory": str(root)}
    if args.action == "start":
        status = service.start(args.experiment, args.destination, requested_steps=args.steps)
        return status.model_dump(mode="json")
    if args.action == "resume":
        status = service.resume(args.package, extracted_destination=args.destination)
        return status.model_dump(mode="json")
    if args.action == "rerun":
        status = service.rerun(args.package, args.destination, requested_steps=args.steps)
        return status.model_dump(mode="json")
    if args.action == "pause":
        service.request_pause(args.directory)
        return {"pause_requested": True, "run_directory": str(Path(args.directory).resolve())}
    if args.action == "cancel":
        service.request_cancel(args.directory)
        return {"cancel_requested": True, "run_directory": str(Path(args.directory).resolve())}
    if args.action == "seal":
        archive = service.seal(args.directory, args.archive)
        return {"archive": str(archive)}
    raise ValueError(f"unsupported Run action: {args.action}")


def _replay(args) -> object:
    from generative_agents.ga_replay.api import ReplayReader

    with ReplayReader(args.package) as replay:
        if args.action == "summary":
            return replay.summary()
        if args.action == "timeline":
            return replay.timeline(start=args.start, end=args.end)
        if args.action == "state":
            return replay.state_at(args.step)
    raise ValueError(f"unsupported Replay action: {args.action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ga", description="Portable GenerativeAgentsCN protocols")
    modules = parser.add_subparsers(dest="module", required=True)

    from generative_agents.adapters.web.options import add_server_arguments
    studio = modules.add_parser("studio", help="author experiments and serve the console")
    studio_actions = studio.add_subparsers(dest="action", required=True)
    add_server_arguments(studio_actions.add_parser("serve"))

    experiment = modules.add_parser("experiment", help="validate or seal a .gaexp")
    experiment_actions = experiment.add_subparsers(dest="action", required=True)
    validate = experiment_actions.add_parser("validate")
    validate.add_argument("package")
    seal_experiment = experiment_actions.add_parser("seal")
    seal_experiment.add_argument("directory")
    seal_experiment.add_argument("archive")

    run = modules.add_parser("run", help="create, execute, control, or seal a Run")
    run_actions = run.add_subparsers(dest="action", required=True)
    for action in ("create", "start"):
        command = run_actions.add_parser(action)
        command.add_argument("experiment")
        command.add_argument("destination")
        command.add_argument("--steps", type=int)
    resume = run_actions.add_parser("resume")
    resume.add_argument("package")
    resume.add_argument("--destination", help="required when resuming a sealed .garun")
    rerun = run_actions.add_parser("rerun")
    rerun.add_argument("package")
    rerun.add_argument("destination")
    rerun.add_argument("--steps", type=int)
    status = run_actions.add_parser("status")
    status.add_argument("package")
    for action in ("pause", "cancel"):
        command = run_actions.add_parser(action)
        command.add_argument("directory")
    seal_run = run_actions.add_parser("seal")
    seal_run.add_argument("directory")
    seal_run.add_argument("archive")

    replay = modules.add_parser("replay", help="read committed facts from a Run")
    replay_actions = replay.add_subparsers(dest="action", required=True)
    summary = replay_actions.add_parser("summary")
    summary.add_argument("package")
    timeline = replay_actions.add_parser("timeline")
    timeline.add_argument("package")
    timeline.add_argument("--start", type=int, default=1)
    timeline.add_argument("--end", type=int)
    state = replay_actions.add_parser("state")
    state.add_argument("package")
    state.add_argument("step", type=int)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.module == "studio":
            from generative_agents.adapters.web.main import serve
            return serve(args)
        if args.module == "experiment":
            result = _experiment(args)
        elif args.module == "run":
            result = _run(args)
        else:
            result = _replay(args)
        _print(result)
        return 0
    except Exception as exc:
        print(f"ga: {str(exc) or exc.__class__.__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

