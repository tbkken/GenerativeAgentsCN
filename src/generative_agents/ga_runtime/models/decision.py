"""Traced, cancellable requests to the Unsloth decision API."""
from __future__ import annotations

import copy
import math

from generative_agents.ga_protocol.skills.decisions import validate_decision_questions
from generative_agents.ga_runtime.models.gateway import LLMModel


def _number(value, *, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))):
        raise ValueError("decision response contains an invalid number")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError("decision response number is outside its question range")
    return value


def validate_answers(response, questions):
    """Reject incomplete or mismatched decisions before exposing them to a Skill."""
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise ValueError("decision response must contain answers")
    answers = response["answers"]
    if set(answers) != set(questions):
        raise ValueError("decision answers do not match the requested questions")
    for key, question in questions.items():
        answer = answers[key]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise ValueError(f"decision answer type disagrees with question {key}")
        if kind == "noul":
            _number(answer.get("noul"), maximum=1)
            continue
        criteria = question["criteria"]
        expected = set(criteria) if kind == "choice" else {str(index) for index in range(len(criteria))}
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != expected:
            raise ValueError(f"decision probabilities disagree with question {key}")
        for probability in probabilities.values():
            _number(probability, maximum=1)
        _number(answer.get("confidence"), maximum=1)
        if kind == "choice":
            if not isinstance(answer.get("choice"), str) or answer["choice"] not in expected:
                raise ValueError(f"decision choice is unavailable for question {key}")
        elif kind == "score":
            _number(answer.get("score"), maximum=len(criteria) - 1)
            legend = answer.get("legend")
            if legend is not None and legend != {str(index): value for index, value in enumerate(criteria)}:
                raise ValueError(f"decision score legend disagrees with question {key}")
        else:
            raise ValueError(f"unsupported decision question type: {kind}")
    return answers


class DecisionModel(LLMModel):
    """Reuse the Run's model-call budgets, trace records, and HTTP cancellation."""

    @property
    def provider(self):
        return "unsloth"

    def setup(self, config):
        if config.get("provider") != "unsloth":
            raise ValueError("decision models require the unsloth provider")
        self._base_url = str(self._base_url).rstrip("/")
        if self._base_url.endswith("/systemone"):
            self._base_url = self._base_url.removesuffix("/systemone")
        if not self._base_url or not self._model or self._model == "auto":
            raise ValueError("decision model requires an explicit service URL and model")
        return None

    def decision_completion(self, state, questions, *, agent_key=None, step_no=None,
                            prompt_key=None, retry=None):
        validate_decision_questions(questions)
        payload = {"model": self._model, "state": copy.deepcopy(state),
                   "questions": copy.deepcopy(questions)}
        return self.completion(
            payload, retry=retry, return_type=None, caller="skill_decision",
            agent_key=agent_key, step_no=step_no, prompt_key=prompt_key,
            raise_on_failure=True, _completion_method=self._decision_completion,
        )

    def _decision_completion(self, payload, _return_type, **_kwargs):
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = self._request_json("POST", self._base_url + "/systemone",
                                      headers=headers, payload=payload)
        if not isinstance(response, dict):
            raise ValueError("decision response must be an object")
        usage = response.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("decision usage must be an object")
        input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        for value in (input_tokens, output_tokens):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError("decision usage tokens must be non-negative integers")
        self._last_usage = {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                            "total_tokens": (input_tokens + output_tokens
                                             if input_tokens is not None and output_tokens is not None else None)}
        answers = validate_answers(response, payload["questions"])
        return copy.deepcopy(answers)


def create_decision_model(config, *, recorder=None, control=None, logger=None, sleep=None):
    return DecisionModel(config, recorder=recorder, control=control, logger=logger, sleep=sleep)
