"""資料(PDF・エクセル・CSV・Word・ZIP)の中身から「いつ時点の一覧か」を読み取る。

自治体の一覧には「令和8年9月30日現在」「(2026年8月末現在)」「基準収受日:令和8年6月30日」
「R8.8.31現在」「2026/8/31現在」など、いろいろな書き方で日付が入っている。
資料に日付が書かれていない場合は、ファイル自体に記録されている保存日時を使う。

使い方:
    document_date(path, "pdf")  → ("2026/09/30", "資料に記載「令和8年9月30日終了時点」")
                                    資料から何も分からなければ None
"""
from __future__ import annotations

import calendar
import os
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 日付の書き方。全角→半角にそろえた(NFKC)文字に対して使う
_DATE_PATTERNS = [
    # 令和8年9月30日 / 令和8年(2026年)6月4日 / 令和8年8月末日 / R8年7月1日
    re.compile(r"(?:令和|(?<![A-Za-z])R)\s*(?P<r>\d{1,2}|元)\s*年\s*(?:\(\s*20\d\d\s*年?\s*\)\s*)?"
               r"(?P<m>\d{1,2})\s*月\s*(?:(?P<d>\d{1,2})\s*日|(?P<end>末日?))"),
    # R8.8.31
    re.compile(r"(?<![A-Za-z])R\s*(?P<r>\d{1,2})\s*\.\s*(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})(?!\d)"),
    # 2026年9月30日 / 2026年8月末
    re.compile(r"(?<!\d)(?P<y>20\d\d)\s*年\s*(?:\(\s*令和\s*\d{1,2}\s*年\s*\)\s*)?"
               r"(?P<m>\d{1,2})\s*月\s*(?:(?P<d>\d{1,2})\s*日|(?P<end>末日?))"),
    # 2026/9/30, 2026.9.30, 2026-09-30
    re.compile(r"(?<!\d)(?P<y>20\d\d)\s*[/.\-]\s*(?P<m>\d{1,2})\s*[/.\-]\s*(?P<d>\d{1,2})(?!\d)"),
]

# 日付のすぐ後ろに「現在」「時点」が付いているもの、または前に「基準日」などがあるものは、一覧の基準日とみなす
_AFTER_BASE = re.compile(r"\s*(?:終了\s*)?(?:時点|現在)")
_BEFORE_BASE = re.compile(r"(?:基準|時点|現在)[^\d\n]{0,8}$")
# 「令和8年10月5日(月) 14時17分 現在」(印刷・出力した日時)や「作成日:」「更新」などは、その次に信用する
_AFTER_MADE = re.compile(r"\s*(?:\([月火水木金土日]\))?\s*(?:\d{1,2}\s*[時:]\s*\d{1,2}\s*分?\s*)?"
                         r"(?:現在|時点|更新|作成|出力|公表|掲載)")
_BEFORE_MADE = re.compile(r"(?:更新|作成|出力|公表|掲載|調製)[^\d\n]{0,8}$")
# 「前回更新時(令和8年8月31日時点)」「令和8年7月1日改正前の条例」のように、一覧の日付ではないもの
_BEFORE_NOT = re.compile(r"前回[^\n]{0,8}$")
_AFTER_NOT = re.compile(r"\s*(?:\)\s*)?(?:改正|施行|より|以降|以前|から|まで)")

# 表の見出し行に出てくる言葉。PDFでは、これが出てくる行より上を「表題の部分」とみなす
_HEADER_WORDS = re.compile(r"(?:^|\s)(?:No\.?|番号)(?:\s|$)|所在地|名称|届出番号|届出年月日|許可番号|許可日|住所")


def _to_date(m: "re.Match") -> date | None:
    g = m.groupdict()
    try:
        if g.get("r") is not None:
            year = 2019 if g["r"] == "元" else 2018 + int(g["r"])
        else:
            year = int(g["y"])
        month = int(g["m"])
        day = calendar.monthrange(year, month)[1] if g.get("end") else int(g["d"])
        d = date(year, month, day)
    except (ValueError, TypeError):
        return None
    # 民泊の制度が始まる前や、未来すぎる日付は一覧の日付ではない
    if not (date(2018, 1, 1) <= d <= date.today() + timedelta(days=31)):
        return None
    return d


