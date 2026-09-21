"""Isolated Studio application factory for HTTP tests."""

from pathlib import Path

from sqlalchemy.engine import make_url

from generative_agents.adapters.web.app import create_studio_app


def create_test_studio(*, database_url: str, var_dir=None, **kwargs):
    """Keep each test's files beside its temporary author database."""
    if var_dir is None:
        database_path = make_url(database_url).database
        if not database_path or database_path == ":memory:":
            raise ValueError("in-memory Studio tests must supply a temporary var_dir")
        var_dir = Path(database_path).parent / "workspace"
    return create_studio_app(database_url=database_url, var_dir=var_dir, **kwargs)
