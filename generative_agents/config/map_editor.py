"""Strict authoring contracts for the versioned map editor.

The simulation still consumes ``WorldConfig.definition.tiles``.  These models
describe the richer authoring document used to deterministically compile that
runtime grid and its render manifest.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator
from typing_extensions import Annotated

from .schema import StrictModel
from .game_object_skills import GameObjectSkillBinding, validate_unique_skill_bindings


Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
]
MapDisplayLevel = Literal["MAP", "SECTOR", "ARENA", "GAME_OBJECT"]
HierarchyNodeKind = Literal["WORLD", "SECTOR", "ARENA", "GAME_OBJECT"]
MaterialSourceKind = Literal["BUNDLED", "UPLOADED", "GENERATED_COLOR", "CANVAS"]
MaterialSliceKind = Literal["TILE", "STAMP", "PIXEL"]
RecipeTransform = Literal[
    "NONE",
    "FLIP_H",
    "FLIP_V",
    "FLIP_D",
    "FLIP_HV",
    "FLIP_HD",
    "FLIP_VD",
    "FLIP_HVD",
]


class GridRect(StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class MaterialSource(StrictModel):
    id: Identifier
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    kind: MaterialSourceKind
    asset_id: str | None = None
    asset_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = (
        None
    )
    bundled_path: (
        Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None
    ) = None
    generated_color: (
        Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None
    ) = None
    media_type: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    width_px: int = Field(ge=1, le=100_000)
    height_px: int = Field(ge=1, le=100_000)
    tile_width: int = Field(ge=1, le=4096)
    tile_height: int = Field(ge=1, le=4096)
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    tile_count: int = Field(ge=1)
    margin: int = Field(default=0, ge=0)
    spacing: int = Field(default=0, ge=0)
    first_gid: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_origin(self) -> "MaterialSource":
        """校验`origin`。

        返回:
            返回 `'MaterialSource'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.kind == "BUNDLED" and not self.bundled_path:
            raise ValueError("bundled material source requires bundled_path")
        if self.kind == "UPLOADED" and not (self.asset_id and self.asset_hash):
            raise ValueError(
                "uploaded material source requires asset_id and asset_hash"
            )
        if self.kind == "GENERATED_COLOR" and not self.generated_color:
            raise ValueError("generated color material source requires generated_color")
        if self.kind == "CANVAS" and any(
            (self.asset_id, self.bundled_path, self.generated_color)
        ):
            raise ValueError("canvas material source cannot declare an external origin")
        return self


class MaterialSlice(StrictModel):
    id: Identifier
    source_id: Identifier
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    kind: MaterialSliceKind
    rotation_degrees: Literal[0, 90, 180, 270] = 0
    grid_rect: GridRect | None = None
    pixel_rect: GridRect
    trim_transparent: bool = True
    indexed_gid: int | None = Field(default=None, ge=1)
    local_tile_id: int | None = Field(default=None, ge=0)
    readonly_indexed: bool = False

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_purpose(cls, value: Any) -> Any:
        """执行 `MaterialSlice` 的`discard``legacy``purpose`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`Any`。

        返回:
            返回 `Any` 类型的处理结果。
        """

        if isinstance(value, dict) and "purpose" in value:
            value = dict(value)
            value.pop("purpose", None)
        return value


class TileOverridePart(StrictModel):
    placement_id: Identifier
    anchor_index: int = Field(ge=0)
    column: int = Field(ge=0)
    row: int = Field(ge=0)
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    rotation_degrees: Literal[0, 90, 180, 270] = 0

    @model_validator(mode="after")
    def validate_position(self) -> "TileOverridePart":
        """校验`position`。

        返回:
            返回 `'TileOverridePart'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.column >= self.columns or self.row >= self.rows:
            raise ValueError("tile override part position must be inside its footprint")
        return self


class TileOverrideLayer(StrictModel):
    """One composited material slice at a map cell, ordered bottom to top."""

    slice_id: Identifier
    part: TileOverridePart | None = None


