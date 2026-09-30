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
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from addresses import collect_all, load_registry  # noqa: E402
from geocode import load_cache  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(BASE_DIR, "docs")
DATA = os.path.join(DOCS, "data")
HISTORY_PATH = os.path.join(DATA, "history.json")
SITE_URL = "https://yumaki5555.github.io/minpaku-map/"

CATS = ["民泊", "特区民泊", "旅館・ホテル", "簡易宿所"]
JST = timezone(timedelta(hours=9))


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    today = datetime.now(JST).strftime("%Y/%m/%d")
    registry = load_registry()
    collected = collect_all(save=True)
    cache = load_cache()

    munis: list[str] = []
    for r in registry:
        if r["自治体名"] not in munis:
            munis.append(r["自治体名"])
    reg_by_code = {r["自治体コード"]: r for r in registry}

    # 同じ場所・同じ種別の施設(同じ建物の別の部屋など)は1つのピンにまとめる
    groups: dict[tuple, dict] = {}
    placed: dict[str, int] = {}
    not_found = 0
    for p in collected["points"]:
        loc = cache.get(p["geo"])
        if not loc:
            not_found += 1
            continue
        lat, lng, exact = loc
        muni = reg_by_code[p["code"]]["自治体名"]
        key = (lat, lng, p["cat"])
        g = groups.setdefault(key, {"lat": lat, "lng": lng, "exact": exact, "cat": CATS.index(p["cat"]),
                                    "muni": munis.index(muni), "items": []})
        g["items"].append([p["name"], p["addr"], p["date"]])
        placed[p["code"]] = placed.get(p["code"], 0) + 1

    pins = [[g["lat"], g["lng"], g["cat"], g["muni"], g["exact"], g["items"]] for g in groups.values()]
    write_json(os.path.join(DATA, "points.json"), {"cats": CATS, "munis": munis, "pins": pins, "updated": today})

    # 自治体・種別ごとの件数表
    table = []
    for r in registry:
        code = r["自治体コード"]
        st = collected["stats"].get(code, {})
        table.append({
            "code": code, "muni": r["自治体名"],
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
    page = render(table, total, by_cat, today, prev["date"] if prev else "", not_found)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    open(os.path.join(DOCS, ".nojekyll"), "a").close()
    print(f"地図ページを作りました: ピン {len(pins)}個 / 施設 {sum(by_cat.values())}件 / 位置不明 {not_found}件")


def render(table, total, by_cat, today, prev_date, not_found) -> str:
    e = html.escape
    rows = []
    for t in table:
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
            f'<tr data-muni="{e(t["muni"])}"><td>{e(t["muni"])}</td><td><span class="dot c{CATS.index(t["kind"]) if t["kind"] in CATS else 2}"></span>{e(t["kind"])}</td>'
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
        diff_note=diff_note, nf_note=nf_note, site_url=SITE_URL,
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>民泊・旅館業マップ｜東京23区・大阪</title>
<meta name="description" content="東京23区と大阪の自治体が公表している民泊（住宅宿泊事業）・特区民泊・旅館業の施設を地図にまとめました。毎週自動更新。">
<meta property="og:type" content="website">
<meta property="og:title" content="民泊・旅館業マップ｜{total}件を地図で">
<meta property="og:description" content="東京23区と大阪の自治体が公表している民泊・特区民泊・旅館業の施設を地図にまとめました。毎週自動更新。">
<meta property="og:url" content="{site_url}">
<meta name="twitter:card" content="summary">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏠</text></svg>">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.min.css">
<style>
:root{{
  --bg:#f6f5f2;--card:#fff;--ink:#1c1b19;--sub:#5f5c56;--line:#e3e0d9;--accent:#1d4ed8;--chip:#efede8;
  --c0:#e11d48;--c1:#9333ea;--c2:#2563eb;--c3:#0d9488;--up:#dc2626;--down:#2563eb;
}}
@media (prefers-color-scheme: dark){{
  :root:not([data-theme="light"]){{
    --bg:#16161a;--card:#202026;--ink:#ecebe8;--sub:#a8a59f;--line:#34343c;--accent:#7aa2ff;--chip:#2c2c33;
    --c0:#fb7185;--c1:#c084fc;--c2:#60a5fa;--c3:#2dd4bf;--up:#f87171;--down:#7aa2ff;
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
.stats{{display:flex;gap:14px;flex-wrap:wrap;font-size:.85rem;color:var(--sub)}}
.stats b{{color:var(--ink);font-size:1.1rem}}
.filters{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0 8px}}
.tagbtn{{border:1.5px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
  padding:5px 12px;font-size:.88rem;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:6px}}
.tagbtn .n{{opacity:.7;font-size:.8em}}
.tagbtn[aria-pressed="false"]{{opacity:.45}}
.tagbtn[aria-pressed="false"] .dot{{background:transparent;border:2px solid var(--sub)}}
select{{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:.9rem;font-family:inherit}}
.dot{{display:inline-block;width:11px;height:11px;border-radius:50%;flex:none;vertical-align:-1px;margin-right:4px}}
.c0{{background:var(--c0)}}.c1{{background:var(--c1)}}.c2{{background:var(--c2)}}.c3{{background:var(--c3)}}
#map{{height:68vh;min-height:420px;border:1px solid var(--line);border-radius:12px;background:var(--chip)}}
.note{{font-size:.8rem;color:var(--sub);margin:6px 0}}
.pin{{border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.35)}}
.pin.approx{{border-style:dashed}}
.cl{{border-radius:50%;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;
  box-shadow:0 0 0 3px rgba(255,255,255,.7),0 1px 4px rgba(0,0,0,.4);font-size:12px}}
.leaflet-popup-content{{font-family:inherit;font-size:13px;line-height:1.5;max-height:280px;overflow:auto;margin:10px 12px}}
.pop h3{{font-size:13px;margin:0 0 4px}}
.pop ul{{margin:0;padding-left:1.1em}}
.pop li{{margin:3px 0}}
.pop .d{{color:#666;font-size:12px}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:.88rem;min-width:620px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{background:var(--chip);font-weight:600;position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.up{{color:var(--up);font-weight:700}}.down{{color:var(--down);font-weight:700}}
.none{{color:var(--sub)}}
.memo{{font-size:.78rem;color:var(--sub)}}
tr.hide{{display:none}}
footer{{font-size:.8rem;color:var(--sub);padding:20px 0 40px;border-top:1px solid var(--line);margin-top:28px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>🏠 民泊・旅館業マップ</h1>
  <p class="lead">東京23区と大阪の自治体が公表している、民泊（住宅宿泊事業）・特区民泊・旅館業の施設を地図にまとめました。毎週月曜に自動で更新しています。</p>
  <div class="stats"><span>掲載 <b>{total}</b> 件</span><span>最終更新 <b>{today}</b></span></div>
</header>

<div class="filters" role="group" aria-label="種別で絞り込み">
  {cat_btns}
  <select id="muni" aria-label="自治体を選ぶ"><option value="">すべての自治体</option></select>
</div>
<div id="map" role="region" aria-label="施設の地図"></div>
<p class="note">ピンを押すと施設名・住所が見られます。数字の丸は近くの施設をまとめたもので、拡大すると分かれます。点線のピンは住所から位置をおおまかにしか特定できなかった施設です。{nf_note}</p>

<h2>自治体ごとの件数</h2>
<p class="note">{diff_note}「データなし」は、自治体が一覧を公開していない、またはファイルを自動で読み取れなかったものです。</p>
<div class="tablewrap"><table>
<thead><tr><th>自治体</th><th>種別</th><th style="text-align:right">件数</th><th style="text-align:right">増減</th><th>資料の日付</th><th>出典</th></tr></thead>
<tbody id="tbody">
{rows}
</tbody></table></div>

<footer>
  <p>各自治体が公表している一覧（PDF・Excel・CSV）をもとに自動で作成しています。地図上の位置は住所から自動で推定したもので、ずれている場合があります。正確な情報は各自治体の公表資料をご確認ください。</p>
  <p>個人の氏名・電話番号は掲載していません。地図：<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>／位置の推定：国土地理院 住所検索</p>
</footer>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.min.js"></script>
<script>
(async function(){{
  const map = L.map('map', {{preferCanvas:true}}).setView([35.68, 139.76], 11);
  L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>'
  }}).addTo(map);

  const css = getComputedStyle(document.documentElement);
  const colors = [0,1,2,3].map(i => css.getPropertyValue('--c'+i).trim());
  const esc = s => String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));

  const res = await fetch('data/points.json');
  const data = await res.json();

  const sel = document.getElementById('muni');
  data.munis.forEach((m, i) => {{ const o = document.createElement('option'); o.value = i; o.textContent = m; sel.appendChild(o); }});

  const cluster = L.markerClusterGroup({{
    chunkedLoading: true, maxClusterRadius: 50, showCoverageOnHover: false,
    iconCreateFunction: c => {{
      const ms = c.getAllChildMarkers();
      let n = 0; const tally = [0,0,0,0];
      ms.forEach(m => {{ n += m.options.n; tally[m.options.cat] += m.options.n; }});
      const top = tally.indexOf(Math.max(...tally));
      const size = n < 10 ? 30 : n < 100 ? 36 : n < 1000 ? 44 : 52;
      return L.divIcon({{html: '<div class="cl" style="width:'+size+'px;height:'+size+'px;background:'+colors[top]+'">'+n.toLocaleString()+'</div>',
        className: '', iconSize: [size, size]}});
    }}
  }});

  const markers = data.pins.map(p => {{
    const [lat, lng, cat, muni, exact, items] = p;
    const n = items.length;
    const size = n > 1 ? 16 : 12;
    const icon = L.divIcon({{className: '', iconSize: [size, size],
      html: '<div class="pin'+(exact ? '' : ' approx')+'" style="width:'+size+'px;height:'+size+'px;background:'+colors[cat]+'"></div>'}});
    const m = L.marker([lat, lng], {{icon, n, cat, muni}});
    m.bindPopup(() => {{
      const list = items.map(it => '<li><b>'+esc(it[0] || '（施設名の記載なし）')+'</b><br>'+esc(it[1])+(it[2] ? '<br><span class="d">'+esc(it[2])+'</span>' : '')+'</li>').join('');
      return '<div class="pop"><h3><span class="dot c'+cat+'"></span>'+esc(data.cats[cat])+'（'+esc(data.munis[muni])+'）'+(n > 1 ? ' '+n+'件' : '')+'</h3>'
        + (exact ? '' : '<div class="d">※位置はおおよそです</div>') + '<ul>'+list+'</ul></div>';
    }});
    return m;
  }});

  const btns = [...document.querySelectorAll('.tagbtn')];
  function refresh(fit){{
    const cats = new Set(btns.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => +b.dataset.cat));
    const muni = sel.value === '' ? null : +sel.value;
    const shown = markers.filter(m => cats.has(m.options.cat) && (muni === null || m.options.muni === muni));
    cluster.clearLayers();
    cluster.addLayers(shown);
    if (fit && shown.length) map.fitBounds(L.latLngBounds(shown.map(m => m.getLatLng())), {{padding: [20, 20], maxZoom: 15}});
    document.querySelectorAll('#tbody tr').forEach(tr => tr.classList.toggle('hide', muni !== null && tr.dataset.muni !== data.munis[muni]));
  }}
  btns.forEach(b => b.addEventListener('click', () => {{
    b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); refresh(false);
  }}));
  sel.addEventListener('change', () => {{
    if (sel.value === '') {{ map.setView([35.68, 139.76], 11); refresh(false); }} else refresh(true);
  }});
  map.addLayer(cluster);
  refresh(false);
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
