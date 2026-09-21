"""Read an experiment definition from its declared file entrypoints."""
from __future__ import annotations
import json
from pathlib import Path
import zipfile
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import read_json

def _experiment_definition(root: Path, *, _reader=None, summary_only=False) -> tuple[dict, dict]:
    """Read the complete experiment definition from package entrypoints."""
    if _reader is None and root.is_file():
        with zipfile.ZipFile(root) as archive:
            return _experiment_definition(root, _reader=lambda relative: json.loads(archive.read(relative)), summary_only=summary_only)
    read_document = _reader or (lambda relative: read_json(root / relative))
    manifest = read_document('manifest.json')
    entrypoints = manifest.get('entrypoints') if isinstance(manifest, dict) else None
    identity = manifest.get('experiment') if isinstance(manifest, dict) else None
    if not isinstance(entrypoints, dict) or not isinstance(identity, dict):
        raise PackageError('experiment manifest is missing identity or entrypoints')

    def document(name: str) -> dict:
        relative = entrypoints.get(name)
        value = read_document(relative) if isinstance(relative, str) else None
        if not isinstance(value, dict):
            raise PackageError(f'experiment entrypoint is invalid: {name}')
        value = dict(value)
        value.pop('schema_version', None)
        return value
    agents = document('agents')
    if summary_only:
        return (manifest, {'agents': list(agents.get('agents') or []), 'world': document('world'), 'simulation': document('simulation')})
    evaluation = document('evaluation') if entrypoints.get('evaluation') else {}
    definition = {'schema_version': 1, 'experiment': dict(identity), 'engine': document('engine'), 'simulation': document('simulation'), 'results': dict(evaluation.get('results') or {}), 'models': document('models'), 'world': document('world'), 'agents': list(agents.get('agents') or []), 'crowds': list(agents.get('crowds') or []), 'evaluation': {'evaluators': list(evaluation.get('evaluators') or [])}}
    return (manifest, definition)