class MaterialCanvas(StrictModel):
    """An editable raster recipe that is also exposed as a reusable material."""

    id: Identifier
    source_id: Identifier
    slice_id: Identifier
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    width_tiles: int = Field(ge=1, le=4096)
    height_tiles: int = Field(ge=1, le=4096)
    tile_size: int = Field(default=32, ge=1, le=4096)
    cells: dict[int, list[TileOverrideLayer]] = Field(
        default_factory=dict,
        max_length=16_000_000,
    )

    @model_validator(mode="after")
    def validate_cells(self) -> "MaterialCanvas":
        """校验`cells`。

        返回:
            返回 `'MaterialCanvas'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        cell_count = self.width_tiles * self.height_tiles
        for index, layers in self.cells.items():
            if index < 0 or index >= cell_count:
                raise ValueError(f"material canvas cell {index} is out of bounds")
            if not layers:
                raise ValueError(f"material canvas cell {index} cannot be empty")
            for layer in layers:
                if layer.part is None:
                    continue
                anchor_layers = self.cells.get(layer.part.anchor_index, [])
                if not any(
                    anchor.part is not None
                    and anchor.part.placement_id == layer.part.placement_id
                    for anchor in anchor_layers
                ):
                    raise ValueError(
                        "material canvas part anchor requires a matching placement"
                    )
        return self


class RecipeEntry(StrictModel):
    slice_id: Identifier
    x: int
    y: int
    z_index: int = Field(ge=-10_000, le=10_000)
    transform: RecipeTransform = "NONE"
    source_raw_gid: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)


class RenderRecipe(StrictModel):
    id: Identifier
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    width_tiles: int = Field(ge=1, le=10_000)
    height_tiles: int = Field(ge=1, le=10_000)
    anchor_x: int = 0
    anchor_y: int = 0
    entries: list[RecipeEntry] = Field(default_factory=list, max_length=100_000)
    imported: bool = False
    fingerprint: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class LayerCellOverride(StrictModel):
    index: int = Field(ge=0)
    slice_id: Identifier | None = None
    transform: RecipeTransform = "NONE"
    collision_override: bool | None = None


class LayerRecipePlacement(StrictModel):
    id: Identifier
    recipe_id: Identifier
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    rotation_degrees: int = Field(default=0, ge=-360, le=360)


class VisualLayer(StrictModel):
    id: Identifier
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    display_level: MapDisplayLevel
    z_index: int = Field(ge=-10_000, le=10_000)
    width: int = Field(ge=1, le=10_000)
    height: int = Field(ge=1, le=10_000)
    raw_gids: list[int] = Field(default_factory=list, max_length=100_000_000)
    cell_overrides: list[LayerCellOverride] = Field(
        default_factory=list, max_length=1_000_000
    )
    recipe_placements: list[LayerRecipePlacement] = Field(
        default_factory=list, max_length=100_000
    )
    visible: bool = True
    opacity: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_grid(self) -> "VisualLayer":
        """校验`grid`。

        返回:
            返回 `'VisualLayer'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.raw_gids and len(self.raw_gids) != self.width * self.height:
            raise ValueError("visual layer raw_gids must match width * height")
        indexes = [item.index for item in self.cell_overrides]
        if len(indexes) != len(set(indexes)):
            raise ValueError("visual layer override indexes must be unique")
        if any(index >= self.width * self.height for index in indexes):
            raise ValueError("visual layer override index is out of bounds")
        return self


class HierarchyNode(StrictModel):
    id: Identifier
    kind: HierarchyNodeKind
    parent_id: Identifier | None = None
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    sort_order: int = Field(default=0, ge=0)
    bounds: GridRect
    semantic: Annotated[str, StringConstraints(max_length=4000)] = ""
    material_slice_id: Identifier | None = None
    render_recipe_id: Identifier | None = None
    render_mode: Literal["LAYER_BACKED", "PLACED_RECIPE"] = "LAYER_BACKED"
    skill_bindings: list[GameObjectSkillBinding] = Field(
        default_factory=list, max_length=20
    )
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("skill_bindings")
    @classmethod
    def unique_skill_bindings(
        cls, value: list[GameObjectSkillBinding]
    ) -> list[GameObjectSkillBinding]:
        """执行 `HierarchyNode` 的`unique`技能`bindings`操作。

        参数:
            value: 当前操作使用的`value`。 类型：`list[GameObjectSkillBinding]`。

        返回:
            返回按接口约定组织的结果集合。
        """
        return validate_unique_skill_bindings(value)

    @model_validator(mode="after")
    def skills_only_on_game_objects(self) -> "HierarchyNode":
        """执行 `HierarchyNode` 的`skills``only``on`仿真世界`objects`操作。

        返回:
            返回 `'HierarchyNode'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.skill_bindings and self.kind != "GAME_OBJECT":
            raise ValueError("only GAME_OBJECT nodes may bind passive Skills")
        return self


