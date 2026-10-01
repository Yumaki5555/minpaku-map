"""地図ページ(docs/)を作る。

- docs/data/points.json : 地図に載せる全施設(位置・施設名・住所・種別・日付)
- docs/data/history.json: 自治体ごとの件数の移り変わり(前回からの増減の表示に使う)
- docs/index.html       : 地図と件数表のページ

使い方: python scripts/build_site.py
"""
from __future__ import annotations

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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(BASE_DIR, "docs")
DATA = os.path.join(DOCS, "data")
HISTORY_PATH = os.path.join(DATA, "history.json")
SITE_URL = "https://yumaki5555.github.io/minpaku-map/"

CATS = ["民泊", "特区民泊", "旅館・ホテル", "簡易宿所"]
PREFS = ["東京都", "神奈川県", "埼玉県", "千葉県", "茨城県", "大阪府"]
AREA = "東京・神奈川・埼玉・千葉・茨城・大阪"


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
    m = re.match(r"^.{1,4}?島(.{1,4}?[町村])", rest)  # 東京都の島しょ(「三宅島三宅村」など)
    if m:
        return m.group(1)
    m = re.match(r"^.+?郡(.{1,5}?[町村])", rest)
    if m:
        return m.group(1)
    m = re.match(r"^(.{1,6}?市)", rest)
    if m:
        return m.group(1)
    m = re.match(r"^(.{1,4}?[町村区])", rest)
    return m.group(1) if m else ""


def known_names(reg_by_code: dict) -> list[str]:
    """登録済みの市区町村名(都府県全体の資料は除く)を、長い名前から順に並べたもの。"""
    return sorted({r["自治体名"] for r in reg_by_code.values() if not r["自治体名"].endswith(("都", "府", "県", ")"))},
                  key=len, reverse=True)


