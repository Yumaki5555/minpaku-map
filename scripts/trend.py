"""自治体・種別ごとの件数の移り変わりを、表(CSV)とグラフのページにする。

- docs/data/件数の記録.csv: 自治体・種別ごとに、一覧の版(資料の日付)ごとの件数を1行ずつ並べた表(エクセルで開ける)
- docs/trend.html        : 全体の合計(TOTAL_PAUSED の間は止めている)と、自治体・種別ごとの件数の推移グラフのページ

元になる記録は docs/data/history.json(地図を作るたびに、件数か資料の日付が変わっていれば1回分を書き足す)。
先頭の "partial": true の回は、記録を始める前の版(手元に残っていた資料やウェブ魚拓の資料を数え直したもの)で、
一部の自治体の分しか無いため、自治体ごとの版には使うが、全体の合計には使わない。
build_site.py から呼ばれる。
"""
from __future__ import annotations

import calendar
import csv
import html
import io
import json
import math
from datetime import datetime, timedelta

CSV_NAME = "件数の記録.csv"


def versions(history: list[dict], code: str) -> list[dict]:
    """その自治体・種別の一覧の版ごとの件数を、資料の日付の順に並べたもの。

    返す形: [{"date": 資料の日付(分からなければ記録した日), "doc": 資料の日付(分からなければ空),
              "count": 件数, "recorded": 記録した日}, ...]
    - 件数も資料の日付も前と同じなら同じ版とみなす(資料の日付が分からない自治体は件数だけで判断)。
    - 同じ資料の日付で件数が変わったときは、読み取りの直しとみなして新しい件数で置き換える。
    - 件数0(データなし・読み取り失敗)の回は飛ばす。
    - 新しい回の資料の日付が前の版より前だったら、新しい方の読み取りを正しいとみなし、それより後の版は消す。
    """
    out: list[dict] = []
    for snap in history:
        count = snap["counts"].get(code, 0)
        if not count:
            continue
        doc = snap.get("dates", {}).get(code) or ""
        if out and out[-1]["count"] == count and (not doc or out[-1]["doc"] == doc):
            continue
        key = doc or snap["date"]
        out = [v for v in out if v["date"] < key]
        out.append({"date": key, "doc": doc, "count": count, "recorded": snap["date"]})
    return out


KINDS = [(0, "民泊"), (1, "特区民泊"), (2, "旅館業")]
# 資料に載っている施設が、自治体コードの末尾とは別の種別だけだったもの(八尾市の「民泊」の一覧が特区民泊だけ、など)。
# 地図の種類分けと件数をそろえるため、build_site.py が中身を見て入れる
KIND_OVERRIDE: dict[str, int] = {}


def kind_of(code: str) -> int:
    """自治体コードの末尾(_民 / _民特 / _旅)から種別の番号(KINDS)を出す。"""
    if code in KIND_OVERRIDE:
        return KIND_OVERRIDE[code]
    return 1 if code.endswith("民特") else 0 if code.endswith("民") else 2


def full_snapshots(history: list[dict]) -> list[dict]:
    """全自治体の件数がそろっている回(記録を始める前の版を数え直した "partial" の回を除く)。"""
    return [s for s in history if not s.get("partial")]


AXIS_MONTHS = 3  # グラフの横軸の長さ(最近何か月分か)

# 全体(全地域)の合計のグラフを止めている理由(推移のページとトップページに出す文)。空にすると、また合計のグラフを出す。
# 2026/10/26 に北海道・愛知・兵庫・福岡(続けて宮城・静岡・京都・広島・沖縄)を加えたが、加えた地域にはそれより前の件数の記録が無い。totals() の数え方では
# 加えた地域がグラフの期間ずっと今と同じ件数だったことになり、合計の増減を正しく表せないため止めた(ユーザーの指示)。
# 都道府県ごとの合計は、加えた地域とそれまでの地域が混ざらないので出したままにする
TOTAL_PAUSED = ("2026年10月26日に北海道・宮城県・静岡県・愛知県・京都府・兵庫県・広島県・福岡県・沖縄県を加えました。加えた地域は今回が初めての記録で、"
                "それより前の件数が分からないため、全地域を足した合計がどれだけ増えたか・減ったかを正しく表せません。")


def axis_range(all_versions: list[list[dict]]) -> tuple[datetime, datetime]:
    """全グラフ共通の横軸(資料の日付)の範囲。
    終わりは一番新しい資料の日付。始まりは、その AXIS_MONTHS か月前の同じ日
    (それより前の古い資料は、グラフの左端に◀で置く)。"""
    end = max(_day(v["date"]) for vs in all_versions for v in vs)
    y, m = divmod(end.year * 12 + end.month - 1 - AXIS_MONTHS, 12)
    start = datetime(y, m + 1, min(end.day, calendar.monthrange(y, m + 1)[1]))
    return start, end


def totals(per_code: dict[str, list[dict]], start: datetime) -> list[dict]:
    """資料の日付で見た全体の合計と、種別ごとの内訳。

    合計が動くのは、どこかの自治体の資料の日付が新しくなり、件数が変わった日だけ。
    前の版が分からない自治体は、分かっている一番古い件数のままとして足す。
    返す形: [{"date": 資料の日付, "count": 合計, "kinds": {種別: 件数}, "changes": [(code, 増減), ...]}, ...]
    (先頭はグラフの始まりの日。そこより前の増減は先頭の件数に含めてある)
    """
    events: list[tuple[str, str, int]] = []
    now = {k: 0 for k, _ in KINDS}
    for code, vs in per_code.items():
        now[kind_of(code)] += vs[-1]["count"]
        for p, v in zip(vs, vs[1:]):
            if v["count"] != p["count"]:
                events.append((v["date"], code, v["count"] - p["count"]))
    s = start.strftime("%Y/%m/%d")

    def at(day: str) -> dict[int, int]:  # その日の時点の件数 = 今の件数から、その日より後の増減を引いたもの
        kinds = dict(now)
        for d, code, delta in events:
            if d > day:
                kinds[kind_of(code)] -= delta
        return kinds

    out = []
    for day in [s] + sorted({d for d, _, _ in events if d > s}):
        kinds = at(day)
        changes = [(c, delta) for d, c, delta in events if d == day] if day != s else []
        out.append({"date": day, "count": sum(kinds.values()), "kinds": kinds, "changes": changes})
    return out


