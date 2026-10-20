// BP 面板。只读状态、不发命令：收到快照后比对每个槽位，只在内容变化时改 DOM 和播动画。
(function () {
    'use strict';

    const { setLogo, setText, params } = WROB.overlay;
    const SIDES = ['blue', 'red'];
    const FEARLESS_LABEL = { team: '无畏征召', global: '双边无畏' };
    const LOW_TIME_MS = 5000;

    const band = document.getElementById('band');
    const teams = {};
    SIDES.forEach(side => {
        const root = band.querySelector('.team.' + side);
        teams[side] = {
            root,
            bans: root.querySelector('.bans'),
            picks: root.querySelector('.picks'),
            short: root.querySelector('.team-short'),
            name: root.querySelector('.team-name'),
            logo: root.querySelector('.logo'),
            score: root.querySelector('.score'),
            banSlots: [],
            pickSlots: [],
        };
    });
    const center = {
        event: document.getElementById('event'),
        phase: document.getElementById('phase'),
        timer: document.getElementById('timer'),
        meta: document.getElementById('meta'),
        history: document.getElementById('history'),
    };
    const fills = { blue: band.querySelector('.fill.blue'), red: band.querySelector('.fill.red') };
    const backdrop = document.getElementById('backdrop');
    backdrop.hidden = params.get('bg') !== '1';

    let painted = false;        // 第一次渲染不播锁定动画（刷新页面时不该整屏闪一下）
    let layoutKey = '';
    let historyKey = '';
    let orderKey = '';

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    // 槽位数量和分组由 BP 流程决定，流程变了才重建。
    function buildSlots(draft) {
        const key = draft.format + ':' + draft.turns.length;
        if (key === layoutKey) return;
        layoutKey = key;
        SIDES.forEach(side => {
            const team = teams[side];
            const firstPhase = (draft.turns.find(turn => turn.type === 'ban') || {}).phase;
            const firstGroup = draft.turns
                .filter(turn => turn.type === 'ban' && turn.side === side && turn.phase === firstPhase)
                .reduce((count, turn) => count + turn.slots.length, 0);
            team.banSlots = draft.slots[side].ban.map((_, index) => {
                const slot = element('div', 'ban' + (index === firstGroup && index > 0 ? ' starts-group' : ''));
                slot.append(element('div', 'art'));
                return slot;
            });
            team.pickSlots = draft.slots[side].pick.map((_, index) => {
                const slot = element('div', 'pick');
                const label = element('div', 'label');
                const who = element('div', 'who');
                who.append(element('span', 'role'), element('span', 'player'));
                label.append(element('div', 'name'), who);
                slot.append(
                    element('div', 'order', (side === 'blue' ? 'B' : 'R') + (index + 1)),
                    element('div', 'art'), element('div', 'shade'),
                    element('div', 'tag', '选择中'), label);
                return slot;
            });
            team.bans.replaceChildren(...team.banSlots);
            team.picks.replaceChildren(...team.pickSlots);
        });
        orderKey = '';
    }

    function paintSlot(node, slot, active, kind) {
        const champion = slot.champion || '';
        const state = slot.locked ? (champion ? 'locked' : 'skipped') : (champion ? 'hover' : 'empty');
        const changedChampion = node.dataset.champion !== champion;
        if (changedChampion) {
            node.dataset.champion = champion;
            const art = node.querySelector('.art');
            art.replaceChildren();
            if (champion) art.append(kind === 'pick' ? WROB.champions.portrait(champion) : WROB.champions.icon(champion));
            if (kind === 'pick') setText(node.querySelector('.name'), WROB.champions.name(champion));
        }
        if (painted && state === 'locked' && (node.dataset.state !== 'locked' || changedChampion)) {
            node.classList.remove('just-locked');
            void node.offsetWidth;                      // 重新触发动画
            node.classList.add('just-locked');
            setTimeout(() => node.classList.remove('just-locked'), 800);
        }
        node.dataset.state = state;
        node.classList.toggle('is-active', active);
    }

    // 阵容确认后，选用位从“选人顺序”换成“上野中下辅”，用 FLIP 做位移动画。
    function reorderPicks(draft) {
        const byRole = draft.status === 'done';
        const key = draft.status + '|' + SIDES.map(side =>
            draft.slots[side].pick.map(slot => slot.role || '').join(',')).join('|');
        if (key === orderKey) return;
        orderKey = key;
        SIDES.forEach(side => {
            const nodes = teams[side].pickSlots;
            const before = nodes.map(node => node.getBoundingClientRect().left);
            nodes.forEach((node, index) => {
                const role = draft.slots[side].pick[index].role;
                node.style.order = byRole && role ? WROB.roles.indexOf(role) : index;
            });
            if (!painted) return;
            nodes.forEach((node, index) => {
                const shift = (before[index] - node.getBoundingClientRect().left) / WROB.overlay.scale;
                if (Math.abs(shift) > 1) {
                    node.animate([{ transform: 'translateX(' + shift + 'px)' }, { transform: 'none' }],
                        { duration: 650, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' });
                }
            });
        });
    }

    function paintHistory(series, fearless) {
        const rows = fearless.mode === 'off' ? [] : series.history;
        const key = JSON.stringify([rows, series.sides]);
        if (key === historyKey) return;
        historyKey = key;
        center.history.replaceChildren(...rows.map(game => {
            const row = element('div', 'history-row');
            const icons = side => {
                const box = element('div', 'icons');
                ((game.teams[series.sides[side]] || {}).picks || []).forEach(champion => {
                    const mini = element('div', 'mini');
                    if (champion) mini.append(WROB.champions.icon(champion));
                    box.append(mini);
                });
                return box;
            };
            row.append(icons('blue'), element('div', 'game', 'G' + game.n), icons('red'));
            return row;
        }));
    }

    function phaseText(draft) {
        if (draft.status === 'idle') return draft.free && !draft.blindBans ? '即将开始' : 'BP 即将开始';
        if (draft.status === 'lineup') return '阵容调整';
        if (draft.status === 'done') return '阵容确认';
        const turn = draft.turns[draft.turn];
        if (turn.both) return '双方' + (turn.type === 'ban' ? '禁用' : '选用');          // 暗禁：双方同时
        return WROB.sideNames[turn.side] + (turn.type === 'ban' ? '禁用' : '选用');
    }

    function render(state) {
        const { series, draft } = state;
        buildSlots(draft);
        band.dataset.status = draft.status;
        band.dataset.free = draft.free ? '1' : '';         // 自选模式：不读秒
        band.dataset.bans = String(draft.slots.blue.ban.length);      // 没有禁用位（不带暗禁的自选）时整行禁用都不显示
        const turn = draft.status === 'running' ? draft.turns[draft.turn] : null;
        band.dataset.turn = turn && !turn.both ? turn.side : '';
        const active = new Set(draft.active.map(ref => ref.side + ref.type + ref.index));

        SIDES.forEach(side => {
            const team = teams[side];
            const info = series.teams[series.sides[side]];
            setText(team.short, info.short || info.name);
            setText(team.name, info.name && info.name !== info.short ? info.name : '');
            setLogo(team.logo, info.logo);
            setText(team.score, String(series.score[series.sides[side]]));
            draft.slots[side].ban.forEach((slot, index) =>
                paintSlot(team.banSlots[index], slot, active.has(side + 'ban' + index), 'ban'));
            draft.slots[side].pick.forEach((slot, index) => {
                const node = team.pickSlots[index];
                paintSlot(node, slot, active.has(side + 'pick' + index), 'pick');
                setText(node.querySelector('.role'), slot.role ? WROB.roleShort[slot.role] : '');
                setText(node.querySelector('.player'), slot.role ? (info.players[slot.role] || '') : '');
            });
        });
        reorderPicks(draft);

        setText(center.event, [series.title, series.stage].filter(Boolean).join(' · '));
        setText(center.phase, phaseText(draft));
        center.meta.replaceChildren();
        const game = document.createElement('b');
        game.textContent = 'GAME ' + series.gameNumber;
        center.meta.append(game, element('span', '', 'BO' + series.bestOf));
        if (series.patch) center.meta.append(element('span', '', '版本 ' + series.patch));
        if (FEARLESS_LABEL[series.fearless]) center.meta.append(element('span', 'chip', FEARLESS_LABEL[series.fearless]));
        paintHistory(series, draft.fearless);

        if (!backdrop.hidden) {
            setText(document.getElementById('backdrop-title'), series.title);
            setText(document.getElementById('backdrop-meta'),
                [series.stage, 'BO' + series.bestOf, '第 ' + series.gameNumber + ' 局'].filter(Boolean).join(' · '));
            SIDES.forEach((side, index) => {
                const info = series.teams[series.sides[side]];
                const box = backdrop.querySelector('.backdrop-team.' + side);
                setLogo(box.querySelector('.logo'), info.logo);
                setText(box.querySelector('b'), info.short || info.name);
                setText(backdrop.querySelectorAll('.backdrop-score span')[index], String(series.score[series.sides[side]]));
            });
        }
        painted = true;
    }

    // 倒计时每帧按服务器给的结束时刻自己算，不靠服务端逐秒推送。
    function frame() {
        const state = WROB.state;
        if (state) {
            const draft = state.draft;
            const timer = draft.timer;
            // 阵容调整默认不读秒（totalMs 为 0），中间显示 VS
            const counting = timer.totalMs > 0 && (draft.status === 'running' || draft.status === 'lineup');
            const remaining = timer.running ? Math.max(0, timer.endsAt - WROB.now()) : timer.remainingMs;
            const fraction = counting && timer.totalMs ? Math.min(1, remaining / timer.totalMs) : 0;
            const turn = draft.status === 'running' ? draft.turns[draft.turn] : null;
            // 和游戏里一样：30 秒的一手从 29 数到 0
            setText(center.timer, counting ? String(Math.max(0, Math.ceil(remaining / 1000) - 1)) : 'VS');
            center.timer.classList.toggle('is-text', !counting);
            center.timer.classList.toggle('is-low', counting && timer.running && remaining <= LOW_TIME_MS);
            SIDES.forEach(side => {
                const on = counting && (draft.status === 'lineup' || (turn && (turn.both || turn.side === side)));
                fills[side].classList.toggle('is-on', Boolean(on));
                fills[side].style.transform = 'scaleX(' + fraction.toFixed(4) + ')';
            });
        }
        requestAnimationFrame(frame);
    }

    WROB.catalog().then(catalog => {
        WROB.champions.load(catalog);
        WROB.onState(render);
        WROB.connect();
        requestAnimationFrame(frame);
    });
})();
