---
name: use-citefinder
description: Look up DOIs, search Crossref or OpenAlex, resolve book chapters, look up books by ISBN, and verify whole `.bib` files with `citefinder` — a small Crossref + OpenAlex client (plus Open Library and the Library of Congress for books) with a JSONL cache that survives sessions and remembers 404s. Use this whenever the user wants to verify a DOI, find a paper by author + title, check whether a citation is real, resolve a chapter DOI, look up an arXiv/preprint DOI Crossref doesn't index, find a book's publisher, place of publication, or full title from its ISBN, generate canonical metadata for a reference list, or audit a `.bib` file end-to-end — even when they don't say "Crossref" or "DOI" explicitly. Phrases like "is this paper real?", "find the published version", "look up this citation", "the subagent gave me these papers — verify them", "audit refs.bib", "what's the DOI for X?", or "where was this book published?" should trigger it.
metadata:
  version: "1.1.0"
  pkgskills-host: "citefinder"
  pkgskills-host-version: "0.14.1a0"
  pkgskills-version: "0.6.3"
  pkgskills-mode: "local"
  pkgskills-note: "generated; install overwrites edits"
---

# use-citefinder

This file is a generated stub, not the source of truth. The instructions live
in the `citefinder` package and are printed on demand, so they always match
the installed version.

**Before following them, confirm this stub is current:**

```bash
uv run citefinder install --local --check
```

Anything other than `ok` means the installed files are out of step with the
package: run `uv run citefinder install --local`, then start a fresh context before continuing. A
`remove:` line in the check's output names a file to delete instead. An
`overwrite:` line, or a refusal from that install, concerns content this package
did not write, so ask before using `--force`.

**Load the instructions and follow them exactly:**

```bash
uv run citefinder skill use-citefinder
```

If that command is not found, `citefinder` is not installed in this
environment; install it first.