def _candidates(text: str, title: bool) -> list[tuple[int, int, date, str]]:
    """text の中の日付を (信用度, 出てきた位置, 日付, 原文) で返す。信用度は小さいほど確か。
    title=True のときは表題の部分なので、前後に何も付いていない日付も候補にする。
    """
    out = []
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            d = _to_date(m)
            if not d:
                continue
            before, after = text[max(0, m.start() - 16):m.start()], text[m.end():m.end() + 24]
            if _BEFORE_NOT.search(before):
                continue
            a = _AFTER_BASE.match(after) or _AFTER_MADE.match(after)
            b = _BEFORE_BASE.search(before) or _BEFORE_MADE.search(before)
            if _AFTER_BASE.match(after) or _BEFORE_BASE.search(before):
                rank = 1
            elif a or b:
                rank = 2
            elif title and not _AFTER_NOT.match(after):
                rank = 3
            else:
                continue
            # 根拠として見せる原文(「基準収受日:令和8年6月30日」「令和8年9月30日終了時点」など)
            start = m.start() - (len(before) - b.start()) if b and not a else m.start()
            quote = text[start:m.end() + (a.end() if a else 0)].strip()
            out.append((rank, m.start(), d, re.sub(r"\s+", " ", quote)))
    return out


def find_in_text(sections: list[tuple[str, bool]]) -> tuple[date, str] | None:
    """(テキスト, 表題の部分かどうか) の並びから、一覧の基準日として一番確かな日付を選ぶ。"""
    best = None
    for order, (text, title) in enumerate(sections):
        text = unicodedata.normalize("NFKC", text or "")
        for rank, pos, d, quote in _candidates(text, title):
            key = (rank, order, pos)
            if best is None or key < best[0]:
                best = (key, d, quote)
    return (best[1], best[2]) if best else None


# ---- 形式ごとに、日付が書かれていそうな部分のテキストを取り出す ----

def _pdf_sections(path: str) -> list[tuple[str, bool]]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        if not pdf.pages:
            return []
        first = pdf.pages[0].extract_text() or ""
        last = (pdf.pages[-1].extract_text() or "") if len(pdf.pages) > 1 else ""
    lines = first.split("\n")
    # 表の見出し行より上(最大6行)を表題の部分とする
    n = next((i for i, l in enumerate(lines[:8]) if _HEADER_WORDS.search(unicodedata.normalize("NFKC", l))), min(len(lines), 6))
    return [("\n".join(lines[:n]), True), (first, False), (last, False)]


def _excel_sections(path: str, fmt: str) -> list[tuple[str, bool]]:
    import pandas as pd

    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    title_parts, other_parts = [" ".join(sheets.keys())], []
    for df in list(sheets.values())[:10]:
        rows = [[str(c) for c in r if str(c) not in ("nan", "None", "")] for r in df.values.tolist()]
        # 3つ以上のマスが埋まった最初の行(=表の見出し行)より上を表題の部分とする
        n = next((i for i, r in enumerate(rows[:15]) if len(r) >= 3), min(len(rows), 6))
        title_parts += [" ".join(r) for r in rows[:n]]
        other_parts += [" ".join(r) for r in rows[n:n + 15] + rows[-5:]]
    return [("\n".join(title_parts), True), ("\n".join(other_parts), False)]


def _csv_sections(path: str) -> list[tuple[str, bool]]:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp932", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    lines = text.splitlines()
    return [("\n".join(lines[:3] + lines[-3:]), False)]


