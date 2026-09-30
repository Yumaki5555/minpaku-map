"""整形済みCSVから、地図に載せる「施設名・住所・種別・日付」を取り出す。

自治体ごとに列名や表の形がばらばらなので、次の順で住所の列を探す。
1. 列名に「所在地」「住所」を含む列(営業者・事業者・管理業者の住所は除く)
2. 見つからなければ、中身が住所らしい列(「丁目」「番」「1-2-3」などを含む)
「丁目・番・号」が別の列に分かれている自治体は、つなげて1つの住所にする。

個人名・電話番号は取り出さない(地図には施設名・住所・種別・日付だけを載せる)。

単体で実行すると、自治体ごとに「住所を取り出せた件数」を表示する:
    python scripts/addresses.py
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "registry.csv")
STATE_DIR = os.path.join(BASE_DIR, "state")
DATA_DIR = os.path.join(BASE_DIR, "data")
POINTS_DIR = os.path.join(STATE_DIR, "points")

PREF_BY_CODE = {"13": "東京都", "27": "大阪府"}

ADDR_WORDS = ("所在地", "住所", "町丁目", "location")
OWNER_WORDS = ("営業者", "事業者", "管理業者", "申請者", "開設者", "経営者", "代表者", "連絡先")
NAME_WORDS = ("施設名", "名称", "施 設 名")
DATE_WORDS = ("届出年月日", "届出日", "届出受理日", "許可日", "許可年月日", "許可開始", "確認", "開始日", "開始年月日")

# 届出番号(民泊)・認定番号(特区民泊)・許可番号(旅館業)の列
NO_WORDS = ("届出番号", "認定番号", "許可番号", "確認番号", "許可・確認・届出番号", "届 出 番 号")
MINPAKU_NO = re.compile(r"第?M\d{9}号?")

FOREIGN_LABELS = {"English", "中文", "한국어", "繁體中文", "简体中文"}

ADDR_LIKE = re.compile(r"(丁目|番地|[0-9０-９]番|[0-9０-９]号|[0-9０-９]+[-ー‐−－][0-9０-９])")
# 住所の番地までを切り出す(後ろの建物名・部屋番号を落として、同じ建物をまとめて調べられるようにする)
HOUSE_NO = re.compile(r"^(.*?(?:[0-9]+|[一二三四五六七八九十]+丁目?)(?:丁目|丁|番地|番|号|[-ー‐−－の]|[0-9]+)*)")


def clean(s: str | None) -> str:
    s = (s or "").replace("\r", "").replace("\n", "")
    return "" if s.strip().lower() in ("nan", "none", "-", "―", "／") else s.strip()


def norm(s: str) -> str:
    """全角英数を半角にし、空白を取り除く。"""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s)


def has_word(header: str, words) -> bool:
    h = norm(header).lower()
    return any(norm(w).lower() in h for w in words)


def format_date(v: str) -> str:
    v = clean(v)
    if not v:
        return ""
    if re.fullmatch(r"\d{5}(\.0)?", v):  # Excelの日付(通し番号)
        d = date(1899, 12, 30) + timedelta(days=int(float(v)))
        return d.strftime("%Y/%m/%d")
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", v)
    if m:
        return f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
    return norm(v)


def read_rows(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f)]
    # 目黒区のように、1つのセルの中にタブ区切りで全列が入っているもの
    if rows and len(rows[0]) == 1 and "\t" in rows[0][0]:
        rows = [next(csv.reader([r[0]], delimiter="\t")) if r else [] for r in rows]
    return rows


def find_col(header: list[str], words, exclude=OWNER_WORDS) -> int | None:
    for i, h in enumerate(header):
        if h and has_word(h, words) and not has_word(h, exclude):
            return i
    return None


def guess_addr_col(rows: list[list[str]]) -> int | None:
    width = max((len(r) for r in rows), default=0)
    best, best_score = None, 0.3
    for i in range(width):
        vals = [clean(r[i]) for r in rows if i < len(r) and clean(r[i])]
        if not vals:
            continue
        score = sum(1 for v in vals if ADDR_LIKE.search(v) and not v.startswith("第")) / len(rows)
        if score > best_score:
            best, best_score = i, score
    return best


def build_address(row: list[str], cols: dict) -> str:
    def cell(i):
        return clean(row[i]) if i is not None and i < len(row) else ""

    addr = cell(cols["addr"])
    if cols.get("ban") is not None:  # 「丁目」「番」「号」が別の列
        chome, ban, go = cell(cols.get("chome")), cell(cols["ban"]), cell(cols.get("go"))
        if chome:
            addr += norm(chome) + ("" if chome.endswith("丁目") else "丁目")
        if ban:
            addr += f"{norm(ban)}番"
        if go:
            addr += f"{norm(go)}号"
    return addr


def prefix_for(a_ns: str, pref: str, city: str) -> str:
    """住所(空白なし)の頭に足りない都道府県名・市区名を返す。"""
    if a_ns.startswith(pref):
        return ""
    if city and a_ns.startswith(city):
        return pref
    if pref == "大阪府" and (not city or a_ns.startswith("大阪市")):
        return pref  # 府全体の資料は市町村名から始まっている
    return pref + city


def make_point(addr: str, pref: str, city: str) -> tuple[str, str]:
    """(表示用の住所, 位置を調べるための住所) を返す。"""
    spaced = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", addr)).strip()
    spaced = re.sub(r"^〒?\d{3}-\d{4}\s*", "", spaced)
    a_ns = spaced.replace(" ", "")
    head = prefix_for(a_ns, pref, city)
    # 番地の後ろの建物名・部屋番号を落とす。「湯島2-31-7 2階」のような空白の区切りも尊重する
    m = HOUSE_NO.match(spaced)
    house = (m.group(1) if m else spaced).replace(" ", "")
    return head + spaced, head + house


def category(kind: str, code: str, path: str, row: list[str]) -> str:
    joined = " ".join(norm(c) for c in row)
    if kind == "民泊":
        if code.endswith("民特") or "特区" in joined:
            return "特区民泊"
        return "民泊"
    if "簡易宿所" in os.path.basename(path) or ("簡易宿所" in joined and "旅館ホテル等" not in os.path.basename(path)):
        return "簡易宿所"
    return "旅館・ホテル"


def header_cols(header: list[str]) -> dict | None:
    """見出し行から各列の位置を調べる。住所の列が無ければ None。"""
    addr = find_col(header, ADDR_WORDS)
    if addr is None:
        return None
    cols = {"addr": addr}
    for key, word in (("ban", "番"), ("go", "号"), ("chome", "丁目")):
        for i, h in enumerate(header):
            if i != addr and clean(h) and norm(h) == word:
                cols[key] = i
    cols["name"] = find_col(header, NAME_WORDS, exclude=OWNER_WORDS + ("カナ", "ビル", "商号"))
    cols["date"] = find_col(header, DATE_WORDS, exclude=("番号",))
    cols["no"] = find_col(header, NO_WORDS, exclude=("管理業", "登録番号", "郵便", "電話"))
    return cols


# 大田区のように「番号 施設名 所在地」が空白区切りで1つのマスに入っている行
COMBINED = re.compile(r"^\s*\d+\s+(.*?)\s+(\S*?(?:丁目|番地|番)\S*)\s*$")


def is_combined_header(cell: str) -> bool:
    return has_word(cell, ("所在地",)) and has_word(cell, ("名称",)) and " " in cell.strip()


def parse_file(path: str, reg: dict) -> tuple[list[dict], int]:
    """1つのCSVから地図用の点を取り出す。戻り値は(点の一覧, データ行数)。"""
    code, muni, kind = reg["自治体コード"], reg["自治体名"], reg["種別"]
    pref = PREF_BY_CODE.get(code[:2], "")
    city = "" if muni == pref else muni

    rows = read_rows(path)
    if not rows:
        return [], 0

    cols = header_cols(rows[0])
    body = rows[1:]
    if cols is None and not is_combined_header(rows[0][0]):
        # 新宿区旅館業のように1行目から中身が始まっているもの
        body = rows
        addr_col = guess_addr_col(body)
        if addr_col is None:
            return [], len(body)
        cols = {"addr": addr_col, "name": addr_col - 1 if addr_col > 0 else None, "date": None}

    pts, n = [], 0

    def add(addr: str, name: str, dt: str, r: list[str], no: str = "") -> None:
        full, geo = make_point(addr, pref, city)
        if not no:
            # 番号の列が見つからなくても、民泊の届出番号(M+9桁)の形のマスがあれば使う
            no = next((norm(c) for c in r if MINPAKU_NO.fullmatch(norm(clean(c)))), "")
        pts.append({"name": norm(name), "addr": full, "geo": geo, "date": dt, "no": no,
                    "cat": category(kind, code, path, r)})

    for r in body:
        if not any(clean(c) for c in r):
            continue
        # 大阪府の特区民泊のように、同じ施設を英語・中国語・韓国語でも載せている行は飛ばす
        if any(norm(c) in FOREIGN_LABELS for c in r):
            continue
        # 途中のページで見出し行がもう一度出てきたら、列の位置を読み直す
        if find_col(r, ADDR_WORDS) is not None and find_col(r, NAME_WORDS + ("番号", "No")) is not None:
            if not is_combined_header(r[0]):
                cols = header_cols(r) or cols
            continue
        m = COMBINED.match(r[0]) if cols is None or cols["addr"] > 0 else None
        if m:
            n += 1
            add(m.group(2), m.group(1), "", r)
            continue
        if cols is None:
            multi = [x for x in body if sum(1 for c in x if clean(c)) >= 3]
            addr_col = guess_addr_col(multi) if multi else None
            if addr_col is None:
                n += 1
                continue
            cols = {"addr": addr_col, "name": addr_col - 1 if addr_col > 0 else None, "date": None}
        addr = build_address(r, cols)
        # 見出しの2段目(「商号、名称又は氏名」など)や外国語の見出し行を飛ばす
        if not addr or not re.search(r"[0-9０-９一二三四五六七八九十]", addr) or has_word(addr, ADDR_WORDS):
            if addr and not has_word(addr, ADDR_WORDS) and not re.search(r"[A-Za-z]{4}", addr):
                n += 1  # 住所らしくない(番地なし等)けれどデータ行
            continue
        n += 1
        nc, dc, oc = cols.get("name"), cols.get("date"), cols.get("no")
        name = clean(r[nc]) if nc is not None and nc < len(r) else ""
        dt = format_date(r[dc]) if dc is not None and dc < len(r) else ""
        no = norm(clean(r[oc])) if oc is not None and oc < len(r) else ""
        add(addr, name, dt, r, no)
    return pts, n


def latest_files(reg: dict) -> list[str]:
    """その行の最新の整形済みCSV。state/に記録があればそれを使い、無ければフォルダから探す。"""
    state_path = os.path.join(STATE_DIR, f"{reg['自治体コード']}.json")
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
        files = [os.path.join(BASE_DIR, p) for p in st.get("normalized_files", [])]
        if files and all(os.path.exists(p) for p in files):
            return files
    folder = os.path.join(DATA_DIR, reg["自治体名"], reg["種別"], "整形済み")
    found = sorted(glob.glob(os.path.join(folder, "*_normalized.csv")))
    if not found:
        return []
    tag = "_特区" if reg["自治体コード"].endswith("民特") else ""
    found = [p for p in found if ("_特区_" in os.path.basename(p)) == bool(tag)]
    if not found:
        return []
    newest = max(os.path.basename(p)[:8] for p in found)
    return [p for p in found if os.path.basename(p).startswith(newest)]


def load_registry() -> list[dict]:
    with open(REGISTRY_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def collect_all(save: bool = False) -> dict:
    """全自治体の点を集める。

    整形済みCSV(個人名・電話番号を含むので公開しない)が手元に無い自治体は、
    前回取り出しておいた控え(state/points/、施設名・住所・種別・日付のみ)を使う。
    save=True のときは、今回取り出せた分で控えを上書きする。
    """
    points, stats = [], {}
    os.makedirs(POINTS_DIR, exist_ok=True)
    for reg in load_registry():
        code = reg["自治体コード"]
        backup = os.path.join(POINTS_DIR, f"{code}.json")
        pts_code, total = [], 0
        for path in latest_files(reg):
            pts, n = parse_file(path, reg)
            pts_code.extend(pts)
            total += n
        if pts_code:
            if save:
                with open(backup, "w", encoding="utf-8") as f:
                    json.dump(pts_code, f, ensure_ascii=False, indent=0)
        elif os.path.exists(backup):
            with open(backup, encoding="utf-8") as f:
                pts_code = json.load(f)
            total = len(pts_code)
        for p in pts_code:
            p["code"] = code
        points.extend(pts_code)
        stats[code] = {"rows": total, "with_addr": len(pts_code)}
    return {"points": points, "stats": stats}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    res = collect_all()
    regs = {r["自治体コード"]: r for r in load_registry()}
    for code, s in res["stats"].items():
        r = regs[code]
        mark = "" if s["rows"] and s["with_addr"] >= s["rows"] * 0.95 else "  ←要確認"
        print(f"{r['自治体名']:<6}{r['種別']:<4}{code:<11} 登録{r['登録件数']:>5} / 行{s['rows']:>5} / 住所{s['with_addr']:>5}{mark}")
    print(f"合計 {len(res['points'])} 件 / 住所の種類 {len({p['geo'] for p in res['points']})}")
    import random

    random.seed(1)
    for p in random.sample(res["points"], 25):
        print(p["code"], p["cat"], p["name"][:15], "|", p["addr"][:40], "|", p["geo"], "|", p["date"])
