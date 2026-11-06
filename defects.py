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
                cells = []
                for cell in row:
                    value = cell.find(CELL_TEXT + "v")
                    if cell.get("t") == "s" and value is not None:
                        cells.append(shared[int(value.text)])
                    elif value is not None:
                        cells.append(value.text or "")
                    else:
                        cells.append("".join(t.text or "" for t in cell.iter(CELL_TEXT + "t")))
                if any(cells):
                    yield " | ".join(" ".join(c.split()) for c in cells)


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
    out.write_text(f"Source: {source.name}\n\n" + "\n".join(sheet_rows(source)) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(export(sys.argv[1]))
