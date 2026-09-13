# Inspecting `bib_to_table` output side-by-side in the terminal

`citefinder.bib_to_table` returns a polars DataFrame, one row per bib entry. polars's default text rendering wraps long values mid-string — fine for short columns, ugly for URLs and titles where the wrap point lands inside a token.

For ad-hoc audits that show two or three fields side by side (e.g. `doi` vs. `url`, `title` vs. `journal`), use this dynamic-width plain-text helper instead. Each column expands to fit its longest value, so URLs and DOIs never break across lines:

```python
from citefinder import bib_to_table

df = bib_to_table(open("refs.bib").read())
fields = ["key", "doi", "url"]  # adjust to taste
rows = [r for r in df.iter_rows(named=True) if all(r.get(f) for f in fields[1:])]

widths = {f: max(len(str(r[f])) for r in rows + [{f: f}]) for f in fields}
sep = "+" + "+".join("-" * (widths[f] + 2) for f in fields) + "+"
hdr = "| " + " | ".join(f"{f:<{widths[f]}}" for f in fields) + " |"
print(f"{len(rows)} rows\n")
print(sep)
print(hdr)
print(sep)
for r in rows:
    print("| " + " | ".join(f"{str(r[f]):<{widths[f]}}" for f in fields) + " |")
print(sep)
```

Edit `fields` and the row-filter predicate for the columns you want. Keep this as a one-off rendering helper — don't promote it into a script unless the same audit shows up across many papers.
