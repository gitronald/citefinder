"""The given-name recipe printed by `citefinder skill` must run as written.

The skill body is documentation, so nothing imports it; this test extracts the
python block from the "Given names and diacritics" section, points its three
literal paths at synthetic fixtures, and executes it. Each fixture entry covers
one shape the recipe has to survive: a diacritic the deposit dropped, a
corporate bib author, a Crossref organisational author with no `given`, an
OpenAlex authorship with no linked author, an `Anonymous` bib author, a torn
trailing cache line, and a DOI-less entry that must count as unchecked.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stdout
from pathlib import Path

from citefinder import install as install_mod

CR_PATH = "data/citefinder/paper/crossref/crossref.jsonl"
OA_PATH = "data/citefinder/paper/openalex/openalex.jsonl"
BIB_PATH = "paper/refs.bib"

BIB = r"""
@article{diacritic,
  author = {Crenshaw, Kimberle},
  title = {Mapping the Margins},
  year = {1991},
  doi = {10.2307/1229039}
}
@article{corporate,
  author = {{National Academies of Sciences, Engineering, and Medicine} and Doe, Jane},
  title = {A report},
  year = {2020},
  doi = {10.1000/corp}
}
@article{orgsource,
  author = {Roe, Jane and Doe, John},
  title = {Guidelines},
  year = {2021},
  doi = {10.1000/org}
}
@article{unlinked,
  author = {Poe, Edgar},
  title = {Raven},
  year = {2022},
  doi = {10.1000/unlinked}
}
@article{anon,
  author = {{Anonymous}},
  title = {Untitled},
  year = {2023},
  doi = {10.1000/anon}
}
@book{nodoi,
  author = {Smith, Ann},
  title = {A book},
  year = {2024}
}
"""


def _cr(doi: str, authors: list[dict]) -> str:
    rec = {
        "key": f"https://api.crossref.org/works/{doi}",
        "value": {"message": {"DOI": doi, "author": authors}},
        "ts": 0,
    }
    return json.dumps(rec)


def _oa(doi: str, authorships: list[dict]) -> str:
    rec = {
        "key": f"https://api.openalex.org/works/doi:{doi}",
        "value": {"doi": f"https://doi.org/{doi}", "authorships": authorships},
        "ts": 0,
    }
    return json.dumps(rec)


def _recipe() -> str:
    body = install_mod.skill_body()
    section = body.split("### Given names and diacritics", 1)[1]
    match = re.search(r"```python\n(.*?)```", section, re.S)
    assert match, "recipe code block missing from the skill"
    return match.group(1)


def test_given_name_recipe_runs_as_printed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "paper").mkdir()
    (tmp_path / BIB_PATH).write_text(BIB, encoding="utf-8")
    cr = tmp_path / CR_PATH
    oa = tmp_path / OA_PATH
    cr.parent.mkdir(parents=True)
    oa.parent.mkdir(parents=True)
    cr.write_text(
        "\n".join(
            [
                _cr("10.2307/1229039", [{"given": "Kimberle", "family": "Crenshaw"}]),
                _cr(
                    "10.1000/corp",
                    [
                        {"name": "National Academies"},
                        {"given": "Jane", "family": "Doe"},
                    ],
                ),
                _cr(
                    "10.1000/org",
                    [
                        {"name": "World Health Organization"},
                        {"given": "John", "family": "Doe"},
                    ],
                ),
                _cr("10.1000/unlinked", [{"given": "Edgar", "family": "Poe"}]),
                _cr("10.1000/anon", [{"given": "Jane", "family": "Roe"}]),
                '{"key": "https://api.crossref.org/works/10.1000/torn", "val',
            ]
        ),
        encoding="utf-8",
    )
    oa.write_text(
        "\n".join(
            [
                _oa(
                    "10.2307/1229039",
                    [{"author": {"display_name": "Kimberlé W. Crenshaw"}}],
                ),
                _oa("10.1000/unlinked", [{"raw_author_name": "Edgar Poe"}]),
                # a cached 404 and a search page must both be ignored
                json.dumps(
                    {
                        "key": "https://api.openalex.org/works/doi:10.1000/missing",
                        "value": None,
                        "ts": 0,
                    }
                ),
                json.dumps(
                    {
                        "key": "https://api.openalex.org/works?filter=x",
                        "value": {"results": []},
                        "ts": 0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out = io.StringIO()
    with redirect_stdout(out):
        exec(compile(_recipe(), "skill-recipe", "exec"), {"__name__": "recipe"})
    lines = out.getvalue().splitlines()

    assert lines[0] == "key\tpos\tbib\tcrossref\topenalex"
    assert lines[1:-1] == ["diacritic\t1\tKimberle\tKimberle\tKimberlé W."]
    assert lines[-1].startswith("checked 5 of 6 entries")
