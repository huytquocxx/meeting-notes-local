"""Copy the newest defect list into a meeting folder as plain text, so the agent needs no shell or Excel reader.

  python defects.py FOLDER    write FOLDER/defects.txt

Reads the "Defect list folder" line in agent-config.md. Does nothing if that line or a matching file is missing.
File names must start with the date, for example "[Client] 20261030 - ...".
"""
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
CELL_TEXT = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# enough to check a defect number and what it is about; comments and long descriptions cost the agent ~100k tokens
KEEP = ("Defect ID", "Defect Status", "Defect Severity", "Module Name", "Summary")


def defect_folder():
    config = HERE / "agent-config.md"
    if not config.exists():
        return None
    found = re.search(r"Defect list folder: (.+)", config.read_text(encoding="utf-8"))
    return Path(found.group(1).strip()) if found else None


def newest_list(folder, meeting_date):
    """The defect list with the latest date on or before the meeting. Dates are YYYYMMDD."""
    dated = {}
    for path in folder.glob("*.xlsx"):
        found = re.search(r"\b(20\d{6})\b", path.name)
        if found and found.group(1) <= meeting_date:
            dated[found.group(1), path.stat().st_mtime] = path
    return dated[max(dated)] if dated else None


def sheet_rows(path):
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            for item in ET.fromstring(book.read("xl/sharedStrings.xml")):
                shared.append("".join(t.text or "" for t in item.iter(CELL_TEXT + "t")))
        for name in sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
            for row in ET.fromstring(book.read(name)).iter(CELL_TEXT + "row"):
                cells = {}  # column number: text. Blank cells are missing from the file, so use each cell's own column.
                for i, cell in enumerate(row):
                    value = cell.find(CELL_TEXT + "v")
                    if cell.get("t") == "s" and value is not None:
                        text = shared[int(value.text)]
                    elif value is not None:
                        text = value.text or ""
                    else:
                        text = "".join(t.text or "" for t in cell.iter(CELL_TEXT + "t"))
                    cells[column(cell.get("r")) if cell.get("r") else i] = " ".join(text.split())
                if any(cells.values()):
                    yield [cells.get(c, "") for c in range(max(cells) + 1)]


def column(ref):
    """Column number of a cell reference: "A7" is 0, "AB7" is 27."""
    number = 0
    for letter in re.match(r"[A-Z]+", ref).group():
        number = number * 26 + ord(letter) - 64
    return number - 1


def short_rows(rows):
    """Only the KEEP columns, found by the header row of each sheet. Rows before a header stay whole."""
    keep = None
    for cells in rows:
        if "Defect ID" in cells:
            keep = [cells.index(name) for name in KEEP if name in cells]
        picked = [cells[i] if i < len(cells) else "" for i in keep] if keep else cells
        yield " | ".join(picked)


def export(meeting_folder):
    """Write defects.txt into the meeting folder. Returns its path, or None if there is no list."""
    meeting_folder = Path(meeting_folder)
    folder = defect_folder()
    if not folder or not folder.exists():
        return None
    source = newest_list(folder, meeting_folder.name[:10].replace("-", ""))
    if not source:
        return None
    out = meeting_folder / "defects.txt"
    out.write_text(f"Source: {source.name}\n\n" + "\n".join(short_rows(sheet_rows(source))) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(export(sys.argv[1]))
