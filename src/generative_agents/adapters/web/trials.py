"""Bridge author preparation and execution using temporary physical files."""
from tempfile import TemporaryDirectory
from generative_agents.ga_studio.api import prepare_skill_trial, prepare_copied_trial
from generative_agents.ga_runtime.api import execute_skill_trial
from generative_agents.ga_runtime.api import compile_skill_decision
from generative_agents.ga_studio.api import prepare_skill_decision_compile


def compile_public_skill_decision(database, registry, name, markdown, *, model_preset_id=None, scripts=None):
    settings = prepare_skill_decision_compile(database, registry, name, markdown,
                                               model_preset_id=model_preset_id, scripts=scripts)
    return compile_skill_decision(**settings)

def run_skill_trial(database, registry, name, input_text, *, model_preset_id=None,
                   experiment_id=None, agent_key=None):
    with TemporaryDirectory(prefix='ga-skill-trial-') as directory:
        credentials = prepare_skill_trial(database, registry, name, input_text,
            destination=directory, model_preset_id=model_preset_id,
            experiment_id=experiment_id, agent_key=agent_key)
        return execute_skill_trial(directory, credentials=credentials)

def run_copied_skill_trial(snapshots, name, input_text, *, config, base_url, model, api_key):
    with TemporaryDirectory(prefix='ga-skill-trial-') as directory:
        prepare_copied_trial(snapshots, name, input_text, destination=directory,
            config=config, base_url=base_url, model=model)
        return execute_skill_trial(directory, credentials={'GA_SKILL_TRIAL_KEY': api_key})
