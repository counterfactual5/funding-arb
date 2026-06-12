#!/usr/bin/env python3
"""Generate zh-TW (Taiwan Traditional) doc sections from zh-CN in web doc articles.

For each article file under web/src/content/docs/articles/:
  - extract the `const zhCN: DocSection[] = [...]` block
  - convert with OpenCC s2twp (Taiwan standard + phrase mapping)
  - insert as `const zhTW: DocSection[] = [...]` before `const en`
  - point sectionsByLocale 'zh-TW' at zhTW

Idempotent: files that already define zhTW are skipped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from opencc import OpenCC

ARTICLES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "web/src/content/docs/articles"
)

cc = OpenCC("s2twp")


def process(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    if "const zhTW" in src:
        return "skip (zhTW exists)"

    m = re.search(
        r"(const zhCN: DocSection\[\] = \[.*?\n\]\n)(\nconst en: DocSection\[\])",
        src,
        re.S,
    )
    if not m:
        return "skip (no zhCN block)"

    zh_cn_block = m.group(1)
    zh_tw_block = cc.convert(zh_cn_block).replace(
        "const zhCN: DocSection[]", "const zhTW: DocSection[]", 1
    )

    out = src[: m.end(1)] + "\n" + zh_tw_block + src[m.end(1) :]
    out = out.replace("'zh-TW': zhCN,", "'zh-TW': zhTW,")
    if "'zh-TW': zhTW," not in out:
        return "ERROR: locale mapping not updated"
    path.write_text(out, encoding="utf-8")
    return "converted"


def main() -> int:
    ok = True
    for f in sorted(ARTICLES_DIR.glob("*.ts")):
        result = process(f)
        print(f"{f.name}: {result}")
        if result.startswith("ERROR"):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
