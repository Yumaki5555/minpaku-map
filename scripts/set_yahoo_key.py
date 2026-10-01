"""Yahoo!ジオコーダAPIの Client ID(アプリケーションID)を登録する道具。

使い方:
  1. Yahoo!デベロッパーネットワークの画面で Client ID をコピーする(Ctrl+C)
  2. パソコンの「ターミナル」で次を実行し、Enter を押す
        python scripts/set_yahoo_key.py
  コピーしてある内容(クリップボード)を直接読み取るので、貼り付けの操作は不要。

- パソコン側: scripts/config.json に保存する(このファイルは公開しない設定になっている)
- GitHub側: 秘密の保管庫(Secrets)に YAHOO_APPID という名前で登録する(gh コマンドを使う)
値そのものは画面に表示しない。
"""
from __future__ import annotations

import json
import os
import re
import subprocess

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
REPO = "Yumaki5555/minpaku-map"


def read_clipboard() -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip()


def main() -> None:
    input("Yahoo! の Client ID をコピーした状態で Enter を押してください: ")
    key = read_clipboard()
    # Client ID は英数字と一部の記号だけでできている。それ以外が混ざっていたら止める
    if not re.fullmatch(r"[A-Za-z0-9\-_]{30,100}", key):
        print("コピーされている内容が Client ID の形ではありません。Client ID だけをコピーしてから、もう一度実行してください。")
        print(f"(読み取った文字数: {len(key)})")
        return

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["yahoo_appid"] = key
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"パソコン側に保存しました（scripts/config.json、{len(key)}文字）。")

    try:
        subprocess.run(["gh", "secret", "set", "YAHOO_APPID", "-R", REPO], input=key, text=True, check=True)
        print("GitHub の秘密の保管庫にも登録しました（YAHOO_APPID）。")
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"GitHub への登録に失敗しました: {e}")


if __name__ == "__main__":
    main()