class MapEditorDocumentV2(StrictModel):
    schema_version: Literal["ga-map-editor/v2"] = "ga-map-editor/v2"
    root_node_id: Identifier
    material_sources: list[MaterialSource] = Field(
        default_factory=list, max_length=10_000
    )
    material_slices: list[MaterialSlice] = Field(
        default_factory=list, max_length=1_000_000
    )
    material_canvases: list[MaterialCanvas] = Field(
        default_factory=list, max_length=100_000
    )
    render_recipes: list[RenderRecipe] = Field(default_factory=list, max_length=100_000)
    visual_layers: list[VisualLayer] = Field(default_factory=list, max_length=1_000)
    hierarchy_nodes: list[HierarchyNode] = Field(
        default_factory=list, max_length=1_000_000
    )
    import_metadata: dict[str, Any] = Field(default_factory=dict)
    tile_overrides: dict[int, Identifier] = Field(
        default_factory=dict, max_length=1_000_000
    )
    tile_override_parts: dict[int, TileOverridePart] = Field(
        default_factory=dict, max_length=1_000_000
    )
    tile_override_layers: dict[int, list[TileOverrideLayer]] = Field(
        default_factory=dict,
        max_length=1_000_000,
    )
    ui_state: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "material_sources",
        "material_slices",
        "material_canvases",
        "render_recipes",
        "visual_layers",
        "hierarchy_nodes",
    )
    @classmethod
    def unique_ids(cls, values: list[Any]) -> list[Any]:
        """执行 `MapEditorDocumentV2` 的`unique``ids`操作。

        参数:
            values: 需要规范化、校验、拼接或批量处理的值集合。 类型：`list[Any]`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        identifiers = [item.id for item in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("map editor collection ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_relations(self) -> "MapEditorDocumentV2":
        """校验`relations`。

        返回:
            返回 `'MapEditorDocumentV2'` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        sources = {item.id for item in self.material_sources}
        source_by_id = {item.id: item for item in self.material_sources}
        slices = {item.id for item in self.material_slices}
        slice_by_id = {item.id: item for item in self.material_slices}
        recipes = {item.id for item in self.render_recipes}
        nodes = {item.id: item for item in self.hierarchy_nodes}
        roots = [item for item in self.hierarchy_nodes if item.kind == "WORLD"]
        if (
            len(roots) != 1
            or roots[0].id != self.root_node_id
            or roots[0].parent_id is not None
        ):
            raise ValueError(
                "map editor document requires exactly one matching WORLD root"
            )
        for item in self.material_slices:
            if item.source_id not in sources:
                raise ValueError(f"material slice {item.id} references missing source")
        for canvas in self.material_canvases:
            source = source_by_id.get(canvas.source_id)
            material_slice = slice_by_id.get(canvas.slice_id)
            if source is None or source.kind != "CANVAS":
                raise ValueError(
                    f"material canvas {canvas.id} requires a CANVAS source"
                )
            if material_slice is None or material_slice.source_id != canvas.source_id:
                raise ValueError(
                    f"material canvas {canvas.id} requires its matching slice"
                )
            expected_size = (
                canvas.width_tiles * canvas.tile_size,
                canvas.height_tiles * canvas.tile_size,
            )
            actual_size = (
                material_slice.pixel_rect.width,
                material_slice.pixel_rect.height,
            )
            if actual_size != expected_size:
                raise ValueError(
                    f"material canvas {canvas.id} slice size must match its canvas"
                )
            for layers in canvas.cells.values():
                for layer in layers:
                    if layer.slice_id not in slices:
                        raise ValueError(
                            "material canvas references missing material slice"
                        )
                    paint_slice = slice_by_id[layer.slice_id]
                    if source_by_id[paint_slice.source_id].kind == "CANVAS":
                        raise ValueError(
                            "material canvas may only paint with non-canvas slices"
                        )
        if any(slice_id not in slices for slice_id in self.tile_overrides.values()):
            raise ValueError("tile override references missing material slice")
        if any(index not in self.tile_overrides for index in self.tile_override_parts):
            raise ValueError("tile override part requires a matching tile override")
        if any(
            part.anchor_index not in self.tile_overrides
            for part in self.tile_override_parts.values()
        ):
            raise ValueError(
                "tile override part anchor requires a matching tile override"
            )
        for index, layers in self.tile_override_layers.items():
            if not layers:
                raise ValueError(f"tile override layer stack {index} cannot be empty")
            if any(layer.slice_id not in slices for layer in layers):
                raise ValueError(
                    "tile override layer references missing material slice"
                )
            for layer in layers:
                if layer.part is None:
                    continue
                anchor_layers = self.tile_override_layers.get(
                    layer.part.anchor_index, []
                )
                if not any(
                    anchor.part is not None
                    and anchor.part.placement_id == layer.part.placement_id
                    for anchor in anchor_layers
                ):
                    raise ValueError(
                        "tile override layer part anchor requires a matching placement"
                    )
        for recipe in self.render_recipes:
            if any(entry.slice_id not in slices for entry in recipe.entries):
                raise ValueError(f"render recipe {recipe.id} references missing slice")
        expected_parent = {
            "SECTOR": "WORLD",
            "ARENA": "SECTOR",
            "GAME_OBJECT": "ARENA",
        }
        for node in self.hierarchy_nodes:
            if node.material_slice_id and node.material_slice_id not in slices:
                raise ValueError(
                    f"hierarchy node {node.id} references missing material slice"
                )
            if node.kind == "WORLD":
                continue
            parent = nodes.get(node.parent_id or "")
            if parent is None or parent.kind != expected_parent[node.kind]:
                raise ValueError(f"hierarchy node {node.id} has an invalid parent")
            if node.render_recipe_id and node.render_recipe_id not in recipes:
                raise ValueError(f"hierarchy node {node.id} references missing recipe")
        return self


__all__ = [
    "GridRect",
    "HierarchyNode",
    "LayerCellOverride",
    "LayerRecipePlacement",
    "MapEditorDocumentV2",
    "MaterialCanvas",
    "MaterialSlice",
    "MaterialSource",
    "TileOverrideLayer",
    "TileOverridePart",
    "RecipeEntry",
    "RenderRecipe",
    "VisualLayer",
]
