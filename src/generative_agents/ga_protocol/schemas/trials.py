"""Diagnostics for an isolated Skill file workspace."""

class SkillTrialError(ValueError):
    def __init__(self, code, message, *, status=422, trace=()):
        super().__init__(message)
        self.code, self.status, self.trace = code, status, list(trace)
