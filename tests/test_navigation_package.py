import json
import zipfile

from generative_agents.ga_protocol.packages.io import seal_directory
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder
from generative_agents.ga_studio.experiments.builder import SkillSource
from generative_agents.ga_studio.resources.maps import normalize_public_world
from tests.foundation.test_navigation import navigation_world
from tests.test_portable_package_protocol import _definition


def test_sealed_experiment_physically_owns_collision_and_covers_it_in_integrity(tmp_path):
    definition = _definition()
    definition['world'] = normalize_public_world(navigation_world()).model_dump(mode='json')
    source = tmp_path / 'SKILL.md'
    source.write_text('---\nname: test-brain\ndescription: navigation test\n---\nUse world-navigate then world-act.\n', encoding='utf-8')
    packages = []
    for name, overrides in [('closed', {}), ('open', {'17': False})]:
        definition['world']['definition']['editor_v2']['navigation']['overrides'] = overrides
        package = ExperimentPackageBuilder().build_directory(
            tmp_path / name, definition=definition,
            skills=[SkillSource('test-brain', 'brain', source)], brain_skill='test-brain')
        validate_experiment_directory(package)
        packages.append(package)
    definition['world']['definition']['editor_v2']['navigation']['overrides'] = {'17': True}
    sealed = seal_directory(packages[1], tmp_path / 'campus.gaexp')
    with zipfile.ZipFile(sealed) as bundle:
        world = json.loads(bundle.read('world/world.json'))
        assert not next(t for t in world['definition']['tiles'] if t['coord'] == [3, 2])['collision']
        assert world['definition']['editor_v2']['navigation']['overrides']['17'] is False
    hashes = [json.loads((p / 'integrity/sha256.json').read_text(encoding='utf-8')) for p in packages]
    assert hashes[0] != hashes[1]
