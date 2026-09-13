"""The `pkgskills` host declaration for citefinder's bundled skill.

`citefinder skill`, `citefinder doc`, and `citefinder install` are the
library's commands, mounted on the CLI from this declaration. The skill body
and its reference documents live under `citefinder/prompts/skills/`.
"""

from __future__ import annotations

from pkgskills import Doc, Host, Skill

SKILL = "use-citefinder"
_REFERENCES = ("openalex", "given-names", "year-mismatches", "inspect-table")

HOST = Host(
    dist="citefinder",
    cli="citefinder",
    prompts="citefinder.prompts",
    artifacts=(Skill(name=SKILL, sources=(f"skills/{SKILL}/SKILL.md",)),),
    docs=tuple(
        Doc(name=f"{SKILL}/{ref}", source=f"skills/{SKILL}/references/{ref}.md")
        for ref in _REFERENCES
    ),
    render_cli=True,
)
