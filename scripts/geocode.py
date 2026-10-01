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


YAHOO_URL = "https://map.yahooapis.jp/geocode/V1/geoCoder"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def yahoo_appid() -> str:
    """Yahoo!ジオコーダAPIの Client ID。GitHubでは秘密の保管庫(環境変数)から、パソコンでは config.json から読む。"""
    if os.environ.get("YAHOO_APPID"):
        return os.environ["YAHOO_APPID"].strip()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("yahoo_appid", "").strip()
    return ""


def numbers(text: str) -> list[str]:
    """住所の中の数字の並び(漢数字の丁目は除く)。番地が一致しているかの確認に使う。"""
    import unicodedata
    return re.findall(r"\d+", unicodedata.normalize("NFKC", text))


def yahoo_lookup(address: str, appid: str, session: requests.Session) -> list | None:
    """Yahoo!の住所検索で番地(地番)まで一致したときだけ [緯度, 経度, 1, "y"] を返す。それ以外は None。"""
    for attempt in range(3):
        try:
            resp = session.get(YAHOO_URL, params={"appid": appid, "query": address, "output": "json", "results": 1}, timeout=20)
            if resp.status_code == 403:
                raise PermissionError("Yahoo!から利用を断られました(Client IDを確認してください)")
            resp.raise_for_status()
            feats = resp.json().get("Feature") or []
            break
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("問い合わせに失敗")
    if not feats:
        return None
    f = feats[0]
    level = int(f.get("Property", {}).get("AddressMatchingLevel") or 0)
    found = f.get("Property", {}).get("Address", "")
    # 番地まで一致し(レベル5以上)、返ってきた住所の数字が問い合わせた住所の数字と同じときだけ使う
    # (「豊岡5-2」で問い合わせて「豊岡815-2」が返るような、別の番地への取り違えを防ぐ)
    if level < 5 or numbers(found) != numbers(address):
        return None
    lng, lat = map(float, f["Geometry"]["Coordinates"].split(","))
    return [round(lat, 6), round(lng, 6), 1, "y"]


def refine_with_yahoo(cache: dict, wanted: list[str], deadline: float | None) -> None:
    """国土地理院では町名までしか分からなかった住所(地番など)を、Yahoo!の住所検索で調べ直す。
    一度調べた住所は印("y-")を付けて覚えておき、次回からは問い合わせない。"""
    appid = yahoo_appid()
    if not appid:
        print("Yahoo!の Client ID が無いので、地番の調べ直しは行いません")
        return
    todo = [a for a in wanted if cache.get(a) and cache[a][2] == 0 and len(cache[a]) < 4]
    if not todo:
        return
    print(f"町名までしか分からなかった住所 {len(todo)}件を、Yahoo!の住所検索で調べ直します")
    session = requests.Session()
    better = tried = 0
    for addr in todo:
        if deadline and time.time() > deadline:
            break
        try:
            result = yahoo_lookup(addr, appid, session)
        except PermissionError as e:
            print(f"  [警告] {e}")
            break
        except RuntimeError:
            continue
        tried += 1
        if result:
            cache[addr] = result
            better += 1
        else:
            cache[addr] = cache[addr][:3] + ["y-"]
        if tried % 200 == 0:
            save_cache(cache)
            print(f"  {tried}/{len(todo)} 件確認(番地まで特定 {better}件)")
        time.sleep(0.2)  # 1日5万回までの決まりがあるので、控えめな速さで
    save_cache(cache)
    print(f"Yahoo!で調べ直し: {tried}件中 {better}件を番地まで特定できました")


def main() -> None:
    max_minutes = float(os.environ.get("GEOCODE_MAX_MINUTES", "0") or 0)
    deadline = time.time() + max_minutes * 60 if max_minutes > 0 else None

    cache = load_cache()
    wanted = sorted({p["geo"] for p in collect_all()["points"] if p.get("geo")})
    todo = [a for a in wanted if a not in cache]
    print(f"住所 {len(wanted)}件のうち、未変換 {len(todo)}件を調べます")
    if not todo:
        refine_with_yahoo(cache, wanted, deadline)
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
    refine_with_yahoo(cache, wanted, deadline)
    not_found = sum(1 for a in wanted if a in cache and cache[a] is None)
    rest = sum(1 for a in wanted if a not in cache)
    print(f"今回 {done}件を変換 / 通信エラー {failed}件 / 位置が見つからない住所 {not_found}件 / 次回へ持ち越し {rest}件")


if __name__ == "__main__":
    main()