def _docx_sections(path: str) -> list[tuple[str, bool]]:
    import docx

    doc = docx.Document(path)
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    heads = [p.text for s in doc.sections for p in list(s.header.paragraphs) + list(s.footer.paragraphs)]
    # 目黒区のように、表題も表の1行目に入っている資料がある
    first_rows = [" ".join(dict.fromkeys(c.text for c in r.cells)) for t in doc.tables[:1] for r in t.rows[:2]]
    return [("\n".join(paras[:6] + heads), True), ("\n".join(first_rows + paras), False)]


# ---- 資料に日付が書かれていないときの、ファイルの保存日時 ----

def _parse_pdf_date(v) -> date | None:
    """PDFの日付 "D:20260907105015+09'00'" を日本時間の日付にする。"""
    if isinstance(v, bytes):
        v = v.decode("latin-1", "ignore")
    m = re.match(r"D?:?(\d{14})(Z|[+-]\d{2}'?\d{2}'?)?", str(v or ""))
    if not m:
        return None
    t = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    tz = m.group(2) or ""
    if tz == "Z":
        t = t.replace(tzinfo=timezone.utc).astimezone(JST)
    elif tz:
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz.replace("'", "")[3:5] or 0)
        t = t.replace(tzinfo=timezone(sign * timedelta(hours=hh, minutes=mm))).astimezone(JST)
    return t.date()


def _office_saved(path: str) -> date | None:
    """エクセル(xlsx)・Word(docx)の中に記録されている最終保存日時。"""
    try:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "ignore")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    m = re.search(r"<dcterms:modified[^>]*>([^<]+)<", core) or re.search(r"<dcterms:created[^>]*>([^<]+)<", core)
    if not m:
        return None
    try:
        t = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t.astimezone(JST) if t.tzinfo else t).date()


def file_saved_date(path: str, fmt: str) -> date | None:
    try:
        if fmt == "pdf":
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                meta = pdf.metadata or {}
            return _parse_pdf_date(meta.get("ModDate")) or _parse_pdf_date(meta.get("CreationDate"))
        if fmt in ("xlsx", "docx"):
            return _office_saved(path)
    except Exception:  # noqa: BLE001
        return None
    return None


def _fmt(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def document_date(path: str, fmt: str, zip_inner: list[str] | None = None) -> tuple[str, str] | None:
    """資料の日付と、その根拠を返す。ZIPのときは中のファイル(zip_inner)を調べる。"""
    if fmt == "zip":
        found = [document_date(p, os.path.splitext(p)[1].lstrip(".").lower()) for p in zip_inner or []]
        found = [f for f in found if f]
        if found:
            return combine_dates(found)
        try:
            with zipfile.ZipFile(path) as z:
                stamps = [date(*i.date_time[:3]) for i in z.infolist() if not i.is_dir()]
        except (zipfile.BadZipFile, OSError, ValueError):
            return None
        return (_fmt(max(stamps)), "資料に日付の記載がないため、ZIPの中のファイルの保存日") if stamps else None
    try:
        if fmt == "pdf":
            sections = _pdf_sections(path)
        elif fmt in ("xlsx", "xls"):
            sections = _excel_sections(path, fmt)
        elif fmt == "csv":
            sections = _csv_sections(path)
        elif fmt == "docx":
            sections = _docx_sections(path)
        else:
            sections = []
    except Exception:  # noqa: BLE001
        sections = []
    hit = find_in_text(sections)
    if hit:
        return _fmt(hit[0]), f"資料に記載「{hit[1]}」"
    saved = file_saved_date(path, fmt)
    if saved:
        return _fmt(saved), "資料に日付の記載がないため、ファイルに記録された保存日"
    return None


def combine_dates(found: list[tuple[str, str]]) -> tuple[str, str]:
    """複数のファイルを1つの一覧にまとめた場合は、一番古い日付を資料の日付とする(全部がその日以降の情報)。"""
    found = sorted(found)
    if found[0][0] == found[-1][0]:
        return found[0]
    return found[0][0], f"{len(found)}個のファイルのうち一番古いもの({found[0][1]})。新しいものは{found[-1][0]}"
