/* 民泊・旅館業マップの動き。build_site.py が docs/app.js に写す。
   ページに入れてある数(#site-data)と、地図の点(data/points.json)から、地図・グラフ・ランキング・一覧表を描く。
   地域・市区町村・施設の種類の「選んでいる条件」は1か所(S)で持ち、変わったら全部の部品を描き直す。 */
(function () {
  'use strict';
  const D = JSON.parse(document.getElementById('site-data').textContent);
  const CATS = D.cats;                                   // ['民泊','特区民泊','旅館・ホテル','簡易宿所']
  const KINDS = ['民泊', '特区民泊', '旅館業'];          // 件数の記録(資料)の種類。旅館業 = 旅館・ホテル + 簡易宿所
  const KIND_CATS = [[0], [1], [2, 3]];
  const KIND_ICON = [0, 1, 2];
  const css = getComputedStyle(document.documentElement);
  const COLORS = [0, 1, 2, 3].map(i => css.getPropertyValue('--c' + i).trim());
  const KCOLOR = k => COLORS[KIND_ICON[k]];
  const HOME = [35.68, 139.76];
  const fmt = n => Number(n).toLocaleString('ja-JP');
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const signed = d => d > 0 ? '▲+' + fmt(d) : d < 0 ? '▼' + fmt(d) : '±0';
  const signCls = d => d > 0 ? 'up' : d < 0 ? 'down' : 'zero';
  const kic = (i, cls) => '<svg class="kic k' + i + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="#i-cat' + i + '"/></svg>';
  const ext = '<svg class="ic" aria-hidden="true"><use href="#i-ext"/></svg>';
  const srcCats = s => s.code.endsWith('民特') ? [1] : s.code.endsWith('民') ? [0] : [2, 3];
  const srcLink = s => s.page || s.doc || '';

  // ---------- 選んでいる条件 ----------
  const S = { pref: null, city: null, cats: new Set([0, 1, 2, 3]) };
  const cityBy = new Map(D.cities.map(c => [c.name, c]));
  const kindOn = k => KIND_CATS[k].some(c => S.cats.has(c));
  const subs = [];
  function update(patch, why) {
    Object.assign(S, patch);
    if (S.city && cityBy.has(S.city)) S.pref = cityBy.get(S.city).pref; else S.city = null;
    saveURL();
    subs.forEach(f => f(why || {}));
  }
  function saveURL() {
    const p = new URLSearchParams();
    if (S.pref !== null) p.set('pref', D.prefs[S.pref]);
    if (S.city) p.set('city', S.city);
    if (S.cats.size < CATS.length) p.set('type', [...S.cats].sort().map(i => CATS[i]).join(','));
    const q = p.toString();
    try { history.replaceState(null, '', location.pathname + (q ? '?' + q : '') + location.hash); } catch (e) { /* 保存できなくても表示は続ける */ }
  }
  function loadURL() {
    const p = new URLSearchParams(location.search);
    const pi = D.prefs.indexOf(p.get('pref') || '');
    if (pi >= 0) S.pref = pi;
    const c = p.get('city');
    if (c && cityBy.has(c)) { S.city = c; S.pref = cityBy.get(c).pref; }
    const t = p.get('type');
    if (t !== null) S.cats = new Set(t.split(',').map(x => CATS.indexOf(x)).filter(i => i >= 0));
  }
  // 画像に書く「元のデータを見られるURL」(今の条件つき。日本語のまま読めるようにする)
  const viewURL = () => D.site + (location.search ? decodeURIComponent(location.search) : '');

  const placeName = () => S.city ? D.prefs[S.pref] + ' ' + S.city : S.pref !== null ? D.prefs[S.pref] + '全体' : '全地域';
  const ALL_AREA = D.prefs.length + '都道府県';             // 「全地域」を選んでいるときの呼び方(画像の題名など)
  const placeLong = () => S.city ? D.prefs[S.pref] + ' ' + S.city : S.pref !== null ? D.prefs[S.pref] + '全体' : ALL_AREA + '（' + D.prefs.join('・') + '）';
  const catsLabel = () => S.cats.size === CATS.length ? '4種類すべて' : S.cats.size === 0 ? '種類がえらばれていません' : [...S.cats].sort().map(i => CATS[i]).join('・');
  const scopeCities = () => D.cities.filter(c => S.pref === null || c.pref === S.pref);
  const sumOn = arr => [...S.cats].reduce((t, i) => t + arr[i], 0);
  function scopeSources() {
    if (S.city) return cityBy.get(S.city).src.map(i => D.sources[i]);
    return D.sources.filter(s => S.pref === null || s.pref === S.pref);
  }
  // 市区町村ごとの「データ未取得」(その市区町村の名前の一覧が、公開されていない・読み取れていない種類)
  function cityMissing(c) {
    const miss = new Set();
    c.src.forEach(i => { const s = D.sources[i]; if (!s.count) srcCats(s).forEach(k => miss.add(k)); });
    return miss;
  }
  function scopeCounts() {          // 今の地域で、種類ごとの施設の数(資料に載っている数)と地図に載せた数
    const n = [0, 0, 0, 0], m = [0, 0, 0, 0];
    (S.city ? [cityBy.get(S.city)] : scopeCities()).forEach(c => c.n.forEach((v, i) => { n[i] += v; m[i] += c.m[i]; }));
    return { n, m };
  }
  // 資料の日付の幅(画像の下に書く)
  function dateSpan(srcs) {
    const ds = srcs.filter(s => s.count && s.date).map(s => s.date).sort();
    if (!ds.length) return '不明';
    return ds[0] === ds[ds.length - 1] ? ds[0] : ds[0] + '〜' + ds[ds.length - 1] + '（自治体ごとに異なります）';
  }

  // ---------- 小さなお知らせ ----------
  let toastTimer = null;
  function toast(html) {
    let t = $('#toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.innerHTML = html; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
  }
  function goTo(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const bar = $('#condbar'), hero = $('.hero');
    let top = el.getBoundingClientRect().top + window.scrollY - 10;
    // 移動した先で上の条件の帯が出る場合は、帯の高さの分だけ手前で止める(「お知らせ」の見出しなどが帯に隠れないように)
    if (bar && top > hero.offsetTop + hero.offsetHeight - 40) {
      const shown = !bar.hidden;
      bar.hidden = false;
      top -= bar.offsetHeight;
      bar.hidden = !shown;
    }
    window.scrollTo({ top, behavior: 'smooth' });
  }
  document.addEventListener('click', ev => {
    const a = ev.target.closest('a[data-go]');
    if (a) { ev.preventDefault(); goTo(a.getAttribute('href').slice(1)); return; }
    const m = ev.target.closest('[data-open-method]');
    if (m) { ev.preventDefault(); const d = $('#method'); if (d) { d.open = true; goTo('method'); } }
    const g = ev.target.closest('[data-goto]');
    if (g) { ev.preventDefault(); goTo(g.dataset.goto); }
  });

  // ---------- 条件の部品(地域ボタン・市区町村・種類のチェック) ----------
  function renderRegions() {
    $$('#regions, [data-regions]').forEach(box => {
      if (!box.dataset.ready) {
        box.innerHTML = '<button type="button" class="chip" data-pref="">全地域</button>'
          + D.prefs.map((p, i) => '<button type="button" class="chip" data-pref="' + i + '">' + esc(p) + '</button>').join('');
        box.addEventListener('click', ev => {
          const b = ev.target.closest('.chip');
          if (!b) return;
          update({ pref: b.dataset.pref === '' ? null : +b.dataset.pref, city: null });
        });
        box.dataset.ready = '1';
      }
      $$('.chip', box).forEach(b => b.setAttribute('aria-pressed', String((b.dataset.pref === '' ? null : +b.dataset.pref) === S.pref)));
    });
  }
  const citySel = $('#citysel');
  citySel.addEventListener('change', () => update({ city: citySel.value || null }));
  function renderCitySel() {
    const byTotal = (a, b) => b.n.reduce((x, y) => x + y, 0) - a.n.reduce((x, y) => x + y, 0);
    const opt = c => '<option value="' + esc(c.name) + '"' + (c.name === S.city ? ' selected' : '') + '>' + esc(c.name) + '（' + fmt(c.n.reduce((x, y) => x + y, 0)) + '件）</option>';
    let h = '<option value="">' + (S.pref === null ? 'すべての市区町村（件数の多い順）' : D.prefs[S.pref] + 'のすべての市区町村（件数の多い順）') + '</option>';
    D.prefs.forEach((p, i) => {
      if (S.pref !== null && S.pref !== i) return;
      const cs = D.cities.filter(c => c.pref === i).sort(byTotal);
      if (cs.length) h += '<optgroup label="' + esc(p) + '">' + cs.map(opt).join('') + '</optgroup>';
    });
    citySel.innerHTML = h;
    citySel.value = S.city || '';
  }
  function renderCatFilters() {
    const { n } = scopeCounts();
    $$('.catchecks').forEach(box => {
      const four = box.dataset.catfilter === '4';
      if (!box.dataset.ready) {
        const items = four ? CATS.map((c, i) => [String(i), i, c]) : KINDS.map((k, i) => ['k' + i, KIND_ICON[i], i === 2 ? '旅館業（旅館・ホテル＋簡易宿所）' : k]);
        box.innerHTML = items.map(([v, icon, label]) => '<label class="catchk k' + icon + '"><input type="checkbox" value="' + v + '"><span class="box"></span>'
          + kic(icon) + '<span>' + esc(label) + '</span><span class="n"></span></label>').join('');
        box.addEventListener('change', ev => {
          const inp = ev.target;
          const cats = new Set(S.cats);
          const list = inp.value[0] === 'k' ? KIND_CATS[+inp.value.slice(1)] : [+inp.value];
          list.forEach(c => inp.checked ? cats.add(c) : cats.delete(c));
          update({ cats });
        });
        box.dataset.ready = '1';
      }
      $$('label', box).forEach(l => {
        const inp = $('input', l);
        const list = inp.value[0] === 'k' ? KIND_CATS[+inp.value.slice(1)] : [+inp.value];
        inp.checked = list.some(c => S.cats.has(c));
        l.classList.toggle('on', inp.checked);
        $('.n', l).textContent = fmt(list.reduce((t, c) => t + n[c], 0)) + '件';
      });
    });
  }
  function renderCond() {
    $('#condtext').textContent = '📍 ' + placeName() + '　｜　' + catsLabel();
  }
  subs.push(renderRegions, renderCitySel, renderCatFilters, renderCond);

  function resetAll() {
    $('#q').value = ''; $('#qmsg').textContent = ''; $('#tq').value = ''; $('#tsort').value = 'area'; $('#barsort').value = 'sum';
    if (mapApi) mapApi.clearSearch();
    update({ pref: null, city: null, cats: new Set([0, 1, 2, 3]) }, { home: true });
  }
  $$('.js-reset').forEach(b => b.addEventListener('click', resetAll));

  // 市区町村をえらんだとき(グラフ・ランキング)のお知らせ
  function picked(name) {
    if (S.city === name) { update({ city: null }); toast(esc(name) + 'の選択をやめました。'); return; }
    update({ city: name });
    toast('<span><b>' + esc(name) + '</b>をえらびました。地図・増減・一覧表に反映しています。</span>'
      + '<a href="#map-sec" data-go>地図を見る</a><a href="#trend-sec" data-go>増減を見る</a>');
  }

  // ---------- 横棒グラフ(市区町村ごとの数) ----------
  let barsAll = false, barsList = [];
  const barSort = $('#barsort');
  barSort.addEventListener('change', renderBars);
  $('#bars-more').addEventListener('click', () => { barsAll = !barsAll; renderBars(); });
  $('#bars').addEventListener('click', ev => { const b = ev.target.closest('.bname'); if (b) picked(b.dataset.city); });
  function sortedCities() {
    const key = barSort.value;
    const list = scopeCities().map(c => ({ c, v: sumOn(c.n) }));
    const cmp = key === 'sum' ? (a, b) => b.v - a.v : key === 'sumasc' ? (a, b) => a.v - b.v : (a, b) => b.c.n[+key] - a.c.n[+key] || b.v - a.v;
    return list.filter(x => x.v > 0 || x.c.name === S.city).sort(cmp);
  }
  function renderBars() {
    const list = sortedCities();
    barsList = list;
    const cats = [...S.cats].sort();
    $('#bars-title').textContent = placeName() + 'の市区町村ごとの施設の数';
    $('#bars-legend').innerHTML = cats.map(i => '<span class="lk"><span class="lsw c' + i + '"></span>' + esc(CATS[i]) + '</span>').join('');
    const total = list.reduce((t, x) => t + x.v, 0);
    $('#bars-sub').textContent = cats.length ? '種類：' + catsLabel() + '／' + fmt(list.length) + '市区町村・合計 ' + fmt(total) + '件' : '';
    const box = $('#bars');
    if (!cats.length) { box.innerHTML = '<p class="bempty">「施設の種類」が1つもえらばれていません。上のチェックで種類をえらんでください。</p>'; $('#bars-more').hidden = true; return; }
    const max = Math.max(1, ...list.map(x => x.v));
    let shown = barsAll ? list : list.slice(0, 15);
    const si = list.findIndex(x => x.c.name === S.city);
    if (si >= 15 && !barsAll) shown = shown.concat([list[si]]);
    box.innerHTML = shown.map(x => {
      const c = x.c, rank = list.indexOf(x) + 1, miss = cityMissing(c);
      let first = true;
      const segs = cats.filter(i => c.n[i] > 0).map(i => {
        const w = (c.n[i] / max * 100).toFixed(3);
        const st = first ? 'width:' + w + '%' : 'width:calc(' + w + '% - 2px)';
        first = false;
        return '<span class="bseg c' + i + '" style="' + st + ';min-width:3px" title="' + esc(CATS[i]) + ' ' + fmt(c.n[i]) + '件"></span>';
      }).join('');
      const det = cats.filter(i => c.n[i] > 0).map(i => '<span><span class="dot c' + i + '"></span> ' + esc(CATS[i]) + ' ' + fmt(c.n[i]) + '</span>')
        .concat(KINDS.map((k, ki) => KIND_CATS[ki].some(i => miss.has(i) && S.cats.has(i)) ? '<span class="na">' + esc(c.name) + 'の' + k + 'の一覧：データ未取得</span>' : '').filter(Boolean))
        .join('');
      return '<div class="brow' + (c.name === S.city ? ' sel' : '') + '" role="listitem">'
        + '<span class="brk">' + rank + '</span>'
        + '<button type="button" class="bname" data-city="' + esc(c.name) + '" aria-pressed="' + (c.name === S.city) + '" title="' + esc(c.name) + 'をえらぶ">' + esc(c.name) + '</button>'
        + '<div class="btrack" aria-hidden="true">' + segs + '</div>'
        + '<span class="btot">' + fmt(x.v) + '<small>件</small></span>'
        + '<div class="bdet">' + (det || '<span>掲載なし</span>') + '</div></div>';
    }).join('') || '<p class="bempty">この条件の施設は掲載されていません。</p>';
    const more = $('#bars-more');
    more.hidden = list.length <= 15;
    more.textContent = barsAll ? '上位15だけ表示する' : 'すべての市区町村を表示する（' + fmt(list.length) + '）';
  }
  subs.push(renderBars);

  // ---------- ランキング ----------
  const RG = [{ title: '民泊', sub: '特区民泊を含む', cats: [0, 1], icon: 0 }, { title: '旅館業', sub: '旅館・ホテル、簡易宿所', cats: [2, 3], icon: 2 }];
  const rankAll = [false, false];
  function rankList(g) {
    const on = g.cats.filter(i => S.cats.has(i));
    const list = scopeCities().map(c => ({ c, v: on.reduce((t, i) => t + c.n[i], 0) })).filter(x => x.v > 0).sort((a, b) => b.v - a.v);
    let prev = null, rank = 0;
    list.forEach((x, i) => { if (x.v !== prev) rank = i + 1; x.rank = rank; prev = x.v; });  // 同じ数は同じ順位
    return { on, list };
  }
  function renderRanks() {
    const box = $('#ranks');
    box.innerHTML = RG.map((g, gi) => {
      const { on, list } = rankList(g);
      const head = '<h3>' + kic(g.icon) + g.title + '<small>（' + g.sub + '）</small></h3>'
        + '<p class="sub">' + esc(placeName()) + '・' + fmt(list.length) + '市区町村' + (on.length && on.length < g.cats.length ? '（' + on.map(i => CATS[i]).join('・') + 'のみ）' : '') + '</p>';
      if (!on.length) return '<div class="card rcard">' + head + '<p class="bempty">この種類は、「施設の種類」でえらばれていません。</p></div>';
      const max = Math.max(1, ...list.map(x => x.v));
      const items = (rankAll[gi] ? list : list.slice(0, 10)).map(x => {
        const c = x.c;
        const bar = on.filter(i => c.n[i]).map((i, j) => '<span class="bseg c' + i + '" style="width:' + (j ? 'calc(' : '') + (c.n[i] / max * 100).toFixed(3) + '%' + (j ? ' - 2px)' : '') + ';min-width:3px"></span>').join('');
        const det = on.filter(i => c.n[i]).map(i => '<span><span class="dot c' + i + '"></span> ' + esc(CATS[i]) + ' ' + fmt(c.n[i]) + '</span>').join('');
        return '<li class="ritem' + (c.name === S.city ? ' sel' : '') + '"><span class="rno' + (x.rank <= 3 ? ' top' + x.rank : '') + '">' + x.rank + '</span>'
          + '<span><button type="button" class="rname" data-city="' + esc(c.name) + '" title="' + esc(c.name) + 'の地図へ移動">' + esc(c.name) + '</button>'
          + (S.pref === null ? '<span class="rpref">' + esc(D.prefs[c.pref]) + '</span>' : '') + '</span>'
          + '<span class="rval">' + fmt(x.v) + '<small>件</small></span>'
          + '<span class="rbar" aria-hidden="true">' + bar + '</span>'
          + (on.length > 1 ? '<span class="rdet">' + det + '</span>' : '') + '</li>';
      }).join('');
      return '<div class="card rcard">' + head + '<ol class="rlist' + (rankAll[gi] ? ' all' : '') + '">' + items + '</ol>'
        + '<div class="actions">' + (list.length > 10 ? '<button type="button" class="btn btn-sub" data-rmore="' + gi + '">' + (rankAll[gi] ? '上位10だけ表示する' : 'すべての順位を見る（' + fmt(list.length) + '市区町村）') + '</button>' : '')
        + '<button type="button" class="btn" data-png="rank' + gi + '"><svg class="ic"><use href="#i-image"/></svg>このランキングを画像で保存</button></div></div>';
    }).join('');
  }
  $('#ranks').addEventListener('click', ev => {
    const m = ev.target.closest('[data-rmore]');
    if (m) { rankAll[+m.dataset.rmore] = !rankAll[+m.dataset.rmore]; renderRanks(); return; }
    const b = ev.target.closest('.rname');
    if (b) { update({ city: b.dataset.city }); goTo('map-sec'); toast('<span><b>' + esc(b.dataset.city) + '</b>の地図を表示しています。</span>'); }
  });
  subs.push(renderRanks);

  // ---------- 増減(折れ線グラフ) ----------
  const day0 = Date.parse(D.axis[0].replace(/\//g, '-'));
  const span = Math.max(1, (Date.parse(D.axis[1].replace(/\//g, '-')) - day0) / 864e5);
  const dayOf = d => (Date.parse(d.replace(/\//g, '-')) - day0) / 864e5;
  function niceAxis(vals) {            // trend.py の _y_axis と同じ決め方(0から始めず、件数の4%は幅をとる)
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const sp = Math.max(hi - lo, hi * 0.04, 2), raw = sp / 2;
    const k = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 5, 10].map(m => m * k).find(s => raw <= s);
    step = Math.max(1, Math.trunc(step));
    let a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step;
    if (a === b) { a -= step; b += step; }
    a = Math.max(0, a);
    const ticks = [];
    for (let t = a; t <= b; t += step) ticks.push(t);
    return { a, b, ticks };
  }
  // 1つの資料の推移: 版ごとの点。グラフの期間より前の資料は、一番新しいものだけ左端に◀で置く
  function seriesOf(s) {
    const vs = s.vs;
    const pts = [];
    const old = vs.filter(v => v[0] < D.axis[0]);
    if (old.length) { const v = old[old.length - 1]; pts.push({ x: 0, v: v[1], d: v[0], old: true, lines: ['グラフの期間より前の資料'] }); }
    vs.forEach((v, i) => {
      if (v[0] < D.axis[0]) return;
      const lines = i ? ['前の資料から ' + signed(v[1] - vs[i - 1][1])] : [];
      pts.push({ x: dayOf(v[0]), v: v[1], d: v[0], lines });
    });
    return pts;
  }
  // いくつかの資料の合計の推移(trend.py の totals と同じ数え方。前の版が分からない資料は、分かっている一番古い件数のまま足す)
  function aggregate(srcs) {
    const events = [];
    let now = 0;
    srcs.forEach(s => {
      if (!s.count || !s.vs.length) return;
      now += s.vs[s.vs.length - 1][1];
      for (let i = 1; i < s.vs.length; i++) if (s.vs[i][1] !== s.vs[i - 1][1]) events.push([s.vs[i][0], s, s.vs[i][1] - s.vs[i - 1][1]]);
    });
    const at = d => events.reduce((t, e) => e[0] > d ? t - e[2] : t, now);
    const days = [D.axis[0]].concat([...new Set(events.filter(e => e[0] > D.axis[0]).map(e => e[0]))].sort());
    let prev = null;
    return days.map((d, i) => {
      const v = at(d);
      const lines = [];
      if (!i) lines.push('グラフの始まり（それより前の増減を含む）');
      else {
        lines.push('前の点から ' + signed(v - prev));
        const ch = events.filter(e => e[0] === d).sort((a, b) => Math.abs(b[2]) - Math.abs(a[2]));
        ch.slice(0, 5).forEach(e => lines.push([e[1].k, e[1].muni + ' ' + KINDS[e[1].k] + ' ' + signed(e[2])]));
        if (ch.length > 5) lines.push('ほか' + (ch.length - 5) + 'の資料');
      }
      prev = v;
      return { x: dayOf(d), v, d, lines };
    });
  }
  function trendCards() {      // 今の条件で表示するグラフ(地図や画像でも同じものを使う)
    const kinds = [0, 1, 2].filter(kindOn);
    if (S.city) {
      const c = cityBy.get(S.city);
      const sn = new Map(c.sn);
      return c.src.map(i => [i, D.sources[i]]).filter(([, s]) => kinds.includes(s.k)).map(([i, s]) => ({
        kind: 'src', s, color: KCOLOR(s.k), title: s.muni + ' ' + KINDS[s.k], inCity: s.muni === S.city ? null : (sn.get(i) || 0),
        pts: s.count ? seriesOf(s) : null,
      }));
    }
    // 全地域の合計は出さない(新しく加えた地域に前の件数の記録が無く、増減を正しく表せないため。理由は D.total_paused)
    if (S.pref === null && D.total_paused) return [];
    const srcs = scopeSources().filter(s => kinds.includes(s.k));
    const cards = [];
    const label = S.pref === null ? '全地域' : D.prefs[S.pref];
    if (kinds.length > 1) cards.push({ kind: 'agg', title: label + 'の合計（' + kinds.map(k => KINDS[k]).join('＋') + '）', color: '#1f2a24', srcs, pts: aggregate(srcs) });
    kinds.forEach(k => {
      const ks = srcs.filter(s => s.k === k);
      if (ks.some(s => s.count)) cards.push({ kind: 'agg', title: label + 'の' + KINDS[k], color: KCOLOR(k), srcs: ks, pts: aggregate(ks) });
    });
    return cards;
  }
  function drawChart(box, card) {
    const w = Math.round(box.clientWidth);
    if (!w || box._w === w) return;
    box._w = w;
    const pts = card.pts, h = w < 420 ? 190 : 210, pr = 12, pt = 12, pb = 28;
    const ax = niceAxis(pts.map(p => p.v));
    const pl = Math.max(...ax.ticks.map(t => 14 + 7.6 * fmt(t).length));
    const fx = x => pl + Math.min(1, Math.max(0, x / span)) * (w - pl - pr);
    const fy = v => pt + (ax.b - v) / (ax.b - ax.a) * (h - pt - pb);
    const r1 = v => Math.round(v * 10) / 10;
    let g = '<g class="grid">' + ax.ticks.map(t => '<line x1="' + pl + '" x2="' + (w - pr) + '" y1="' + r1(fy(t)) + '" y2="' + r1(fy(t)) + '"/>'
      + '<text x="' + (pl - 6) + '" y="' + r1(fy(t) + 4.5) + '" text-anchor="end">' + fmt(t) + '</text>').join('') + '</g>';
    // 月の区切り
    const [sy, sm] = D.axis[0].split('/').map(Number);
    let minX = pl + 84;
    for (let mth = 1; mth < 24; mth++) {
      const md = new Date(Date.UTC(sy, sm - 1 + mth, 1));
      const x = fx((md - day0) / 864e5);
      if ((md - day0) / 864e5 >= span) break;
      if (x >= minX && x <= w - pr - 90) {
        g += '<line class="tk" x1="' + r1(x) + '" x2="' + r1(x) + '" y1="' + (h - pb) + '" y2="' + (h - pb + 4) + '"/><text x="' + r1(x) + '" y="' + (h - 8) + '" text-anchor="middle">' + (md.getUTCMonth() + 1) + '月</text>';
        minX = x + 50;
      }
    }
    const P = pts.map(p => [r1(fx(p.x)), r1(fy(p.v))]);
    const segs = P.slice(1).map((q, i) => '<path class="line' + (pts[0].old && i === 0 ? ' dash' : '') + '" d="M' + P[i][0] + ',' + P[i][1] + ' L' + q[0] + ',' + q[1] + '"/>').join('');
    const marks = P.map((p, i) => pts[i].old ? '<path class="old" d="M' + (p[0] - 1) + ',' + p[1] + ' l8,-5 v10 z"/>' : '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="4.5"/>').join('');
    const none = pts.length === 1 && pts[0].old ? '<text x="' + r1((pl + w - pr) / 2) + '" y="' + r1(P[0][1] - 12) + '" text-anchor="middle">この期間に新しい資料なし</text>' : '';
    box.innerHTML = '<svg class="ch" viewBox="0 0 ' + w + ' ' + h + '" tabindex="0" role="img" style="--s:' + card.color + '">' + g
      + '<line class="cross" x1="0" x2="0" y1="' + pt + '" y2="' + (h - pb) + '" visibility="hidden"/>'
      + segs + '<g class="dots">' + marks + '</g>' + none
      + '<circle class="hl" r="7" cx="0" cy="0" visibility="hidden"/>'
      + '<text x="' + pl + '" y="' + (h - 8) + '" text-anchor="start">' + D.axis[0] + '</text>'
      + '<text x="' + (w - pr) + '" y="' + (h - 8) + '" text-anchor="end">' + D.axis[1] + '</text></svg>';
    const svg = box.firstChild;
    svg.setAttribute('aria-label', card.title + 'の件数の推移：' + pts.map(p => p.d + ' ' + fmt(p.v) + '件').join('、'));
    $$('.line, .hl', svg).forEach(el => el.style.stroke = card.color);
    $$('.dots circle, .old', svg).forEach(el => el.style.fill = card.color);
    bindTip(svg, box.parentNode, P, pts);
  }
  function bindTip(svg, holder, P, pts) {
    const tip = $('.tip', holder), cross = $('.cross', svg), hl = $('.hl', svg);
    const W = svg.viewBox.baseVal.width;
    let cur = -1;
    function show(i) {
      cur = i;
      const [x, y] = P[i], p = pts[i];
      cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
      hl.setAttribute('cx', x); hl.setAttribute('cy', y); hl.setAttribute('visibility', 'visible');
      tip.innerHTML = '<b>' + fmt(p.v) + '件</b> <span class="d">' + esc(p.d) + 'の資料</span>'
        + p.lines.map(l => Array.isArray(l) ? '<div class="d"><span class="lk2" style="background:' + KCOLOR(l[0]) + '"></span>' + esc(l[1]) + '</div>' : '<div class="d">' + esc(l) + '</div>').join('');
      tip.hidden = false;
      const r = svg.getBoundingClientRect(), cr = holder.getBoundingClientRect(), s = r.width / W;
      const half = tip.offsetWidth / 2;
      tip.style.left = Math.min(Math.max(r.left - cr.left + x * s, half + 4), cr.width - half - 4) + 'px';
      tip.style.top = (r.top - cr.top + y * s) + 'px';
    }
    function hide() { cur = -1; tip.hidden = true; cross.setAttribute('visibility', 'hidden'); hl.setAttribute('visibility', 'hidden'); }
    function nearest(ev) {
      const r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) * W / r.width;
      let best = 0;
      P.forEach((p, i) => { if (Math.abs(p[0] - x) < Math.abs(P[best][0] - x)) best = i; });
      return best;
    }
    svg.addEventListener('pointermove', ev => { const i = nearest(ev); if (i !== cur) show(i); });
    svg.addEventListener('pointerdown', ev => show(nearest(ev)));
    svg.addEventListener('pointerleave', ev => { if (ev.pointerType === 'mouse') hide(); });
    svg.addEventListener('focus', () => show(P.length - 1));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowLeft' && cur > 0) { show(cur - 1); ev.preventDefault(); }
      else if (ev.key === 'ArrowRight' && cur < P.length - 1) { show(cur + 1); ev.preventDefault(); }
      else if (ev.key === 'Escape') hide();
    });
  }
  const chartRO = window.ResizeObserver ? new ResizeObserver(es => es.forEach(e => e.target._card && drawChart(e.target, e.target._card))) : null;
  function cardHTML(c) {
    const head = '<h3><span class="lk2" style="background:' + c.color + '"></span>' + esc(c.title)
      + (c.kind === 'src' && srcLink(c.s) ? '<a class="src" href="' + esc(srcLink(c.s)) + '" target="_blank" rel="noopener">出典' + ext + '</a>' : '') + '</h3>';
    if (c.kind === 'src') {
      const s = c.s;
      if (!s.count) return head + '<div class="tna"><b>データ未取得</b><br>' + esc(s.memo || '一覧が公開されていないか、ファイルを自動で読み取れませんでした。') + '<br>（0件という意味ではありません）</div>';
      const vs = s.vs, last = vs[vs.length - 1], prev = vs.length > 1 ? vs[vs.length - 2] : null;
      const d = prev ? last[1] - prev[1] : null;
      return head + '<p class="tval"><b>' + fmt(s.count) + '</b>件<span class="asof">' + esc(s.date || last[0]) + 'の資料</span></p>'
        + '<p class="tchg">' + (prev ? '前回との差 <span class="' + signCls(d) + '">' + signed(d) + '件</span><span class="dates">前回 ' + esc(prev[0]) + 'の資料 ' + fmt(prev[1]) + '件 → 今回 ' + esc(last[0]) + 'の資料 ' + fmt(last[1]) + '件</span>'
          : '<span class="dates">前回の記録はまだありません（記録を始めてから、この資料は更新されていません）</span>') + '</p>'
        + (c.inCity !== null ? '<p class="tmix">' + esc(s.muni) + 'がまとめて公表している一覧の全体の件数です（そのうち' + esc(S.city) + 'の分は ' + fmt(c.inCity) + '件）。</p>' : '')
        + '<div class="chbox"></div><div class="tip" hidden></div>';
    }
    const now = c.pts[c.pts.length - 1].v, first = c.pts[0].v;
    const n = c.srcs.filter(s => s.count).length, miss = c.srcs.filter(s => !s.count);
    return head + '<p class="tval"><b>' + fmt(now) + '</b>件<span class="asof">' + n + 'の資料の合計</span></p>'
      + '<p class="tchg">グラフの期間の増減 <span class="' + signCls(now - first) + '">' + signed(now - first) + '件</span><span class="dates">期間 ' + D.axis[0] + '〜' + D.axis[1] + '（資料の日付）</span></p>'
      + (n > 1 ? '<p class="tmix">自治体ごとに資料の日付が違うものを足した合計です。同じ期間どうしの比較ではありません。</p>' : '')
      + (miss.length ? '<p class="tmix">データ未取得（合計に入っていません）：' + miss.map(s => esc(s.muni + ' ' + KINDS[s.k])).join('、') + '</p>' : '')
      + '<div class="chbox"></div><div class="tip" hidden></div>';
  }
  function renderTrend() {
    const cards = trendCards();
    const sc = $('#trend-scope');
    if (S.city) {
      const shared = cards.some(c => c.inCity !== null);
      sc.innerHTML = '<span class="tag">' + esc(placeName()) + '</span>に関係する資料の推移' + (shared ? '（県などがまとめて公表している一覧は、ほかの市区町村も含んだ件数です）' : '');
    } else sc.innerHTML = '<span class="tag">' + esc(placeName()) + '</span>の資料の推移（種類ごと）';
    const box = $('#trendcharts');
    if (chartRO) $$('.chbox', box).forEach(b => chartRO.unobserve(b));
    const paused = !S.city && S.pref === null && D.total_paused;
    box.innerHTML = cards.length ? cards.map(c => '<div class="card tcard">' + cardHTML(c) + '</div>').join('')
      : paused ? '<div class="tna tpaused"><b>全地域の合計のグラフは表示していません</b><br>' + esc(D.total_paused) + '上の「地域」で都道府県をえらぶと、その地域の推移グラフが出ます。</div>'
      : '<p class="bempty">えらんだ種類の資料がありません。「施設の種類」でえらんでください。</p>';
    $('[data-png="trend"]').hidden = paused;
    $$('.tcard', box).forEach((el, i) => {
      const b = $('.chbox', el);
      if (!b) return;
      b._card = cards[i];
      drawChart(b, cards[i]);
      if (chartRO) chartRO.observe(b);
    });
    renderChanges();
  }
  let chgAll = false;
  $('#chg-more').addEventListener('click', () => { chgAll = !chgAll; renderChanges(); });
  function renderChanges() {
    const rows = scopeSources().filter(s => kindOn(s.k)).map(s => ({ s, d: s.vs.length > 1 && s.count ? s.vs[s.vs.length - 1][1] - s.vs[s.vs.length - 2][1] : null }));
    rows.sort((a, b) => (b.d !== null) - (a.d !== null) || (b.s.vs.length ? b.s.vs[b.s.vs.length - 1][0] : '').localeCompare(a.s.vs.length ? a.s.vs[a.s.vs.length - 1][0] : '') || Math.abs(b.d || 0) - Math.abs(a.d || 0));
    const shown = chgAll ? rows : rows.slice(0, 10);
    $('#chgbody').innerHTML = shown.map(({ s, d }) => {
      const vs = s.vs, last = vs[vs.length - 1], prev = vs.length > 1 ? vs[vs.length - 2] : null;
      const name = '<span class="dot" style="background:' + KCOLOR(s.k) + '"></span> <b>' + esc(s.muni) + '</b> ' + KINDS[s.k]
        + (s.count ? ' <a href="trend.html#' + esc(s.code) + '">推移</a>' : '');
      if (!s.count) return '<tr><td>' + name + '</td><td colspan="2"><b>データ未取得</b><span class="dt">' + esc(s.memo || '一覧が公開されていない、または読み取れません') + '</span></td><td class="num">—</td></tr>';
      return '<tr><td>' + name + '</td>'
        + '<td>' + (prev ? esc(prev[0]) + 'の資料<span class="dt">' + fmt(prev[1]) + '件</span>' : '<span class="dt">記録なし</span>') + '</td>'
        + '<td>' + esc(last[0]) + 'の資料<span class="dt">' + fmt(last[1]) + '件</span></td>'
        + '<td class="num">' + (d === null ? '—' : '<span class="' + signCls(d) + '">' + signed(d) + '</span>') + '</td></tr>';
    }).join('') || '<tr><td colspan="4">えらんだ種類の資料がありません。</td></tr>';
    const more = $('#chg-more');
    more.hidden = rows.length <= 10;
    more.textContent = chgAll ? '10件だけ表示する' : 'すべて表示する（' + rows.length + '件）';
  }
  subs.push(renderTrend);

  // ---------- 自治体ごとの一覧表 ----------
  const tOpen = new Set();
  const tq = $('#tq'), tsort = $('#tsort');
  function dateLabel(b) { return /記載がない/.test(b) ? 'ファイルの日付' : /毎月の追加分/.test(b) ? '追加分の日付' : ''; }
  function buildTable() {
    const tab = $('#mtab');
    D.prefs.forEach((p, pi) => {
      const idx = D.sources.map((s, i) => i).filter(i => D.sources[i].pref === pi);
      if (!idx.length) return;
      const tb = document.createElement('tbody');
      tb.dataset.pref = pi;
      tb.innerHTML = '<tr class="grp"><th colspan="6"><button type="button" class="grpbtn" aria-expanded="false"><span class="arw">▶</span>' + esc(p) + '<span class="gsub"></span></button></th></tr>'
        + idx.map(i => {
          const s = D.sources[i];
          const ki = s.k, icon = KIND_ICON[ki];
          const bd = s.n.map((v, c) => [v, c]).filter(([v, c]) => v && (ki === 2 || !KIND_CATS[ki].includes(c))).map(([v, c]) => esc(CATS[c]) + ' ' + fmt(v)).join('／');
          const cnt = s.count ? '<span class="cnt">' + fmt(s.count) + '件</span>' + (s.placed < s.count ? '<span class="placed">地図に表示 ' + fmt(s.placed) + '件（位置が分からない ' + fmt(s.count - s.placed) + '件）</span>' : '')
            : '<span class="na">データ未取得</span>' + (s.memo ? '<span class="memo">' + esc(s.memo) + '</span>' : '');
          const diff = s.diff !== null ? '<span class="' + signCls(s.diff) + '">' + signed(s.diff) + '</span><span class="since">前回 ' + esc(s.prev) + 'の資料より</span>'
            : s.count ? '<span class="since">前回の記録なし</span>' : '';
          const dl = dateLabel(s.basis);
          const date = s.date ? '<span title="' + esc(s.basis) + '">' + esc(s.date) + '</span>' + (dl ? '<span class="dlabel">' + dl + '</span>' : '') : '';
          const link = srcLink(s);
          const links = (link ? '<a class="lbtn" href="' + esc(link) + '" target="_blank" rel="noopener" aria-label="' + esc(s.muni + 'の' + s.kind) + 'の出典（自治体のページ。別のタブで開きます）">出典' + ext + '</a>' : '')
            + (s.count ? '<a class="lbtn" href="trend.html#' + esc(s.code) + '" aria-label="' + esc(s.muni + 'の' + s.kind) + 'の推移グラフ">推移グラフ</a>' : '');
          return '<tr class="row" data-i="' + i + '"><td class="cl-name" data-label="自治体"><span class="mname">' + esc(s.muni) + '</span></td>'
            + '<td data-label="種類"><span><span class="kd">' + kic(icon) + esc(s.kind) + '</span>' + (bd ? '<span class="bd">' + bd + '</span>' : '') + '</span></td>'
            + '<td class="num" data-label="件数"><span>' + cnt + '</span></td>'
            + '<td class="num" data-label="前回との差"><span>' + (diff || '—') + '</span></td>'
            + '<td data-label="資料の日付"><span>' + (date || '—') + '</span></td>'
            + '<td class="cl-src" data-label="出典・推移"><span>' + (links || '—') + '</span></td></tr>';
        }).join('');
      tab.appendChild(tb);
    });
    tab.addEventListener('click', ev => {
      const b = ev.target.closest('.grpbtn');
      if (!b) return;
      const pi = +b.closest('tbody').dataset.pref;
      tOpen.has(pi) ? tOpen.delete(pi) : tOpen.add(pi);
      applyTable();
    });
  }
  let lastTScope = '';
  function applyTable(why) {
    const q = tq.value.trim().replace(/\s+/g, '');
    const key = S.pref + '|' + S.city;
    if (key !== lastTScope) { lastTScope = key; if (S.pref !== null) tOpen.add(S.pref); }
    const citySrc = S.city ? new Set(cityBy.get(S.city).src) : null;
    const sortKey = tsort.value;
    const val = {
      area: s => -D.sources.indexOf(s), count: s => s.count, up: s => s.diff === null ? -Infinity : s.diff,
      down: s => s.diff === null ? -Infinity : -s.diff, date: s => s.date ? Date.parse(s.date.replace(/\//g, '-')) : -Infinity,
    }[sortKey];
    let shownAll = 0;
    $$('#mtab tbody').forEach(tb => {
      const pi = +tb.dataset.pref;
      const rows = $$('tr.row', tb);
      let n = 0, sum = 0;
      rows.forEach(tr => {
        const s = D.sources[+tr.dataset.i];
        const ok = (S.pref === null || s.pref === S.pref) && kindOn(s.k) && (!q || s.muni.includes(q)) && (!citySrc || citySrc.has(+tr.dataset.i));
        tr._ok = ok;
        if (ok) { n++; sum += s.count; }
        tr.classList.toggle('hit', !!(q && ok) || !!(citySrc && ok && s.muni === S.city));
      });
      if (q && n && why === 'q') tOpen.add(pi);
      rows.sort((a, b) => val(D.sources[+b.dataset.i]) - val(D.sources[+a.dataset.i])).forEach(tr => tb.appendChild(tr));
      const open = tOpen.has(pi);
      rows.forEach(tr => tr.classList.toggle('hide', !tr._ok || !open));
      tb.hidden = !n;
      const btn = $('.grpbtn', tb);
      btn.setAttribute('aria-expanded', String(open));
      $('.gsub', tb).textContent = fmt(sum) + '件・' + n + 'の資料' + (open ? '' : '（押すと開きます）');
      shownAll += n;
    });
    const sc = $('#table-scope');
    sc.innerHTML = (S.city ? '<span class="tag">' + esc(placeName()) + '</span>に関係する資料（県などがまとめて公表している一覧も含む）'
      : '<span class="tag">' + esc(placeName()) + '</span>の資料') + '：' + shownAll + '件' + (q ? '（「' + esc(q) + '」をふくむ自治体）' : '');
    let em = $('#tempty');
    if (!shownAll) {
      if (!em) { em = document.createElement('p'); em.id = 'tempty'; em.className = 'tempty'; $('#mtab').after(em); }
      em.textContent = 'この条件にあう資料はありません。「条件を解除」を押すと、すべて表示します。';
    } else if (em) em.remove();
  }
  tq.addEventListener('input', () => applyTable('q'));
  tsort.addEventListener('change', () => applyTable());
  $('#t-open').addEventListener('click', () => { D.prefs.forEach((p, i) => tOpen.add(i)); applyTable(); });
  $('#t-close').addEventListener('click', () => { tOpen.clear(); applyTable(); });
  subs.push(() => applyTable());

  // ---------- 地図 ----------
  // 背景の地図。標準は淡いグレーの地図で、国土地理院の淡色地図にも切り替えられる。画像で保存するときも同じ地図を使う。
  // 淡いグレーは、OpenStreetMap Japan が配信している日本語の地図を灰色にしたもの
  // (CARTO の Positron は 2026年10月に試したところ登録キーが必要になっていて「API KEY REQUIRED」の画像しか出なかった)。
  // filter: 地図の色を薄めるかけ方(画面と、画像で保存するときの両方に使う)
  const BASES = {
    gray: {
      url: 'https://tile.openstreetmap.jp/styles/maptiler-basic-ja/{z}/{x}/{y}{r}.png', img: z => 'https://tile.openstreetmap.jp/styles/maptiler-basic-ja/' + z + '@2x.png', native: 19,
      filter: 'grayscale(.8) contrast(.72) brightness(1.15)',
      attribution: '<a href="https://openstreetmap.jp/" target="_blank" rel="noopener">OpenStreetMap Japan</a> &copy; <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>',
      credit: '地図：© OpenStreetMap contributors © OpenMapTiles（OpenStreetMap Japan）',
    },
    gsi: {
      url: 'https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png', img: z => 'https://cyberjapandata.gsi.go.jp/xyz/pale/' + z + '.png', native: 18,
      filter: 'saturate(.25) contrast(.85) brightness(1.06)',
      attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
      credit: '地図：地理院タイル（国土地理院）',
    },
  };
  const MAXZ = 19;
  // 地理院の淡色地図は大きく拡大すると建物が灰色で塗られて暗く見えるので、拡大するほど背景を透かして明るさをそろえる
  const opacityAt = (key, z) => key !== 'gsi' || z <= 14 ? 1 : z === 15 ? 0.8 : z === 16 ? 0.6 : 0.45;
  // 点の大きさ(直径px)。同じ地点の施設が多いほど少しずつ大きくする(1件 6px・2〜3件 8px・4〜6件 10px・7〜10件 12px・11件以上 14px)。
  // 大きく拡大したときだけ少し大きくする
  const tier = n => n <= 1 ? 0 : n <= 3 ? 1 : n <= 6 ? 2 : n <= 10 ? 3 : 4;
  const dotSize = (n, z) => 6 + tier(n) * 2 + (z >= 16 ? 2 : z >= 14 ? 1 : 0);
  const dotLine = d => d > 8 ? 1.5 : 1;   // 白い輪郭の太さ
  // 点を1つ描く。comp: [[種類, 件数], …](多い順)。同じ地点に種類の違う施設があるときは件数の割合で塗り分け、
  // 位置が目安(町の中心付近)の点は真ん中を白くする。sel: 選んだ点(黒い縁取り)
  function paintDot(g, x, y, d, w, comp, exact, sel) {
    const ro = d / 2 + w / 2, ri = d / 2 - w / 2;
    if (sel) { g.beginPath(); g.arc(x, y, ro + 2.5, 0, Math.PI * 2); g.fillStyle = '#1f2a24'; g.fill(); }
    g.beginPath(); g.arc(x, y, ro, 0, Math.PI * 2); g.fillStyle = '#fff'; g.fill();
    if (comp.length === 1) { g.beginPath(); g.arc(x, y, ri, 0, Math.PI * 2); g.fillStyle = COLORS[comp[0][0]]; g.fill(); }
    else {
      const n = comp.reduce((t, c) => t + c[1], 0);
      let a = -Math.PI / 2;
      comp.forEach(([c, v]) => { const b = a + v / n * Math.PI * 2; g.beginPath(); g.moveTo(x, y); g.arc(x, y, ri, a, b); g.closePath(); g.fillStyle = COLORS[c]; g.fill(); a = b; });
    }
    if (!exact) { g.beginPath(); g.arc(x, y, ri * 0.45, 0, Math.PI * 2); g.fillStyle = '#fff'; g.fill(); }
  }
  // 施設の点を描く層。数万の点を1枚のキャンバスにまとめて描く(点を1つずつ地図の部品にするより、ずっと速い)。
  // 地図を動かしたり拡大したりするたびに全部描き直し、点を間引いたり、近くの点をまとめたりはしない
  const DotLayer = L.Renderer.extend({
    options: { padding: 0.4 },
    _initContainer() { this._container = L.DomUtil.create('canvas', 'dotlayer'); this._ctx = this._container.getContext('2d'); },
    _destroyContainer() { L.DomUtil.remove(this._container); },
    // 描き直し(_update)は後回しにするので、最初の描き直しの前に地図が動くとまだ中心が決まっていない
    // (URLで地域を指定して開いたとき)。そのときは何もしない(すぐ後の描き直しで正しい位置に描く)
    _updateTransform(center, zoom) { if (this._center) L.Renderer.prototype._updateTransform.call(this, center, zoom); },
    // 1回の操作(拡大・移動)で何度か呼ばれるので、描き直しはその操作が終わった直後の1回にまとめる(画面が書き換わる前なので、ちらつかない)
    _update() {
      if (this._queued) return;
      this._queued = true;
      Promise.resolve().then(() => {
        this._queued = false;
        if (!this._map || (this._map._animatingZoom && this._bounds)) return;
        L.Renderer.prototype._update.call(this);
        const b = this._bounds, size = b.getSize(), c = this._container;
        this._dpr = Math.min(3, window.devicePixelRatio || 1);
        L.DomUtil.setPosition(c, b.min);
        c.width = Math.round(size.x * this._dpr); c.height = Math.round(size.y * this._dpr);
        c.style.width = size.x + 'px'; c.style.height = size.y + 'px';
        if (this.onDraw) this.onDraw();
      });
    },
  });

  let mapApi = null;
  async function initMap() {
    const el = $('#map');
    el.innerHTML = '';
    const map = L.map(el, { maxZoom: MAXZ }).setView(HOME, 10);
    // 画面の向きや文字の読み込みで地図の枠の大きさが変わったら、地図にも知らせる
    if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe(el);

    // ---- 背景の地図の切り替え。選んだ方はこの端末に覚えておく ----
    let baseKey = 'gray', base = null;
    try { if (localStorage.getItem('minpaku-base') === 'gsi') baseKey = 'gsi'; } catch (e) { /* 覚えられなくても動く */ }
    const bopts = $$('.vopt[data-base]');
    const soften = () => base.setOpacity(opacityAt(baseKey, map.getZoom()));
    function setBase(k) {
      baseKey = k;
      const b = BASES[k];
      if (base) map.removeLayer(base);
      base = L.tileLayer(b.url, { maxZoom: MAXZ, maxNativeZoom: b.native, crossOrigin: 'anonymous', attribution: b.attribution }).addTo(map);
      base.getContainer().style.filter = b.filter;
      soften();
      bopts.forEach(o => o.setAttribute('aria-checked', String(o.dataset.base === k)));
      try { localStorage.setItem('minpaku-base', k); } catch (e) { /* 同上 */ }
    }
    map.on('zoomend', () => soften());
    bopts.forEach(o => o.addEventListener('click', () => setBase(o.dataset.base)));
    $('#baseswitch').addEventListener('keydown', ev => {
      if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
      const k = baseKey === 'gray' ? 'gsi' : 'gray'; setBase(k); $('.vopt[data-base="' + k + '"]').focus(); ev.preventDefault();
    });
    setBase(baseKey);

    const loading = L.control({ position: 'topright' });
    loading.onAdd = () => { const d = L.DomUtil.create('div', 'maploading'); d.style.background = '#fff'; d.style.borderRadius = '8px'; d.textContent = '施設の点を読み込んでいます…'; return d; };
    loading.addTo(map);

    const P = await (await fetch('data/points.json')).json();
    loading.remove();
    const srcBy = new Map();
    D.sources.forEach(s => s.n.forEach((v, c) => { if (v) srcBy.set(s.muni + '|' + c, s); }));
    const cityPref = new Map(D.cities.map(c => [c.name, c.pref]));

    const touch = window.matchMedia('(hover: none)').matches;
    const TOL = touch ? 12 : 4;   // 指で押すときは、点の周りも押せるようにする
    // 完全に同じ位置の施設は(種類や資料が違っても)点1つにまとめ、点の大きさで件数を表す。位置が少しでも違えば別の点にする
    const spots = [], spotAt = new Map();
    P.pins.forEach(p => {
      const k = p[0] + ',' + p[1];
      let s = spotAt.get(k);
      if (!s) {
        const w = map.project([p[0], p[1]], 0);   // ズーム0での位置。ズームzでの位置はこれの2のz乗倍
        s = { lat: p[0], lng: p[1], x0: w.x, y0: w.y, pins: [], vis: [], comp: [], n: 0, exact: true, key: '' };
        spotAt.set(k, s); spots.push(s);
      }
      s.pins.push(p);
    });
    // 描く順番を種類に関係なく混ぜる(ある種類をまとめて上に描くと、重なった所でその色ばかり目立つため)
    let seed = 12345;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let i = spots.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [spots[i], spots[j]] = [spots[j], spots[i]]; }

    // ---- 点を描く ----
    // 同じ見た目の点は1度だけ小さなキャンバスに描いておき、それを画面の画素にそろえて貼る(速く、輪郭もぼやけない)
    const layer = new DotLayer().addTo(map);
    const sprites = new Map();
    function sprite(s, d, w, sel, r) {
      const key = s.key + (s.exact ? '' : '~') + '|' + d + '|' + w + '|' + (sel ? 1 : 0) + '|' + r;
      let sp = sprites.get(key);
      if (!sp) {
        let n = Math.ceil((d + w + (sel ? 5 : 0) + 2) * r); n += n % 2;
        const cv = document.createElement('canvas'); cv.width = cv.height = n;
        const g = cv.getContext('2d'); g.scale(r, r);
        paintDot(g, n / 2 / r, n / 2 / r, d, w, s.comp, s.exact, sel);
        sp = { cv, h: n / 2 }; sprites.set(key, sp);
      }
      return sp;
    }
    let shown = [], drawn = [], grid = new Map(), sel = null, selVis = null, selOthers = 0;
    const CELL = 32;   // 押した場所の近くの点を探すためのます目(px)
    const cellKey = (cx, cy) => (cx + 4096) * 8192 + cy + 4096;
    function draw() {
      const c = layer._container, g = layer._ctx, b = layer._bounds, r = layer._dpr;
      if (!b) return;
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.clearRect(0, 0, c.width, c.height);
      const z = map.getZoom(), k = Math.pow(2, z), o = map.getPixelOrigin();
      const ox = b.min.x + o.x, oy = b.min.y + o.y, W = b.max.x - b.min.x, H = b.max.y - b.min.y;
      drawn = []; grid = new Map();
      for (const s of shown) {
        const x = s.x0 * k - ox, y = s.y0 * k - oy;
        if (x < -10 || y < -10 || x > W + 10 || y > H + 10) continue;
        const d = dotSize(s.n, z), w = dotLine(d), sp = sprite(s, d, w, false, r);
        g.drawImage(sp.cv, Math.round(x * r) - sp.h, Math.round(y * r) - sp.h);
        const key = cellKey(Math.floor(x / CELL), Math.floor(y / CELL)), list = grid.get(key);
        if (list) list.push(drawn.length); else grid.set(key, [drawn.length]);
        drawn.push({ s, x, y, rad: d / 2 + w / 2 });
      }
      // 選んだ点は一番上に、ひとまわり大きく黒い縁取りで描く
      if (sel && sel.vis.length) {
        const x = sel.x0 * k - ox, y = sel.y0 * k - oy, sp = sprite(sel, dotSize(sel.n, z) + 6, 2, true, r);
        g.drawImage(sp.cv, Math.round(x * r) - sp.h, Math.round(y * r) - sp.h);
      }
    }
    layer.onDraw = draw;
    const redraw = () => { if (!map._animatingZoom) draw(); };
    // 地図を大きく動かして、描いてある範囲の外が見えそうになったら、動かしている途中でも描き直す
    map.on('move', () => {
      if (map._animatingZoom || !layer._bounds) return;
      const tl = map.containerPointToLayerPoint([0, 0]), br = tl.add(map.getSize()), b = layer._bounds;
      if (tl.x < b.min.x || tl.y < b.min.y || br.x > b.max.x || br.y > b.max.y) layer._update();
    });
    // 指(マウス)の下にある点。上に描いた点ほど優先し、なければ一番近い点。others: そこに重なっているほかの点の数
    function hit(lp) {
      const b = layer._bounds;
      if (!b) return null;
      const x = lp.x - b.min.x, y = lp.y - b.min.y, cx = Math.floor(x / CELL), cy = Math.floor(y / CELL);
      let best = null, bestScore = Infinity, near = 0;
      for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) {
        (grid.get(cellKey(cx + i, cy + j)) || []).forEach(idx => {
          const t = drawn[idx], dist = Math.hypot(t.x - x, t.y - y);
          if (dist > t.rad + TOL) return;
          near++;
          const score = dist <= t.rad ? -1 - idx : dist - t.rad;
          if (score < bestScore) { bestScore = score; best = t; }
        });
      }
      return best && { s: best.s, others: near - 1 };
    }

    // ---- マウスを重ねると「この地点に○件」を出す ----
    let moving = false, tipFor = null;
    const tip = L.tooltip({ direction: 'top', opacity: 1 });
    function hideTip() { if (tipFor) { map.closeTooltip(tip); tipFor = null; } el.classList.remove('ondot'); }
    function tipHTML(s) {
      const one = s.n === 1 ? s.vis[0][5][0][0] || '（施設名の記載なし）' : '';
      return '<div class="ctip"><b>この地点に ' + fmt(s.n) + '件</b>' + (one ? '<div class="nm">' + esc(one) + '</div>' : '')
        + '<div>' + s.comp.map(([c, v]) => '<span class="dot c' + c + '"></span> ' + esc(CATS[c]) + ' ' + fmt(v) + '件').join('<br>') + '</div>'
        + '<div class="d">' + (s.exact ? '' : '位置は目安（町の中心付近）・') + '押すと一覧を表示</div></div>';
    }
    map.on('movestart', () => { moving = true; hideTip(); });
    map.on('moveend', () => { moving = false; });
    if (!touch) {
      map.on('mousemove', e => {
        if (moving) return;
        const h = hit(e.layerPoint);
        if (!h) { hideTip(); return; }
        el.classList.add('ondot');
        if (tipFor === h.s) return;
        tipFor = h.s;
        tip.options.offset = [0, -Math.ceil(dotSize(h.s.n, map.getZoom()) / 2 + 2)];
        tip.setLatLng([h.s.lat, h.s.lng]).setContent(tipHTML(h.s));
        map.openTooltip(tip);
      });
      map.on('mouseout', hideTip);
    }
    map.on('click', e => { const h = hit(e.layerPoint); if (h) { hideTip(); showSpot(h.s, h.others); } });

    // ---- 情報カード ----
    const card = $('#infocard'), body = $('#infobody'), closeBtn = $('.infox', card);
    function emptyCard() {
      card.classList.remove('open'); card.classList.add('empty'); closeBtn.hidden = true;
      if (sel) { sel = null; redraw(); }
      body.innerHTML = '<div class="infoempty"><svg aria-hidden="true"><use href="#i-pin"/></svg><div><b>地図の点を押すと、ここに施設の情報が出ます。</b><br>名前・住所・種類・出典（自治体の資料）を表示します。</div></div>';
    }
    function openCard(html) { body.innerHTML = html; card.classList.add('open'); card.classList.remove('empty'); closeBtn.hidden = false; card.scrollTop = 0; }
    closeBtn.addEventListener('click', emptyCard);
    // 押した地点の施設の一覧。同じ地点に別の種類・別の資料の施設があるときは、種類ごとに分けて並べる
    function showSpot(s, others) {
      const vis = s.vis, LIM = 30, rest = [];
      const place = p => esc(D.prefs[cityPref.get(P.cities[p[6]])] || '') + ' ' + esc(P.cities[p[6]]);
      const places = [...new Set(vis.map(place))];
      const count = 'この地点に' + fmt(s.n) + '件';
      const badge = s.exact ? '<span class="badge ok">✓ 住所の番地まで分かった位置</span>'
        : '<span class="badge approx">⚠ 位置は目安です：番地まで分からなかったため、町の中心付近に表示しています</span>';
      const part = (p, head) => {
        const [, , cat, mi, , items] = p;
        const src = srcBy.get(P.munis[mi] + '|' + cat);
        const noLabel = ['届出番号', '認定番号', '許可番号', '許可番号'][cat], dLabel = ['届出日', '認定日', '許可日', '許可日'][cat];
        const item = it => '<li><div class="nm">' + esc(it[0] || '（施設名の記載なし）') + '</div><div class="ad">' + esc(it[1]) + '</div>'
          + (it[3] || it[2] ? '<div class="meta">' + (it[3] ? noLabel + '：' + esc(it[3]) : '') + (it[3] && it[2] ? '　' : '') + (it[2] ? dLabel + '：' + esc(it[2]) : '') + '</div>' : '') + '</li>';
        rest.push(items.slice(LIM).map(item).join(''));
        return head + '<ul class="ilist">' + items.slice(0, LIM).map(item).join('') + '</ul>'
          + (items.length > LIM ? '<button type="button" class="btn btn-sub btn-sm imore" data-i="' + (rest.length - 1) + '">残りの' + (items.length - LIM) + '件も表示する</button>' : '')
          + (src ? '<div class="isrc">出典：' + esc(src.muni) + (src.title ? '「' + esc(src.title) + '」' : 'の公表資料') + '<br>資料の日付：' + esc(src.date || '不明')
            + (srcLink(src) ? '<br><a href="' + esc(srcLink(src)) + '" target="_blank" rel="noopener">自治体のページを開く' + ext + '</a>' : '') + '</div>' : '');
      };
      let html;
      if (vis.length === 1) {
        const cat = vis[0][2];
        html = part(vis[0], '<div class="ihead">' + kic(cat) + '<div><div class="kname">' + esc(CATS[cat]) + '</div><div class="kcity">' + place(vis[0]) + '・' + count + '</div></div></div>' + badge);
      } else {
        html = '<div class="ihead"><svg class="kic" aria-hidden="true" style="color:#1f2a24"><use href="#i-pin"/></svg><div><div class="kname">' + count + '</div><div class="kcity">' + places.join('、') + '</div></div></div>'
          + '<ul class="ctally">' + s.comp.map(([c, v]) => '<li><span class="dot c' + c + '"></span>' + esc(CATS[c]) + '<span class="v">' + fmt(v) + '件</span></li>').join('') + '</ul>' + badge
          + vis.slice().sort((a, b) => a[2] - b[2] || b[5].length - a[5].length).map(p => '<section class="ipart">'
            + part(p, '<h4 class="iph">' + kic(p[2]) + esc(CATS[p[2]]) + ' ' + fmt(p[5].length) + '件' + (places.length > 1 ? '<small>' + place(p) + '</small>' : '') + '</h4>') + '</section>').join('');
      }
      if (others > 0) html += '<p class="inear">この点の近くに、ほかに' + fmt(others) + 'か所の点が重なっています。地図を拡大すると分かれて見えます。<br><button type="button" class="btn btn-sub btn-sm izoom">この場所を拡大する</button></p>';
      openCard(html);
      $$('.imore', body).forEach(b => b.addEventListener('click', () => { b.previousElementSibling.insertAdjacentHTML('beforeend', rest[+b.dataset.i]); b.remove(); }));
      const zb = $('.izoom', body);
      if (zb) zb.addEventListener('click', () => map.setView([s.lat, s.lng], Math.min(MAXZ, map.getZoom() + 3)));
      sel = s; selVis = vis; selOthers = others;
      redraw();
    }

    // ---- しぼりこみ ----
    const visible = p => S.cats.has(p[2]) && (S.pref === null || P.mpref[p[3]] === S.pref) && (!S.city || P.cities[p[6]] === S.city);
    let lastKey = null;
    function refresh(why) {
      shown = [];
      let N = 0;
      spots.forEach(s => {
        s.vis = s.pins.filter(visible);
        if (!s.vis.length) return;
        const t = [0, 0, 0, 0];
        s.vis.forEach(p => { t[p[2]] += p[5].length; });
        s.comp = t.map((v, i) => [i, v]).filter(c => c[1]).sort((a, b) => b[1] - a[1]);
        s.n = t[0] + t[1] + t[2] + t[3];
        s.key = s.comp.map(c => c.join(':')).join(',');
        s.exact = s.vis.every(p => p[4]);
        N += s.n;
        shown.push(s);
      });
      // 大きい点(同じ地点の施設が多い点)を先に描いて下にし、小さい点が隠れないようにする
      shown.sort((a, b) => tier(b.n) - tier(a.n));
      const { n } = scopeCounts();
      const M = sumOn(n);
      $('#mapcount').innerHTML = '地図に表示中：<b>' + fmt(N) + '件</b>（' + esc(placeName()) + '・' + esc(catsLabel()) + '）'
        + (M > N ? '<br><span class="sub">この条件の施設 ' + fmt(M) + '件のうち、住所から位置を特定できなかった ' + fmt(M - N) + '件は地図に出ていません。</span>' : '');
      if (sel && !sel.vis.length) emptyCard();
      else if (sel && (sel.vis.length !== selVis.length || sel.vis.some((p, i) => p !== selVis[i]))) showSpot(sel, selOthers);
      redraw();
      const key = S.pref + '|' + S.city;
      map.invalidateSize();   // 地図の枠の大きさが変わっていても、正しい範囲に合わせる
      if (why.home) { map.setView(HOME, 10); lastKey = key; return; }
      if (key !== lastKey && !why.nofit) {
        if (S.pref === null && !S.city) { if (lastKey !== null) map.setView(HOME, 10); }
        else if (shown.length) map.fitBounds(L.latLngBounds(shown.map(s => [s.lat, s.lng])), { padding: [24, 24], maxZoom: 15, animate: lastKey !== null });
      }
      lastKey = key;
    }
    subs.push(refresh);

    // ---- 住所・市区町村名で探す ----
    const citylist = $('#citylist');
    citylist.innerHTML = D.cities.slice().sort((a, b) => b.n.reduce((x, y) => x + y, 0) - a.n.reduce((x, y) => x + y, 0)).map(c => '<option value="' + esc(c.name) + '">').join('');
    const qmsg = $('#qmsg');
    let searchMark = null;
    function clearSearch() { if (searchMark) { map.removeLayer(searchMark); searchMark = null; } }
    $('#search').addEventListener('submit', async ev => {
      ev.preventDefault();
      const q = $('#q').value.trim().replace(/\s+/g, '');
      if (!q) { qmsg.textContent = '住所か市区町村名を入れてから「この場所を表示」を押してください。'; return; }
      qmsg.textContent = '';
      clearSearch();
      // 1) 都道府県名・市区町村名と一致したら、その地域をえらぶ(地図・グラフ・表がその地域になる)
      const pi = D.prefs.indexOf(q);
      if (pi >= 0) { update({ pref: pi, city: null }); qmsg.textContent = D.prefs[pi] + 'を表示しています。'; return; }
      const head = D.prefs.find(pf => q.startsWith(pf));
      const bare = head ? q.slice(head.length) : q;
      // 大阪府と兵庫県の「太子町」のように、同じ名前の町は「太子町（兵庫県）」という名前になっている
      const name = [bare, bare + '市', bare + '区', bare + '町', bare + '村', head && bare + '（' + head + '）'].find(n => n && cityBy.has(n));
      if (name) {
        update({ city: name });
        qmsg.innerHTML = '<b>' + esc(D.prefs[S.pref] + ' ' + name) + '</b>の施設だけを表示しています。ほかの地域も見るときは「検索・しぼりこみを解除」を押してください。';
        return;
      }
      // 2) それ以外は国土地理院の住所検索で場所を調べる
      qmsg.textContent = '検索中…';
      try {
        const res = await fetch('https://msearch.gsi.go.jp/address-search/AddressSearch?q=' + encodeURIComponent(q));
        const found = (await res.json()).filter(f => D.prefs.some(pf => f.properties.title.startsWith(pf)));
        if (!found.length) { qmsg.textContent = '「' + q + '」は見つかりませんでした。市区町村名や住所で試してください。'; return; }
        // 同じ地名が複数あるときは、近く(約1km以内)に施設が多い場所を優先する
        const near = f => { const [x, y] = f.geometry.coordinates; return P.pins.reduce((n, p) => n + (Math.abs(p[0] - y) < 0.009 && Math.abs(p[1] - x) < 0.011 ? p[5].length : 0), 0); };
        const ranked = found.slice(0, 20).map(f => [f, near(f)]).sort((a, b) => b[1] - a[1]).map(x => x[0]);
        const go = f => {
          const [lng, lat] = f.geometry.coordinates, t = f.properties.title;
          // 探した場所が、えらんでいる地域の外なら、地域の選択をはずす
          const fp = D.prefs.findIndex(pf => t.startsWith(pf));
          const fc = D.cities.find(c => c.pref === fp && t.slice(D.prefs[fp].length).startsWith(c.name));
          if ((S.pref !== null && S.pref !== fp) || (S.city && (!fc || fc.name !== S.city))) update({ pref: null, city: null }, { nofit: true });
          map.setView([lat, lng], 16);
          clearSearch();
          searchMark = L.marker([lat, lng], { icon: L.divIcon({ className: '', iconSize: [34, 46], iconAnchor: [17, 46],
            html: '<svg width="34" height="46" viewBox="0 0 24 24" style="color:#1f2a24;filter:drop-shadow(0 2px 2px rgba(0,0,0,.3))"><use href="#i-pin"/></svg>' }), interactive: false, keyboard: false }).addTo(map);
          return t;
        };
        const t0 = go(ranked[0]);
        qmsg.innerHTML = '<b>' + esc(t0) + '</b> を表示しました（黒いピンの場所）。'
          + (ranked.length > 1 ? '<br>ほかの候補：' + ranked.slice(1, 6).map((f, i) => '<a href="#" data-i="' + (i + 1) + '">' + esc(f.properties.title) + '</a>').join('／') : '');
        $$('a', qmsg).forEach(a => a.addEventListener('click', e => { e.preventDefault(); const t = go(ranked[+a.dataset.i]); qmsg.innerHTML = '<b>' + esc(t) + '</b> を表示しました（黒いピンの場所）。'; }));
      } catch (e) {
        qmsg.textContent = '検索できませんでした。時間をおいて試してください。';
      }
    });

    emptyCard();
    refresh({});  // 条件付きのURLで開いたときも、ここでその地域に合わせる

    mapApi = {
      map, clearSearch, shownSpots: () => shown, baseKey: () => baseKey,
    };
  }

  // ---------- 画像で保存(1920×1080) ----------
  const PW = 1920, PH = 1080, FONT = '"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Yu Gothic UI","Meiryo",sans-serif';
  const INK = '#303637', SUB = '#50585a', LINE = '#e4e4e0', BRAND = '#945945';  // BRAND はサイトの色(テラコッタ)
  function setFont(g, size, weight) { g.font = (weight || 400) + ' ' + size + 'px ' + FONT; }
  // 幅に入るよう文字を小さくする(min より小さくはしない。入らなければ末尾を…にする)
  function fitText(g, s, x, y, maxW, size, weight, color, align, min) {
    min = min || 32;
    let sz = size;
    setFont(g, sz, weight);
    while (g.measureText(s).width > maxW && sz > min) { sz -= 2; setFont(g, sz, weight); }
    let t = s;
    if (g.measureText(t).width > maxW) { while (t.length > 1 && g.measureText(t + '…').width > maxW) t = t.slice(0, -1); t += '…'; }
    g.fillStyle = color || INK; g.textAlign = align || 'left'; g.textBaseline = 'alphabetic';
    g.fillText(t, x, y);
    return g.measureText(t).width;
  }
  function frame(title, sub) {
    const cv = document.createElement('canvas');
    cv.width = PW; cv.height = PH;
    const g = cv.getContext('2d');
    g.fillStyle = '#fff'; g.fillRect(0, 0, PW, PH);
    g.fillStyle = BRAND; g.fillRect(0, 0, PW, 14);
    // 左上の小さなピンの絵
    g.save(); g.translate(56, 46); g.fillStyle = COLORS[0];
    g.beginPath(); g.moveTo(26, 0); g.bezierCurveTo(11.6, 0, 0, 11.3, 0, 25.4); g.bezierCurveTo(0, 44.4, 26, 70, 26, 70); g.bezierCurveTo(26, 70, 52, 44.4, 52, 25.4); g.bezierCurveTo(52, 11.3, 40.4, 0, 26, 0); g.fill();
    g.fillStyle = '#fff'; g.beginPath(); g.arc(26, 25, 10.5, 0, Math.PI * 2); g.fill(); g.restore();
    fitText(g, title, 130, 100, PW - 190, 62, 800, INK);
    fitText(g, sub, 130, 156, PW - 190, 36, 400, SUB);
    return { cv, g };
  }
  function footer(g, srcs) {
    g.fillStyle = LINE; g.fillRect(60, 900, PW - 120, 2);
    const ok = srcs.filter(s => s.count);
    fitText(g, '資料の日付：' + dateSpan(ok) + '　／　地図の更新：' + D.updated, 60, 950, PW - 120, 34, 400, SUB);
    const one = ok.length === 1 ? ok[0] : null;
    fitText(g, one ? '出典：' + one.muni + (one.title ? '「' + one.title + '」' : 'の公表資料') + '　' + srcLink(one)
      : '出典：各自治体の公表資料（' + ok.length + 'の資料。資料ごとのリンクは下のページの一覧表にあります）', 60, 998, PW - 120, 34, 400, SUB);
    fitText(g, '元のデータ：' + viewURL(), 60, 1048, PW - 120, 36, 700, BRAND);
  }
  function roundRect(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
  function loadImg(url) {
    return new Promise(res => {
      const im = new Image(); im.crossOrigin = 'anonymous';
      const t = setTimeout(() => res(null), 10000);
      im.onload = () => { clearTimeout(t); res(im); }; im.onerror = () => { clearTimeout(t); res(null); };
      im.src = url;
    });
  }
  async function drawMap(g, A) {
    const { map } = mapApi, key = mapApi.baseKey(), B = BASES[key];
    const z0 = map.getZoom(), size = map.getSize();
    const s = Math.min(A.w / size.x, A.h / size.y);
    const zt = Math.max(5, Math.min(B.native, Math.round(z0 + Math.log2(s))));
    const k = Math.pow(2, zt - z0);
    const c = map.project(map.getCenter(), z0);
    const X = px => A.x + A.w / 2 + (px - c.x) * s, Y = py => A.y + A.h / 2 + (py - c.y) * s;
    g.save(); roundRect(g, A.x, A.y, A.w, A.h, 18); g.clip();
    g.fillStyle = '#fff'; g.fillRect(A.x, A.y, A.w, A.h);
    const wx0 = c.x - A.w / 2 / s, wx1 = c.x + A.w / 2 / s, wy0 = c.y - A.h / 2 / s, wy1 = c.y + A.h / 2 / s;
    const jobs = [];
    for (let tx = Math.floor(wx0 * k / 256); tx <= Math.floor(wx1 * k / 256); tx++) for (let ty = Math.floor(wy0 * k / 256); ty <= Math.floor(wy1 * k / 256); ty++) {
      jobs.push(loadImg(B.img(zt + '/' + tx + '/' + ty)).then(im => [im, tx, ty]));
    }
    const tiles = await Promise.all(jobs);
    g.globalAlpha = opacityAt(key, z0);
    try { g.filter = B.filter; } catch (e) { /* 使えない環境ではそのまま */ }
    let okTiles = 0;
    tiles.forEach(([im, tx, ty]) => { if (!im) return; okTiles++; const d = 256 / k * s; g.drawImage(im, X(tx * 256 / k), Y(ty * 256 / k), d + 0.6, d + 0.6); });
    g.filter = 'none'; g.globalAlpha = 1;
    const inA = (x, y) => x > A.x - 30 && x < A.x + A.w + 30 && y > A.y - 30 && y < A.y + A.h + 30;
    // 画面と同じ点を、画像の大きさに合わせて描く
    const k0 = Math.pow(2, z0);
    mapApi.shownSpots().forEach(sp => {
      const x = X(sp.x0 * k0), y = Y(sp.y0 * k0);
      if (!inA(x, y)) return;
      const d = dotSize(sp.n, z0);
      paintDot(g, x, y, d * s, dotLine(d) * s, sp.comp, sp.exact, false);
    });
    if (!okTiles) { g.fillStyle = 'rgba(255,255,255,.85)'; g.fillRect(A.x + 20, A.y + 20, 760, 60); fitText(g, '背景の地図を読み込めませんでした', A.x + 36, A.y + 62, 720, 34, 700, SUB); }
    // 地図の出典
    setFont(g, 30, 400);
    const at = B.credit, aw = g.measureText(at).width;
    g.fillStyle = 'rgba(255,255,255,.88)'; g.fillRect(A.x + A.w - aw - 32, A.y + A.h - 50, aw + 32, 50);
    g.fillStyle = SUB; g.textAlign = 'right'; g.fillText(at, A.x + A.w - 16, A.y + A.h - 14); g.textAlign = 'left';
    g.restore();
    g.strokeStyle = '#d3d3ce'; g.lineWidth = 2; roundRect(g, A.x, A.y, A.w, A.h, 18); g.stroke();
  }
  function swatch(g, x, y, color, sz) { g.fillStyle = color; roundRect(g, x, y, sz, sz, 6); g.fill(); }
  async function pngMap() {
    const { cv, g } = frame(placeLong() + 'の民泊・旅館業の地図', '種類：' + catsLabel() + '　／　各自治体の公表資料をもとに作成');
    const A = { x: 60, y: 192, w: 1180, h: 690 };
    await drawMap(g, A);
    const { n, m } = scopeCounts();
    const x = 1290;
    fitText(g, 'この条件の施設', x, 248, 570, 40, 700, SUB);
    const tot = sumOn(n), shownN = sumOn(m);
    fitText(g, fmt(tot) + '件', x, 352, 570, 100, 800, INK);
    let y = 430;
    [...S.cats].sort().forEach(i => {
      swatch(g, x, y - 34, COLORS[i], 38);
      fitText(g, CATS[i], x + 56, y, 300, 40, 700, INK);
      fitText(g, fmt(n[i]) + '件', x + 570, y, 220, 40, 800, INK, 'right');
      y += 66;
    });
    y = Math.max(y, 640);
    g.fillStyle = LINE; g.fillRect(x, y - 30, 570, 2);
    const grayDot = (cx, cy, r, hollow) => {
      g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fillStyle = '#6b7280'; g.fill();
      if (hollow) { g.beginPath(); g.arc(cx, cy, r * 0.45, 0, Math.PI * 2); g.fillStyle = '#fff'; g.fill(); }
    };
    grayDot(x + 18, y + 6, 12, false);
    fitText(g, '番地まで分かった位置', x + 48, y + 18, 520, 34, 400, SUB);
    grayDot(x + 18, y + 62, 12, true);
    fitText(g, '中が白い点：町の中心付近（目安）', x + 48, y + 74, 520, 34, 400, SUB);
    grayDot(x + 6, y + 118, 5, false); grayDot(x + 22, y + 118, 8, false); grayDot(x + 44, y + 118, 12, false);
    fitText(g, '大きい点：同じ地点に複数の施設', x + 66, y + 130, 500, 34, 400, SUB);
    if (tot > shownN) fitText(g, '※位置が分からない' + fmt(tot - shownN) + '件は地図に出ていません', x, y + 184, 570, 32, 400, SUB, 'left', 24);
    footer(g, scopeSources().filter(s => kindOn(s.k)));
    return cv;
  }
  function hbars(g, rows, A, cats, maxV, opts) {          // 横棒(積み上げ)を描く。rows: [{name, sub, n:[4], v}]
    const rh = Math.min(76, A.h / Math.max(rows.length, 1));
    const nameW = opts.nameW || 360, valW = 230, bx = A.x + nameW + (opts.rank ? 70 : 0), bw = A.x + A.w - valW - bx - 20;
    rows.forEach((r, i) => {
      const cy = A.y + i * rh + rh / 2;
      if (opts.rank) {
        const col = r.rank === 1 ? '#f6c700' : r.rank === 2 ? '#d5dbd7' : r.rank === 3 ? '#eab68c' : '#f2f2ef';
        g.beginPath(); g.arc(A.x + 28, cy, 27, 0, Math.PI * 2); g.fillStyle = col; g.fill();
        setFont(g, 32, 800); g.fillStyle = INK; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(r.rank), A.x + 28, cy + 1); g.textBaseline = 'alphabetic';
      }
      const nx = A.x + (opts.rank ? 70 : 0);
      fitText(g, r.name, nx, cy + 15, nameW - 20, 44, 800, r.sel ? BRAND : INK, 'left', 34);
      let x = bx;
      cats.forEach(c => {
        const v = r.n[c];
        if (!v) return;
        const w = Math.max(4, v / maxV * bw);
        g.fillStyle = COLORS[c];
        roundRect(g, x, cy - 17, Math.max(w - 3, 2), 34, 5); g.fill();
        setFont(g, 28, 700);
        const lab = fmt(v);
        if (cats.length > 1 && g.measureText(lab).width + 16 < w - 3) { g.fillStyle = c === 3 ? INK : '#fff'; g.textAlign = 'center'; g.fillText(lab, x + (w - 3) / 2, cy + 10); g.textAlign = 'left'; }
        x += w;
      });
      fitText(g, fmt(r.v) + '件', A.x + A.w, cy + 15, valW, 44, 800, INK, 'right', 34);
      if (i < rows.length - 1) { g.fillStyle = LINE; g.fillRect(A.x, A.y + (i + 1) * rh - 1, A.w, 1.5); }
    });
  }
  function legendRow(g, cats, x, y) {
    cats.forEach(i => { swatch(g, x, y - 30, COLORS[i], 34); const w = fitText(g, CATS[i], x + 46, y, 340, 34, 700, INK); x += 46 + w + 44; });
  }
  function pngBars() {
    const cats = [...S.cats].sort();
    const sortLabel = barSort.options[barSort.selectedIndex].text;
    const list = barsList;
    let rows = list.slice(0, 10);
    const si = list.findIndex(x => x.c.name === S.city);
    if (si >= 10) rows = rows.concat([list[si]]);
    const { cv, g } = frame((S.pref === null ? ALL_AREA : D.prefs[S.pref]) + 'の市区町村ごとの施設の数', '種類：' + catsLabel() + '　／　' + sortLabel + '（上位' + Math.min(10, list.length) + '）');
    legendRow(g, cats, 60, 236);
    const maxV = Math.max(1, ...rows.map(x => x.v));
    hbars(g, rows.map(x => ({ name: x.c.name + (S.pref === null ? '（' + D.prefs[x.c.pref].replace(/[都府県]$/, '') + '）' : ''), n: x.c.n, v: x.v, sel: x.c.name === S.city, rank: list.indexOf(x) + 1 })),
      { x: 60, y: 266, w: PW - 120, h: 616 }, cats, maxV, { rank: true, nameW: S.pref === null ? 420 : 340 });
    footer(g, (S.pref === null ? D.sources : D.sources.filter(s => s.pref === S.pref)).filter(s => KIND_CATS[s.k].some(c => S.cats.has(c))));
    return cv;
  }
  function pngRank(gi) {
    const gr = RG[gi], { on, list } = rankList(gr);
    const rows = list.slice(0, 10);
    const { cv, g } = frame((S.pref === null ? ALL_AREA : D.prefs[S.pref]) + 'の' + gr.title + 'の数 市区町村ランキング', '種類：' + on.map(i => CATS[i]).join('・') + '　／　上位' + rows.length + '（全' + fmt(list.length) + '市区町村）');
    legendRow(g, on, 60, 236);
    const maxV = Math.max(1, ...rows.map(x => x.v));
    hbars(g, rows.map(x => ({ name: x.c.name + (S.pref === null ? '（' + D.prefs[x.c.pref].replace(/[都府県]$/, '') + '）' : ''), n: x.c.n, v: x.v, sel: x.c.name === S.city, rank: x.rank })),
      { x: 60, y: 266, w: PW - 120, h: 616 }, on, maxV, { rank: true, nameW: S.pref === null ? 420 : 340 });
    footer(g, (S.pref === null ? D.sources : D.sources.filter(s => s.pref === S.pref)).filter(s => KIND_CATS[s.k].some(c => on.includes(c))));
    return cv;
  }
  function lineCanvas(g, card, A) {            // 推移の折れ線を1つ描く
    const pts = card.pts, ax = niceAxis(pts.map(p => p.v));
    setFont(g, 28, 400);
    const pl = Math.max(...ax.ticks.map(t => g.measureText(fmt(t)).width)) + 18, pb = 44;
    const fx = x => A.x + pl + Math.min(1, Math.max(0, x / span)) * (A.w - pl - 10), fy = v => A.y + (ax.b - v) / (ax.b - ax.a) * (A.h - pb);
    ax.ticks.forEach(t => { g.fillStyle = LINE; g.fillRect(A.x + pl, fy(t), A.w - pl - 10, 1.5); g.fillStyle = SUB; g.textAlign = 'right'; g.fillText(fmt(t), A.x + pl - 10, fy(t) + 10); });
    g.textAlign = 'left'; g.fillText(D.axis[0], A.x + pl, A.y + A.h - 4); g.textAlign = 'right'; g.fillText(D.axis[1], A.x + A.w - 10, A.y + A.h - 4); g.textAlign = 'left';
    const P = pts.map(p => [fx(p.x), fy(p.v)]);
    g.strokeStyle = card.color; g.lineWidth = 5; g.lineJoin = 'round'; g.lineCap = 'round';
    P.slice(1).forEach((q, i) => { g.setLineDash(pts[0].old && i === 0 ? [6, 10] : []); g.beginPath(); g.moveTo(P[i][0], P[i][1]); g.lineTo(q[0], q[1]); g.stroke(); });
    g.setLineDash([]);
    P.forEach((p, i) => {
      g.fillStyle = card.color; g.strokeStyle = '#fff'; g.lineWidth = 3;
      if (pts[i].old) { g.beginPath(); g.moveTo(p[0] - 2, p[1]); g.lineTo(p[0] + 16, p[1] - 10); g.lineTo(p[0] + 16, p[1] + 10); g.closePath(); g.fill(); g.stroke(); }
      else { g.beginPath(); g.arc(p[0], p[1], 9, 0, Math.PI * 2); g.fill(); g.stroke(); }
    });
  }
  function pngTrend() {
    const cards = trendCards().filter(c => c.pts).slice(0, 4);
    const { cv, g } = frame(placeLong() + 'の民泊・旅館業の件数の推移', '横軸：資料の日付（' + D.axis[0] + '〜' + D.axis[1] + '）　／　増減は自治体ごとの前回の資料との差');
    if (!cards.length) { fitText(g, 'えらんだ条件で表示できる推移がありません', 60, 400, PW - 120, 44, 700, SUB); footer(g, []); return cv; }
    const n = cards.length, cols = n === 4 ? 2 : n, rowsN = n === 4 ? 2 : 1;
    const gap = 36, W = (PW - 120 - gap * (cols - 1)) / cols, H = (690 - gap * (rowsN - 1)) / rowsN;
    cards.forEach((c, i) => {
      const x = 60 + (i % cols) * (W + gap), y = 192 + Math.floor(i / cols) * (H + gap);
      g.strokeStyle = LINE; g.lineWidth = 2; roundRect(g, x, y, W, H, 18); g.stroke();
      g.fillStyle = c.color; roundRect(g, x + 24, y + 30, 30, 10, 4); g.fill();
      const big = H > 500;
      fitText(g, c.title, x + 66, y + 48, W - 90, 40, 800, INK, 'left', 32);
      const now = c.pts[c.pts.length - 1].v;
      let ty = y + 48 + (big ? 84 : 66);
      const vw = fitText(g, fmt(now) + '件', x + 24, ty, W - 48, big ? 72 : 56, 800, INK);
      let line1 = '', line2 = '', d = null;
      if (c.kind === 'src') {
        const vs = c.s.vs, last = vs[vs.length - 1], prev = vs.length > 1 ? vs[vs.length - 2] : null;
        fitText(g, (c.s.date || last[0]) + 'の資料', x + 36 + vw, ty, W - 70 - vw, 32, 400, SUB, 'left', 30);
        if (prev) { d = last[1] - prev[1]; line1 = '前回との差 ' + signed(d) + '件'; line2 = '前回 ' + prev[0] + '：' + fmt(prev[1]) + '件 → 今回 ' + last[0] + '：' + fmt(last[1]) + '件'; }
        else line1 = '前回の記録はまだありません';
      } else {
        d = now - c.pts[0].v;
        fitText(g, c.srcs.filter(s => s.count).length + 'の資料の合計', x + 36 + vw, ty, W - 70 - vw, 32, 400, SUB, 'left', 30);
        line1 = 'グラフの期間の増減 ' + signed(d) + '件';
        line2 = '※資料の日付が違う自治体の合計（同じ期間の比較ではありません）';
      }
      ty += big ? 62 : 52;
      fitText(g, line1, x + 24, ty, W - 48, big ? 40 : 36, 800, d > 0 ? '#c62828' : d < 0 ? '#1d4ed8' : SUB, 'left', 32);
      if (line2) { ty += big ? 48 : 42; fitText(g, line2, x + 24, ty, W - 48, 32, 400, SUB, 'left', 30); }
      lineCanvas(g, c, { x: x + 24, y: ty + 30, w: W - 48, h: y + H - ty - 50 });
    });
    const srcs = S.city ? cards.map(c => c.s) : cards.length ? cards[0].srcs : [];
    footer(g, srcs.filter(Boolean));
    return cv;
  }
  const dlg = $('#pngdlg');
  $$('[data-close]', dlg).forEach(b => b.addEventListener('click', () => dlg.close()));
  document.addEventListener('click', async ev => {
    const b = ev.target.closest('[data-png]');
    if (!b) return;
    const what = b.dataset.png;
    if (what === 'map' && !mapApi) { toast('地図の読み込みが終わってから、もう一度押してください。'); return; }
    const img = $('#pngimg'), dl = $('#pngdl'), msg = $('#pngmsg');
    img.hidden = true; dl.hidden = true; msg.textContent = '画像を作っています…';
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    try {
      const cv = what === 'map' ? await pngMap() : what === 'bars' ? pngBars() : what === 'trend' ? pngTrend() : pngRank(+what.slice(4));
      const blob = await new Promise(res => cv.toBlob(res, 'image/png'));
      const url = URL.createObjectURL(blob);
      const label = { map: '地図', bars: '市区町村の比較', trend: '推移', rank0: '民泊ランキング', rank1: '旅館業ランキング' }[what];
      img.src = url; img.alt = label + 'の画像（' + placeName() + '）'; img.hidden = false;
      dl.href = url; dl.download = '民泊旅館業マップ_' + label + '_' + placeName().replace(/\s+/g, '') + '_' + D.updated.replace(/\//g, '') + '.png'; dl.hidden = false;
      msg.textContent = '横長（1920×1080）の画像ができました。下のボタンで保存できます。';
    } catch (e) {
      msg.textContent = '画像を作れませんでした（' + (e && e.message || e) + '）。地図の背景が読み込めないときは、時間をおいて試してください。';
    }
  });

  // ---------- 画面の上の条件の帯・上に戻るボタン ----------
  const bar = $('#condbar'), totop = $('#totop'), hero = $('.hero');
  let ticking = false;
  function onScroll() {
    ticking = false;
    const past = window.scrollY > hero.offsetTop + hero.offsetHeight - 40;
    bar.hidden = !past;
    totop.hidden = window.scrollY < 700;
  }
  window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
  totop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

  // ---------- はじめに ----------
  loadURL();
  buildTable();
  $('#legend-cats').innerHTML = CATS.map((c, i) => '<span class="lk">' + kic(i) + '<span class="dot c' + i + '"></span>' + esc(c) + '</span>').join('');
  update({});
  onScroll();
  initMap().catch(e => { $('#map').innerHTML = '<p class="maploading">地図を読み込めませんでした。ページを開き直してください。（' + esc(e && e.message || e) + '）</p>'; });
})();