def records_csv(table: list[dict], history: list[dict], prefs: list[str]) -> str:
    """件数の記録(エクセルで開けるよう、先頭に印(BOM)を付けて保存する)。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["都道府県", "自治体", "種別", "資料の日付", "件数", "前の版からの増減", "記録した日"])
    for t in table:
        prev = None
        for v in versions(history, t["code"]):
            w.writerow([prefs[t["pref"]], t["muni"], t["kind"], v["doc"], v["count"],
                        "" if prev is None else f'{v["count"] - prev:+d}', v["recorded"]])
            prev = v["count"]
    return buf.getvalue()


# ---- グラフ(1枚ずつ、SVGで書く) ----

W, H = 250, 104          # グラフの大きさ(この比率のまま、カードの幅に合わせて伸び縮みする)
PL, PR, PT, PB = 40, 8, 9, 21  # 左(目盛り)・右・上・下(日付)の余白


def _day(s: str) -> datetime:
    return datetime.strptime(s, "%Y/%m/%d")


def _y_axis(values: list[int]) -> tuple[int, int, list[int]]:
    """縦軸の範囲と目盛り。小さな増減も見えるよう0から始めず、きりのよい数で区切る。
    ただし件数の4%未満の動きは大きく見せすぎないよう、幅は少なくとも件数の4%(最低2件)とる。"""
    lo, hi = min(values), max(values)
    span = max(hi - lo, hi * 0.04, 2)
    raw = span / 2
    k = 10 ** math.floor(math.log10(raw))
    step = next(m * k for m in (1, 2, 5, 10) if raw <= m * k)
    step = max(1, int(step))
    a, b = math.floor(lo / step) * step, math.ceil(hi / step) * step
    if a == b:
        a, b = a - step, b + step
    a = max(0, a)
    return a, b, list(range(a, b + 1, step))


def _months(x0: datetime, x1: datetime) -> list[datetime]:
    """x0 より後、x1 より前の各月の1日。"""
    out = []
    m = (x0.replace(day=1) + timedelta(days=32)).replace(day=1)
    while m < x1:
        out.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return out


def chart_svg(vs: list[dict], x0: datetime, x1: datetime, label: str) -> str:
    """1つの自治体・種別のグラフ。横軸(資料の日付)は全グラフ共通の x0〜x1。
    点は一覧の版(資料の日付)ごとに1つ打ち、点と点をまっすぐ結ぶ折れ線にして、一番新しい資料の日付で終える。
    x0 より前の資料は、一番新しいものだけを左端に◀で置き、そこからの線は点線にする(本当の日付は左端より前のため)。"""
    e = html.escape
    days = (x1 - x0).days
    diffs = [None] + [v["count"] - p["count"] for p, v in zip(vs, vs[1:])]
    shown = [(v, d) for v, d in zip(vs, diffs) if _day(v["date"]) >= x0]
    old = [(v, d) for v, d in zip(vs, diffs) if _day(v["date"]) < x0]
    if old:
        shown = [old[-1]] + shown
    a, b, ticks = _y_axis([v["count"] for v, _ in shown])
    if len(shown) == 1:  # 点が1つだけなら、目盛りはその件数だけ
        ticks = [shown[0][0]["count"]]
    fx = lambda day: PL + max(0, (day - x0).days) / days * (W - PL - PR)  # noqa: E731
    fy = lambda n: PT + (b - n) / (b - a) * (H - PT - PB)  # noqa: E731

    grid = "".join(
        f'<line x1="{PL}" x2="{W - PR}" y1="{fy(t):.1f}" y2="{fy(t):.1f}"/>'
        f'<text x="{PL - 5}" y="{fy(t) + 4:.1f}" text-anchor="end">{t:,}</text>'
        for t in ticks
    )
    months = "".join(f'<line class="tk" x1="{fx(m):.1f}" x2="{fx(m):.1f}" y1="{H - PB}" y2="{H - PB + 3}"/>'
                     for m in _months(x0, x1))
    pts = [(fx(_day(v["date"])), fy(v["count"])) for v, _ in shown]
    seg = lambda p, q: f"M{p[0]:.1f},{p[1]:.1f} L{q[0]:.1f},{q[1]:.1f}"  # noqa: E731
    segs = "".join(f'<path class="line{" dash" if old and i == 0 else ""}" d="{seg(p, q)}"/>'
                   for i, (p, q) in enumerate(zip(pts, pts[1:])))
    marks = "".join(
        f'<path class="old" d="M{x - 1:.1f},{y:.1f} l7,-4.5 v9 z"/>' if old and i == 0
        else f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4"/>'
        for i, (x, y) in enumerate(pts)
    )
    data = []
    for i, ((x, y), (v, d)) in enumerate(zip(pts, shown)):
        lines = ["グラフの期間より前の資料"] if old and i == 0 else []
        if d is not None:
            lines.append(f"前の版から {d:+,}")
        data.append([round(x, 1), round(y, 1), v["date"], v["count"], lines])
    desc = "、".join(f'{v["date"]} {v["count"]:,}件' for v in vs)
    # 期間内に点が無い(一番新しい資料も期間より前)ときは、空のグラフに見えないよう一言そえる
    none = (f'<text x="{(PL + W - PR) / 2:.1f}" y="{pts[0][1] - 10:.1f}" text-anchor="middle">この期間に新しい資料なし</text>'
            if old and len(shown) == 1 else "")
    return (
        f'<svg class="ch" viewBox="0 0 {W} {H}" tabindex="0" role="img" aria-label="{e(label)}の件数の推移：{e(desc)}"'
        f' data-p=\'{e(json.dumps(data, ensure_ascii=False))}\'>'
        f'<g class="grid">{grid}{months}</g>'
        f'<line class="cross" x1="0" x2="0" y1="{PT}" y2="{H - PB}" visibility="hidden"/>'
        f'{segs}<g class="dots">{marks}</g>{none}'
        f'<circle class="hl" r="6" cx="0" cy="0" visibility="hidden"/>'
        f'<text x="{PL}" y="{H - 5}" text-anchor="start">{x0:%Y/%m/%d}</text>'
        f'<text x="{W - PR}" y="{H - 5}" text-anchor="end">{x1:%Y/%m/%d}</text>'
        f"</svg>"
    )


def _pct(diff: int, base: int) -> str:
    return f"{diff / base * 100:+.1f}%" if base else ""


def _signed(d: int) -> str:
    cls = "up" if d > 0 else "down" if d < 0 else "zero"
    return f'<span class="{cls}">{d:+,}</span>' if d else '<span class="zero">±0</span>'


def render_hero(per_code: dict[str, list[dict]], names: dict[str, str], x0: datetime, x1: datetime,
                today: str) -> tuple[str, str]:
    """ページ一番上の「全体の合計」(数字と推移のグラフ)と、表で見る用の行。横軸は自治体のグラフと同じ資料の日付。
    グラフは画面の幅が変わっても文字の大きさが変わらないよう、ページを開いたときに描く(ここではデータだけ渡す)。"""
    e = html.escape
    tl = totals(per_code, x0)
    now, first = tl[-1], tl[0]
    a, b, ticks = _y_axis([t["count"] for t in tl])
    pts = []
    prev = None
    for t in tl:
        lines: list = []
        if prev is None:
            lines.append("グラフの始まりの時点（それより前の増減を含む）")
        else:
            lines.append(f'前の点から {t["count"] - prev:+,}')
            top = sorted(t["changes"], key=lambda x: -abs(x[1]))
            lines += [[kind_of(c), f"{names.get(c, c)} {d:+,}"] for c, d in top[:5]]
            if len(top) > 5:
                lines.append(f"ほか{len(top) - 5}件の自治体・種別")
        pts.append([(_day(t["date"]) - x0).days, t["count"], t["date"], lines])
        prev = t["count"]
    data = {"a": a, "b": b, "ticks": ticks, "span": (x1 - x0).days, "start": f"{x0:%Y/%m/%d}",
            "end": f"{x1:%Y/%m/%d}", "pts": pts,
            "label": "全体の合計の推移：" + "、".join(f'{t["date"]} {t["count"]:,}件' for t in tl)}

    m1 = (x1 - timedelta(days=30)).strftime("%Y/%m/%d")
    base1 = next((t for t in reversed(tl) if t["date"] <= m1), first)
    chg = (f'<li title="{e(m1)}より後の資料で">直近30日<span class="n">{_signed(now["count"] - base1["count"])}</span>'
           f'<span class="kd pct">{_pct(now["count"] - base1["count"], base1["count"])}</span></li>'
           f'<li>{x0:%Y/%m/%d}から<span class="n">{_signed(now["count"] - first["count"])}</span>'
           f'<span class="kd pct">{_pct(now["count"] - first["count"], first["count"])}</span></li>')
    kinds = "".join(
        f'<li><span class="dot c{k}"></span>{name}<span class="n">{now["kinds"][k]:,}件</span>'
        f'<span class="kd">{_signed(now["kinds"][k] - first["kinds"][k])}</span></li>'
        for k, name in KINDS if now["kinds"][k]
    )
    hero = f"""<section class="hero" id="hero" aria-labelledby="hero-h">
