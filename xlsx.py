"""Minimal stdlib .xlsx reader/writer (first sheet only)."""
import datetime as dt
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

EPOCH = dt.date(1899, 12, 30)
DATE_IDS = set(range(14, 23)) | {45, 46, 47}


def _t(e):  # local tag name
    return e.tag.rsplit("}", 1)[-1]


def _is_date_code(code):
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code)
    return code.lower() != "general" and re.search(r"[dmy]", code, re.I) is not None


def _col(ref):  # "AB5" -> (27, 4) zero-based
    m = re.match(r"([A-Z]+)(\d+)", ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1, int(m.group(2)) - 1


def read_rows(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        parse = lambda n: ET.fromstring(z.read(n))
        # locate first sheet in workbook order
        path = "xl/worksheets/sheet1.xml"
        try:
            wb, rels = parse("xl/workbook.xml"), parse("xl/_rels/workbook.xml.rels")
            sheet = next(e for e in wb.iter() if _t(e) == "sheet")
            rid = next(v for k, v in sheet.attrib.items() if k.endswith("}id"))
            tgt = next(r.get("Target") for r in rels if r.get("Id") == rid)
            path = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
        except (KeyError, StopIteration):
            pass
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in parse("xl/sharedStrings.xml"):
                shared.append("".join(t.text or "" for t in si.iter() if _t(t) == "t"
                                      and _t_parent_ok(si, t)))
        date_xf = []  # per cellXfs index: is date?
        if "xl/styles.xml" in z.namelist():
            st = parse("xl/styles.xml")
            codes = {int(n.get("numFmtId")): n.get("formatCode", "")
                     for n in st.iter() if _t(n) == "numFmt"}
            xfs = next((e for e in st if _t(e) == "cellXfs"), [])
            for xf in xfs:
                i = int(xf.get("numFmtId", 0))
                date_xf.append(i in DATE_IDS or (i in codes and _is_date_code(codes[i])))
        rows = {}
        for c in parse(path).iter():
            if _t(c) != "c":
                continue
            x, y = _col(c.get("r"))
            t = c.get("t")
            kids = {_t(k): k for k in c}
            v = kids["v"].text if "v" in kids and kids["v"].text is not None else None
            if t == "inlineStr" and "is" in kids:
                val = "".join(e.text or "" for e in kids["is"].iter() if _t(e) == "t")
            elif v is None:
                val = ""
            elif t == "s":
                val = shared[int(v)]
            elif t == "b":
                val = "TRUE" if v.strip() == "1" else "FALSE"
            elif t in ("str", "e"):
                val = v
            else:
                s = int(c.get("s", 0))
                try:
                    f = float(v)
                except ValueError:
                    f = None
                if f is None:
                    val = v
                elif s < len(date_xf) and date_xf[s]:
                    val = (EPOCH + dt.timedelta(days=int(f // 1))).isoformat()
                else:
                    val = str(int(f)) if f.is_integer() else repr(f)
            rows.setdefault(y, {})[x] = val
        out = []
        for y in range(max(rows) + 1 if rows else 0):
            r = rows.get(y, {})
            line = [r.get(x, "") for x in range(max(r) + 1 if r else 0)]
            while line and line[-1] == "":
                line.pop()
            out.append(line)
        return out
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, IndexError, AttributeError):
        raise ValueError("not a valid .xlsx file")


def _t_parent_ok(si, t):  # skip phonetic runs (<rPh><t>)
    return t not in [x for rp in si if _t(rp) == "rPh" for x in rp.iter()]


def _name(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_rows(path, rows):
    sst, idx, body = [], {}, []
    for y, row in enumerate(rows, 1):
        cells = []
        for x, v in enumerate(row):
            ref = f"{_name(x)}{y}"
            if v is None or v == "":
                continue
            if isinstance(v, dt.date):  # datetime is a subclass; use its date
                n = (v.date() if isinstance(v, dt.datetime) else v) - EPOCH
                cells.append(f'<c r="{ref}" s="1"><v>{n.days}</v></c>')
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(f'<c r="{ref}"><v>{v!r}</v></c>')
            else:
                v = str(v)
                if v not in idx:
                    idx[v] = len(sst)
                    sst.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{idx[v]}</v></c>')
        body.append(f'<row r="{y}">{"".join(cells)}</row>')
    H = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    P = "http://schemas.openxmlformats.org/package/2006/relationships"
    rel = lambda i, t, tg: f'<Relationship Id="{i}" Type="{R}/{t}" Target="{tg}"/>'
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    parts = {
        "[Content_Types].xml": H + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{ct}sheet.main+xml"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{ct}worksheet+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{ct}styles+xml"/>'
            f'<Override PartName="/xl/sharedStrings.xml" ContentType="{ct}sharedStrings+xml"/></Types>',
        "_rels/.rels": H + f'<Relationships xmlns="{P}">{rel("rId1", "officeDocument", "xl/workbook.xml")}</Relationships>',
        "xl/workbook.xml": H + f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>'
            '<sheet name="Inventar" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": H + f'<Relationships xmlns="{P}">'
            + rel("rId1", "worksheet", "worksheets/sheet1.xml") + rel("rId2", "styles", "styles.xml")
            + rel("rId3", "sharedStrings", "sharedStrings.xml") + "</Relationships>",
        "xl/styles.xml": H + f'<styleSheet xmlns="{M}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/sharedStrings.xml": H + f'<sst xmlns="{M}" count="{len(sst)}" uniqueCount="{len(sst)}">'
            + "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in sst) + "</sst>",
        "xl/worksheets/sheet1.xml": H + f'<worksheet xmlns="{M}"><sheetData>{"".join(body)}</sheetData></worksheet>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in parts.items():
            z.writestr(n, d)
