"""地図ページ(docs/)を作る。

- docs/data/points.json : 地図に載せる全施設(位置・施設名・住所・種別・日付)
- docs/data/history.json: 自治体ごとの件数と資料の日付の移り変わり(前回の一覧からの増減の表示に使う)
- docs/data/件数の記録.csv: 上の記録を、自治体・種別と資料の日付ごとに1行ずつ並べた表(エクセル用)
- docs/index.html       : 地図・グラフ・ランキング・件数表のページ(ひな形は scripts/site/index.html)
- docs/style.css, docs/app.js: そのページの見た目と動き(scripts/site/ から写す)
- docs/trend.html       : 自治体ごとの件数の推移グラフのページ(trend.py)

使い方: python scripts/build_site.py
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from addresses import PREF_BY_CODE, collect_all, load_registry  # noqa: E402
from geocode import load_cache  # noqa: E402
import trend  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(BASE_DIR, "docs")
DATA = os.path.join(DOCS, "data")
HISTORY_PATH = os.path.join(DATA, "history.json")
SITE_SRC = os.path.join(BASE_DIR, "scripts", "site")  # ページのひな形・見た目(css)・動き(js)
SITE_URL = "https://yumaki5555.github.io/minpaku-map/"

CATS = ["民泊", "特区民泊", "旅館・ホテル", "簡易宿所"]
PREFS = ["東京都", "神奈川県", "埼玉県", "千葉県", "茨城県", "北海道", "宮城県", "静岡県", "愛知県", "京都府", "大阪府",
         "兵庫県", "広島県", "福岡県", "沖縄県"]
AREA = "東京・神奈川・埼玉・千葉・茨城・北海道・宮城・静岡・愛知・京都・大阪・兵庫・広島・福岡・沖縄"


def pref_index(code: str) -> int:
    return PREFS.index(PREF_BY_CODE[code[:2]])
JST = timezone(timedelta(hours=9))


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def open_retry(path: str, mode: str, **kwargs):
    """Dropboxの同期中はファイルが一時的に開けないことがあるので、少し待って何度かやり直す。"""
    for attempt in range(10):
        try:
            return open(path, mode, **kwargs)
        except OSError:
            if attempt == 9:
                raise
            time.sleep(3)


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open_retry(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def city_of(addr: str, pref: str, known: list[str]) -> str:
    """住所から市区町村名を取り出す(横浜市・大阪市などの政令市は市全体でまとめる)。"""
    rest = addr.replace(" ", "")
    if rest.startswith(pref):
        rest = rest[len(pref):]
    for name in known:  # 登録済みの自治体名(23区・政令市など)で始まるならそれ
        if rest.startswith(name):
            return name
    # 東京都の島しょ(「三宅島三宅村」など)。「昭島市玉川町」を「市玉川町」としないよう、島のすぐ後ろが「市」のものは除く
    m = re.match(r"^[^市]{1,4}?島([^市].{0,3}?[町村])", rest) if pref == "東京都" else None
    if m:
        return m.group(1)
    # 郡の名前は2文字以上(「蒲郡市竹島町」の「蒲郡」は市の名前の一部)。「赤穂郡上郡町」のように町の名前に郡が入ることもある。
    # 「群」は北海道の資料の「虻田群洞爺湖町」のような書き誤り
    m = re.match(r"^.{2,}[郡群]([^郡].{0,4}?[町村])", rest)
    if m:
        return m.group(1)
    m = re.match(r"^(.{1,6}?市)", rest)
    if m and rest[m.end():m.end() + 1] in ("町", "村") and len(m.group(1)) <= 2:
        return rest[:m.end() + 1]  # 北海道の「余市町」
    if m and rest[m.end():m.end() + 1] != "街":  # 北海道の「当麻町市街」の「市」は市の名前ではない
        return m.group(1)
    m = re.match(r"^(.{1,4}?[町村区])", rest)
    return m.group(1) if m else ""


def known_names(reg_by_code: dict) -> list[str]:
    """登録済みの市区町村名(都府県全体の資料は除く)を、長い名前から順に並べたもの。"""
    return sorted({r["自治体名"] for r in reg_by_code.values() if not r["自治体名"].endswith(("都", "府", "県", ")"))},
                  key=len, reverse=True)


def main() -> None:
    today = datetime.now(JST).strftime("%Y/%m/%d")
    registry = load_registry()
    collected = collect_all(save=True)
    cache = load_cache()

    registry = sorted(registry, key=lambda r: pref_index(r["自治体コード"]))  # 同じ都府県の中は登録順
    munis: list[str] = []
    muni_pref: list[int] = []
    for r in registry:
        if r["自治体名"] not in munis:
            munis.append(r["自治体名"])
            muni_pref.append(pref_index(r["自治体コード"]))
    reg_by_code = {r["自治体コード"]: r for r in registry}

    known = known_names(reg_by_code)
    cities: list[str] = []
    city_idx: dict[tuple[str, str], int] = {}  # (都道府県, 市区町村) → 番号
    # 市区町村(住所から分けたもの)ごとの数。県がまとめて公表している一覧も、住所の市区町村に分けて数える
    city_pref: list[int] = []
    city_n: list[list[int]] = []          # 資料に載っている施設の数(種類ごと)
    city_m: list[list[int]] = []          # そのうち地図に載せた数(種類ごと)
    city_src: list[dict[str, int]] = []   # どの資料に何件載っているか
    code_n: dict[str, list[int]] = {}     # 資料ごとの、種類ごとの数(旅館業の資料は 旅館・ホテル と 簡易宿所 に分かれる)

    # 同じ場所・同じ種別・同じ市区町村の施設(同じ建物の別の部屋など)は1つのピンにまとめる
    groups: dict[tuple, dict] = {}
    placed: dict[str, int] = {}
    not_found = 0
    by_source = {"yahoo": 0, "gsi": 0, "approx": 0}  # 位置をどうやって決めたか(施設の数)
    for p in collected["points"]:
        muni = reg_by_code[p["code"]]["自治体名"]
        pref = PREF_BY_CODE[p["code"][:2]]
        city = city_of(p["addr"], pref, known) or muni
        cat = CATS.index(p["cat"])
        if (pref, city) not in city_idx:
            city_idx[(pref, city)] = len(cities)
            cities.append(city)
            city_pref.append(PREFS.index(pref))
            city_n.append([0] * len(CATS))
            city_m.append([0] * len(CATS))
            city_src.append({})
        ci = city_idx[(pref, city)]
        city_n[ci][cat] += 1
        city_src[ci][p["code"]] = city_src[ci].get(p["code"], 0) + 1
        code_n.setdefault(p["code"], [0] * len(CATS))[cat] += 1
        loc = cache.get(p["geo"])
        if not loc:
            not_found += 1
            continue
        by_source["approx" if not loc[2] else "yahoo" if loc[3:] == ["y"] else "gsi"] += 1
        lat, lng, exact = loc[0], loc[1], loc[2]
        key = (lat, lng, cat, muni, ci)
        if key not in groups:
            groups[key] = {"lat": lat, "lng": lng, "exact": exact, "cat": cat,
                           "muni": munis.index(muni), "city": ci, "items": []}
        g = groups[key]
        g["items"].append([p["name"], p["addr"], p["date"], p.get("no", "")])
        placed[p["code"]] = placed.get(p["code"], 0) + 1
        city_m[ci][cat] += 1

    # 大阪府と兵庫県の「太子町」のように、別の都道府県に同じ名前の町があるときは、名前に都道府県を添えて見分ける
    # (ページでは市区町村を名前で選ぶため)。city_names は住所から取り出したままの名前で、登録済みの自治体名との照らし合わせに使う
    city_names = list(cities)
    cities = [f"{c}（{PREFS[city_pref[i]]}）" if city_names.count(c) > 1 else c for i, c in enumerate(city_names)]

    pins = [[g["lat"], g["lng"], g["cat"], g["muni"], g["exact"], g["items"], g["city"]] for g in groups.values()]
    # 載っている施設が全部、自治体コードの末尾とは別の種別だった資料は、その種別として数える(地図の種類分けと件数をそろえる)
    for code, n in code_n.items():
        groups_n = [n[0], n[1], n[2] + n[3]]
        for k, v in enumerate(groups_n):
            if v and v == sum(n) and k != (1 if code.endswith("民特") else 0 if code.endswith("民") else 2):
                trend.KIND_OVERRIDE[code] = k
    write_json(os.path.join(DATA, "points.json"), {"cats": CATS, "munis": munis, "mpref": muni_pref, "cities": cities,
                                                   "prefs": PREFS, "pins": pins, "updated": today})

    # 自治体・種別ごとの件数表
    table = []
    for r in registry:
        code = r["自治体コード"]
        st = collected["stats"].get(code, {})
        table.append({
            "code": code, "muni": r["自治体名"], "pref": pref_index(code),
            "kind": trend.KINDS[trend.kind_of(code)][1],
            "count": st.get("with_addr", 0), "placed": placed.get(code, 0), "n": code_n.get(code, [0] * len(CATS)),
            "src_date": r.get("元データ日付", ""), "date_basis": r.get("日付の根拠", ""), "page": r.get("元HP", ""), "doc": r.get("文書リンク", ""),
            "title": r.get("文書タイトル", ""),
            "mymap": r.get("Googleマイマップ", ""), "memo": r.get("メモ", "") if not st.get("with_addr") else "",
        })

    # 件数と資料の日付を記録しておき、自治体ごとに「前回の一覧」からの増減を出す
    history = load_json(HISTORY_PATH, [])
    current = {t["code"]: t["count"] for t in table}
    dates = {t["code"]: t["src_date"] for t in table if t["src_date"]}
    if not history or history[-1]["counts"] != current or history[-1].get("dates") != dates:
        if history and history[-1]["date"] == today:
            history[-1].update(counts=current, dates=dates)
        else:
            history.append({"date": today, "counts": current, "dates": dates})
    write_json(HISTORY_PATH, history)
    for t in table:
        t["diff"], t["prev_date"] = previous_version(history, t["code"])
    with open_retry(os.path.join(DATA, trend.CSV_NAME), "w", encoding="utf-8-sig", newline="") as f:
        f.write(trend.records_csv(table, history, PREFS))
    with open_retry(os.path.join(DOCS, "trend.html"), "w", encoding="utf-8") as f:
        f.write(trend.render_page(table, history, today, PREFS, CATS, SITE_URL))

    # ページの中で使う数(資料ごと・市区町村ごと)と、件数の推移
    all_vs = {t["code"]: trend.versions(history, t["code"]) for t in table}
    x0, x1 = trend.axis_range([vs for vs in all_vs.values() if vs])
    code_index = {t["code"]: i for i, t in enumerate(table)}
    sources = []
    for t in table:
        vs = all_vs[t["code"]]
        sources.append({
            "code": t["code"], "muni": t["muni"], "pref": t["pref"], "kind": t["kind"], "k": trend.kind_of(t["code"]),
            "title": t["title"], "count": t["count"], "placed": t["placed"], "n": t["n"],
            "date": t["src_date"], "basis": t["date_basis"], "page": t["page"], "doc": t["doc"], "memo": t["memo"],
            "diff": t["diff"], "prev": t["prev_date"], "vs": [[v["date"], v["count"]] for v in vs],
        })
    city_rows = []
    for ci, name in enumerate(cities):
        sn = sorted(([code_index[c], n] for c, n in city_src[ci].items()), key=lambda x: x[0])
        # その市区町村の名前の資料は、件数0(データ未取得)でも関係する資料として入れる
        own = [i for i, t in enumerate(table) if t["muni"] == city_names[ci] and i not in {s for s, _ in sn}]
        city_rows.append({"name": name, "pref": city_pref[ci], "n": city_n[ci], "m": city_m[ci],
                          "sn": sn, "src": sorted([s for s, _ in sn] + own)})
    site = {"updated": today, "site": SITE_URL, "prefs": PREFS, "cats": CATS,
            "axis": [f"{x0:%Y/%m/%d}", f"{x1:%Y/%m/%d}"], "sources": sources, "cities": city_rows,
            "total_paused": trend.TOTAL_PAUSED}  # 全地域の合計のグラフを止めている理由(空なら出す)

    total = sum(t["count"] for t in table)
    page = render(site, total, today, not_found, by_source, len(munis), f"{x0:%Y/%m/%d}〜{x1:%Y/%m/%d}")
    with open_retry(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    open(os.path.join(DOCS, ".nojekyll"), "a").close()
    print(f"地図ページを作りました: ピン {len(pins)}個 / 施設 {sum(len(p[5]) for p in pins)}件 / 位置不明 {not_found}件")


def previous_version(history: list[dict], code: str) -> tuple[int | None, str]:
    """その自治体の前回の一覧(件数か資料の日付が今と違う版)と比べた増減と、前回の資料の日付。

    自治体ごとに一覧を更新する時期が違うので、地図の更新日ではなく自治体の一覧の版で比べる。
    版の分け方は推移のグラフ(trend.versions)と同じにして、表とグラフの数字をそろえる。
    """
    if not history[-1]["counts"].get(code, 0):
        return None, ""
    vs = trend.versions(history, code)
    if len(vs) < 2:
        return None, ""
    return vs[-1]["count"] - vs[-2]["count"], vs[-2]["date"]


def render_method(by_source: dict, not_found: int, today: str) -> str:
    """「位置の決め方と精度」の説明。件数は毎回の更新で数え直す。"""
    all_ = sum(by_source.values()) + not_found

    def row(label, n, desc):
        return f'<tr><th>{label}</th><td class="num">{n:,}件</td><td class="num">{n / all_:.0%}</td><td>{desc}</td></tr>'

    return f"""<details class="method" id="method">