def ranking(points: list[dict], reg_by_code: dict, cats: set[str], top: int = 10) -> list[tuple[str, int]]:
    known = known_names(reg_by_code)
    counts: dict[str, int] = {}
    for p in points:
        if p["cat"] not in cats:
            continue
        pref = PREF_BY_CODE[p["code"][:2]]
        city = city_of(p["addr"], pref, known)
        if city:
            counts[city] = counts.get(city, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])[:top]


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

    # 同じ場所・同じ種別の施設(同じ建物の別の部屋など)は1つのピンにまとめる
    groups: dict[tuple, dict] = {}
    placed: dict[str, int] = {}
    not_found = 0
    for p in collected["points"]:
        loc = cache.get(p["geo"])
        if not loc:
            not_found += 1
            continue
        lat, lng, exact = loc[0], loc[1], loc[2]
        muni = reg_by_code[p["code"]]["自治体名"]
        key = (lat, lng, p["cat"])
        if key not in groups:
            city = city_of(p["addr"], PREF_BY_CODE[p["code"][:2]], known) or muni
            if city not in cities:
                cities.append(city)
            groups[key] = {"lat": lat, "lng": lng, "exact": exact, "cat": CATS.index(p["cat"]),
                           "muni": munis.index(muni), "city": cities.index(city), "items": []}
        g = groups[key]
        g["items"].append([p["name"], p["addr"], p["date"], p.get("no", "")])
        placed[p["code"]] = placed.get(p["code"], 0) + 1

    pins = [[g["lat"], g["lng"], g["cat"], g["muni"], g["exact"], g["items"], g["city"]] for g in groups.values()]
    write_json(os.path.join(DATA, "points.json"), {"cats": CATS, "munis": munis, "mpref": muni_pref, "cities": cities,
                                                   "prefs": PREFS, "pins": pins, "updated": today})

    # 自治体・種別ごとの件数表
    table = []
    for r in registry:
        code = r["自治体コード"]
        st = collected["stats"].get(code, {})
        table.append({
            "code": code, "muni": r["自治体名"], "pref": pref_index(code),
            "kind": "特区民泊" if code.endswith("民特") else r["種別"],
            "count": st.get("with_addr", 0), "placed": placed.get(code, 0),
            "src_date": r.get("元データ日付", ""), "page": r.get("元HP", ""), "doc": r.get("文書リンク", ""),
            "mymap": r.get("Googleマイマップ", ""), "memo": r.get("メモ", "") if not st.get("with_addr") else "",
        })

    history = load_json(HISTORY_PATH, [])
    current = {t["code"]: t["count"] for t in table}
    if not history or history[-1]["counts"] != current:
        if history and history[-1]["date"] == today:
            history[-1]["counts"] = current
        else:
            history.append({"date": today, "counts": current})
    write_json(HISTORY_PATH, history)
    prev = history[-2] if len(history) >= 2 else None
    for t in table:
        if prev and t["code"] in prev["counts"]:
            t["diff"] = t["count"] - prev["counts"][t["code"]]
        else:
            t["diff"] = None

    total = sum(t["count"] for t in table)
    by_cat = {c: sum(len(p[5]) for p in pins if CATS[p[2]] == c) for c in CATS}
    ranks = [
        ("民泊", "特区民泊を含む", 0, ranking(collected["points"], reg_by_code, {"民泊", "特区民泊"})),
        ("旅館・ホテル", "簡易宿所を含む", 2, ranking(collected["points"], reg_by_code, {"旅館・ホテル", "簡易宿所"})),
    ]
    page = render(table, total, by_cat, today, prev["date"] if prev else "", not_found, ranks)
    with open_retry(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    open(os.path.join(DOCS, ".nojekyll"), "a").close()
    print(f"地図ページを作りました: ピン {len(pins)}個 / 施設 {sum(by_cat.values())}件 / 位置不明 {not_found}件")


def render_ranks(ranks) -> str:
    e = html.escape
    out = []
    for title, sub, color, items in ranks:
        mx = max((n for _, n in items), default=1)
        lis = "".join(
            f'<li title="{e(name)}：{n:,}件"><span class="rk">{i}</span><span class="rn">{e(name)}</span>'
            f'<span class="rb"><span class="rf c{color}" style="width:{n / mx * 100:.1f}%"></span></span>'
            f'<span class="rv">{n:,}</span></li>'
            for i, (name, n) in enumerate(items, 1)
        )
        out.append(f'<section class="rank"><h3><span class="dot c{color}"></span>{e(title)}'
                   f'<span class="rsub">（{e(sub)}）</span></h3><ol>{lis}</ol></section>')
    return "".join(out)


def render(table, total, by_cat, today, prev_date, not_found, ranks) -> str:
    e = html.escape
    rows = []
    last_pref = None
    for t in table:
        if t["pref"] != last_pref:
            last_pref = t["pref"]
            sub = sum(x["count"] for x in table if x["pref"] == t["pref"])
            rows.append(f'<tr class="grp" data-pref="{t["pref"]}"><th colspan="6"><button type="button" aria-expanded="false">'
                        f'<span class="arw">▸</span>{e(PREFS[t["pref"]])}<span class="sub">{sub:,}件</span></button></th></tr>')
        link = f'<a href="{e(t["page"] or t["doc"])}" target="_blank" rel="noopener">自治体のページ</a>' if (t["page"] or t["doc"]) else ""
        if t["count"] == 0:
            num = '<span class="none">データなし</span>'
        else:
            num = f'{t["count"]:,}'
        diff = ""
        if t["diff"]:
            diff = f'<span class="{"up" if t["diff"] > 0 else "down"}">{t["diff"]:+,}</span>'
        note = f'<div class="memo">{e(t["memo"])}</div>' if t["memo"] else ""
        rows.append(
            f'<tr data-muni="{e(t["muni"])}" data-pref="{t["pref"]}"><td>{e(t["muni"])}</td><td><span class="dot c{CATS.index(t["kind"]) if t["kind"] in CATS else 2}"></span>{e(t["kind"])}</td>'
            f'<td class="num">{num}</td><td class="num">{diff}</td><td>{e(t["src_date"])}</td><td>{link}{note}</td></tr>'
        )
    cat_btns = "".join(
        f'<button class="tagbtn" data-cat="{i}" aria-pressed="true"><span class="dot c{i}"></span>{e(c)}<span class="n">{by_cat[c]:,}</span></button>'
        for i, c in enumerate(CATS)
    )
    diff_note = f"増減は前回（{e(prev_date)}）との比較です。" if prev_date else ""
    nf_note = f"住所から位置を特定できなかった {not_found:,} 件は地図に表示されていません。" if not_found else ""
    return TEMPLATE.format(
        total=f"{total:,}", today=e(today), cat_btns=cat_btns, rows="\n".join(rows),
        diff_note=diff_note, nf_note=nf_note, site_url=SITE_URL, area=AREA, ranks=render_ranks(ranks),
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>民泊・旅館業マップ｜首都圏・大阪</title>
<meta name="description" content="{area}の自治体が公表している民泊（住宅宿泊事業）・特区民泊・旅館業の施設を地図にまとめました。毎週自動更新。">
<meta property="og:type" content="website">
<meta property="og:title" content="民泊・旅館業マップ｜{total}件を地図で">
<meta property="og:description" content="{area}の自治体が公表している民泊・特区民泊・旅館業の施設を地図にまとめました。毎週自動更新。">
<meta property="og:url" content="{site_url}">
<meta name="twitter:card" content="summary">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏠</text></svg>">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.min.css">
<style>
:root{{
  --bg:#f6f5f2;--card:#fff;--ink:#1c1b19;--sub:#5f5c56;--line:#e3e0d9;--accent:#1d4ed8;--chip:#efede8;
  --c0:#e34948;--c1:#4a3aa7;--c2:#2a78d6;--c3:#eda100;--up:#dc2626;--down:#2563eb;
}}
@media (prefers-color-scheme: dark){{
  :root:not([data-theme="light"]){{
    --bg:#16161a;--card:#202026;--ink:#ecebe8;--sub:#a8a59f;--line:#34343c;--accent:#7aa2ff;--chip:#2c2c33;
    --c0:#e66767;--c1:#9085e9;--c2:#3987e5;--c3:#c98500;--up:#f87171;--down:#7aa2ff;
  }}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Hiragino Sans","Noto Sans JP","Yu Gothic UI","Meiryo",sans-serif;line-height:1.6}}
a{{color:var(--accent)}}
.wrap{{max-width:1100px;margin:0 auto;padding:0 16px}}
header{{padding:24px 0 8px}}
h1{{font-size:1.6rem;margin:0 0 4px;letter-spacing:.02em}}
h2{{font-size:1.15rem;margin:28px 0 8px}}
.lead{{color:var(--sub);margin:0 0 10px;font-size:.95rem}}
.lead b{{color:var(--ink);font-size:1.1rem}}
.red{{color:var(--up)}}
.filters{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0 8px}}
.viewtabs{{display:flex;gap:0;margin:14px 0 0;border:1.5px solid var(--line);border-radius:10px;overflow:hidden;width:max-content;max-width:100%}}
.viewtab{{border:0;background:var(--card);color:var(--sub);padding:7px 16px;font-family:inherit;font-size:.92rem;font-weight:600;cursor:pointer;
  display:flex;flex-direction:column;align-items:flex-start;line-height:1.3}}
.viewtab span{{font-size:.72rem;font-weight:400}}
.viewtab + .viewtab{{border-left:1.5px solid var(--line)}}
.viewtab[aria-selected="true"]{{background:var(--ink);color:var(--bg)}}
.search{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 8px}}
.search input{{flex:1 1 260px;min-width:0;padding:8px 12px;border:1.5px solid var(--line);border-radius:8px;
  background:var(--card);color:var(--ink);font-size:.95rem;font-family:inherit}}
.search button{{border:0;background:var(--ink);color:var(--bg);border-radius:8px;padding:8px 16px;font-weight:700;font-family:inherit;cursor:pointer}}
.qmsg{{font-size:.82rem;color:var(--sub);flex-basis:100%}}
.qmsg:empty{{display:none}}
.tagbtn{{border:1.5px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
  padding:5px 12px;font-size:.88rem;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:6px}}
.tagbtn .n{{opacity:.7;font-size:.8em}}
.tagbtn[aria-pressed="false"]{{opacity:.45}}
.tagbtn[aria-pressed="false"] .dot{{background:transparent;border:2px solid var(--sub)}}
select{{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:.9rem;font-family:inherit}}
.dot{{display:inline-block;width:11px;height:11px;border-radius:50%;flex:none;vertical-align:-1px;margin-right:4px}}
.c0{{background:var(--c0)}}.c1{{background:var(--c1)}}.c2{{background:var(--c2)}}.c3{{background:var(--c3)}}
.basemap{{transition:filter .3s;filter:saturate(.25) contrast(.85) brightness(1.06)}}
#map.zoomed .basemap{{filter:saturate(.2) contrast(.45) brightness(1.18)}}
#map{{height:68vh;min-height:420px;border:1px solid var(--line);border-radius:12px;background:var(--chip)}}
.note{{font-size:.8rem;color:var(--sub);margin:6px 0}}
.pin{{border-radius:50%;border:1.5px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.35),0 1px 3px rgba(0,0,0,.3);
  color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;line-height:1}}
.pin.approx{{border-style:dashed}}
.cl{{border-radius:50%;display:flex;align-items:center;justify-content:center;
  box-shadow:0 0 0 2px rgba(255,255,255,.9),0 1px 4px rgba(0,0,0,.35)}}
.cl span{{background:#fff;color:#1c1b19;border-radius:50%;width:calc(100% - 12px);height:calc(100% - 12px);
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:11px;letter-spacing:-.02em}}
.ctip{{font-size:12px;line-height:1.6;min-width:200px;max-width:280px;white-space:normal}}
.ctip .cc{{white-space:nowrap}}
.mapwrap{{position:relative}}
.cinfo{{position:absolute;left:8px;right:8px;bottom:8px;z-index:1000;background:var(--card);color:var(--ink);
  border:1px solid var(--line);border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.25);padding:10px 36px 10px 12px}}
