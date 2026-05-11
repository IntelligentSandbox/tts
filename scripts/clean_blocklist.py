#!/usr/bin/env python
# TODO: Add duplicate checker, etc...
from pathlib import Path
import re

fn = "mod_blocklist-clean.txt"
content = ""
b = Path(input("Absolute path to block list: "))
p = b.parents[0]
with b.open() as f:
    for l in f:
        content += re.sub(r"\W+", "", l) + "\n"
p = Path(p)
p.mkdir(parents=True, exist_ok=True)
filepath = p / fn
with filepath.open("w", encoding="utf-8") as f:
    f.write(content)
