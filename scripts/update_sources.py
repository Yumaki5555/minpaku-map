"""毎月の自動更新パイプライン(フェーズ1: 東京23区)。

registry.csv の各行(自治体×種別)について、
1. 文書リンクから最新ファイルをダウンロード
2. 表として読み取り、件数を数える
3. 前回(registry.csv の「登録件数」列 / state/ の保存内容)と比較して差分を出す
4. 整形済みCSVを保存
5. registry.csv を最新の件数・日付で更新
6. 実行結果のサマリーを表示・保存

Googleマイマップへのインポートは対象外(このスクリプトは行わない)。
自治体ごとの列名の違いを完全に吸収するものではなく、
「件数の変化」と「生データの保存」までを自動化することが目的。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
from datetime import datetime
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
from extractors import (  # noqa: E402
    ExtractResult,
    extract,
    find_source_date,
    split_kani_shukusho,
    split_minpaku_ryokan,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "registry.csv")
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_DIR = os.path.join(BASE_DIR, "state")

TODAY = datetime.now().strftime("%Y%m%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) minpaku-map-updater/0.1"
}


def open_with_retry(path: str, mode: str, retries: int = 12, delay: float = 3.0, **kwargs):
    """Dropboxが同期中にファイルを一時的にロックすることがあるため、
    書き込み時に PermissionError が出ても少し待って再試行する。
    """
    last_err = None
    for attempt in range(retries):
        try:
            return open(path, mode, **kwargs)
        except PermissionError as e:  # noqa: PERF203
            last_err = e
            time.sleep(delay)
    raise last_err


def merge_split_rows(rows: list) -> list:
    """先頭の列だけが入った行を、先頭の列が空の直前の行に合体させる。"""
    def filled(c) -> bool:
        return str(c).strip() not in ("", "nan", "None")

    out: list = []
    for r in rows:
        r = list(r)
        if (
            out and r and filled(r[0]) and not any(filled(c) for c in r[1:])
            and not filled(out[-1][0]) and any(filled(c) for c in out[-1][1:])
        ):
            out[-1][0] = r[0]
            continue
        out.append(r)
    return out


def find_latest_link(row: dict) -> tuple[str, str] | None:
    """「元HP」のページを開き、リンクの文字が「リンク文字」(正規表現)に合う最初のファイルの (URL, 形式) を返す。"""
    page, pattern = row.get("元HP", "").strip(), row.get("リンク文字", "").strip()
    if not page or not pattern:
        return None
    found = page_links(page, pattern)
    if not found:
        print(f"  [警告] ページ内に「{pattern}」に合うファイルが見つかりませんでした: {page}")
        return None
    # 同じ名前でPDFとExcelの両方がある自治体(枚方市など)は、今までと同じ形式を優先する
    prev_fmt = infer_format(row.get("文書リンク", "")) or row.get("ファイル形式", "")
    same = [x for x in found if link_format(*x) == prev_fmt]
    found = same or found
    # 板橋区のように月ごとの一覧が並んでいる場合は、日付が一番新しいものを選ぶ
    dated = [(link_date(t) or link_date(u), u, t) for u, t in found]
    if sum(1 for d, _, _ in dated if d) >= 2:
        _, u, t = max(x for x in dated if x[0])
        return u, link_format(u, t)
    return found[0][0], link_format(*found[0])


def link_date(text: str) -> tuple[int, int] | None:
    """リンクの文字やURLから (西暦年, 月) を読み取る。読めなければ None。"""
    t = unicodedata.normalize("NFKC", text)
    m = re.search(r"令和(\d+)年(\d+)月", t)
    if m:
        return 2018 + int(m.group(1)), int(m.group(2))
    # 「000420123」のような通し番号を日付と取り違えないよう、前に数字が付いたものは除く
    m = re.search(r"(?<!\d)(20\d{2})[年/_-]?(0?[1-9]|1[0-2])(?!\d{3})", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def infer_years(links: list[tuple[str, str]], start_year: int) -> list[tuple[int, int] | None]:
    """新宿区の「旅館業8月分新規」のように年が書かれていないリンクに、年を補う。
    新しい月から順に並んでいるので、月が前より大きくなったら1年前に戻ったとみなす。
    """
    out, year, prev = [], start_year, 13
    for url, text in links:
        d = link_date(text) or link_date(url)
        if d:
            out.append(d)
            continue
        m = re.search(r"(\d{1,2})月", unicodedata.normalize("NFKC", text))
        if not m:
            out.append(None)
            continue
        month = int(m.group(1))
        if month > prev:
            year -= 1
        prev = month
        out.append((year, month))
    return out


def page_links(page: str, pattern: str) -> list[tuple[str, str]]:
    """ページ内で、リンクの文字が pattern(正規表現)に合うファイルの (URL, リンクの文字) を載っている順に返す。
    bodik(CKAN)のデータセットページの場合は、データの名前をリンクの文字として扱う。
    """
    m = re.match(r"https://data\.bodik\.jp/dataset/([^/?#]+)", page)
    try:
        if m:
            resp = requests.get("https://data.bodik.jp/api/3/action/package_show", params={"id": m.group(1)}, timeout=30)
            resp.raise_for_status()
            items = [(r["url"], r.get("name", "")) for r in resp.json()["result"]["resources"]]
        else:
            resp = requests.get(page, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")
            # 大阪市のように <base href> でリンクの基準の場所を指定しているページに対応する
            base_tag = soup.find("base", href=True)
            origin = urljoin(page, base_tag["href"]) if base_tag else page
            items = [(urljoin(origin, a["href"]), re.sub(r"\s+", " ", a.get_text(" ", strip=True)))
                     for a in soup.find_all("a", href=True)]
    except Exception as e:  # noqa: BLE001
        print(f"  [警告] 自治体のページを開けませんでした: {page} ({e})")
        return []
    out, seen = [], set()
    for url, text in items:
        if re.search(pattern, f"{text} {url}") and link_format(url, text) and url not in seen:
            out.append((url, text))
            seen.add(url)
    return out


def untab(rows: list) -> list:
    """目黒区のように、1つのマスにタブ区切りで全部の列が入っている表を、普通の表に直す。"""
    if rows and len(rows[0]) == 1 and "\t" in str(rows[0][0]):
        return [next(csv.reader([str(r[0])], delimiter="\t")) if r else [] for r in rows]
    return rows


def read_tables(path: str, zip_pattern: str = "") -> list[tuple[str, list[list[str]]]]:
    """CSV/Excel/PDF(ZIPの中身を含む)を (シート名, 行の一覧) の並びとして読む。"""
    if infer_format(path) == "zip":
        return [t for inner in unzip(path, zip_pattern) for t in read_tables(inner)]
    if infer_format(path) == "pdf":
        res = extract(path, "pdf")
        return [("", [list(res.header or [])] + [list(r) for r in res.rows])]
    if infer_format(path) in ("xlsx", "xls"):
        sheets = pd.read_excel(path, header=None, sheet_name=None, dtype=str)
        return [(name, [["" if pd.isna(c) else str(c).strip() for c in row] for row in df.values.tolist()])
                for name, df in sheets.items()]
    raw = open(path, "rb").read()
    text = ""
    encs = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp932")
    for enc in encs:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ""
    delim = "\t" if first.count("\t") > first.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    return [("", untab(rows))]


def unzip(path: str, pattern: str = "") -> list[str]:
    """ZIPを同じ場所に展開し、中の表ファイル(CSV/Excel/PDF)の場所を返す。pattern でファイル名を絞り込める。"""
    out_dir = path + "_files"
    os.makedirs(out_dir, exist_ok=True)
    files = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            if not (info.flag_bits & 0x800):
                try:
                    name = name.encode("cp437").decode("cp932")  # 日本のZIPはファイル名がShift_JISのことが多い
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            base = os.path.basename(name)
            if info.is_dir() or not infer_format(base) or infer_format(base) == "zip":
                continue
            if pattern and not re.search(pattern, base):
                continue
            dest = os.path.join(out_dir, safe_name(base))
            with z.open(info) as src, open_with_retry(dest, "wb") as dst:
                dst.write(src.read())
            files.append(dest)
    return sorted(files)


def match_col(name: str, header: list) -> int | None:
    """列名が同じ列を探す。千葉県のように「施設名称」と「施設名称１」の違いは同じ列とみなす。"""
    if name in header:
        return header.index(name)
    def base(x):
        return re.sub(r"[1１]$", "", squash(x)).replace("氏名", "名")
    for i, h in enumerate(header):
        if base(h) == base(name):
            return i
    return None


def combine_results(results: list) -> "ExtractResult":
    """複数のファイルから読んだ表を1つにまとめる。列の並びが違う場合は列名で合わせる。"""
    results = [r for r in results if r.header or r.rows]
    if not results:
        return ExtractResult(row_count=0, note="読み取れる表がありませんでした")
    header = list(results[0].header)
    rows = [list(r) for r in results[0].rows]
    for r in results[1:]:
        if list(r.header) == header or not header:
            rows += [list(x) for x in r.rows]
            continue
        idx = [match_col(h, r.header) for h in header]
        if all(i is None for i in idx) and len(r.header) == len(header):
            rows += [list(r.header)] + [list(x) for x in r.rows]  # 見出しの無い表は並び順が同じとみなす
            continue
        rows += [[(x[i] if i is not None and i < len(x) else "") for i in idx] for x in r.rows]
    notes = "・".join(sorted({r.note for r in results if r.note}))
    return ExtractResult(row_count=len(rows), header=header, rows=rows,
                         note=f"{len(results)}ファイルを結合({notes})", raw_text=results[0].raw_text)


def load_source(path: str, fmt: str, row: dict, kind: str) -> "ExtractResult":
    """ダウンロードしたファイルを表として読む。ZIPなら中のファイルをすべて読んでまとめる。"""
    sheet = row.get("シート指定", "").strip() or None
    if sheet == "*" and fmt in ("xlsx", "xls"):
        names = list(pd.read_excel(path, sheet_name=None, header=None, nrows=0).keys())
        return combine_results([extract(path, fmt, kind_hint=kind, sheet_hint=n) for n in names])
    if fmt == "zip":
        inner = unzip(path, row.get("ZIP内ファイル", "").strip())
        results = [extract(p, infer_format(p), kind_hint=kind, sheet_hint=sheet) for p in inner]
        return combine_results(results)
    return extract(path, fmt, kind_hint=kind, sheet_hint=sheet)


def facility_key(rec: dict) -> tuple:
    """施設名・所在地・方書を、表記ゆれ(全角/半角・空白)をそろえて並べたもの。"""
    def n(*names):
        for name in names:
            if rec.get(name):
                return squash(rec[name])
        return ""
    return (n("施設名称", "名称"), n("施設所在地", "所在地"), n("施設方書", "方書"))


def squash(v) -> str:
    """全角/半角・空白・改行の違いをそろえる(PDFから読むと途中に改行が入るため)。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v or "")))