<div class="hstat">
  <h2 id="hero-h">全体の合計</h2>
  <div><span class="big">{now["count"]:,}</span><span class="unit">件</span></div>
  <div class="asof">各自治体の最新の資料の合計（{e(today)} 確認）</div>
  <ul class="hd" aria-label="合計の増減（資料の日付で）">{chg}</ul>
  <ul aria-label="種別ごとの内訳（増減はグラフの期間）">{kinds}</ul>
</div>
<div class="hchart" data-d='{e(json.dumps(data, ensure_ascii=False))}'></div>
<div class="tip" hidden></div>
<p class="note hnote">地図に載せている全自治体の件数の合計です。横軸は下の自治体ごとのグラフと同じ<b>資料の日付</b>で、どこかの自治体の資料の日付が新しくなり件数が変わった日に点を打ち、点と点を線で結んでいます（点にマウスを重ねると、その日に件数が変わった自治体が出ます）。前の版が分からない自治体は、分かっている一番古い件数のままとして足しています。種別ごとの内訳の増減は、グラフの期間のものです。</p>
</section>"""
    rows = []
    prev = None
    for t in tl:
        rows.append(f'<tr><td>{e(t["date"])}</td><td class="num"><b>{t["count"]:,}</b></td>'
                    + "".join(f'<td class="num">{t["kinds"][k]:,}</td>' for k, _ in KINDS)
                    + f'<td class="num">{"" if prev is None else _signed(t["count"] - prev)}</td></tr>')
        prev = t["count"]
    return hero, "\n".join(rows)


def render_page(table: list[dict], history: list[dict], today: str, prefs: list[str], cats: list[str],
                site_url: str) -> str:
    e = html.escape
    all_vs = {t["code"]: versions(history, t["code"]) for t in table}
    x0, x1 = axis_range([vs for vs in all_vs.values() if vs])
    items = []
    up = down = flat = 0
    last_pref = None
    order = 0
    kinds_used: list[int] = []
    names: dict[str, str] = {}
    for t in table:
        vs = all_vs[t["code"]]
        if not vs:
            continue
        kind = cats.index(t["kind"]) if t["kind"] in cats else 2
        if kind not in kinds_used:
            kinds_used.append(kind)
        if t["pref"] != last_pref:
            last_pref = t["pref"]
            items.append(f'<h2 class="pref" data-pref="{t["pref"]}" data-order="{order + 0.5}">{e(prefs[t["pref"]])}</h2>')
        order += 1
        kind_label = "旅館業" if kind == 2 else t["kind"]
        now = t["count"]
        if len(vs) >= 2 and now:
            d = vs[-1]["count"] - vs[-2]["count"]
            up, down, flat = up + (d > 0), down + (d < 0), flat + (d == 0)
            chg = (f'{_signed(d)} <span class="pct">{_pct(d, vs[-2]["count"]) if d else ""}</span>'
                   f'<span class="since">前回 {e(vs[-2]["date"])} は {vs[-2]["count"]:,}件</span>')
            diff_attr = str(d)
        else:
            flat += bool(now)
            chg = '<span class="since">まだ変化の記録なし</span>'
            diff_attr = ""
        val = (f'<b>{now:,}</b>件<span class="asof">{e(vs[-1]["date"])}時点</span>' if now
               else '<span class="none">データなし（最新の一覧を読み取れず）</span>')
        link = t["page"] or t["doc"]
        src = f'<a class="src" href="{e(link)}" target="_blank" rel="noopener">出典</a>' if link else ""
        name = f'{t["muni"]} {kind_label}'
        names[t["code"]] = name
        items.append(
            f'<article class="card" id="{e(t["code"])}" data-pref="{t["pref"]}" data-kind="{kind}" data-diff="{diff_attr}"'
            f' data-order="{order}">'
            f'<h3><span class="dot c{kind}"></span>{e(t["muni"])}<span class="kind">{e(kind_label)}</span>{src}</h3>'
            f'<div class="val">{val}</div><div class="chg">{chg}</div>'
            f'{chart_svg(vs, x0, x1, name)}<div class="tip" hidden></div></article>'
        )

    pref_opts = "".join(f'<option value="{i}">{e(p)}</option>' for i, p in enumerate(prefs)
                        if any(t["pref"] == i for t in table))
    kind_btns = "".join(
        f'<button type="button" class="tagbtn" data-kind="{k}" aria-pressed="true"><span class="dot c{k}"></span>'
        f'{"旅館業" if k == 2 else e(cats[k])}</button>'
        for k in sorted(kinds_used)
    )
    rows = []
    for t in table:
        prev = None
        for v in all_vs[t["code"]]:
            d = "" if prev is None else _signed(v["count"] - prev)
            rows.append(f'<tr><td>{e(prefs[t["pref"]])}</td><td>{e(t["muni"])}</td><td>{e(t["kind"])}</td>'
                        f'<td>{e(v["doc"])}</td><td class="num">{v["count"]:,}</td><td class="num">{d}</td>'
                        f'<td>{e(v["recorded"])}</td></tr>')
            prev = v["count"]
    if TOTAL_PAUSED:  # 全体の合計のグラフと表の代わりに、止めている理由を出す
        hero = PAUSED.format(note=e(TOTAL_PAUSED))
        total_table = ""
    else:
        # 全体の合計は、地図の合計とそろうよう、今の件数がある(データなしでない)自治体・種別だけで出す
        per_code = {t["code"]: all_vs[t["code"]] for t in table if t["count"] and all_vs[t["code"]]}
        hero, total_rows = render_hero(per_code, names, x0, x1, today)
        total_table = TOTAL_TABLE.format(total_rows=total_rows)
    return TEMPLATE.format(
        x0=f"{x0:%Y/%m/%d}", x1=f"{x1:%Y/%m/%d}",
        today=e(today), start=e(full_snapshots(history)[0]["date"]), items="\n".join(items), pref_opts=pref_opts,
        kind_btns=kind_btns, up=up, down=down, flat=flat, rows="\n".join(rows), csv=e(CSV_NAME), site_url=site_url,
        hero=hero, total_table=total_table, og_title="自治体ごと" if TOTAL_PAUSED else "全体の合計と自治体ごと",
    )


PAUSED = """<section class="paused" id="hero" aria-labelledby="paused-h">
<h2 id="paused-h">全体の合計のグラフは、表示を止めています</h2>
<p>{note}そのため、全体の合計のグラフと表は表示していません。自治体ごとのグラフは、下でこれまでどおりご覧いただけます。</p>
</section>"""

TOTAL_TABLE = """<p class="note"><b>全体の合計</b>（資料の日付で、件数が変わった日ごと）</p>
<div class="tablewrap"><table>
<thead><tr><th>資料の日付</th><th style="text-align:right">合計</th><th style="text-align:right">民泊</th><th style="text-align:right">特区民泊</th><th style="text-align:right">旅館業</th><th style="text-align:right">前の行から</th></tr></thead>
<tbody>
{total_rows}
</tbody></table></div>
"""


TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>件数の推移｜民泊・旅館業マップ</title>
<meta name="description" content="首都圏・北海道・宮城・静岡・愛知・京都・大阪・兵庫・広島・福岡・沖縄の自治体が公表している民泊・旅館業の件数を、一覧が更新されるたびに記録し、自治体ごとの増減をグラフにしています。">
<meta property="og:type" content="website">
<meta property="og:title" content="民泊・旅館業の件数の推移（{og_title}）">
<meta property="og:description" content="自治体の一覧が更新されるたびに件数を記録し、増減をグラフにしています。週2回自動更新。">
<meta property="og:url" content="{site_url}trend.html">
<meta name="twitter:card" content="summary">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%23b75035'/><g fill='white'><path d='M4 6h4v12h20v8h-4v-4H8v4H4z'/><rect x='10' y='12' width='6' height='4' rx='2'/><path d='M18 18v-4a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v4z'/></g></svg>">
<meta name="theme-color" content="#b75035">
<style>
:root{{
  color-scheme:light;
  --bg:#fff;--card:#fff;--ink:#1f2a24;--sub:#47534c;--line:#e6dfdb;--accent:#923d28;--chip:#faf6f4;
  --g:#b75035;--g2:#923d28;--gsoft:#fbefea;
  --c0:#e44c49;--c1:#8b5cf6;--c2:#1676d2;--c3:#e69b17;--up:#c62828;--down:#1d4ed8;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-size:17px;
  font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Yu Gothic UI","Meiryo",sans-serif;line-height:1.75}}
a{{color:var(--accent);text-underline-offset:3px}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 16px}}
/* 一番上の帯(地図のページと同じサイトの色) */
.brandbar{{background:var(--g)}}
.brandbar .wrap{{display:flex;align-items:center;min-height:60px}}
.logo{{display:inline-flex;align-items:center;gap:8px;font-weight:800;font-size:19px;color:#fff;text-decoration:none}}
.logo svg{{width:30px;height:30px;flex:none}}
header{{padding:16px 0 4px}}
.back{{display:inline-flex;align-items:center;gap:6px;min-height:46px;padding:6px 16px;border:2px solid var(--g);border-radius:12px;
  font-size:16px;font-weight:700;text-decoration:none;color:var(--g2);background:#fff}}
.back:hover{{background:var(--gsoft)}}
h1{{font-size:clamp(26px,4vw,34px);margin:14px 0 6px;letter-spacing:.02em;line-height:1.35}}
.lead{{color:var(--sub);margin:0 0 8px;font-size:17px}}
.lead b{{color:var(--ink)}}
.note{{font-size:15.5px;color:var(--sub);margin:8px 0}}
.summary{{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0 4px;font-size:17px}}
.summary b{{font-size:1.3rem;margin:0 2px}}
h2.sec{{font-size:clamp(22px,3vw,28px);margin:36px 0 0}}
.hero{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px 8px;margin:10px 0 0;position:relative;
  display:grid;grid-template-columns:270px minmax(0,1fr);gap:6px 24px;align-items:start}}
@media (max-width:760px){{.hero{{grid-template-columns:minmax(0,1fr)}}}}
.hero h2{{font-size:20px;margin:0}}
.hero .big{{font-size:3rem;font-weight:700;line-height:1.15}}
.hero .unit{{font-size:1rem;color:var(--sub);margin-left:3px}}
.hero .asof{{color:var(--sub);margin:0}}
.hero ul{{list-style:none;margin:8px 0 0;padding:8px 0 0;border-top:1px solid var(--line);font-size:16px}}
.hero ul.hd{{border-top:0;padding-top:0;margin-top:4px}}
.hero .kd.pct{{font-size:.78rem}}
.hero li{{display:flex;align-items:center;gap:2px;line-height:1.8}}
.hero li .n{{margin-left:auto;font-variant-numeric:tabular-nums}}
.hero li .kd{{min-width:4.2em;text-align:right;font-variant-numeric:tabular-nums}}
.hero .hchart{{min-width:0;min-height:170px}}
.hero svg.ch{{--s:var(--ink);margin-top:0}}
.hero .hnote{{grid-column:1/-1;margin:2px 0 0}}
.paused{{background:#fdf6e7;border:1px solid #f3d9a6;border-radius:12px;padding:12px 16px;margin:10px 0 0}}
.paused h2{{font-size:19px;margin:0 0 2px}}
.paused p{{margin:0;font-size:16px}}
.lk{{display:inline-block;width:12px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}}
.filters{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0 6px;padding:10px 0;border-top:1px solid var(--line)}}
/* 種類の切り替え(トップページと同じ、大きなチェックの形) */
.tagbtn{{border:2px solid #cfc5bf;background:#fff;color:var(--ink);border-radius:12px;min-height:50px;
  padding:6px 14px 6px 10px;font-size:16px;font-weight:700;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:8px}}
.tagbtn::before{{content:"";width:24px;height:24px;border-radius:7px;border:2.5px solid #cfc5bf;background:#fff;flex:none}}
.tagbtn[aria-pressed="true"]{{border-color:var(--g);background:var(--gsoft)}}
.tagbtn[aria-pressed="true"]::before{{background:var(--g) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M5 12.5l4.5 4.5L19 7.5' fill='none' stroke='white' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/18px no-repeat;border-color:var(--g)}}
.tagbtn[aria-pressed="false"] .dot{{opacity:.45}}
select{{min-height:48px;padding:8px 12px;border:2px solid #cfc5bf;border-radius:10px;background:#fff;color:var(--ink);font-size:17px;font-family:inherit;max-width:100%}}
label.chk{{font-size:16px;font-weight:700;display:inline-flex;align-items:center;gap:8px;cursor:pointer;min-height:48px}}
label.chk input{{width:24px;height:24px;accent-color:var(--g)}}
.shown{{font-size:16px;color:var(--sub);margin-left:auto}}
.totop{{position:fixed;right:16px;bottom:16px;z-index:50;display:inline-flex;align-items:center;gap:6px;min-height:52px;padding:8px 18px;
  border:2px solid var(--g);border-radius:999px;background:var(--g);color:#fff;font:inherit;font-size:16px;font-weight:800;cursor:pointer;box-shadow:0 3px 10px rgba(0,0,0,.2)}}
.totop[hidden]{{display:none}}
.dot{{display:inline-block;width:11px;height:11px;border-radius:50%;flex:none;vertical-align:-1px;margin-right:5px}}
.c0{{background:var(--c0)}}.c1{{background:var(--c1)}}.c2{{background:var(--c2)}}.c3{{background:var(--c3)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}}
.pref{{grid-column:1/-1;font-size:1.1rem;margin:16px 0 0}}
.pref:first-child{{margin-top:4px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px 6px;position:relative;scroll-margin-top:16px}}
.card:target{{outline:2px solid var(--accent);outline-offset:1px}}
.card[hidden],.pref[hidden]{{display:none}}
.card h3{{font-size:18px;margin:0;display:flex;align-items:center}}
.card .kind{{font-weight:400;color:var(--sub);font-size:15px;margin-left:6px}}
.card .src{{margin-left:auto;font-size:15px;font-weight:700}}
.val{{font-size:15px;color:var(--sub);line-height:1.3;margin-top:4px}}
.val b{{font-size:1.7rem;color:var(--ink);margin-right:2px}}
.asof{{font-size:14.5px;margin-left:8px}}
.chg{{font-size:16px;min-height:2.7em;line-height:1.45;margin-top:2px}}
.up{{color:var(--up);font-weight:700}}.down{{color:var(--down);font-weight:700}}.zero{{color:var(--sub);font-weight:700}}
.pct{{color:var(--sub);font-size:14.5px}}
.since{{display:block;font-size:14.5px;color:var(--sub)}}
.none{{color:var(--sub);font-size:15px}}
svg.ch{{display:block;width:100%;height:auto;margin-top:4px;overflow:visible;outline:none;touch-action:pan-y}}
svg.ch:focus-visible{{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}}
svg.ch .grid line{{stroke:var(--line);stroke-width:1}}
svg.ch text{{fill:var(--sub);font-size:12px;font-variant-numeric:tabular-nums}}
svg.ch .line{{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
svg.ch .line.dash{{stroke-dasharray:2 4;opacity:.7}}
svg.ch .old{{stroke:var(--card);stroke-width:1.5;stroke-linejoin:round}}
svg.ch .dots circle{{stroke:var(--card);stroke-width:2}}
svg.ch .cross{{stroke:var(--sub);stroke-width:1}}
svg.ch .hl{{fill:none;stroke-width:2}}
svg.ch .tk{{stroke:var(--line);stroke-width:1}}
.card[data-kind="0"] svg.ch{{--s:var(--c0)}}.card[data-kind="1"] svg.ch{{--s:var(--c1)}}
.card[data-kind="2"] svg.ch{{--s:var(--c2)}}.card[data-kind="3"] svg.ch{{--s:var(--c3)}}
svg.ch .line,svg.ch .hl{{stroke:var(--s)}}
svg.ch .dots circle,svg.ch .old{{fill:var(--s)}}
.tip{{position:absolute;z-index:5;pointer-events:none;background:var(--card);color:var(--ink);border:1px solid var(--line);
  border-radius:10px;box-shadow:0 3px 10px rgba(0,0,0,.18);padding:6px 10px;font-size:15px;line-height:1.45;white-space:nowrap;
  transform:translate(-50%,calc(-100% - 10px))}}
.tip b{{font-size:.95rem}}
.tip .d{{color:var(--sub)}}
.tip[hidden]{{display:none}}
details.all{{margin:28px 0 0;font-size:16px}}
details.all summary{{cursor:pointer;color:var(--accent);font-weight:800;padding:10px 0;min-height:44px}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card);margin-top:6px;max-height:70vh}}
table{{border-collapse:collapse;width:100%;font-size:15.5px;min-width:560px}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{background:var(--chip);font-weight:600;position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
footer{{font-size:15.5px;color:var(--sub);padding:24px 0 90px;border-top:1px solid var(--line);margin-top:40px}}
</style>
</head>
<body>
<div class="brandbar"><div class="wrap"><a class="logo" href="./"><svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#fff"/><g fill="#b75035"><path d="M4 6h4v12h20v8h-4v-4H8v4H4z"/><rect x="10" y="12" width="6" height="4" rx="2"/><path d="M18 18v-4a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v4z"/></g></svg>民泊・旅館業マップ</a></div></div>
<div class="wrap">
<header>
  <a class="back" href="./">← 民泊・旅館業マップ（地図・グラフ）に戻る</a>
  <h1>📈 民泊・旅館業の件数の推移</h1>
  <p class="lead">地図に載せている民泊・旅館業の件数を、自治体の一覧が更新されるたびに記録しています（記録開始 {start}／最終確認 <b>{today}</b>・毎週月曜・木曜に自動更新）。</p>
</header>

{hero}

<section>
  <h2 class="sec">自治体ごとの推移</h2>
  <div class="summary" aria-label="直近の一覧での増減">
    <span>直近の一覧で 増えた <b class="up">{up}</b></span><span>減った <b class="down">{down}</b></span><span>変わらない・変化の記録なし <b>{flat}</b></span><span class="note" style="margin:0">（自治体・種別の数）</span>
  </div>
  <p class="note">グラフの横軸は<b>全グラフ共通の資料の日付</b>（自治体の一覧が「いつ時点」のものか。{x0}〜{x1}）です。点は、その自治体の資料の日付が新しくなったときだけ増え（資料1つにつき点1つ）、点と点を線で結んで、一番新しい資料の日付で終わります。{x0}より前の古い資料は、左端に◀で置き、そこからの線は点線にしています。縦軸はグラフごとに目盛りが違い、小さな増減も見えるよう0から始めていません（数字は目盛りでご確認ください）。点にマウスを重ねる（スマホは押す）と、その時点の件数が出ます。<br>
  自動で読み取っているため、同じ日付の一覧の読み取りを直したときは、記録も直した件数に置き換えています。すべての記録は <a href="data/{csv}" download>表（CSV・エクセルで開けます）</a> でダウンロードできます。</p>
</section>

<div class="filters" role="group" aria-label="絞り込みと並び順">
  <select id="pref" aria-label="都道府県"><option value="">すべての都道府県</option>{pref_opts}</select>
  {kind_btns}
  <label class="chk"><input type="checkbox" id="chg">増減があったものだけ</label>
  <select id="sort" aria-label="並び順">
    <option value="area">地域順</option>
    <option value="up">増えた数が多い順</option>
    <option value="down">減った数が多い順</option>
  </select>
  <span id="shown" class="shown" aria-live="polite"></span>
</div>

<div class="grid" id="grid">
{items}
</div>

<details class="all">
<summary>すべての記録を表で見る</summary>
{total_table}<p class="note"><b>自治体・種別ごと</b>（一覧の版ごと）</p>
<div class="tablewrap"><table>
<thead><tr><th>都道府県</th><th>自治体</th><th>種別</th><th>資料の日付</th><th style="text-align:right">件数</th><th style="text-align:right">前の版から</th><th>記録した日</th></tr></thead>
<tbody>
{rows}
</tbody></table></div>
<p class="note">資料の日付が空欄のものは、資料から日付を読み取れなかったため、グラフでは記録した日の位置に置いています。</p>
</details>

<button type="button" class="totop" id="totop" hidden>↑ 上へ戻る</button>
<footer>
  <p>各自治体が公表している一覧（PDF・Excel・CSV）から自動で数えた件数です。自治体によって公表範囲が違い、すべての施設が載っているわけではありません。正確な情報は各自治体の公表資料をご確認ください。</p>
  <p><a href="./">民泊・旅館業マップ</a></p>
</footer>
</div>

<script>
(function(){{
  // グラフ: マウスを重ねる・押す・矢印キーで、いちばん近い時点の件数を吹き出しで出す
  const fmt = n => n.toLocaleString('ja-JP');
  function bind(svg) {{
    const pts = JSON.parse(svg.dataset.p);
    const card = svg.closest('.card, .hero'), tip = card.querySelector('.tip');
    const cross = svg.querySelector('.cross'), hl = svg.querySelector('.hl');
    const W = svg.viewBox.baseVal.width;
    let cur = -1;
    function show(i) {{
      cur = i;
      const [x, y, date, n, lines] = pts[i];
      cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
      hl.setAttribute('cx', x); hl.setAttribute('cy', y); hl.setAttribute('visibility', 'visible');
      tip.replaceChildren();
      const v = document.createElement('b'); v.textContent = fmt(n) + '件';
      const dt = document.createElement('span'); dt.className = 'd'; dt.textContent = ' ' + date + '時点';
      tip.append(v, dt);
      lines.forEach(l => {{
        const c = document.createElement('div');
        c.className = 'd';
        if (Array.isArray(l)) {{  // [種別の番号, 文]: 先頭に種別の色の短い線を付ける
          const k = document.createElement('span');
          k.className = 'lk c' + l[0];
          c.append(k, l[1]);
        }} else c.textContent = l;
        tip.append(c);
      }});
      tip.hidden = false;
      const r = svg.getBoundingClientRect(), cr = card.getBoundingClientRect(), s = r.width / W;
      const half = tip.offsetWidth / 2;
      const left = Math.min(Math.max(r.left - cr.left + x * s, half + 4), cr.width - half - 4);
      tip.style.left = left + 'px';
      tip.style.top = (r.top - cr.top + y * s) + 'px';
    }}
    function hide() {{
      cur = -1; tip.hidden = true;
      cross.setAttribute('visibility', 'hidden'); hl.setAttribute('visibility', 'hidden');
    }}
    function nearest(ev) {{
      const r = svg.getBoundingClientRect();
      const x = (ev.clientX - r.left) * W / r.width;
      let best = 0;
      pts.forEach((p, i) => {{ if (Math.abs(p[0] - x) < Math.abs(pts[best][0] - x)) best = i; }});
      return best;
    }}
    svg.addEventListener('pointermove', ev => {{ const i = nearest(ev); if (i !== cur) show(i); }});
    svg.addEventListener('pointerdown', ev => show(nearest(ev)));
    svg.addEventListener('pointerleave', ev => {{ if (ev.pointerType === 'mouse') hide(); }});
    svg.addEventListener('focus', () => show(pts.length - 1));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', ev => {{
      if (ev.key === 'ArrowLeft' && cur > 0) {{ show(cur - 1); ev.preventDefault(); }}
      else if (ev.key === 'ArrowRight' && cur < pts.length - 1) {{ show(cur + 1); ev.preventDefault(); }}
      else if (ev.key === 'Escape') hide();
    }});
  }}
  document.querySelectorAll('svg.ch').forEach(bind);
  // スマホで吹き出しを出したあと、グラフの外を押したら閉じる
  document.addEventListener('pointerdown', ev => {{
    if (!ev.target.closest('svg.ch')) document.querySelectorAll('.tip').forEach(t => {{ if (!t.hidden) t.closest('.card, .hero').querySelector('svg.ch').dispatchEvent(new Event('blur')); }});
  }});

  // 全体の合計のグラフ: 表示する幅ちょうどの大きさで描く(幅が変わったら描き直す)
  const hbox = document.querySelector('.hero .hchart');
  if (hbox) {{
    const D = JSON.parse(hbox.dataset.d);
    const r1 = v => Math.round(v * 10) / 10;
    let lastW = 0;
    const draw = () => {{
      const w = Math.round(hbox.clientWidth);
      if (!w || w === lastW) return;
      lastW = w;
      const h = w < 480 ? 170 : 200, pr = 10, pt = 10, pb = 24;
      const pl = Math.max(...D.ticks.map(t => 12 + 6.6 * fmt(t).length));
      const fx = d => pl + (D.span ? d / D.span : 1) * (w - pl - pr);
      const fy = n => pt + (D.b - n) / (D.b - D.a) * (h - pt - pb);
      const P = D.pts.map(p => [r1(fx(p[0])), r1(fy(p[1])), p[2], p[1], p[3]]);
      let s = '<g class="grid">' + D.ticks.map(t => '<line x1="' + pl + '" x2="' + (w - pr) + '" y1="' + r1(fy(t)) + '" y2="' + r1(fy(t)) + '"/>'
        + '<text x="' + (pl - 5) + '" y="' + r1(fy(t) + 4) + '" text-anchor="end">' + fmt(t) + '</text>').join('') + '</g>';
      // 月の区切り(両端の日付や隣の月の文字と重ならないものだけ)
      const [sy, sm, sd] = D.start.split('/').map(Number);
      const t0 = Date.UTC(sy, sm - 1, sd);
      let minX = pl + 75;
      for (let m = Date.UTC(sy, sm, 1); (m - t0) / 864e5 < D.span; ) {{
        const md = new Date(m), x = r1(fx((m - t0) / 864e5));
        if (x >= minX && x <= w - pr - 80) {{
          const lab = md.getUTCMonth() === 0 ? md.getUTCFullYear() + '年1月' : (md.getUTCMonth() + 1) + '月';
          s += '<line class="tk" x1="' + x + '" x2="' + x + '" y1="' + (h - pb) + '" y2="' + (h - pb + 4) + '"/>'
            + '<text x="' + x + '" y="' + (h - 6) + '" text-anchor="middle">' + lab + '</text>';
          minX = x + 55;
        }}
        m = Date.UTC(md.getUTCFullYear(), md.getUTCMonth() + 1, 1);
      }}
      s += '<line class="cross" x1="0" x2="0" y1="' + pt + '" y2="' + (h - pb) + '" visibility="hidden"/>';
      // 件数が変わった日ごとの点を、まっすぐ結ぶ折れ線にする
      s += '<path class="line" d="M' + P.map(p => p[0] + ',' + p[1]).join(' L') + '"/>'
        + '<g class="dots">' + P.map(p => '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="3.5"/>').join('') + '</g>'
        + '<circle class="hl" r="6" cx="0" cy="0" visibility="hidden"/>'
        + '<text x="' + pl + '" y="' + (h - 6) + '" text-anchor="start">' + D.start + '</text>'
        + '<text x="' + (w - pr) + '" y="' + (h - 6) + '" text-anchor="end">' + D.end + '</text>';
      hbox.innerHTML = '<svg class="ch" viewBox="0 0 ' + w + ' ' + h + '" tabindex="0" role="img">' + s + '</svg>';
      const svg = hbox.firstChild;
      svg.setAttribute('aria-label', D.label);
      svg.dataset.p = JSON.stringify(P);
      hbox.parentNode.querySelector('.tip').hidden = true;
      bind(svg);
    }};
    draw();
    if (window.ResizeObserver) new ResizeObserver(draw).observe(hbox);
    window.addEventListener('resize', draw);
  }}

  // 絞り込みと並び順
  const grid = document.getElementById('grid');
  const items = [...grid.children];
  const cards = items.filter(el => el.classList.contains('card'));
  const pref = document.getElementById('pref'), chg = document.getElementById('chg'), sort = document.getElementById('sort');
  const btns = [...document.querySelectorAll('.tagbtn')];
  const shown = document.getElementById('shown');
  function apply() {{
    const kinds = new Set(btns.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.kind));
    let n = 0;
    cards.forEach(c => {{
      const ok = (pref.value === '' || c.dataset.pref === pref.value) && kinds.has(c.dataset.kind)
        && (!chg.checked || (c.dataset.diff !== '' && c.dataset.diff !== '0'));
      c.hidden = !ok; n += ok;
    }});
    const byArea = sort.value === 'area';
    items.filter(el => el.classList.contains('pref')).forEach(h => {{
      h.hidden = !byArea || !cards.some(c => !c.hidden && c.dataset.pref === h.dataset.pref);
    }});
    const diff = c => c.dataset.diff === '' ? null : +c.dataset.diff;
    const sorted = byArea ? items.slice().sort((a, b) => a.dataset.order - b.dataset.order)
      : cards.slice().sort((a, b) => {{
          const x = diff(a), y = diff(b);
          if (x === null || y === null) return (x === null) - (y === null) || a.dataset.order - b.dataset.order;
          return sort.value === 'up' ? y - x : x - y;
        }});
    sorted.forEach(el => grid.appendChild(el));
    shown.textContent = n + '件のグラフ';
  }}
  btns.forEach(b => b.addEventListener('click', () => {{
    b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); apply();
  }}));
  [pref, chg, sort].forEach(el => el.addEventListener('change', apply));
  apply();

  // 長いページなので、下へ読み進めたら「上へ戻る」ボタンを出す
  const totop = document.getElementById('totop');
  window.addEventListener('scroll', () => {{ totop.hidden = window.scrollY < 700; }}, {{passive: true}});
  totop.addEventListener('click', () => window.scrollTo({{top: 0, behavior: 'smooth'}}));
}})();
</script>
</body>
</html>
"""
