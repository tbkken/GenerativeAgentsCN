"""Build handwritten package Skill inputs without a mutable runtime registry."""
def write_skill(registry, name, description, *, kind="atomic", body=""):
    folder = "atomic" if kind == "atomic" else kind + "s"
    path = registry.root / folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nname: {name}\ndescription: "{description}"\n---\n\n{body}\n', encoding="utf-8")
    return registry.get(name)
