"""Author metadata is separate from copied Skill file contracts."""
from dataclasses import dataclass
from generative_agents.ga_protocol.skills.documents import SkillDocument

@dataclass(frozen=True, slots=True)
class AuthorSkillDocument(SkillDocument):
    storage: str = "filesystem"
    storage_ref: str | None = None
    resource_id: str | None = None
    is_builtin: bool = False
    archived_at: str | None = None

    def summary(self):
        return {**SkillDocument.summary(self), "path": self.storage_ref or self.path.as_posix(),
                "storage": self.storage, "storage_ref": self.storage_ref,
                "resource_id": self.resource_id, "is_builtin": self.is_builtin,
                "archived_at": self.archived_at}