.cinfo[hidden]{{display:none}}
.cinfo .ctip{{min-width:0;max-width:none;font-size:13px}}
.cinfo .cc{{white-space:normal;display:flex;flex-wrap:wrap;gap:2px 12px}}
.cinfo .cc br{{display:none}}
.cinfo .d{{color:var(--sub)}}
.cinfo .cx{{position:absolute;top:4px;right:6px;border:0;background:none;font-size:20px;line-height:1;color:var(--sub);cursor:pointer;padding:4px}}
.ctip .cc{{margin-top:4px}}
.ctip .d{{color:#666;font-size:11px;margin-top:2px}}
.leaflet-popup-content{{font-family:inherit;font-size:13px;line-height:1.5;max-height:280px;overflow:auto;margin:10px 12px}}
.pop h3{{font-size:13px;margin:0 0 4px}}
.pop ul{{margin:0;padding-left:1.1em}}
.pop li{{margin:3px 0}}
.pop .d{{color:#666;font-size:12px}}
.pop .approxnote{{background:#fef3c7;color:#92400e;border-radius:6px;padding:4px 6px;margin:2px 0 4px}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:.88rem;min-width:620px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}
td:last-child{{white-space:normal;width:100%}}
th{{background:var(--chip);font-weight:600;position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.up{{color:var(--up);font-weight:700}}.down{{color:var(--down);font-weight:700}}
.none{{color:var(--sub)}}
.memo{{font-size:.78rem;color:var(--sub)}}
tr.hide{{display:none}}
tr.grp th{{background:var(--card);font-size:.95rem;padding-top:14px;border-bottom:2px solid var(--line)}}
tr.grp button{{border:0;background:none;color:inherit;font:inherit;font-weight:700;cursor:pointer;padding:0;display:flex;align-items:center;width:100%}}
tr.grp .arw{{display:inline-block;width:1.2em;transition:transform .15s}}
tr.grp button[aria-expanded="true"] .arw{{transform:rotate(90deg)}}
tr.grp .sub{{font-weight:400;color:var(--sub);font-size:.8rem;margin-left:8px}}
.caution{{background:var(--card);border:1px solid var(--line);border-left:4px solid #d97706;border-radius:10px;
  padding:10px 14px;margin:12px 0 4px;font-size:.85rem;line-height:1.7}}
.caution .ct{{font-weight:700;margin:0 0 2px}}
.caution ul{{margin:0;padding-left:1.2em}}
.countgrid{{display:grid;grid-template-columns:320px minmax(0,1fr);gap:16px;align-items:start}}
@media (max-width:900px){{.countgrid{{grid-template-columns:1fr}}}}
.ranks{{display:flex;flex-direction:column;gap:12px}}
.ranks .rt{{margin:0;font-weight:700;font-size:.95rem}}
.rank{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}}
.rank h3{{font-size:.92rem;margin:0 0 6px;display:flex;align-items:center}}
.rank .rsub{{font-weight:400;color:var(--sub);font-size:.78rem;margin-left:4px}}
.rank ol{{list-style:none;margin:0;padding:0}}
.rank li{{display:grid;grid-template-columns:1.4em 6.2em minmax(0,1fr) 3.6em;align-items:center;gap:6px;
  font-size:.82rem;padding:3px 0;border-radius:6px}}
.rank li:hover{{background:var(--chip)}}
.rank .rk{{color:var(--sub);text-align:right;font-variant-numeric:tabular-nums}}
.rank .rn{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.rank .rb{{height:10px}}
.rank .rf{{display:block;height:100%;border-radius:0 4px 4px 0;min-width:2px}}
.rank .rv{{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink)}}
footer{{font-size:.8rem;color:var(--sub);padding:20px 0 40px;border-top:1px solid var(--line);margin-top:28px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>🏠 民泊・旅館業マップ</h1>
  <p class="lead">首都圏・大阪の民泊・旅館業 <b>{total}件</b> を地図にしました。毎週月曜に自動更新（最終更新 {today}）。</p>
  <div class="caution">
    <p class="ct">このマップについて</p>
    <ul>
      <li>身近な地域の民泊・旅館の<b>数を「見える化」</b>するのが目的です。</li>
      <li><b class="red">すべての施設が載っているわけではありません</b>（公表範囲は自治体ごとに違います）。</li>
      <li><b class="red">今も営業しているかは分かりません。</b></li>
      <li>自動で読み取っているため、<b>内容や位置に誤りがある</b>ことがあります。</li>
      <li>同じような施設でも、<b>自治体によって「旅館・ホテル」「簡易宿所」など区分が違います。</b></li>
    </ul>
  </div>
</header>

<div class="viewtabs" role="tablist" aria-label="地図の表示方法">
  <button type="button" role="tab" class="viewtab" data-view="dots" aria-selected="true">点で見る<span>1か所＝点1つ</span></button>
  <button type="button" role="tab" class="viewtab" data-view="cluster" aria-selected="false">まとめて見る<span>近くの施設を数字の円に</span></button>
</div>
<div class="filters" role="group" aria-label="種別で絞り込み">
  {cat_btns}
  <select id="muni" aria-label="自治体を選ぶ"><option value="">すべての自治体</option></select>
</div>
<form id="search" class="search" role="search" autocomplete="off">
  <input type="search" id="q" list="citylist" placeholder="市区町村名・住所で探す（例：新宿区、箱根町、西新宿2丁目）" aria-label="市区町村名・住所で探す">
  <button type="submit">移動</button>
  <span id="qmsg" class="qmsg" aria-live="polite"></span>
</form>
<datalist id="citylist"></datalist>
<div class="mapwrap">
<div id="map" role="region" aria-label="施設の地図"></div>
<div id="cinfo" class="cinfo" hidden aria-live="polite"><button type="button" class="cx" aria-label="閉じる">×</button><div class="cbody"></div></div>
</div>
<p class="note">点を押すと施設名・住所が出ます。大きい点は同じ場所に複数の施設があります。<br><b>点線の薄い点</b>は番地まで分からず、町の中心付近に置いたものです（実際の場所と離れていることがあります）。{nf_note}</p>

<h2>自治体ごとの件数</h2>
<p class="note">{diff_note}「データなし」は、自治体が一覧を公開していない、またはファイルを自動で読み取れなかったものです。</p>
<div class="countgrid">
<div class="ranks" aria-label="市区町村別の件数ランキング">
<p class="rt">市区町村別ランキング 上位10</p>
{ranks}
</div>
<div class="tablewrap"><table>
<thead><tr><th>自治体</th><th>種別</th><th style="text-align:right">件数</th><th style="text-align:right">増減</th><th>資料の日付</th><th>出典</th></tr></thead>
<tbody id="tbody">
{rows}
</tbody></table></div>
</div>

<footer>
  <p>各自治体が公表している一覧（PDF・Excel・CSV）をもとに自動で作成しています。地図上の位置は住所から自動で推定したもので、ずれている場合があります。正確な情報は各自治体の公表資料をご確認ください。</p>
  <p>個人の氏名・電話番号は掲載していません。地図：<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>／位置の推定：国土地理院 住所検索、<a href="https://developer.yahoo.co.jp/sitemap/" target="_blank" rel="noopener">Web Services by Yahoo! JAPAN</a></p>
</footer>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.min.js"></script>
<script>
(async function(){{
  const map = L.map('map', {{preferCanvas:true}}).setView([35.68, 139.76], 10);
  L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 18, className: 'basemap',
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>'
  }}).addTo(map);
  // 地理院の淡色地図は大きく拡大すると線や文字が濃くなるので、拡大時だけ背景を薄くしてピンを見やすくする
  const soften = () => document.getElementById('map').classList.toggle('zoomed', map.getZoom() >= 15);
  map.on('zoomend', soften); soften();

  const css = getComputedStyle(document.documentElement);
  const colors = [0,1,2,3].map(i => css.getPropertyValue('--c'+i).trim());
  const esc = s => String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));

  const res = await fetch('data/points.json');
  const data = await res.json();

  const sel = document.getElementById('muni');
  data.prefs.forEach((pf, pi) => {{
    const idx = data.munis.map((m, i) => i).filter(i => data.mpref[i] === pi);
    if (!idx.length) return;
    const g = document.createElement('optgroup'); g.label = pf;
    const all = document.createElement('option'); all.value = 'p' + pi; all.textContent = pf + '（すべて）'; g.appendChild(all);
    idx.forEach(i => {{ const o = document.createElement('option'); o.value = 'm' + i; o.textContent = data.munis[i]; g.appendChild(o); }});
    sel.appendChild(g);
  }});

  // まとめずに全部の点をそのまま描く(Googleマイマップと同じ見せ方)。
  // 点が約3万個あるので、1個ずつ部品にせず地図に直接描き込む方式(canvas)で軽くする
  const renderer = L.canvas({{padding: 0.5, tolerance: 5}});
  const dotLayer = L.layerGroup();
  const touch = window.matchMedia('(hover: none)').matches;
  // 同じ場所の施設は点1つにまとめ、件数が多いほど点を少し大きくする
  // 拡大したときは「まとめて見る」のピン(直径22px)と同じくらいの大きさにする
  const radiusFor = z => (z <= 9 ? 2 : z <= 10 ? 2.5 : z <= 11 ? 3 : z <= 12 ? 4 : z <= 13 ? 5.5 : z <= 14 ? 8 : 11)
    + (touch && z >= 14 ? 1 : 0);
  const weightFor = z => z <= 11 ? 0.5 : z <= 13 ? 1 : 1.5;
  const grow = n => Math.min(2, 1 + 0.25 * Math.log2(n));

  const dots = [];
  // 押したときの一覧(施設名・住所・番号)。点で見る・まとめて見るの両方で使う
  function popupFor(p) {{
    const [lat, lng, cat, muni, exact, items, city] = p;
    const n = items.length;
    return () => {{
      const noLabel = ['届出番号', '認定番号', '許可番号', '許可番号'][cat];
      const list = items.map(it => '<li><b>'+esc(it[0] || '（施設名の記載なし）')+'</b><br>'+esc(it[1])
        + (it[3] ? '<br><span class="d">'+noLabel+'：'+esc(it[3])+'</span>' : '')
        + (it[2] ? '<br><span class="d">'+esc(it[2])+'</span>' : '')+'</li>').join('');
      // 番地まで特定できなかった施設は、町名(大字)の中心付近にまとめて置いているので、そのことを書く
      const head = exact ? (n > 1 ? ' 同じ建物に'+n+'件' : '') : (n > 1 ? ' この付近に'+n+'件' : '');
      return '<div class="pop"><h3><span class="dot c'+cat+'"></span>'+esc(data.cats[cat])+'（'+esc(data.cities[city] || data.munis[muni])+'）'+head+'</h3>'
        + (exact ? '' : '<div class="d approxnote">※番地まで特定できなかったため、町名の中心付近にまとめて表示しています。実際の場所とは離れていることがあります。</div>')
        + '<ul>'+list+'</ul></div>';
    }};
  }}
  data.pins.forEach(p => {{
    const [lat, lng, cat, muni, exact, items, city] = p;
    const n = items.length;
    const popup = popupFor(p);
    const m = L.circleMarker([lat, lng], {{renderer, radius: 3, color: '#fff', weight: 1, fillColor: colors[cat],
      fillOpacity: exact ? 0.9 : 0.5, dashArray: exact ? null : '2 2', n, cat, muni, city}});
    m.bindPopup(popup);
    dots.push(m);
  }});
  // 描く順番を種類に関係なく混ぜる(ある種類をまとめて上に描くと、重なった所でその色ばかり目立つため)
  let seed = 12345;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = dots.length - 1; i > 0; i--) {{ const j = Math.floor(rnd() * (i + 1)); [dots[i], dots[j]] = [dots[j], dots[i]]; }}

  // 倍率に合わせて点の大きさを決め直す
  function place() {{
    if (view !== 'dots') return;
    const z = map.getZoom(), r = radiusFor(z), w = weightFor(z);
    dots.forEach(m => {{
      m.setRadius(r * grow(m.options.n));
      m.setStyle({{weight: w}});
    }});
  }}
  map.on('zoomend', place);

  // まとめて見る: 同じ建物の施設を1つのピンにし、近くのピンを数字入りの円(種類ごとの割合のドーナツ)にまとめる
  const cluster = L.markerClusterGroup({{
    chunkedLoading: true, maxClusterRadius: 50, showCoverageOnHover: false, zoomToBoundsOnClick: false,
    iconCreateFunction: c => {{
      let n = 0; const tally = [0,0,0,0];
      c.getAllChildMarkers().forEach(m => {{ n += m.options.n; tally[m.options.cat] += m.options.n; }});
      const size = n < 10 ? 34 : n < 100 ? 40 : n < 1000 ? 48 : 56;
      let acc = 0; const stops = [];
      tally.forEach((v, i) => {{ if (!v) return; const a = acc / n * 100, b = (acc + v) / n * 100; stops.push(colors[i]+' '+a.toFixed(2)+'% '+b.toFixed(2)+'%'); acc += v; }});
      return L.divIcon({{html: '<div class="cl" style="width:'+size+'px;height:'+size+'px;background:conic-gradient('+stops.join(',')+')">'
        + '<span>'+n.toLocaleString()+'</span></div>', className: '', iconSize: [size, size]}});
    }}
  }});
  const pins = data.pins.map(p => {{
    const [lat, lng, cat, muni, exact, items, city] = p;
    const n = items.length;
    const size = (n > 1 ? 26 : 22) + (touch ? 2 : 0);
    const icon = L.divIcon({{className: '', iconSize: [size, size],
      html: '<div class="pin'+(exact ? '' : ' approx')+'" style="width:'+size+'px;height:'+size+'px;background:'+colors[cat]+'">'+(n > 1 ? n : '')+'</div>'}});
    return L.marker([lat, lng], {{icon, n, cat, muni, city}}).bindPopup(popupFor(p));
  }});

  // 円の内訳(含まれる市区町村と種類ごとの件数)。パソコンはマウスを重ねると吹き出し、
  // スマホは1回目に押すと地図の下端の情報欄に表示・2回目で拡大
  const cinfo = document.getElementById('cinfo');
  let tipped = null;
  function showTip(layer) {{
    const byCity = new Map(); const tally = [0,0,0,0]; let n = 0;
    layer.getAllChildMarkers().forEach(m => {{ const c = data.cities[m.options.city]; byCity.set(c, (byCity.get(c) || 0) + m.options.n); tally[m.options.cat] += m.options.n; n += m.options.n; }});
    const top = [...byCity.entries()].sort((a, b) => b[1] - a[1]);
    const names = top.slice(0, 3).map(([c, v]) => '<b>'+esc(c)+'</b> '+v.toLocaleString()+'件').join('、')
      + (top.length > 3 ? ' ほか'+(top.length - 3)+'市区町村' : '');
    const cats = tally.map((v, i) => v ? '<span class="dot c'+i+'"></span>'+esc(data.cats[i])+' '+v.toLocaleString()+'件' : '').filter(Boolean).join('<br>');
    const body = '<div class="ctip">'+names+'<div class="cc">'+cats+'</div><div class="d">合計 '+n.toLocaleString()+'件（'+(touch ? 'もう一度押すと拡大' : '押すと拡大')+'）</div></div>';
    tipped = layer;
    if (touch) {{ cinfo.querySelector('.cbody').innerHTML = body; cinfo.hidden = false; return; }}
    layer.unbindTooltip();
    layer.bindTooltip(body, {{direction: 'top', offset: [0, -14], opacity: 1}}).openTooltip();
  }}
  function hideTip() {{ if (tipped && !touch) tipped.closeTooltip(); tipped = null; cinfo.hidden = true; }}
  cinfo.querySelector('.cx').addEventListener('click', hideTip);
  if (!touch) {{
    cluster.on('clustermouseover', e => showTip(e.layer));
    cluster.on('clustermouseout', hideTip);
  }}
  cluster.on('clusterclick', e => {{
    if (touch && tipped !== e.layer) {{ hideTip(); showTip(e.layer); return; }}
    hideTip();
    e.layer.zoomToBounds({{padding: [20, 20]}});
  }});
  map.on('click zoomstart movestart', () => {{ if (touch) hideTip(); }});

  // 表示の切り替え(点で見る/まとめて見る)。選んだ方はこの端末に覚えておく
  let view = 'dots';
  try {{ if (localStorage.getItem('minpaku-view') === 'cluster') view = 'cluster'; }} catch (e) {{}}
  const tabs = [...document.querySelectorAll('.viewtab')];
  function setView(v) {{
    view = v;
    tabs.forEach(t => t.setAttribute('aria-selected', t.dataset.view === v ? 'true' : 'false'));
    hideTip();
    if (v === 'dots') {{ map.removeLayer(cluster); map.addLayer(dotLayer); place(); }}
    else {{ map.removeLayer(dotLayer); map.addLayer(cluster); }}
    try {{ localStorage.setItem('minpaku-view', v); }} catch (e) {{}}
    refresh(false);
  }}
  tabs.forEach(t => t.addEventListener('click', () => setView(t.dataset.view)));

  const btns = [...document.querySelectorAll('.tagbtn')];
  function refresh(fit){{
    const cats = new Set(btns.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => +b.dataset.cat));
    const v = sel.value;
    const muni = v[0] === 'm' ? +v.slice(1) : null;
    const pref = v[0] === 'p' ? +v.slice(1) : null;
    const hit = mi => (muni === null || mi === muni) && (pref === null || data.mpref[mi] === pref);
    const pick = m => cats.has(m.options.cat) && hit(m.options.muni);
    let shown;
    if (view === 'dots') {{
      shown = dots.filter(pick);
      dotLayer.clearLayers();
      shown.forEach(m => dotLayer.addLayer(m));
    }} else {{
      shown = pins.filter(pick);
      cluster.clearLayers();
      cluster.addLayers(shown);
    }}
    if (fit && shown.length) map.fitBounds(L.latLngBounds(shown.map(m => m.getLatLng())), {{padding: [20, 20], maxZoom: 15}});
    // 都府県や自治体を選んだときは、件数表のその都府県を開く
    if (pref !== null) openPrefs.add(pref);
    if (muni !== null) openPrefs.add(data.mpref[muni]);
    foldTable(pref, muni);
  }}
  // 件数表は都府県ごとに折りたたむ(見出しを押すと開く)
  const openPrefs = new Set();
  let lastPref = null, lastMuni = null;
  function foldTable(pref, muni) {{
    lastPref = pref; lastMuni = muni;
    document.querySelectorAll('#tbody tr').forEach(tr => {{
      const tp = +tr.dataset.pref;
      const isGrp = tr.classList.contains('grp');
      const show = isGrp
        ? (pref === null || tp === pref) && (muni === null || data.mpref[muni] === tp)
        : openPrefs.has(tp) && (pref === null || tp === pref) && (muni === null || tr.dataset.muni === data.munis[muni]);
      tr.classList.toggle('hide', !show);
      if (isGrp) tr.querySelector('button').setAttribute('aria-expanded', openPrefs.has(tp) ? 'true' : 'false');
    }});
  }}
  document.querySelectorAll('#tbody tr.grp button').forEach(b => b.addEventListener('click', () => {{
    const tp = +b.closest('tr').dataset.pref;
    openPrefs.has(tp) ? openPrefs.delete(tp) : openPrefs.add(tp);
    foldTable(lastPref, lastMuni);
  }}));
  btns.forEach(b => b.addEventListener('click', () => {{
    b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); refresh(false);
  }}));
  sel.addEventListener('change', () => {{
    if (sel.value === '') {{ map.setView([35.68, 139.76], 10); refresh(false); }} else refresh(true);
  }});
  // 市区町村名・住所で探して地図を移動する
  const cityPins = new Map();
  data.pins.forEach(p => {{ const c = data.cities[p[6]]; if (!cityPins.has(c)) cityPins.set(c, []); cityPins.get(c).push([p[0], p[1]]); }});
  const citylist = document.getElementById('citylist');
  [...cityPins.entries()].sort((a, b) => b[1].length - a[1].length).forEach(([c]) => {{
    const o = document.createElement('option'); o.value = c; citylist.appendChild(o);
  }});
  const qmsg = document.getElementById('qmsg');
  const AREA_PREFS = data.prefs;
  document.getElementById('search').addEventListener('submit', async ev => {{
    ev.preventDefault();
    const q = document.getElementById('q').value.trim().replace(/\\s+/g, '');
    if (!q) return;
    qmsg.textContent = '';
    // 1) 市区町村名(都府県名が付いていてもよい)と一致したら、その市区町村の施設がすべて入る範囲へ
    const bare = q.replace(/^(東京都|神奈川県|埼玉県|千葉県|茨城県|大阪府)/, '');
    const hit = cityPins.get(bare) || cityPins.get(bare + '市') || cityPins.get(bare + '区') || cityPins.get(bare + '町');
    if (hit) {{
      map.fitBounds(L.latLngBounds(hit), {{padding: [20, 20], maxZoom: 15}});
      return;
    }}
    // 2) それ以外は国土地理院の住所検索で場所を調べる
    qmsg.textContent = '検索中…';
    try {{
      const res = await fetch('https://msearch.gsi.go.jp/address-search/AddressSearch?q=' + encodeURIComponent(q));
      const found = (await res.json()).filter(f => AREA_PREFS.some(pf => f.properties.title.startsWith(pf)));
      if (!found.length) {{ qmsg.textContent = '「' + q + '」は見つかりませんでした。市区町村名や住所で試してください。'; return; }}
      // 同じ地名が複数あるときは、近く(約1km以内)に施設が多い場所を優先する
      const near = f => {{ const [x, y] = f.geometry.coordinates; return data.pins.reduce((n, p) => n + (Math.abs(p[0] - y) < 0.009 && Math.abs(p[1] - x) < 0.011 ? p[5].length : 0), 0); }};
      const ranked = found.slice(0, 20).map(f => [f, near(f)]).sort((a, b) => b[1] - a[1]).map(x => x[0]);
      const go = f => {{ const [lng, lat] = f.geometry.coordinates; map.setView([lat, lng], 16); }};
      go(ranked[0]);
      qmsg.innerHTML = esc(ranked[0].properties.title) + ' に移動しました'
        + (ranked.length > 1 ? '　ほかの候補：' + ranked.slice(1, 6).map((f, i) => '<a href="#" data-i="' + (i + 1) + '">' + esc(f.properties.title) + '</a>').join('／') : '');
      qmsg.querySelectorAll('a').forEach(a => a.addEventListener('click', e => {{
        e.preventDefault(); const f = ranked[+a.dataset.i]; go(f); qmsg.innerHTML = esc(f.properties.title) + ' に移動しました';
      }}));
    }} catch (e) {{
      qmsg.textContent = '検索できませんでした。時間をおいて試してください。';
    }}
  }});

  setView(view);
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
