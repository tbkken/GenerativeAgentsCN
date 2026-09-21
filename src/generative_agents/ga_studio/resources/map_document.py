"""Studio editor metadata and public asset locators; never runtime lookups."""
from typing import Any
from pydantic import Field, model_validator
from generative_agents.ga_protocol.schemas.world import MaterialSource as PackageMaterialSource
from generative_agents.ga_protocol.schemas.world import WorldDocument

class MaterialSource(PackageMaterialSource):
    asset_id: str | None = None

    @model_validator(mode="after")
    def validate_author_origin(self):
        if self.kind == "UPLOADED" and not (self.asset_id and self.asset_hash):
            raise ValueError("uploaded material source requires asset_id and asset_hash")
        if self.kind == "CANVAS" and self.asset_id:
            raise ValueError("canvas material source cannot declare an external origin")
        return self

class MapEditorDocumentV2(WorldDocument):
    material_sources: list[MaterialSource] = Field(default_factory=list, max_length=10_000)
    import_metadata: dict[str, Any] = Field(default_factory=dict)
    ui_state: dict[str, Any] = Field(default_factory=dict)

def package_world(document: dict) -> dict:
    """Remove author metadata after materials have been physically copied."""
    value = dict(document)
    value.pop("ui_state", None)
    value.pop("import_metadata", None)
    value["material_sources"] = [{key: item for key, item in source.items() if key != "asset_id"}
                                 for source in value.get("material_sources", [])]
    return WorldDocument.model_validate(value).model_dump(mode="json")
