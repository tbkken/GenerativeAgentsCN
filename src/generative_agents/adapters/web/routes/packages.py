"""Packages HTTP routes."""
from __future__ import annotations
import shutil
from fastapi import HTTPException, Query, Response
from fastapi.responses import FileResponse
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.adapters.web.context import _catalog_item

def install_routes(router, ctx):

    @router.get('/packages')
    def list_packages(kind: str | None=Query(default=None, pattern='^(experiment|run)$')):
        return {'items': [_catalog_item(row) for row in ctx.catalog.list(package_kind=kind)]}

    @router.post('/packages/rebuild')
    def rebuild_packages():
        try:
            records = ctx.catalog.rebuild([ctx.package_root])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {'items': [{'package_kind': item.package_kind, 'package_id': item.package_id, 'experiment_id': item.experiment_id, 'run_id': item.run_id, 'location': item.location, 'display_name': item.display_name, 'status': item.package_status, 'content_sha256': item.content_sha256, 'is_archive': item.is_archive} for item in records]}

    @router.get('/packages/{package_kind}/{package_id}')
    def package_detail(package_kind: str, package_id: str):
        location = ctx.catalog_location(package_kind, package_id)
        with open_package(location) as root:
            manifest_name = 'manifest.json' if package_kind == 'experiment' else 'run.json'
            result = {'catalog': _catalog_item(ctx.catalog.get(package_kind, package_id)), 'manifest': read_json(root / manifest_name)}
            if package_kind == 'run':
                result['status'] = read_json(root / 'status.json')
            return result

    @router.get('/packages/{package_kind}/{package_id}/download')
    def download_package(package_kind: str, package_id: str):
        location = ctx.catalog_location(package_kind, package_id)
        if not location.is_file():
            raise HTTPException(status_code=409, detail='seal the package before download')
        return FileResponse(location, filename=location.name, media_type='application/zip')

    @router.delete('/packages/{package_kind}/{package_id}', status_code=204)
    def delete_package(package_kind: str, package_id: str):
        if package_kind == 'run':
            return ctx.delete_run(package_id)
        location = ctx.catalog_location(package_kind, package_id)
        if location.is_dir():
            shutil.rmtree(location)
        else:
            location.unlink()
        ctx.catalog.delete(package_kind, package_id)
        return Response(status_code=204)
