"""Host-side author model choices, separate from portable model resources."""
from pathlib import Path

from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.schemas.errors import ServiceError


class StudioModelSettings:
    def __init__(self, root):
        self.path = Path(root) / "studio-model-settings.json"

    def _read(self):
        try:
            document = read_json(self.path) if self.path.exists() else {}
            if not isinstance(document, dict):
                raise ValueError("settings must be an object")
            selected = document.get("decision_compiler_model_id")
            if selected is not None and (not isinstance(selected, str) or not selected.strip()):
                raise ValueError("decision compiler model id must be nonempty text")
            return document
        except (OSError, ValueError) as exc:
            raise ServiceError("STUDIO_MODEL_SETTINGS_INVALID",
                "Studio 统一模型设置无法读取，请检查 studio-model-settings.json。", status_code=422) from exc

    def decision_compiler_model_id(self):
        with package_lock(self.path):
            return self._read().get("decision_compiler_model_id")

    def set_decision_compiler(self, model_id, enabled):
        """Change only an explicit selection; unchecking another model is harmless."""
        with package_lock(self.path):
            document = self._read()
            selected = document.get("decision_compiler_model_id")
            if enabled:
                if selected == model_id:
                    return
                document["decision_compiler_model_id"] = model_id
            elif selected == model_id:
                document.pop("decision_compiler_model_id")
            else:
                return
            atomic_write_json(self.path, document)
