"""住所 → 地図上の位置(緯度・経度)への変換。

国土地理院の住所検索サービスを使う(無料・登録不要)。
一度調べた住所は geocode_cache.json に覚えておき、2回目以降は新しい住所だけ調べる。

使い方:
    python scripts/geocode.py            # まだ調べていない住所をすべて調べる
    GEOCODE_MAX_MINUTES=30 python ...    # 1回の実行時間に上限をつける(残りは次回へ持ち越し)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(__file__))
from addresses import collect_all  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(BASE_DIR, "geocode_cache.json")
API_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
HEADERS = {"User-Agent": "minpaku-map-updater/0.2 (+https://github.com/Yumaki5555/minpaku-map)"}

WORKERS = 3  # 同時に問い合わせる数(相手のサーバーに負担をかけないよう控えめに)
SAVE_EVERY = 500


def load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    with open(CACHE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache: dict) -> None:
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # 1住所1行にしておくと、Gitで変更点が見やすい
        f.write("{\n")
        items = sorted(cache.items())
        for i, (k, v) in enumerate(items):
            sep = "," if i < len(items) - 1 else ""
            f.write(f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v)}{sep}\n")
        f.write("}\n")
    os.replace(tmp, CACHE_PATH)


def lookup(address: str, session: requests.Session) -> list | None:
    """[緯度, 経度, 精度] を返す。精度 1 = 番地まで一致、0 = 町名あたりまでの大まかな位置。"""
    for attempt in range(3):
        try:
            resp = session.get(API_URL, params={"q": address}, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            results = resp.json()
            break
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("問い合わせに失敗")
    if not results:
        return None
    best = results[0]
    lng, lat = best["geometry"]["coordinates"]
    title = best["properties"].get("title", "")
    exact = 1 if re.search(r"[0-9０-９一二三四五六七八九十]+(番|号|-|−)", title) or re.search(r"\d+$", title) else 0
    return [round(lat, 6), round(lng, 6), exact]


def main() -> None:
    max_minutes = float(os.environ.get("GEOCODE_MAX_MINUTES", "0") or 0)
    deadline = time.time() + max_minutes * 60 if max_minutes > 0 else None

    cache = load_cache()
    wanted = sorted({p["geo"] for p in collect_all()["points"] if p.get("geo")})
    todo = [a for a in wanted if a not in cache]
    print(f"住所 {len(wanted)}件のうち、未変換 {len(todo)}件を調べます")
    if not todo:
        return

    lock = threading.Lock()
    done = 0
    failed = 0
    session = requests.Session()
    stop = threading.Event()

    def work(addr: str) -> None:
        nonlocal done, failed
        if stop.is_set():
            return
        if deadline and time.time() > deadline:
            stop.set()
            return
        try:
            result = lookup(addr, session)
        except RuntimeError:
            with lock:
                failed += 1
            return  # 通信エラーは覚えずに次回また調べる
        with lock:
            cache[addr] = result
            done += 1
            if done % SAVE_EVERY == 0:
                save_cache(cache)
                print(f"  {done}/{len(todo)} 件完了")
        time.sleep(0.1)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, todo))

    save_cache(cache)
    not_found = sum(1 for a in wanted if a in cache and cache[a] is None)
    rest = sum(1 for a in wanted if a not in cache)
    print(f"今回 {done}件を変換 / 通信エラー {failed}件 / 位置が見つからない住所 {not_found}件 / 次回へ持ち越し {rest}件")


if __name__ == "__main__":
    main()
