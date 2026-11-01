"""The `pkgskills` host declaration for citefinder's bundled skill.

Mounts `citefinder skill`, `doc`, and `install`; the prompts live under
`citefinder/prompts/skills/`.
"""

from __future__ import annotations

from pkgskills import CLAUDE_CODE, Doc, Host, Skill

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
    # The stub lands in repositories as a tracked file, so a Codex copy would add
    # a second tracked file to each. Reaches global installs too: `Host.harnesses`
    # applies to both modes.
    harnesses=(CLAUDE_CODE,),
)