KEY_COLS = ("施設名称", "名称", "施設所在地", "所在地")


def apply_monthly_diffs(row: dict, header: list, rows: list, dest_dir: str, base_url: str) -> tuple[list, str]:
    """「基準日の全体一覧」+「毎月の新規・廃止」で公表している自治体(台東区・墨田区・目黒区・港区など)向け。
    追加情報のファイルを順に読み、新規の施設を足し、廃止の施設を取り除く。
    廃止の照合は、許可番号があればそれで、無ければ施設名・所在地・方書で行う。
    """
    pattern = row.get("追加情報文字", "").strip()
    if not pattern or not header:
        return rows, ""
    if not any(h in KEY_COLS for h in header):
        return rows, ""  # 列の名前が分からないと照合できない(列名指定を使う)
    # 全体一覧の日付。URLに無ければ、ページ上のリンクの文字(「令和8年6月30日終了時点」など)から読む
    base_date = link_date(base_url)
    if not base_date and row.get("リンク文字"):
        base_date = next((link_date(t) for u, t in page_links(row["元HP"], row["リンク文字"]) if u == base_url), None)
    rows = [list(r) for r in rows]
    recs = [dict(zip(header, r)) for r in rows]
    added = removed = used = 0

    def same(x: dict, rec: dict) -> bool:
        no = squash(rec.get("許可番号"))
        if no and x.get("許可番号"):
            return squash(x["許可番号"]) == no
        key = facility_key(rec)
        return bool(key[0] or key[1]) and facility_key(x) == key

    links = page_links(row.get("元HP", ""), pattern)
    # 墨田区のように同じ内容がExcelとPDFの両方で載っている場合は、読み取りが確実なExcel/CSVだけを使う
    if any(link_format(u, t) != "pdf" for u, t in links):
        links = [(u, t) for u, t in links if link_format(u, t) != "pdf"]
    # ページの一番上(最新)の月は今年のものとみなして、そこから年をさかのぼる
    dates = infer_years(links, datetime.now().year)
    for (url, text), d in zip(links, dates):
        if url == base_url:
            continue
        if base_date and d and d <= base_date:
            continue  # 全体一覧より前の分は、すでに全体一覧に含まれている
        path = download(url, dest_dir, f"diff.{link_format(url, text)}")
        if not path:
            continue
        used += 1
        hint = "廃止" if re.search(r"廃止|haishi", text + url) else "新規"
        for sheet, table in read_tables(path, row.get("ZIP内ファイル", "").strip()):
            section = "廃止" if "廃止" in sheet else "新規" if "新規" in sheet else hint
            dh = None
            for r in table:
                cells = [str(c).strip() for c in r]
                joined = "".join(cells)
                if re.fullmatch(r"(環境)?[（(]?新規[)）]?", joined):
                    section, dh = "新規", None
                    continue
                if re.fullmatch(r"(環境)?[（(]?廃止[)）]?", joined):
                    section, dh = "廃止", None
                    continue
                if any(c in KEY_COLS for c in cells):
                    dh = cells
                    continue
                if not dh and len(cells) == len(header) and re.fullmatch(r"\d+", cells[0] or ""):
                    dh = header  # 新宿区のPDFのように見出し行が無いものは、全体一覧と同じ列の並びとみなす
                if not dh:
                    continue
                rec = dict(zip(dh, cells))
                if rec.get("業種") and "旅館" not in rec["業種"]:
                    continue  # 理容所・美容所などは対象外
                if section == "新規":
                    if any(same(x, rec) for x in recs):
                        continue
                    new = [rec.get(h, "") for h in header]
                    rows.append(new)
                    recs.append(dict(zip(header, new)))
                    added += 1
                else:
                    keep = [not same(x, rec) for x in recs]
                    removed += keep.count(False)
                    rows = [r for r, k in zip(rows, keep) if k]
                    recs = [x for x, k in zip(recs, keep) if k]
    return rows, f"(毎月の追加情報{used}件を反映: 新規+{added} 廃止-{removed})"


