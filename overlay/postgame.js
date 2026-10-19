// 赛后：导播点“结束本局 · X 胜”后，整个局内面板换成胜利画面；识别在数据机的结算页“统计 · 数据对比”上读到
// 双方十个人的数据之后，换成赛后面板（版式参考 data/screenshots/结算/赛后面板参考.png）。读不到就一直停在胜利画面。
// 两样都按 1920×1080 排版，再等比缩放、居中放进画布（单设备 2800×1264 时两侧用底色补满）。
(function () {
    'use strict';

    const { setLogo, setText, clock } = WROB.overlay;
    const SIDES = ['blue', 'red'];
    const DESIGN = { width: 1920, height: 1080 };
    const VICTORY_MIN_MS = 6000;        // 胜利画面至少放这么久：结算数据读得再快，也不会一闪就换掉
    const COLORS = { blue: '#2f9bff', red: '#ff4b5c' };
    const METRICS = {
        damage: ['TOTAL DAMAGE DEALT TO CHAMPIONS', '对英雄造成的伤害'],
        taken: ['TOTAL DAMAGE TAKEN', '承受伤害'],
        healing: ['TOTAL HEALING & SHIELDING', '治疗与护盾'],
        gold: ['TOTAL GOLD EARNED', '获得经济'],
    };
    const ROWS = [
        ['kda', 'K/D/A', '击杀 / 死亡 / 助攻'],
        ['gold', 'GOLDS', '经济'],
        ['towers', 'TURRETS', '防御塔'],
        ['heralds', 'HERALDS', '峡谷先锋'],
        ['dragons', 'DRAKES', '元素亚龙'],
        ['elders', 'ELDER DRAGONS', '远古巨龙'],
        ['barons', 'BARONS', '纳什男爵'],
    ];

    const stage = document.getElementById('stage');
    const root = document.getElementById('postgame');
    const canvas = root.querySelector('.pg-canvas');
    const victory = root.querySelector('.pg-victory');
    const panel = root.querySelector('.pg-panel');
    let paintedKey = '';
    let view = 'none';

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }
    function svg(tag, attrs, text) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
        if (text != null) node.textContent = text;
        return node;
    }
    function logo(url, className) {
        const holder = element('span', 'logo ' + (className || ''));
        holder.append(element('img'));
        setLogo(holder, url);
        return holder;
    }
    function icon(name) {
        const node = element('i', 'pg-icon ' + name);
        node.innerHTML = WROB.icons.svg(name);
        return node;
    }
    const teamName = team => team.short || team.name || '';
    const number = value => value == null ? '–' : String(value);
    const compact = value => (Math.abs(value) / 1000).toFixed(1) + 'K';

    // ---- 胜利画面 ----

    function paintVictory(post) {
        const side = post.winnerSide;
        const team = post.teams[side];
        const wins = Math.floor(post.bestOf / 2) + 1;
        victory.dataset.side = side;
        setLogo(victory.querySelector('.pv-logo'), team.logo);
        setText(victory.querySelector('.pv-short'), teamName(team));
        setText(victory.querySelector('.pv-name'), team.name && team.name !== team.short ? team.name : '');
        setText(victory.querySelector('.pv-cn'), post.score[side] >= wins && post.bestOf > 1
            ? '拿下系列赛胜利' : '赢下第 ' + post.n + ' 局');
        const score = victory.querySelector('.pv-score');
        score.replaceChildren(...SIDES.map(each => {
            const box = element('div', 'pv-side ' + each + (each === side ? ' is-winner' : ''));
            box.append(logo(post.teams[each].logo), element('b', '', teamName(post.teams[each])),
                element('span', 'num', String(post.score[each])));
            return box;
        }));
        score.insertBefore(element('i', 'pv-colon', ':'), score.lastChild);
        setText(victory.querySelector('.pv-meta'), [post.title, post.stage, 'GAME ' + post.n,
            post.length != null ? '用时 ' + clock(post.length) : '', 'BO' + post.bestOf].filter(Boolean).join('  ·  '));
    }

    // ---- 赛后面板 ----

    function paintHead(post) {
        SIDES.forEach(side => {
            const box = panel.querySelector('.pp-team.' + side);
            const won = post.winnerSide === side;
            box.classList.toggle('is-winner', won);
            setText(box.querySelector('.pp-short'), teamName(post.teams[side]));
            setText(box.querySelector('.pp-result'), won ? 'WIN' : 'LOSS');
            setLogo(box.querySelector('.logo'), post.teams[side].logo);
            setText(box.querySelector('.pp-score'), String(post.score[side]));
        });
        setText(panel.querySelector('.pp-time'), post.length != null ? clock(post.length) : '--:--');
        setText(panel.querySelector('.pp-format'), 'BO' + post.bestOf);
    }

    function cell(key, totals, side) {
        const box = element('div', 'pp-v ' + side);
        const t = totals[side];
        const icons = (count, name) => {
            if (!count) return box.append(element('span', 'pp-none', '-'));
            for (let i = 0; i < count; i++) box.append(icon(name));
        };
        if (key === 'kda') box.append(element('b', 'num', t.kills + '/' + t.deaths + '/' + t.assists));
        else if (key === 'gold') box.append(element('b', 'num', number(t.gold)));
        else if (key === 'towers') box.append(element('b', 'num', number(t.towers)));
        else if (key === 'heralds') icons(t.heralds, 'herald');
        else if (key === 'elders') icons(t.elders, 'elder');
        else if (key === 'barons') icons(t.barons, 'baron');
        else if (key === 'dragons') {
            if (!t.dragons.length) box.append(element('span', 'pp-none', '-'));
            t.dragons.forEach(kind => box.append(WROB.icons.dragon(kind)));
        }
        return box;
    }

    function paintTable(post) {
        panel.querySelector('.pp-table').replaceChildren(...ROWS.map(([key, title, cn]) => {
            const row = element('div', 'pp-row');
            const label = element('div', 'pp-label');
            label.append(element('b', '', title), element('span', '', cn));
            row.append(cell(key, post.totals, 'blue'), label, cell(key, post.totals, 'red'));
            return row;
        }));
    }

    // 经济差曲线：零线上面蓝方领先、下面红方领先，和参考图一样只画面积
    function paintChart(post) {
        const W = 840, H = 262;
        const pad = { left: 64, right: 8, top: 10, bottom: 30 };
        const plotW = W - pad.left - pad.right, plotH = H - pad.top - pad.bottom;
        const points = post.curve && post.curve.length ? post.curve : [[0, 0]];
        const tMax = Math.max(60, post.length || 0, points[points.length - 1][0]);
        const high = Math.max(0, ...points.map(point => point[1]));
        const low = Math.min(0, ...points.map(point => point[1]));
        const span = Math.max(1000, high - low);
        const top = high + span * 0.06, bottom = low - span * 0.06;
        const x = t => pad.left + (t / tMax) * plotW;
        const y = d => pad.top + ((top - d) / (top - bottom)) * plotH;
        const zero = y(0);

        const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pp-chart-svg', role: 'img', 'aria-label': '经济差曲线' });
        const defs = svg('defs');
        [['pp-above', 0, zero], ['pp-below', zero, H]].forEach(([id, from, to]) => {
            const clip = svg('clipPath', { id });
            clip.append(svg('rect', { x: 0, y: from, width: W, height: Math.max(0, to - from) }));
            defs.append(clip);
        });
        root.append(defs, svg('rect', { x: pad.left, y: pad.top, width: plotW, height: plotH, class: 'pp-plot' }));
        const ticks = 8;
        for (let i = 0; i <= ticks; i++) {
            const t = tMax * i / ticks;
            if (i > 0 && i < ticks) root.append(svg('line', { x1: x(t), x2: x(t), y1: pad.top, y2: pad.top + plotH, class: 'pp-grid' }));
            root.append(svg('text', { x: x(t), y: H - 6, class: 'pp-tick', 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle' },
                clock(t)));
        }
        const line = points.map((point, i) => (i ? 'L' : 'M') + x(point[0]).toFixed(1) + ' ' + y(point[1]).toFixed(1)).join(' ');
        const area = line + ` L${x(points[points.length - 1][0]).toFixed(1)} ${zero.toFixed(1)} L${x(points[0][0]).toFixed(1)} ${zero.toFixed(1)} Z`;
        [['pp-above', 'blue'], ['pp-below', 'red']].forEach(([clip, side]) => {
            root.append(svg('path', { d: area, fill: COLORS[side], 'fill-opacity': 0.82, 'clip-path': `url(#${clip})` }));
        });
        root.append(svg('line', { x1: pad.left, x2: pad.left + plotW, y1: zero, y2: zero, class: 'pp-zero' }));
        // 纵轴只标两头：蓝方最多领先多少、红方最多领先多少
        root.append(svg('text', { x: pad.left - 8, y: pad.top + 14, class: 'pp-tick blue', 'text-anchor': 'end' }, compact(high)));
        root.append(svg('text', { x: pad.left - 8, y: pad.top + plotH, class: 'pp-tick red', 'text-anchor': 'end' }, compact(low)));
        panel.querySelector('.pp-chart-box').replaceChildren(root);
    }

    function paintPlayers(post) {
        const metric = METRICS[post.metric] ? post.metric : 'damage';
        setText(panel.querySelector('.pp-metric b'), METRICS[metric][0]);
        setText(panel.querySelector('.pp-metric span'), METRICS[metric][1]);
        const stats = post.stats || { blue: [], red: [] };
        const values = SIDES.flatMap(side => (stats[side] || []).map(row => row[metric] || 0));
        const most = Math.max(1, ...values);
        panel.querySelector('.pp-players').replaceChildren(...[0, 1, 2, 3, 4].map(index => {
            const pair = element('div', 'pp-pair');
            SIDES.forEach(side => {
                const player = post.players[side][index] || {};
                const value = ((stats[side] || [])[index] || {})[metric];
                const box = element('div', 'pp-player ' + side);
                const portrait = element('span', 'pp-portrait');
                if (player.champion) portrait.append(WROB.champions.icon(player.champion));
                const text = element('div', 'pp-ptext');
                const bar = element('i', 'pp-bar');
                bar.style.setProperty('--share', value ? (value / most).toFixed(4) : '0');
                bar.style.setProperty('--delay', (index * 90 + (side === 'red' ? 45 : 0)) + 'ms');
                text.append(element('b', 'pp-pname', player.name || WROB.champions.name(player.champion) || ''),
                    element('span', 'pp-pvalue num', value == null ? '–' : String(value)), bar);
                box.append(portrait, text);
                pair.append(box);
            });
            return pair;
        }));
    }

    function paintBans(post) {
        const any = SIDES.some(side => (post.bans[side] || []).some(Boolean));
        const box = panel.querySelector('.pp-bans');
        box.hidden = !any;                  // 自选模式没有禁用
        SIDES.forEach(side => {
            box.querySelector('.' + side + ' .pp-ban-icons').replaceChildren(...(post.bans[side] || []).map(slug => {
                const mini = element('span', 'pp-ban');
                if (slug) mini.append(WROB.champions.icon(slug));
                return mini;
            }));
        });
    }

    function paint(post) {
        const key = JSON.stringify([post.n, post.at, post.statsAt, post.metric, post.teams, post.score, post.length]);
        if (key === paintedKey) return;
        paintedKey = key;
        paintVictory(post);
        paintHead(post);
        paintTable(post);
        paintChart(post);
        paintPlayers(post);
        paintBans(post);
        if (view === 'panel') replay(panel);        // 换了比较的那一项：条形图重新长一遍
    }

    // ---- 显示哪一个 ----

    function replay(node) {
        node.classList.remove('is-in');
        void node.offsetWidth;              // 让入场动画从头再放
        node.classList.add('is-in');
    }

    function fit() {
        const width = Number(stage.dataset.width) || DESIGN.width, height = Number(stage.dataset.height) || DESIGN.height;
        const scale = Math.min(width / DESIGN.width, height / DESIGN.height);
        canvas.style.transform = `translate(${(width - DESIGN.width * scale) / 2}px, ${(height - DESIGN.height * scale) / 2}px) scale(${scale})`;
    }

    function update() {
        const post = WROB.state && WROB.state.postgame;
        const want = !post || !post.show ? 'none'
            : post.phase === 'stats' && post.stats && WROB.now() >= post.at + VICTORY_MIN_MS ? 'panel' : 'victory';
        if (want === view) return;
        view = want;
        root.dataset.view = want;
        root.setAttribute('aria-hidden', want === 'none' ? 'true' : 'false');
        stage.classList.toggle('is-postgame', want !== 'none');
        fit();
        if (want === 'victory') replay(victory);
        if (want === 'panel') replay(panel);
    }

    WROB.catalog().then(() => {
        WROB.onState(state => {
            if (state.postgame) paint(state.postgame);
            fit();
            update();
        });
        setInterval(update, 250);
    });
})();