<summary>地図上の位置の決め方と精度について（現時点での判断）</summary>
<p>施設の位置は、公表されている住所から自動で推定しています。住所はまず国土地理院の住所検索で調べ、続けてYahoo!の住所検索でも調べて、次のように使い分けています（{html.escape(today)}時点の件数）。</p>
<div class="tablewrap"><table class="mtable">
{row("Yahoo!の位置", by_source["yahoo"], "Yahoo!の答えの番地の数字と町名が、元の住所と一致したもの。建物単位の細かい位置です。")}
{row("国土地理院の位置", by_source["gsi"], "Yahoo!で見つからない、または番地が一致しなかったもの。街区（ブロック）の代表点なので、数十〜数百mずれることがあります。")}
{row("町の中心付近（中が白い点）", by_source["approx"], "どちらでも番地まで分からなかったもの。多くは地図データに載っていない地番の住所です。実際の場所と大きく離れていることがあります。")}
{row("地図に表示なし", not_found, "どちらでも住所が見つからなかったもの（住所の書き誤りなど）。")}
</table></div>
<p><b>この使い分けにした理由</b></p>
<ul>
<li>2026年10月1日に全住所を両方で調べて比べたところ、国土地理院が「番地まで分かった」と答えた住所でも約900件が200m以上（最大で約13km）ずれていました。山あいや海沿いなどの地番の地域で、町の中心付近を返すことがあるためです。そこで、条件を満たすYahoo!の位置を優先しています。</li>
<li>一方でYahoo!は、「墨田区墨田3丁目」で問い合わせると「墨田区緑3丁目」を返すなど、別の町や別の番地を答えることがあります。そのため、番地の数字と町名の両方が一致したときだけ使っています。</li>
<li>新しく加わった住所も、定期の更新で同じ方法で調べています。この判断は、今後の確認結果によって見直すことがあります。</li>
</ul>
</details>"""


def copy_asset(name: str) -> str:
    """scripts/site/ の見た目(css)・動き(js)を docs/ に写し、中身の短い印(更新のたびに変わる)を返す。
    ページから「?v=印」付きで読み込むので、ブラウザに古いファイルが残っていても新しいものが使われる。"""
    with open(os.path.join(SITE_SRC, name), encoding="utf-8") as f:
        text = f.read()
    with open_retry(os.path.join(DOCS, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]


def render(site: dict, total: int, today: str, not_found: int, by_source: dict, n_munis: int, axis: str) -> str:
    """ひな形(scripts/site/index.html)の {{名前}} を埋めて、地図ページを作る。
    市区町村ごとの数・資料ごとの件数・推移は site としてページに入れておき、表やグラフはページを開いたときに描く。"""
    with open(os.path.join(SITE_SRC, "index.html"), encoding="utf-8") as f:
        page = f.read()
    data = json.dumps(site, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    values = {
        "TOTAL": f"{total:,}", "TODAY": html.escape(today), "AREA": AREA, "SITE_URL": SITE_URL,
        "N_CITIES": f"{len(site['cities']):,}", "N_MUNIS": str(n_munis), "AXIS": html.escape(axis),
        "CSV": html.escape(trend.CSV_NAME), "METHOD": render_method(by_source, not_found, today),
        "CSS_V": copy_asset("style.css"), "JS_V": copy_asset("app.js"), "DATA": data,
    }
    for k, v in values.items():
        page = page.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", page)
    if left:
        raise ValueError(f"ひな形に埋めていない所があります: {left}")
    return page


if __name__ == "__main__":
    main()
