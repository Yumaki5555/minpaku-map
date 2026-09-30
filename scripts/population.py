"""東京都「住民基本台帳による世帯と人口(毎月)」から、
区ごとの最新人口(万人単位、小数点1桁)を取得する。

参照元: https://www.toukei.metro.tokyo.lg.jp/juukim/jm-index.htm
"""
from __future__ import annotations

import csv
import io
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://www.toukei.metro.tokyo.lg.jp/juukim/jm-index.htm"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) minpaku-map-updater/0.1"
}


def _find_latest_csv_url() -> str:
    resp = requests.get(INDEX_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    year_links = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        m = re.search(r"令和(\d+)年", text)
        if m and a.get("href"):
            year_links.append((int(m.group(1)), a["href"]))
    if not year_links:
        raise RuntimeError("年ページの一覧が見つかりませんでした")
    year_links.sort(key=lambda t: t[0], reverse=True)
    year_page_url = urljoin(INDEX_URL, year_links[0][1])

    resp2 = requests.get(year_page_url, headers=HEADERS, timeout=30)
    resp2.raise_for_status()
    soup2 = BeautifulSoup(resp2.content, "html.parser")

    csv_links = [
        urljoin(year_page_url, a["href"])
        for a in soup2.find_all("a")
        if a.get_text(strip=True).startswith("CSV_1") and a.get("href")
    ]
    if not csv_links:
        raise RuntimeError("月次CSVリンクが見つかりませんでした")
    return csv_links[-1]  # 一番新しく公表されている月


def fetch_population_by_ward(ward_names: set[str]) -> tuple[dict[str, float], str]:
    """区名 -> 人口(万人、小数1桁) の辞書と、参照したCSVのURLを返す。"""
    csv_url = _find_latest_csv_url()
    resp = requests.get(csv_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise RuntimeError("人口統計CSVが空でした")

    header = rows[0]
    try:
        idx_name = header.index("地域")
        idx_total = header.index("人口総数（日本人＋外国人）")
    except ValueError as e:
        raise RuntimeError(f"人口統計CSVの列構成が想定と違います: {e}")

    result: dict[str, float] = {}
    for r in rows[1:]:
        if len(r) <= max(idx_name, idx_total):
            continue
        name = r[idx_name].strip()
        if name in ward_names and name not in result:
            try:
                result[name] = round(int(r[idx_total]) / 10000, 1)
            except ValueError:
                continue
    return result, csv_url


if __name__ == "__main__":
    wards = {
        "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区", "江東区",
        "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区", "杉並区", "豊島区",
        "北区", "荒川区", "板橋区", "練馬区", "足立区", "葛飾区", "江戸川区",
    }
    data, url = fetch_population_by_ward(wards)
    print("参照元:", url)
    for k, v in data.items():
        print(k, v)
