#!/usr/bin/env python3
"""Convert the Markdown guides in docs/guides/ to Word (.docx) files next to them.

Usage (from the repository root):  python3 scripts/convert_guides_to_docx.py [GUIDE.md ...]
Needs python-docx (pip install python-docx). Without arguments every docs/guides/*.md is converted.

Handles headings, bullet and numbered lists, block quotes, fenced code blocks (blank lines
included), inline **bold**, *italic*, `code` and [links](url), and embeds images whose files
exist (other images become a short "[Image: ...]" note). Tables are kept as plain text rows.
"""
import os
import re
import sys

from docx import Document
from docx.shared import Inches, Pt

INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*\s][^*]*\*)")
IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")


def add_inline(paragraph, text):
    """Add text to a paragraph, turning Markdown emphasis, code and links into Word runs."""
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Courier New"
        elif part.startswith("[") and "](" in part:
            label, url = part[1:-1].split("](", 1)
            paragraph.add_run(label)
            if not url.startswith("#"):
                paragraph.add_run(" ({0})".format(url))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def code_line(doc, line):
    paragraph = doc.add_paragraph(style="No Spacing")
    run = paragraph.add_run(line)  # an empty code line still gets a run (python-docx adds none for "")
    run.font.name = "Courier New"
    run.font.size = Pt(10)


def convert(md_path):
    docx_path = md_path[:-3] + ".docx"
    base = os.path.dirname(md_path)
    doc = Document()
    in_code = False
    with open(md_path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for raw in lines:
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            code_line(doc, line)
            continue
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        image = IMAGE.match(stripped)
        if heading:
            paragraph = doc.add_heading("", level=len(heading.group(1)))
            add_inline(paragraph, heading.group(2))
        elif image:
            alt, target = image.groups()
            path = os.path.normpath(os.path.join(base, target))
            if os.path.isfile(path):
                try:
                    doc.add_picture(path, width=Inches(6))
                    continue
                except Exception:  # noqa: BLE001 - an unreadable image becomes a note
                    pass
            doc.add_paragraph("[Image: {0}]".format(alt or os.path.basename(target)))
        elif re.match(r"^[-*]\s+", stripped):
            add_inline(doc.add_paragraph(style="List Bullet"), re.sub(r"^[-*]\s+", "", stripped))
        elif re.match(r"^\d+\.\s+", stripped):
            add_inline(doc.add_paragraph(style="List Number"), re.sub(r"^\d+\.\s+", "", stripped))
        elif stripped.startswith(">"):
            paragraph = doc.add_paragraph()
            add_inline(paragraph, stripped.lstrip("> ").strip())
            for run in paragraph.runs:
                run.italic = True
        elif re.match(r"^\|?\s*:?-{3,}", stripped):
            continue  # table separator row
        else:
            add_inline(doc.add_paragraph(), stripped)
    doc.save(docx_path)
    return docx_path


def main(argv):
    targets = argv[1:]
    if not targets:
        guides = os.path.join(os.getcwd(), "docs", "guides")
        if not os.path.isdir(guides):
            print("Directory not found: {0} (run from the repository root)".format(guides))
            return 2
        targets = sorted(os.path.join(guides, f) for f in os.listdir(guides) if f.endswith(".md"))
    failed = 0
    for md_path in targets:
        try:
            print("Saved {0}".format(convert(md_path)))
        except Exception as exc:  # noqa: BLE001 - report every guide, then fail
            failed += 1
            print("FAILED {0}: {1}".format(md_path, exc))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
