"""registry.csv と、Googleスプレッドシート上の「自治体マスタ」シートを
同期するための小さなクライアント。

スプレッドシート側には Apps Script(AppsScript_Code.gs)をWebアプリとして
デプロイしてあり、そのURLに対して合言葉(secret)付きでアクセスする。
"""
from __future__ import annotations

import csv
import json
import os
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
REGISTRY_PATH = os.path.join(BASE_DIR, "registry.csv")


def load_config() -> dict:
    # GitHub Actions では合言葉を環境変数(Secrets)から受け取る
    url = os.environ.get("SHEETS_WEBAPP_URL")
    secret = os.environ.get("SHEETS_SECRET")
    if url and secret:
        return {"sheets_webapp_url": url, "sheets_secret": secret}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _post(action: str, **payload) -> dict:
    cfg = load_config()
    body = {"secret": cfg["sheets_secret"], "action": action, **payload}
    # Apps Script は結果を別のURLに転送して返す。requests の自動転送だと
    # 404 になることがあるため、転送先は自分で読みに行く
    # 一時的に 404 が返ることがあるので、3回までやり直す
    for attempt in range(3):
        resp = requests.post(cfg["sheets_webapp_url"], json=body, timeout=60, allow_redirects=False)
        if resp.status_code in (301, 302, 303) and resp.headers.get("location"):
            resp = requests.get(resp.headers["location"], timeout=60)
        if resp.ok:
            break
        time.sleep(5 * (attempt + 1))
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"sheets_sync failed: {data.get('error')}")
    return data


def read_all() -> list[dict]:
    cfg = load_config()
    resp = requests.get(
        cfg["sheets_webapp_url"],
        params={"secret": cfg["sheets_secret"], "action": "read_all"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"sheets_sync failed: {data.get('error')}")
    return data["rows"]


def replace_all(rows: list[dict]) -> None:
    _post("replace_all", rows=rows)


def update_rows(rows: list[dict]) -> None:
    _post("update_rows", rows=rows)


def update_motodata(rows: list[dict], timestamp: str) -> None:
    """「元データ」シートの民泊・旅館・人口列と、更新日時(J1)を書き換える。
    rows は [{"区": "千代田区", "民泊": 56, "旅館": 173, "人口": 7.0}, ...] の形式。
    """
    _post("update_motodata", rows=rows, timestamp=timestamp)


def load_registry_csv() -> list[dict]:
    with open(REGISTRY_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def push_registry_to_sheet() -> int:
    """registry.csv の内容で、シート側を丸ごと置き換える(初回移行用)。"""
    rows = load_registry_csv()
    replace_all(rows)
    return len(rows)


def pull_sheet_to_registry() -> int:
    """シート側の内容で、ローカルの registry.csv を丸ごと置き換える。"""
    rows = read_all()
    if not rows:
        return 0
    fieldnames = list(rows[0].keys())
    with open(REGISTRY_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "push":
        n = push_registry_to_sheet()
        print(f"registry.csv の {n} 行をスプレッドシートへ反映しました")
    elif len(sys.argv) > 1 and sys.argv[1] == "pull":
        n = pull_sheet_to_registry()
        print(f"スプレッドシートの {n} 行を registry.csv に反映しました")
    else:
        print("使い方: python sheets_sync.py [push|pull]")
