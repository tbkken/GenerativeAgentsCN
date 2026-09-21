"""The experiment editor derives its viewport from package-owned geometry."""
from generative_agents.ga_studio.experiments.builder import build_semantic_index
from generative_agents.ga_studio.experiments.editor import ExperimentResourceEditor
from tests.foundation.test_navigation import navigation_world


def test_imported_world_can_be_edited_and_resaved_without_author_metadata():
    packaged, index = build_semantic_index(navigation_world())
    assert 'import_metadata' not in packaged['definition']['editor_v2']
    view = ExperimentResourceEditor.map_detail(packaged, 'digest', True, 'experiment')['world']
    height, width = packaged['definition']['size']
    assert view['definition']['editor_v2']['import_metadata'] == {
        'width': width, 'height': height, 'tile_size': packaged['definition']['tile_size']}
    assert 'import_metadata' not in packaged['definition']['editor_v2']
    saved, saved_index = build_semantic_index(view)
    assert saved == packaged
    assert saved_index == index
