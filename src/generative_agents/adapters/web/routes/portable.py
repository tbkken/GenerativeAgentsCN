"""Assemble feature routers around explicit service dependencies."""
from fastapi import APIRouter
from generative_agents.adapters.web.context import WebContext
from generative_agents.adapters.web.operations import WebOperations
from generative_agents.adapters.web.routes import experiments, runs, results, artifacts, packages, authoring, replay, resource_exchange

def create_portable_router(database, *, package_root, max_concurrent_runs=2):
    ctx = WebContext(database, package_root=package_root, max_concurrent_runs=max_concurrent_runs)
    ctx.jobs = WebOperations(ctx.package_root.parent / 'studio-operations')
    router = APIRouter(prefix='/api/studio', tags=['package-studio'])
    router.operation_jobs = ctx.jobs.jobs
    ctx.jobs.install(router)
    experiments.install_routes(router, ctx)
    runs.install_routes(router, ctx)
    results.install_routes(router, ctx)
    artifacts.install_routes(router, ctx)
    packages.install_routes(router, ctx)
    authoring.install_routes(router, ctx)
    resource_exchange.install_routes(router, ctx)
    replay.install_routes(router, ctx)
    return router
