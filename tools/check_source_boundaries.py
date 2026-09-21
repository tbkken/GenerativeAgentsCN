"""Check every product module, including lazy imports and bundled scripts."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "generative_agents"
ALLOWED = {
    "ga_protocol": {"ga_protocol"},
    "ga_studio": {"ga_studio", "ga_protocol"},
    "ga_runtime": {"ga_runtime", "ga_protocol"},
    "ga_replay": {"ga_replay", "ga_protocol"},
    "adapters": {"adapters", "ga_protocol", "ga_studio", "ga_runtime", "ga_replay"},
}
DATABASE = {"sqlalchemy", "alembic", "sqlite3", "psycopg", "psycopg2", "pymysql"}
EXECUTION = {"requests", "httpx", "fastapi", "uvicorn", "openai", "llama_index"}


def source_graph(root: Path = ROOT):
    paths = list(root.rglob("*.py"))
    graph, owners, errors = {}, {}, []
    assert paths, f"No source files inspected: {root}"
    for path in paths:
        relative = path.relative_to(root)
        module = "generative_agents." + ".".join(relative.with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
        owner = relative.parts[0]
        owners[module] = owner
        edges = graph.setdefault(module, set())
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            targets = []
            if isinstance(node, ast.Import):
                targets = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                if any(item.name == "*" for item in node.names):
                    errors.append(f"{relative}:{node.lineno}: wildcard import")
                target = node.module or ""
                if node.level:
                    target = importlib.util.resolve_name("." * node.level + target, package)
                targets = [target]
                targets += [target + "." + item.name for item in node.names]
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if name in {"__import__", "import_module"} and node.args and isinstance(node.args[0], ast.Constant):
                    if isinstance(node.args[0].value, str):
                        targets = [node.args[0].value]
            for target in targets:
                head = target.split(".")[0]
                if head in DATABASE and (owner != "ga_studio" or "bundled" in relative.parts):
                    errors.append(f"{relative}:{node.lineno}: database import {target}")
                if owner in {"ga_protocol", "ga_replay"} and head in EXECUTION:
                    errors.append(f"{relative}:{node.lineno}: execution dependency {target}")
                if target.startswith("generative_agents."):
                    parts = target.split(".")
                    if owner == "adapters" and parts[1] in {"ga_studio", "ga_runtime", "ga_replay"}:
                        if len(parts) < 3 or parts[2] != "api":
                            errors.append(f"{relative}:{node.lineno}: adapter bypasses public API {target}")
                    edges.add(target)
    return graph, owners, errors


def check_boundaries(root: Path = ROOT) -> list[str]:
    graph, owners, errors = source_graph(root)
    def module_for(target):
        while target and target not in graph:
            target = target.rpartition(".")[0]
        return target
    for module, owner in owners.items():
        if owner not in ALLOWED:
            if module != "generative_agents":
                errors.append(f"unowned source module: {module}")
            continue
        pending = [(module, [module])]
        seen = set()
        while pending:
            current, chain = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            for target in graph.get(current, ()):
                target_owner = target.split(".")[1]
                if target_owner not in ALLOWED[owner]:
                    errors.append(" -> ".join([*chain, target]))
                    continue
                resolved = module_for(target)
                if not resolved:
                    errors.append(f"{current}: missing product module {target}")
                elif resolved not in seen:
                    pending.append((resolved, [*chain, resolved]))
    return sorted(set(errors))


if __name__ == "__main__":
    violations = check_boundaries()
    if violations:
        raise SystemExit("\n".join(violations))
    print("All source dependency boundaries passed.")
