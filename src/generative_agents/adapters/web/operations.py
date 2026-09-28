"""HTTP admission and light polling for explicitly requested background work."""
from functools import wraps
import inspect
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from generative_agents.ga_studio.api import OperationJobs


class WebOperations:
    def __init__(self, directory):
        self.jobs = OperationJobs(directory)

    def action(self, kind):
        def decorate(operation):
            signature = inspect.signature(operation)
            annotations = inspect.get_annotations(inspect.unwrap(operation), eval_str=True)
            parameters = [p.replace(annotation=annotations.get(p.name, p.annotation)) for p in signature.parameters.values()]
            parameters.append(inspect.Parameter('_operation_request', inspect.Parameter.KEYWORD_ONLY, annotation=Request))

            @wraps(operation)
            def wrapped(*args, **kwargs):
                request = kwargs.pop('_operation_request')
                if 'respond-async' not in request.headers.get('prefer', '').lower():
                    return operation(*args, **kwargs)
                scope = {key: value for key, value in kwargs.items() if key in {'experiment_id', 'run_id'}}

                def execute():
                    return jsonable_encoder(operation(*args, **kwargs))

                try:
                    item = self.jobs.submit(kind, execute, scope=scope)
                except ValueError as exc:
                    raise HTTPException(429, str(exc)) from exc
                return JSONResponse(item, status_code=202, headers={'Preference-Applied': 'respond-async', 'Location': item['status_url']})
            wrapped.__signature__ = signature.replace(parameters=parameters, return_annotation=annotations.get('return', signature.return_annotation))
            return wrapped
        return decorate

    def install(self, router):
        @router.get('/operations/{operation_id}')
        def operation_status(operation_id: str):
            try:
                return self.jobs.get(operation_id)
            except (ValueError, OSError) as exc:
                raise HTTPException(404, 'Operation is not present') from exc

        @router.delete('/operations/{operation_id}')
        def cancel_operation(operation_id: str):
            try:
                return self.jobs.cancel(operation_id)
            except FileNotFoundError as exc:
                raise HTTPException(404, 'Operation is not present') from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
