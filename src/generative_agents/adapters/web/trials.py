"""Bridge author preparation and execution using temporary physical files."""
from tempfile import TemporaryDirectory
from generative_agents.ga_studio.api import prepare_skill_trial, prepare_copied_trial
from generative_agents.ga_runtime.api import execute_skill_trial

def run_skill_trial(database, registry, name, input_text, context, *, model_preset_id=None):
    with TemporaryDirectory(prefix='ga-skill-trial-') as directory:
        credentials = prepare_skill_trial(database, registry, name, input_text, context,
            destination=directory, model_preset_id=model_preset_id)
        return execute_skill_trial(directory, credentials=credentials)

def run_copied_skill_trial(snapshots, name, input_text, context, *, config, base_url, model, api_key):
    with TemporaryDirectory(prefix='ga-skill-trial-') as directory:
        prepare_copied_trial(snapshots, name, input_text, context, destination=directory,
            config=config, base_url=base_url, model=model)
        return execute_skill_trial(directory, credentials={'GA_SKILL_TRIAL_KEY': api_key})
