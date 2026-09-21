"""HTTP adapter for author model connections."""
from fastapi import APIRouter, HTTPException
from generative_agents.ga_protocol.schemas.errors import ServiceError
from generative_agents.ga_studio.api import ModelService, ModelServiceInput

def create_model_service_router(database, root):
    service = ModelService(database, root)
    router = APIRouter(prefix='/api/studio/resources/model-services', tags=['models'])
    def call(operation):
        try:
            return operation()
        except ServiceError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    @router.get('')
    def list_models():
        return call(service.list_models)

    @router.post('', status_code=201)
    def create(body: ModelServiceInput):
        return call(lambda: service.create(body))

    @router.put('/{model_id}')
    def update(model_id: str, body: ModelServiceInput):
        return call(lambda: service.update(model_id, body))

    @router.delete('/{model_id}', status_code=204)
    def delete(model_id: str):
        return call(lambda: service.delete(model_id))

    @router.post('/{model_id}/test/{purpose}')
    def test(model_id: str, purpose: str):
        return call(lambda: service.test(model_id, purpose))
    return router