def infer_format(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in ("pdf", "xlsx", "xls", "csv", "docx", "zip"):
        if path.endswith("." + ext):
            return ext
    return ""


def text_format(text: str) -> str:
    """東京都のサイトのように拡張子のないリンクは、リンクの文字(「PDF」「CSV」など)から形式を判断する。"""
    t = unicodedata.normalize("NFKC", text).lower()
    for word, fmt in (("csv", "csv"), ("zip", "zip"), ("エクセル", "xlsx"), ("excel", "xlsx"),
                      ("xlsx", "xlsx"), ("pdf", "pdf")):
        if word in t:
            return fmt
    return ""


def link_format(url: str, text: str = "") -> str:
    return infer_format(url) or text_format(text)


def safe_name(s: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", s).strip()


def load_registry() -> list[dict]:
    with open(REGISTRY_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_registry(rows: list[dict], fieldnames: list[str]) -> None:
    with open_with_retry(REGISTRY_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def download(url: str, dest_dir: str, basename_hint: str) -> str | None:
    os.makedirs(dest_dir, exist_ok=True)
    parsed = urlparse(url)
    orig_name = os.path.basename(parsed.path) or basename_hint
    if not infer_format(orig_name) and infer_format(basename_hint):
        orig_name += "." + infer_format(basename_hint)
    fname = f"{TODAY}_{safe_name(orig_name)}"
    dest_path = os.path.join(dest_dir, fname)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"  [失敗] ダウンロードできませんでした: {e}")
        return None
    with open_with_retry(dest_path, "wb") as f:
        f.write(resp.content)
    return dest_path


def file_tag(row: dict) -> str:
    """自治体コードの末尾が「民特」(特区民泊)の行は、ファイル名に「_特区」を付けて区別する。"""
    return "_特区" if row["自治体コード"].endswith("民特") else ""


def remove_old_normalized(norm_dir: str, stem: str) -> None:
    """同じ資料の、今日より前の日付の整形済みCSVを削除する(最新だけ残す)。
    過去の版はGitの履歴に残るので、ここに溜めておく必要はない。
    """
    pattern = re.compile(r"^(\d{8})_" + re.escape(stem) + r"(_旅館ホテル等|_簡易宿所)?_normalized\.csv$")
    for name in os.listdir(norm_dir):
        m = pattern.match(name)
        if m and m.group(1) != TODAY:
            try:
                os.remove(os.path.join(norm_dir, name))
            except OSError as e:
                print(f"  [警告] 古いCSVを削除できませんでした: {name} ({e})")


def state_key(row: dict) -> str:
    return safe_name(f"{row['自治体コード']}")


def load_state(key: str) -> dict | None:
    path = os.path.join(STATE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(key: str, data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{key}.json")
    with open_with_retry(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def process_row(row: dict) -> dict:
    ward = row["自治体名"]
    kind = row["種別"]
    # ファイル名が毎回変わる自治体向け: 自治体のページから最新のファイルを探し直す
    latest = find_latest_link(row)
    if latest:
        if latest[0] != row.get("文書リンク", "").strip():
            print(f"[{ward}/{kind}] 新しいファイルを見つけました: {latest[0]}")
        row["文書リンク"] = latest[0]
        row["ファイル形式"] = latest[1]
    url = row.get("文書リンク", "").strip()
    fmt = (infer_format(url) or row.get("ファイル形式", "")) if url else ""
    row["ファイル形式"] = fmt
    key = state_key(row)

    summary = {"自治体コード": row["自治体コード"], "自治体名": ward, "種別": kind, "status": "", "detail": ""}
    row["実行日"] = datetime.now().strftime("%Y/%m/%d")

    if not url or not url.startswith("http"):
        summary["status"] = "対象外"
        summary["detail"] = "文書リンクが直接ファイルではないため今回はスキップ(手動確認が必要)"
        return summary

    dest_dir = os.path.join(DATA_DIR, safe_name(ward), safe_name(kind), "元データ")
    print(f"[{ward}/{kind}] ダウンロード中: {url}")
    # 神奈川県の旅館業のように、地域ごとに分かれた複数のファイルをまとめて1つの一覧として扱う
    if row.get("まとめ取得", "").strip() == "1" and row.get("リンク文字"):
        links = page_links(row["元HP"], row["リンク文字"])
        same = [x for x in links if link_format(*x) == fmt]
        targets = [(u, link_format(u, t)) for u, t in (same or links)]
    else:
        targets = [(url, fmt)]
    results, path = [], None
    for t_url, t_fmt in targets:
        if len(targets) > 1:
            print(f"[{ward}/{kind}] ダウンロード中: {t_url}")
        path = download(t_url, dest_dir, f"{key}.{t_fmt or 'bin'}")
        if not path:
            summary["status"] = "要確認"
            summary["detail"] = "ダウンロード失敗"
            return summary
        results.append(load_source(path, t_fmt, row, kind))
    result = results[0] if len(results) == 1 else combine_results(results)

    # 目黒区のように、1つのマスにタブ区切りで全部の列が入っている表を直す
    if result.header and len(result.header) == 1 and "\t" in str(result.header[0]):
        fixed = untab([result.header] + list(result.rows))
        result.header, result.rows = fixed[0], fixed[1:]

    # 新宿区旅館業のように見出し行が無い資料は、登録一覧の「列名指定」で列の名前を付ける
    names = [c.strip() for c in row.get("列名指定", "").split(",") if c.strip()]
    if names and result.header and "施設名称" not in result.header and len(result.header) == len(names):
        result.rows = [list(result.header)] + list(result.rows)
        result.header = names

    diff_rows, diff_note = apply_monthly_diffs(row, result.header, result.rows, dest_dir, url)
    if diff_note:
        result.rows, result.row_count = diff_rows, len(diff_rows)
        result.note += diff_note

    # 1つの資料に民泊と旅館業が混在している自治体(八尾市など)向け:
    # 種別列が見つかれば、この行の種別(民泊/旅館業)に該当する行だけに絞り込む
    if kind in ("民泊", "旅館業"):
        split = split_minpaku_ryokan(result.header, result.rows)
        if split:
            minpaku_rows, ryokan_rows = split
            picked = minpaku_rows if kind == "民泊" else ryokan_rows
            result.rows = picked
            result.row_count = len(picked)
            result.note += "(民泊/旅館業混在資料のため種別で絞り込み)"

    # 北区のように、1件が「住所の行」と「届出番号だけの行」の2行に分かれて読み取られる資料は1行にまとめる
    merged = merge_split_rows(result.rows)
    if len(merged) != len(result.rows):
        result.note += f"(2行に分かれた{len(result.rows) - len(merged)}件を1行にまとめた)"
        result.rows = merged
        result.row_count = len(merged)

    # 空行は数えない(横浜市のCSVには空行が多く含まれる)
    filled = [r for r in result.rows if any(str(c).strip() not in ("", "nan", "None") for c in r)]
    if len(filled) != len(result.rows):
        result.rows, result.row_count = filled, len(filled)

    # 旅館業以外の施設も入っている資料(中野区など)は、指定された列の値で絞り込む
    cond = row.get("絞り込み", "").strip()
    if cond and "=" in cond and result.header:
        col, val = cond.split("=", 1)
        if col in result.header:
            i = result.header.index(col)
            result.rows = [r for r in result.rows if i < len(r) and str(r[i]).strip() == val]
            result.row_count = len(result.rows)
            result.note += f"({cond} で絞り込み)"

    prev_state = load_state(key)
    prev_count = None
    if prev_state:
        prev_count = prev_state.get("row_count")
    elif row.get("登録件数", "").strip().isdigit():
        prev_count = int(row["登録件数"])

    diff_text = ""
    if prev_count is not None:
        diff = result.row_count - prev_count
        if diff > 0:
            diff_text = f"前回({prev_count}件)から+{diff}件"
        elif diff < 0:
            diff_text = f"前回({prev_count}件)から{diff}件"
        else:
            diff_text = "前回から変化なし"
    else:
        diff_text = "前回データなし(今回が基準)"

    # 整形済みCSVとして保存
    norm_dir = os.path.join(DATA_DIR, safe_name(ward), safe_name(kind), "整形済み")
    os.makedirs(norm_dir, exist_ok=True)
    norm_paths = []
    # 同じ自治体・種別に資料が2つある場合(民泊と特区民泊など)に、ファイル名がぶつからないようにする
    stem = f"{safe_name(ward)}_{safe_name(kind)}{file_tag(row)}"

    split = split_kani_shukusho(result.header, result.rows) if kind == "旅館業" else None
    if split:
        kani_rows, other_rows = split
        other_path = os.path.join(norm_dir, f"{TODAY}_{stem}_旅館ホテル等_normalized.csv")
        kani_path = os.path.join(norm_dir, f"{TODAY}_{stem}_簡易宿所_normalized.csv")
        for p, rows_ in ((other_path, other_rows), (kani_path, kani_rows)):
            with open_with_retry(p, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if result.header:
                    w.writerow(result.header)
                w.writerows(rows_)
            norm_paths.append(p)
        norm_path = f"{other_path} (旅館・ホテル等 {len(other_rows)}件) / {kani_path} (簡易宿所 {len(kani_rows)}件)"
    else:
        single_path = os.path.join(norm_dir, f"{TODAY}_{stem}_normalized.csv")
        with open_with_retry(single_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if result.header:
                w.writerow(result.header)
            w.writerows(result.rows)
        norm_paths.append(single_path)
        norm_path = single_path

    remove_old_normalized(norm_dir, stem)

    status = "OK"
    if result.row_count == 0:
        status = "要確認"
    elif prev_count is not None and prev_count > 0 and abs(result.row_count - prev_count) / prev_count > 0.5:
        status = "要確認(件数急変)"

    summary.update(
        {
            "status": status,
            "detail": f"{result.row_count}件 / {diff_text} / {result.note}",
            "row_count": result.row_count,
            "normalized_path": norm_path,
            "normalized_paths": norm_paths,
            "source_path": path,
        }
    )

    save_state(
        key,
        {
            "row_count": result.row_count,
            "date": TODAY,
            "header": result.header,
            "source_url": url,
            # 地図ページ作成(build_site.py)がどのCSVを読めばよいか分かるように記録する
            "normalized_files": [os.path.relpath(p, BASE_DIR).replace(os.sep, "/") for p in norm_paths],
        },
    )

    row["登録件数"] = str(result.row_count)

    source_date = find_source_date(result.raw_text, allow_unanchored=False) or find_source_date(
        row.get("文書タイトル"), allow_unanchored=True
    )
    if source_date:
        row["元データ日付"] = source_date
        summary["source_date"] = source_date
    # 見つからなかった場合は、前回までにわかっている値をそのまま残す

    return summary


def update_motodata_sheet(rows: list[dict]) -> None:
    """registry.csv の最新件数を区ごとに集計し、東京都の最新人口とあわせて
    「元データ」シートの 民泊/旅館/人口 列と、更新日時(J1)を書き換える。
    """
    import sheets_sync
    from population import fetch_population_by_ward

    totals: dict[str, dict[str, int]] = {}
    for row in rows:
        ward = row.get("自治体名", "")
        kind = row.get("種別", "")
        count_text = (row.get("登録件数") or "").strip()
        if not ward or not count_text.isdigit():
            continue
        count = int(count_text)
        entry = totals.setdefault(ward, {"民泊": 0, "旅館": 0})
        if kind == "民泊":
            entry["民泊"] += count
        elif kind == "旅館業":
            entry["旅館"] += count

    if not totals:
        print("[情報] 集計対象がなかったため「元データ」シートの更新をスキップしました")
        return

    try:
        population, pop_url = fetch_population_by_ward(set(totals.keys()))
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 東京都の人口統計の取得に失敗しました(民泊/旅館業の件数のみ更新します): {e}")
        population, pop_url = {}, None

    payload = []
    for ward, counts in totals.items():
        entry = {"区": ward, "民泊": counts["民泊"], "旅館": counts["旅館"]}
        if ward in population:
            entry["人口"] = population[ward]
        payload.append(entry)

    timestamp = datetime.now().strftime("%Y/%m/%d %H:%M")
    sheets_sync.update_motodata(payload, timestamp)
    msg = f"「元データ」シートを更新しました({len(payload)}区、{timestamp}時点)"
    if pop_url:
        msg += f" / 人口の参照元: {pop_url}"
    print(msg)


def main() -> None:
    rows = load_registry()
    fieldnames = list(rows[0].keys())

    only_codes = set(sys.argv[1:])
    if only_codes:
        rows_to_run = [r for r in rows if any(r["自治体コード"].startswith(c) for c in only_codes)]
        print(f"[テストモード] 指定された{len(rows_to_run)}件のみ実行します: {only_codes}")
    else:
        rows_to_run = rows

    results = []
    for row in rows_to_run:
        try:
            results.append(process_row(row))
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "自治体コード": row.get("自治体コード"),
                    "自治体名": row.get("自治体名"),
                    "種別": row.get("種別"),
                    "status": "エラー",
                    "detail": str(e),
                }
            )

    save_registry(rows, fieldnames)

    try:
        import sheets_sync

        sheets_sync.push_registry_to_sheet()
        print("Googleスプレッドシート(自治体マスタ)も最新化しました")
    except Exception as e:  # noqa: BLE001
        print(f"[警告] Googleスプレッドシートへの反映に失敗しました: {e}")

    try:
        update_motodata_sheet(rows)
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 「元データ」シートの更新に失敗しました: {e}")

    print("\n===== 実行サマリー =====")
    for r in results:
        print(f"{r['自治体名']} / {r['種別']} : {r['status']} - {r.get('detail','')}")

    # 記録は公開されるので、パソコンのフォルダの場所(ユーザー名を含む)は書かず、
    # 「data/…」のようにプロジェクト内での場所だけを残す
    results = strip_base(results)

    summary_path = os.path.join(BASE_DIR, "state", f"summary_{TODAY}.json")
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open_with_retry(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nサマリーを保存しました: {summary_path}")

    write_report(results)


def strip_base(obj):
    """文字列の中の BASE_DIR(プロジェクトフォルダの場所)を取り除き、区切りを / にそろえる。"""
    if isinstance(obj, str):
        return obj.replace(BASE_DIR + os.sep, "").replace(os.sep, "/") if BASE_DIR in obj else obj
    if isinstance(obj, list):
        return [strip_base(x) for x in obj]
    if isinstance(obj, dict):
        return {k: strip_base(v) for k, v in obj.items()}
    return obj


def write_report(results: list[dict]) -> None:
    """今回の実行結果を、人が読みやすい日本語のレポートにして
    プロジェクト直下の「最新の実行結果.txt」に書き出す(毎回上書き)。
    """
    now_text = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    lines = [f"民泊マップ 自動更新レポート({now_text}実行)", "=" * 40, ""]

    changed = [r for r in results if r.get("status") == "OK" and "変化なし" not in r.get("detail", "")]
    unchanged = [r for r in results if r.get("status") == "OK" and "変化なし" in r.get("detail", "")]
    need_check = [r for r in results if r.get("status") not in ("OK", "対象外")]
    skipped = [r for r in results if r.get("status") == "対象外"]

    lines.append(f"【変化があった区・種別】 {len(changed)}件")
    for r in changed:
        lines.append(f"  ・{r['自治体名']} {r['種別']}: {r.get('detail','')}")
        for p in r.get("normalized_paths") or ([r["normalized_path"]] if r.get("normalized_path") else []):
            lines.append(f"      → {p}")
    if not changed:
        lines.append("  (なし)")

    lines.append("")
    lines.append(f"【変化なし】 {len(unchanged)}件")
    for r in unchanged:
        lines.append(f"  ・{r['自治体名']} {r['種別']}")

    lines.append("")
    lines.append(f"【要確認・失敗】 {len(need_check)}件")
    for r in need_check:
        lines.append(f"  ・{r['自治体名']} {r['種別']}: {r.get('detail','')}")
    if not need_check:
        lines.append("  (なし)")

    lines.append("")
    lines.append(f"【手動確認が必要(自動取得なし)】 {len(skipped)}件")
    for r in skipped:
        lines.append(f"  ・{r['自治体名']} {r['種別']}")
    if not skipped:
        lines.append("  (なし)")

    lines.append("")
    lines.append("変化があった区だけ、Google My Mapsで対象レイヤーを開き、上記のCSVをインポートしてください。")

    report_path = os.path.join(BASE_DIR, "最新の実行結果.txt")
    with open_with_retry(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"レポートを保存しました: {report_path}")


if __name__ == "__main__":
    main()
