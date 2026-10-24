// 局内面板的特效：连杀、出装提示、野怪 / 龙魂播报、经济曲线。
// 特效事件由状态中心放在 game.fx 里（state/effects.py），这里按 id 播放没播过的、刚发生的。
// 美术是占位的初版，样式都在 effects.css。
(function () {
    'use strict';

    const { stage } = WROB.panel;
    const fxLayer = document.getElementById('fx');
    const chart = document.getElementById('chart');
    const SIDES = ['blue', 'red'];
    const STALE_MS = 10000;             // 晚于这么久才收到的特效不放了（页面刚打开、断线重连）
    const MULTIKILL = {
        2: { key: 'double', title: 'DOUBLE KILL', cn: '双杀' },
        3: { key: 'triple', title: 'TRIPLE KILL', cn: '三杀' },
        4: { key: 'quadra', title: 'QUADRA KILL', cn: '四杀' },
        5: { key: 'penta', title: 'PENTA KILL', cn: '五杀' },
    };
    const CHART_REFRESH_MS = 2000;

    let config = { hold: {}, texts: {}, dragons: {} };
    let seen = null;                    // 播到哪一个 id 了

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    // 一个元素在舞台坐标里的位置（舞台本身被整体缩放过）
    function rectOf(node) {
        const r = node.getBoundingClientRect();
        const s = stage.getBoundingClientRect();
        const k = WROB.overlay.scale || 1;
        return { x: (r.left - s.left) / k, y: (r.top - s.top) / k, w: r.width / k, h: r.height / k };
    }
    function union(rects) {
        const x = Math.min(...rects.map(r => r.x)), y = Math.min(...rects.map(r => r.y));
        const right = Math.max(...rects.map(r => r.x + r.w)), bottom = Math.max(...rects.map(r => r.y + r.h));
        return { x, y, w: right - x, h: bottom - y };
    }
    function place(node, box) {
        Object.assign(node.style, { left: box.x + 'px', top: box.y + 'px', width: box.w + 'px', height: box.h + 'px' });
        node.style.setProperty('--h', box.h + 'px');
        node.style.setProperty('--w', box.w + 'px');
    }
    function teamOf(state, side) {
        const series = state.series;
        return series.teams[series.sides[side]];
    }

    // ---- 连杀：双杀在这名选手那一栏，三杀在这一方的半边，四杀整个底部，五杀全屏 ----
    // 底部是对位面板时“那一栏”是一行；切成团战 UI 时是一列（窄而高，排法另有一套，见 effects.css 的 is-column）

    const multikillNodes = {};          // 同一名选手的上一个连杀还在放：直接换成新的
    function playMultikill(event, state) {
        const look = MULTIKILL[event.count];
        if (!look) return;
        const known = event.index != null;
        const player = known ? state.game.players[event.side][event.index] : {};
        const cells = WROB.roles.map((_, index) => WROB.panel.cell(event.side, index));
        // 不知道是谁的双杀：放在这一方的半边（和三杀一样大）
        const box = event.count === 2 && known ? rectOf(cells[event.index])
            : event.count <= 3 ? union(cells.map(rectOf))
            : event.count === 4 ? rectOf(WROB.panel.bottom())
            : { x: 0, y: 0, w: stage.offsetWidth, h: stage.offsetHeight };
        // 同一方上一个多杀还没放完（游戏里二连破、三连破会接连出现）：直接换成新的
        const key = event.side;
        if (multikillNodes[key]) multikillNodes[key].remove();

        const hold = Number(config.hold[look.key]) || 3;
        const shape = event.count !== 2 ? '' : !known ? ' is-wide' : WROB.panel.teamfight() ? ' is-column' : '';
        const node = element('div', 'fx-mk fx-' + look.key + ' ' + event.side + shape);
        place(node, box);
        node.style.setProperty('--hold', hold + 's');
        const face = element('span', 'fx-mk-face');
        if (player.champion) face.append(event.count === 5 ? WROB.champions.portrait(player.champion) : WROB.champions.icon(player.champion));
        const team = teamOf(state, event.side);
        const copy = element('div', 'fx-mk-copy');
        copy.append(element('b', 'fx-mk-title', look.title),
            element('span', 'fx-mk-sub', look.cn + ' · ' + [team.short || team.name || WROB.sideNames[event.side], player.name].filter(Boolean).join(' ')));
        node.append(element('i', 'fx-mk-shine'), face, copy);
        fxLayer.append(node);
        multikillNodes[key] = node;
        setTimeout(() => { node.remove(); if (multikillNodes[key] === node) delete multikillNodes[key]; }, hold * 1000);
    }

    // ---- 出装：传说装的图标盖住这名选手的头像，一次出了好几件就轮流 ----

    const itemQueues = {};
    function playItems(event) {
        const key = event.side + event.index;
        const queue = itemQueues[key] || (itemQueues[key] = { items: [], running: false });
        queue.items.push(...(event.items || []));
        if (!queue.running) nextItem(event.side, event.index, queue);
    }
    function nextItem(side, index, queue) {
        const portrait = WROB.panel.portrait(side, index);          // 团战 UI 显示时是那里的头像
        portrait.querySelectorAll('.fx-item').forEach(node => node.remove());
        const name = queue.items.shift();
        queue.running = Boolean(name);
        if (!name) return;
        const hold = Number(config.hold.item) || 2;
        const flash = element('span', 'fx-item');
        flash.style.setProperty('--hold', hold + 's');
        const img = new Image();
        img.alt = '';
        img.src = '/assets/items/' + encodeURIComponent(name) + '.png';
        flash.append(img);
        portrait.append(flash);
        setTimeout(() => nextItem(side, index, queue), hold * 1000);
    }

    // ---- 野怪 / 龙魂播报：在右上角那一块里替换显示几秒再淡出（corners.js），接连来的排队 ----

    function fill(template, values) {
        return String(template || '').replace(/\{(\w+)\}/g, (_, key) => values[key] ?? '');
    }
    function playBanner(event, state) {
        const names = config.dragons[event.element] || {};
        const team = event.side ? teamOf(state, event.side) : null;
        const values = {
            team: team ? (team.short || team.name || WROB.sideNames[event.side]) : '',
            side: event.side ? WROB.sideNames[event.side] : '',
            dragon: names.dragon || '元素亚龙', soul: names.soul || '龙魂',
        };
        const template = event.kind === 'objective' ? config.texts[event.what]
            : event.kind === 'soul' ? config.texts.soul : config.texts.soul_element;
        if (!template) return;
        const node = element('div', 'fx-obj ' + (event.side || 'neutral') + ' fx-obj-' + event.kind);
        if (team && team.logo) {
            const logo = element('span', 'fx-obj-logo');
            const img = new Image();
            img.alt = '';
            img.src = team.logo;
            logo.append(img);
            node.append(logo);
        }
        const kind = event.element || event.what;
        const icon = ['infernal', 'mountain', 'ocean'].includes(kind) ? WROB.icons.dragon(kind)
            : Object.assign(element('i', 'icon'), { innerHTML: WROB.icons.svg(kind) });
        icon.classList.add('fx-obj-icon');
        node.append(icon, element('b', 'fx-obj-text', fill(template, values)));
        WROB.corners.notify(node, Number(config.hold.objective) || 2.5);
    }

    // ---- 播放 ----

    function play(state) {
        const list = state.game.fx || [];
        const last = list.length ? list[list.length - 1].id : 0;
        if (seen === null || last < seen) {        // 页面刚打开 / 开了新的一局：之前的都不补放
            seen = seen === null ? last : 0;
            if (seen === last) return;
        }
        list.filter(event => event.id > seen).forEach(event => {
            if (WROB.now() - event.at > STALE_MS) return;
            if (!state.ui.effects && !event.manual) return;
            if (event.kind === 'multikill') playMultikill(event, state);
            else if (event.kind === 'item') playItems(event);
            else playBanner(event, state);
        });
        seen = last;
    }

    // ---- 经济曲线：顶替底部面板的位置；蓝方领先画在零线上面，红方在下面 ----

    const SVG = 'http://www.w3.org/2000/svg';
    const COLORS = { blue: '#2b8ff0', red: '#f0414f' };     // 在深色底上验过对比度和色盲区分
    let curve = [];
    let chartTimer = 0;
    let chartShown = false;

    function svg(tag, attrs, text) {
        const node = document.createElementNS(SVG, tag);
        Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
        if (text != null) node.textContent = text;
        return node;
    }
    const compact = value => {
        const abs = Math.abs(value);
        return abs >= 1000 ? (abs / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : String(Math.round(abs));
    };
    const mmss = seconds => String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');
    function niceMax(value) {
        const steps = [1000, 2000, 3000, 5000, 8000, 10000, 15000, 20000, 30000];
        return steps.find(step => step >= value) || Math.ceil(value / 10000) * 10000;
    }

    function drawChart(state) {
        const W = chart.clientWidth || 1500, H = chart.clientHeight || 280;
        const pad = { left: 96, right: 150, top: 62, bottom: 58 };
        const plotW = W - pad.left - pad.right, plotH = H - pad.top - pad.bottom;
        const zero = pad.top + plotH / 2;
        const points = curve.length ? curve : [[0, 0]];
        const tMax = Math.max(300, points[points.length - 1][0]);
        const yMax = niceMax(Math.max(1, ...points.map(point => Math.abs(point[1]))));
        const x = t => pad.left + (t / tMax) * plotW;
        const y = d => zero - (d / yMax) * (plotH / 2);
        const teams = { blue: teamOf(state, 'blue'), red: teamOf(state, 'red') };
        const label = side => teams[side].short || teams[side].name || WROB.sideNames[side];

        const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img',
            'aria-label': '团队经济差曲线' });
        const defs = svg('defs');
        const above = svg('clipPath', { id: 'fx-above' }), below = svg('clipPath', { id: 'fx-below' });
        above.append(svg('rect', { x: 0, y: 0, width: W, height: zero }));
        below.append(svg('rect', { x: 0, y: zero, width: W, height: H }));
        defs.append(above, below);
        root.append(defs);

        // 标题、图例、现在的经济差
        root.append(svg('text', { x: 28, y: 42, class: 'chart-title' }, '经济差'));
        let legendX = 150;
        SIDES.forEach(side => {
            root.append(svg('rect', { x: legendX, y: 27, width: 16, height: 16, rx: 3, fill: COLORS[side] }));
            const text = svg('text', { x: legendX + 24, y: 41, class: 'chart-legend' }, label(side) + ' 领先');
            root.append(text);
            legendX += 24 + 16 * (label(side).length + 3) + 40;
        });
        const now = points[points.length - 1][1];
        const leader = now > 0 ? 'blue' : now < 0 ? 'red' : null;
        root.append(svg('text', { x: W - 28, y: 44, class: 'chart-now', 'text-anchor': 'end' },
            leader ? label(leader) + ' 领先 ' + Math.abs(now).toLocaleString('en-US') : '经济持平'));

        // 网格：零线和 ±一半、±满格，都是细实线
        [-1, -0.5, 0.5, 1].forEach(k => root.append(svg('line', { x1: pad.left, x2: W - pad.right, y1: y(k * yMax), y2: y(k * yMax), class: 'chart-grid' })));
        root.append(svg('line', { x1: pad.left, x2: W - pad.right, y1: zero, y2: zero, class: 'chart-zero' }));
        [-1, 1].forEach(k => root.append(svg('text', { x: pad.left - 14, y: y(k * yMax) + 7, class: 'chart-tick', 'text-anchor': 'end' }, compact(yMax))));
        root.append(svg('text', { x: pad.left - 14, y: zero + 7, class: 'chart-tick', 'text-anchor': 'end' }, '0'));
        // 左边一道色条：上半是蓝方领先，下半是红方领先
        root.append(svg('rect', { x: pad.left - 6, y: pad.top, width: 4, height: plotH / 2 - 2, rx: 2, fill: COLORS.blue }));
        root.append(svg('rect', { x: pad.left - 6, y: zero + 2, width: 4, height: plotH / 2 - 2, rx: 2, fill: COLORS.red }));

        // 时间刻度
        const step = tMax > 1200 ? 300 : tMax > 600 ? 180 : 60;
        for (let t = 0; t <= tMax; t += step) {
            root.append(svg('text', { x: x(t), y: H - 10, class: 'chart-tick', 'text-anchor': 'middle' }, mmss(t)));
        }

        // 曲线：同一条路径，零线上面涂蓝、下面涂红
        const line = points.map((point, i) => (i ? 'L' : 'M') + x(point[0]).toFixed(1) + ' ' + y(point[1]).toFixed(1)).join(' ');
        const area = line + ` L${x(points[points.length - 1][0]).toFixed(1)} ${zero} L${x(points[0][0]).toFixed(1)} ${zero} Z`;
        [['fx-above', 'blue'], ['fx-below', 'red']].forEach(([clip, side]) => {
            root.append(svg('path', { d: area, fill: COLORS[side], 'fill-opacity': 0.26, 'clip-path': `url(#${clip})` }));
            root.append(svg('path', { d: line, fill: 'none', stroke: COLORS[side], 'stroke-width': 3, 'stroke-linejoin': 'round', 'clip-path': `url(#${clip})` }));
        });
        const end = points[points.length - 1];
        root.append(svg('circle', { cx: x(end[0]), cy: y(end[1]), r: 7, fill: leader ? COLORS[leader] : '#8b93a3', class: 'chart-end' }));
        root.append(svg('text', { x: x(end[0]) + 16, y: y(end[1]) + 8, class: 'chart-end-label' }, (end[1] ? '+' : '') + compact(end[1])));

        // 野怪：打掉的时刻，按阵营上色的小圆点 + 图标，排在时间刻度上方
        (state.game.events || []).filter(event => event.side && event.t <= tMax).forEach(event => {
            const cx = x(event.t), cy = H - pad.bottom + 14;
            root.append(svg('circle', { cx, cy, r: 13, fill: COLORS[event.side], class: 'chart-mark' }));
            const glyph = svg('g', { transform: `translate(${cx - 9} ${cy - 9}) scale(0.75)`, fill: '#fff' });
            glyph.innerHTML = WROB.icons.svg(event.element || event.what).replace(/^<svg[^>]*>|<\/svg>$/g, '');
            root.append(glyph);
        });
        chart.replaceChildren(root);
    }

    function refreshCurve() {
        fetch('/api/curve').then(response => response.json()).then(data => {
            curve = Array.isArray(data) ? data : [];
            if (chartShown && WROB.state) drawChart(WROB.state);
        }).catch(() => {});
    }

    function paintChart(state) {
        const show = Boolean(state.ui.chart);
        chart.classList.toggle('is-shown', show);
        if (show && !chartShown) {
            refreshCurve();
            chartTimer = setInterval(refreshCurve, CHART_REFRESH_MS);
        } else if (!show && chartShown) {
            clearInterval(chartTimer);
        }
        chartShown = show;
        if (show) drawChart(state);
    }

    WROB.catalog().then(catalog => {
        config = Object.assign(config, catalog.effects || {});
        WROB.onState(state => { paintChart(state); play(state); });
    });
})();
