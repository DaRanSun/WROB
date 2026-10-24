// 导播台 · 局内页：比赛时钟、比分、野怪击杀、特效、结束本局与赛后画面、逐人数据（经济、KDA、存活、装备）。
// 识别服务在跑的时候这些数会自己更新，这里是“覆盖 / 锁定”的兜底入口；没接识别时全部手填。
(function () {
    'use strict';

    const { h, toast, send, armed, setValue, clock, teamOn } = Director;
    const SIDES = ['blue', 'red'];
    const OBJECTIVES = ['herald', 'dragon', 'baron', 'elder'];
    const ELEMENT_NAMES = { infernal: '火', mountain: '山', ocean: '海', cloud: '风' };
    const WHOLE_LOCKS = ['clock', 'objectives'];        // 这两个锁管的是一整块，各有自己的勾选框
    const ITEM_SLOT_NAMES = ['鞋子', '第 5 格', '第 4 格', '第 3 格', '第 2 格', '第 1 格'];     // 由外到内
    const METRIC_NAMES = { damage: '输出', taken: '承伤', healing: '治疗护盾', gold: '经济' };
    const STAT_COLUMNS = [['damage', '输出', 'damageShare'], ['taken', '承伤'], ['healing', '治疗护盾', 'healingShare'],
                          ['share', '参团', null, '%'], ['gold', '经济', 'goldShare']];
    const itemIcon = name => '/assets/items/' + encodeURIComponent(name) + '.png';

    const nodes = {};
    let state = null;
    let rules = null;
    let legendary = new Set();

    const gameTime = () => {
        const c = state.game.clock;
        return c.baseSec + (c.running ? (WROB.now() - c.baseAt) / 1000 : 0);
    };
    // 识别服务在跑的时候，导播手动改的字段要锁住，不然两秒后又被识别改回去。
    const visionAlive = () => Boolean(state && state.game.vision && WROB.now() - state.game.vision.at < 6000);
    const set = (path, value) => send(Object.assign({ type: 'game.set', path, value }, visionAlive() ? { lock: true } : {}));
    const add = (path, delta) => send({ type: 'game.add', path, delta }).then(reply => {
        if (reply.ok && visionAlive()) send({ type: 'game.lock', path, on: true });
    });
    const button = (label, onclick, className, title) =>
        h('button', { type: 'button', class: className || '', title: title || '', onclick }, label);

    // ---- 时钟 ----

    // 开局那一下要点得准，所以不做二次确认；只有时钟已经在走的时候才要求再点一次，防止误触清零。
    let restartArmedAt = 0;
    function restart() {
        if (state.game.clock.running && gameTime() > 3 && Date.now() - restartArmedAt > 3000) {
            restartArmedAt = Date.now();
            return toast('时钟正在走。3 秒内再点一次，才会重新从 00:00 开始');
        }
        restartArmedAt = 0;
        send({ type: 'game.clock', action: 'start', seconds: 0 });
    }

    function buildClock() {
        const input = h('input', { type: 'text', class: 'short', placeholder: '12:30', title: '把游戏时间对到这个读数' });
        nodes.time = h('div', { class: 'clock num' }, '00:00');
        nodes.toggle = button('暂停', () => send({ type: 'game.clock', action: state.game.clock.running ? 'pause' : 'resume' }));
        nodes.status = h('span', { class: 'dim' });
        nodes.vision = h('div', { class: 'vision-status' });
        nodes.clockLock = h('input', {
            type: 'checkbox', onchange: event => send({ type: 'game.lock', path: 'clock', on: event.target.checked }),
        });
        nodes.objectivesLock = h('input', {
            type: 'checkbox', onchange: event => send({ type: 'game.lock', path: 'objectives', on: event.target.checked }),
        });
        nodes.clock.replaceChildren(
            h('h3', null, '比赛时钟 ', nodes.status),
            nodes.vision,
            nodes.time,
            h('div', { class: 'row' },
                button('从 00:00 开始', restart, 'primary', '游戏里 00:00 那一刻点'),
                nodes.toggle,
                button('−1 秒', () => send({ type: 'game.clock', action: 'nudge', seconds: -1 })),
                button('+1 秒', () => send({ type: 'game.clock', action: 'nudge', seconds: 1 }))),
            h('div', { class: 'row' }, input,
                button('对表', () => send({ type: 'game.clock', action: 'set', seconds: input.value }).then(reply => {
                    if (reply.ok) input.value = '';
                }), '', '游戏暂停或时间对不上时，照着游戏里的时间填'),
                armed(h('button', { type: 'button', class: 'danger' }, '清空局内数据'), () => send({ type: 'game.reset' }))),
            h('div', { class: 'row' },
                h('label', { class: 'check', title: '单设备：面板直接盖在这台手机的画面上，底部面板不显示等级、召唤师技能和大招（画面里看得见），原生面板收起时跟着收起。双设备：16:9 画布，上下两条面板夹着观战机的纯净画面，面板不压画面，显示全部内容' },
                    '画面模式 ', nodes.uiMode = h('select', { onchange: event => send({ type: 'ui.set', values: { mode: event.target.value } }) },
                        h('option', { value: 'single' }, '单设备（面板盖在画面上）'),
                        h('option', { value: 'dual' }, '双设备（16:9，面板不压画面）'))),
                h('label', { class: 'check', title: '男爵 / 远古龙 buff 的剩余时间和这段时间的经济收益' }, nodes.uiBuffs = h('input', {
                    type: 'checkbox', onchange: event => send({ type: 'ui.set', values: { buffs: event.target.checked } }),
                }), ' 面板显示 buff 计时'),
                h('label', { class: 'check' }, nodes.uiMatchups = h('input', {
                    type: 'checkbox', onchange: event => send({ type: 'ui.set', values: { matchups: event.target.checked } }),
                }), ' 面板显示底部的对位面板'),
                h('label', { class: 'check', title: '勾上后画面识别不再改时钟，完全由上面的按钮控制' },
                    nodes.clockLock, ' 时钟由我手动控制'),
                h('label', { class: 'check', title: '勾上后画面识别不再记野怪击杀，完全由右边的按钮控制' },
                    nodes.objectivesLock, ' 野怪由我手动控制'),
                nodes.unlock = button('', () => state.game.locks.filter(path => !WHOLE_LOCKS.includes(path))
                    .forEach(path => send({ type: 'game.lock', path, on: false })), '',
                    '这些字段是你手动改过的，识别不会再动它们。点一下全部交还给识别')));
    }

    // ---- 比分 ----

    function buildScore() {
        nodes.counters = {};
        nodes.golds = {};
        const counter = (side, field, label) => {
            const value = h('b', { class: 'num' }, '0');
            nodes.counters[side + '.' + field] = value;
            return h('div', { class: 'counter' }, h('span', { class: 'dim' }, label),
                button('−', () => add(side + '.' + field, -1)), value,
                button('+', () => add(side + '.' + field, 1), 'plus'));
        };
        nodes.diff = h('div', { class: 'diff num' });
        nodes.scoreTeams = {};
        nodes.score.replaceChildren(h('h3', null, '比分'), h('div', { class: 'score-cols' }, SIDES.map(side => {
            const gold = h('input', {
                type: 'text', class: 'short', placeholder: '25.4k', title: '团队经济；五个人的经济都填了就以求和为准',
                onchange: event => set(side + '.gold', event.target.value),
            });
            nodes.golds[side] = gold;
            nodes.scoreTeams[side] = h('b', { class: side });
            return h('div', { class: 'score-col ' + side }, nodes.scoreTeams[side],
                counter(side, 'kills', '人头'), counter(side, 'towers', '推塔'),
                h('div', { class: 'counter' }, h('span', { class: 'dim' }, '经济'), gold));
        })), nodes.diff);
    }

    // ---- 野怪 ----

    function buildObjectives() {
        nodes.objectiveRows = {};
        nodes.element = h('select', { title: '这条小龙的属性（可不选）' },
            h('option', { value: '' }, '属性'),
            rules.dragon.elements.map(key => h('option', { value: key }, ELEMENT_NAMES[key] || key)));
        nodes.holders = h('div', { class: 'holders', hidden: true });
        nodes.events = h('div', { class: 'events' });
        nodes.objectives.replaceChildren(
            h('h3', null, '野怪 ', h('span', { class: 'dim' }, '开了识别时小龙自动记；先锋、男爵、远古龙只认得出“被打掉了”，是哪一方要你点')),
            ...OBJECTIVES.map(what => {
                const status = h('span', { class: 'obj-status num' });
                nodes.objectiveRows[what] = status;
                return h('div', { class: 'obj-row' },
                    h('span', { class: 'obj-name' }, rules[what].name), status,
                    what === 'dragon' ? nodes.element : null,
                    ...SIDES.map(side => button(WROB.sideNames[side], () => send({
                        type: 'game.objective', what, side, element: what === 'dragon' ? nodes.element.value : undefined,
                    }).then(reply => { if (reply.ok && what === 'dragon') nodes.element.value = ''; }), 'take ' + side)));
            }),
            nodes.holders, nodes.events);
    }

    function paintObjectives() {
        const view = state.game.objectives;
        const t = gameTime();
        OBJECTIVES.forEach(what => {
            const item = view[what];
            let text;
            if (item.at == null) text = what !== 'herald' ? '不再刷新' : item.by ? WROB.sideNames[item.by] + '已拿' : '已被击杀';
            else if (item.until != null && t >= item.until) text = clock(item.until) + ' 已消失';
            else if (t < item.at) text = clock(item.at) + ' 刷新（' + clock(item.at - t) + '）';
            else text = '存活';
            const buff = item.buff && t < item.buff.endsAt ? item.buff : null;
            if (buff) text += ' · ' + (buff.side ? WROB.sideNames[buff.side] : '归属待定') + ' buff ' + clock(buff.endsAt - t);
            if (what === 'dragon') text = item.count + '/' + item.max + ' · ' + text + (item.soul ? ' · ' + WROB.sideNames[item.soul] + '龙魂' : '');
            nodes.objectiveRows[what].textContent = text;
        });
    }

    function paintEvents() {
        const view = state.game.objectives;
        const elder = view.elder.buff;
        // 按完整 buff 时长判断：就算点错导致“全员丢失”提前结束，也还能在这里加回来
        const inWindow = elder && elder.holders && gameTime() < elder.fullEndsAt;
        nodes.holders.hidden = !inWindow;
        if (inWindow) {
            nodes.holders.replaceChildren(h('span', { class: 'dim' }, WROB.sideNames[elder.side] + '远古 buff 还在身上的：'),
                ...WROB.roles.map((role, index) => h('button', {
                    type: 'button', class: elder.holders.includes(index) ? 'is-on' : '',
                    onclick: () => send({ type: 'game.buff_holder', what: 'elder', index, on: !elder.holders.includes(index) }),
                }, WROB.roleShort[role])));
        }
        const events = state.game.events.slice().reverse();
        // 识别只认得出“被打掉了”的那些击杀没有阵营：在这里点一下是哪一方（点上面那一行的按钮也一样）
        nodes.events.replaceChildren(...events.map(event => h('div', { class: 'event ' + (event.side || 'pending') },
            h('span', { class: 'num' }, clock(event.t)),
            h('span', null, (event.side ? WROB.sideNames[event.side] + ' ' : '') + rules[event.what].name
                + (event.element ? '（' + (ELEMENT_NAMES[event.element] || event.element) + '）' : '')
                + (event.side ? '' : ' 被击杀，是哪一方？')),
            ...(event.side ? [] : SIDES.map(side => button(WROB.sideNames[side],
                () => send({ type: 'game.objective', what: event.what, side }), 'take ' + side))),
            button('×', () => send({ type: 'game.remove_objective', id: event.id }), 'slot-clear', '记错了，撤掉这条'))));
    }

    // ---- 装备：点一格，弹出装备库，打字筛选或直接点 ----

    let picking = null;             // 正在改哪一格：{ side, index, slot }

    function buildPicker(items) {
        const search = h('input', { type: 'text', placeholder: '打字筛选，回车选第一件', oninput: () => filterPicker() });
        const grid = h('div', { class: 'item-grid' }, items.map(name => h('button', {
            type: 'button', class: 'item-opt', title: name, dataset: { name }, onclick: () => pickItem(name),
        }, h('img', { src: itemIcon(name), alt: '', loading: 'lazy' }), h('span', null, name))));
        const title = h('b');
        nodes.picker = h('div', { class: 'item-picker', hidden: true },
            h('div', { class: 'row' }, title, search, button('清空这一格', () => pickItem(null)), button('关闭', closePicker)), grid);
        Object.assign(nodes, { pickerSearch: search, pickerGrid: grid, pickerTitle: title });
        document.body.append(nodes.picker);
        search.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            const first = grid.querySelector('.item-opt:not([hidden])');
            if (first) pickItem(first.dataset.name);
        });
        document.addEventListener('keydown', event => { if (event.key === 'Escape') closePicker(); });
        document.addEventListener('mousedown', event => {
            if (picking && !nodes.picker.contains(event.target) && !event.target.closest('.item-slot')) closePicker();
        });
    }

    function filterPicker() {
        const words = nodes.pickerSearch.value.trim().split(/\s+/).filter(Boolean);
        nodes.pickerGrid.querySelectorAll('.item-opt').forEach(option => {
            option.hidden = !words.every(word => option.dataset.name.includes(word));
        });
    }

    function openPicker(side, index, slot, anchor) {
        picking = { side, index, slot };
        const player = state.game.players[side][index];
        nodes.pickerTitle.textContent = WROB.sideNames[side] + WROB.roleNames[player.role] + ' · ' + ITEM_SLOT_NAMES[slot];
        nodes.pickerSearch.value = '';
        filterPicker();
        nodes.picker.hidden = false;
        // 贴着被点的那一格往下放；放不下就往上、往左挪
        const box = anchor.getBoundingClientRect();
        const width = nodes.picker.offsetWidth, height = nodes.picker.offsetHeight;
        const left = Math.max(8, Math.min(box.left, window.innerWidth - width - 8));
        const top = box.bottom + 6 + height > window.innerHeight ? Math.max(8, box.top - height - 6) : box.bottom + 6;
        nodes.picker.style.left = left + 'px';
        nodes.picker.style.top = top + 'px';
        nodes.pickerSearch.focus();
    }

    function closePicker() {
        picking = null;
        if (nodes.picker) nodes.picker.hidden = true;
    }

    function pickItem(name) {
        if (!picking) return;
        const { side, index, slot } = picking;
        const items = state.game.players[side][index].items.slice();
        items[slot] = name;
        closePicker();
        set(side + '.' + index + '.items', items);
    }

    // ---- 底部换成经济曲线 / 团战 UI：按钮和快捷键（E、T）都走这里。两者互相顶替，再切一次回到对位面板 ----

    function toggleChart(byKey) {
        const on = !state.ui.chart;
        send({ type: 'ui.set', values: { chart: on } }).then(reply => {
            if (reply.ok && byKey) toast(on ? '经济曲线：已插入（E）' : '经济曲线：已收起（E）');
        });
    }

    function toggleFight(byKey) {
        if (state.ui.mode !== 'dual') return toast('团战 UI 只用于双设备模式，先在上面的“画面模式”里选双设备', 'error');
        const on = !state.ui.teamfight;
        send({ type: 'ui.set', values: { teamfight: on } }).then(reply => {
            if (reply.ok && byKey) toast(on ? '团战 UI：已切入（T）' : '团战 UI：已切出，回到常规面板（T）');
        });
    }

    // 快捷键在哪个页签都能用；光标在文字输入框里（BP 槽位、对表、装备筛选……）时不算，留给打字。
    // 用按键的物理位置（event.code），中文输入法、大写锁定都不影响。
    // 装备价值：底部两侧，团战 UI 开着时不管这个开关都显示
    function toggleLoadout(byKey) {
        if (state.ui.mode !== 'dual') return toast('装备价值只用于双设备模式，先在上面的“画面模式”里选双设备', 'error');
        const on = !state.ui.loadout;
        send({ type: 'ui.set', values: { loadout: on } }).then(reply => {
            if (reply.ok && byKey) toast(on ? '装备价值：已显示（L）' : '装备价值：已收起（L）' + (state.ui.teamfight ? '。团战 UI 开着时仍然显示' : ''));
        });
    }

    const SHORTCUTS = { KeyE: toggleChart, KeyT: toggleFight, KeyL: toggleLoadout };
    const NOT_TYPING = ['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'];
    function typing(target) {
        if (!(target instanceof Element)) return false;
        if (target.closest('textarea, [contenteditable]:not([contenteditable="false"])')) return true;
        return target.tagName === 'INPUT' && !NOT_TYPING.includes(target.type);
    }
    function onShortcut(event) {
        const run = SHORTCUTS[event.code];
        if (!run || !state || event.repeat || event.isComposing || event.metaKey || event.ctrlKey || event.altKey) return;
        if (typing(event.target)) return;
        event.preventDefault();         // 下拉框有焦点时，别让它拿这个字母去跳选项
        run(true);
    }

    const keyed = (label, key) => [h('span', { class: 'label' }, label), h('kbd', null, key)];
    const FIGHT_TIP = '团战 UI 顶替底部的对位面板：只留召唤师技能、大招、关键装备、等级、血条。再点一次切回。只用于双设备。快捷键 T';
    const LOADOUT_TIP = '底部两侧显示双方身上装备的价值总和和各占的百分比（价值表 config/loadout.yaml）。团战 UI 开着时自动显示。只用于双设备。快捷键 L';

    // ---- 特效：经济曲线、团战 UI 的切换，自动特效的总开关，手动放（预览美术、识别漏了时补上） ----

    function buildFx() {
        const select = (options, title) => h('select', { title }, options.map(([value, label]) => h('option', { value }, label)));
        const side = select(SIDES.map(key => [key, WROB.sideNames[key]]), '哪一方');
        const lane = select(WROB.roles.map((role, index) => [index, WROB.roleNames[role]]), '哪一路');
        const element = select(rules.dragon.elements.map(key => [key, ELEMENT_NAMES[key] + '龙']), '元素龙属性');
        const playOne = data => send(Object.assign({ type: 'fx.play' }, data));
        const who = () => ({ side: side.value, index: Number(lane.value) });
        nodes.chartToggle = button(keyed('插入经济曲线', 'E'), () => toggleChart(false), 'primary',
            '经济曲线顶替底部的对位面板；再点一次收起，面板回来。快捷键 E');
        nodes.fightToggle = button(keyed('切入团战 UI', 'T'), () => toggleFight(false), 'primary', FIGHT_TIP);
        nodes.loadoutToggle = button(keyed('显示装备价值', 'L'), () => toggleLoadout(false), 'primary', LOADOUT_TIP);
        nodes.loadoutStatus = h('div', { class: 'dim shortcut-hint' });
        nodes.uiEffects = h('input', { type: 'checkbox', onchange: event => send({ type: 'ui.set', values: { effects: event.target.checked } }) });
        nodes.fx.replaceChildren(
            h('h3', null, '特效 ', h('span', { class: 'dim' }, '连杀、出装、野怪播报会跟着识别自动放；这里可以手动放')),
            h('div', { class: 'row' }, nodes.chartToggle, nodes.fightToggle, nodes.loadoutToggle,
                h('label', { class: 'check', title: '关掉后只放你在这里手动点的' }, nodes.uiEffects, ' 自动特效')),
            h('div', { class: 'dim shortcut-hint' }, '快捷键（任何页签，光标不在输入框里时）：E 插入 / 收起经济曲线 · T 切入 / 切出团战 UI · L 显示 / 收起装备价值（团战 UI 开着时自动显示）'),
            nodes.loadoutStatus,
            h('div', { class: 'row' }, side, lane,
                ...[2, 3, 4, 5].map(count => button(['', '', '双杀', '三杀', '四杀', '五杀'][count], () => playOne(Object.assign({ kind: 'multikill', count }, who())))),
                button('出装', () => {
                    const player = state.game.players[side.value][Number(lane.value)];
                    const owned = (player.items || []).filter(item => legendary.has(item));
                    const items = owned.length ? owned.slice(-1) : [Director.catalog.legendary[0]];
                    playOne(Object.assign({ kind: 'item', items }, who()));
                }, '', '这名选手身上最后一件传说装；他还没有的话拿一件做预览')),
            h('div', { class: 'row' }, element,
                button('小龙', () => playOne({ kind: 'objective', what: 'dragon', side: side.value, element: element.value })),
                button('先锋', () => playOne({ kind: 'objective', what: 'herald', side: side.value })),
                button('男爵', () => playOne({ kind: 'objective', what: 'baron', side: side.value })),
                button('远古', () => playOne({ kind: 'objective', what: 'elder', side: side.value })),
                button('龙魂', () => playOne({ kind: 'soul', side: side.value, element: element.value })),
                button('龙魂属性', () => playOne({ kind: 'soulElement', element: element.value }), '', '本局龙魂是什么属性（第一条小龙的属性）')));
    }

    // ---- 结束本局 · 赛后 ----

    function buildFinish() {
        nodes.finishButtons = ['a', 'b'].map(team => armed(h('button', { type: 'button', class: 'win', dataset: { team } }, ''),
            () => send({ type: 'game.finish', winner: team, length: nodes.finishLength.value }).then(reply => {
                if (reply.ok) nodes.finishLength.value = '';
                if (reply.ok) toast('已记录比分。OBS 放胜利画面；数据机打开结算页“统计 · 数据对比”后弹出赛后面板。点错了用 BP 页的“撤销”');
            })));
        nodes.postStatus = h('div', { class: 'post-status' });
        nodes.postShow = button('', () => send({ type: 'postgame.set', show: !state.postgame.show }));
        nodes.postMetric = h('select', { title: '赛后面板右边那一栏比哪一项', onchange: event => send({ type: 'postgame.set', metric: event.target.value }) },
            Object.entries(METRIC_NAMES).map(([key, name]) => h('option', { value: key }, '右侧比较：' + name)));
        nodes.postStats = h('div', { class: 'post-stats' });
        nodes.finishLength = h('input', { type: 'text', class: 'short', placeholder: '时长',
            title: '本局时长，比如 18:20。不填就用比赛时钟（识别在跑时取它最后一次看到游戏时钟的时刻）' });
        nodes.finish.replaceChildren(
            h('h3', null, '结束本局 ', h('span', { class: 'dim' }, '基地爆了点胜方：记比分、OBS 放胜利画面、识别停止读局内数据；读到结算页的数据后弹出赛后面板')),
            h('div', { class: 'row' }, ...nodes.finishButtons, nodes.finishLength, nodes.postShow, nodes.postMetric),
            nodes.postStatus,
            nodes.postStats);
    }

    function paintFinish() {
        const post = state.postgame;
        nodes.finishButtons.forEach(node => {
            const team = state.series.teams[node.dataset.team];
            const side = state.series.sides.blue === node.dataset.team ? 'blue' : 'red';
            node.dataset.label = '结束本局 · ' + (team.short || team.name) + ' 胜';
            if (!node.classList.contains('is-armed')) node.textContent = node.dataset.label;
            node.classList.toggle('blue', side === 'blue');
            node.classList.toggle('red', side === 'red');
        });
        nodes.postShow.hidden = nodes.postMetric.hidden = !post;
        if (!post) {
            nodes.postStatus.textContent = '';
            nodes.postStats.replaceChildren();
            return;
        }
        nodes.postShow.textContent = post.show ? '收起胜利画面 / 赛后面板' : '重新放出来';
        if (document.activeElement !== nodes.postMetric) nodes.postMetric.value = post.metric;
        const winner = post.teams[post.winnerSide];
        const vision = state.game.vision;
        const onPage = vision && vision.screen === 'stats' && WROB.now() - vision.at < 6000;
        nodes.postStatus.textContent = '第 ' + post.n + ' 局 ' + (winner.short || winner.name) + ' 胜'
            + (post.length != null ? '（' + clock(post.length) + '）' : '') + ' · '
            + (!post.show ? '已收起'
                : post.stats ? '已读到结算数据，OBS 显示赛后面板'
                : onPage ? '识别看到了结算页的数据对比，正在读（数字跳完才算）……'
                : 'OBS 显示胜利画面，等结算数据：数据机在结算界面点“统计”，停在底部“数据”、右上角“综合”这一页');
        nodes.postStatus.dataset.state = post.stats ? 'ok' : 'wait';
        const key = JSON.stringify([post.n, post.statsAt]);
        if (nodes.postStats.dataset.key === key) return;
        nodes.postStats.dataset.key = key;
        if (!post.stats) return nodes.postStats.replaceChildren();
        // 读到的数据，逐人列出来给导播核对
        nodes.postStats.replaceChildren(h('table', null,
            h('thead', null, h('tr', null, h('th', null, ''), ...STAT_COLUMNS.map(([, name]) => h('th', null, name)))),
            h('tbody', null, SIDES.flatMap(side => post.stats[side].map((row, index) => {
                const player = post.players[side][index];
                return h('tr', { class: side },
                    h('td', null, player.name || WROB.champions.name(player.champion) || WROB.roleNames[player.role]),
                    ...STAT_COLUMNS.map(([key, , share, unit]) => h('td', { class: 'num' },
                        row[key] + (unit || '') + (share ? '（' + row[share] + '%）' : ''))));
            })))));
    }

    // ---- 逐人数据 ----

    function buildPlayers() {
        nodes.playerRows = [];
        const cell = (side, index) => {
            const path = side + '.' + index + '.';
            const number = (field, title) => h('input', {
                type: 'text', inputmode: 'numeric', class: 'tiny', title,
                onchange: event => set(path + field, event.target.value || 0),
            });
            const parts = {
                icon: h('span', { class: 'slot-icon' }),
                name: h('input', { type: 'text', class: 'name', placeholder: '选手', onchange: event => set(path + 'name', event.target.value) }),
                gold: h('input', { type: 'text', class: 'short', placeholder: '经济', onchange: event => set(path + 'gold', event.target.value) }),
                kills: number('kills', '击杀'), deaths: number('deaths', '死亡'), assists: number('assists', '助攻'),
                alive: h('button', { type: 'button', class: 'alive', onclick: () => set(path + 'alive', !state.game.players[side][index].alive) }),
            };
            parts.slots = ITEM_SLOT_NAMES.map((label, slot) => h('button', {
                type: 'button', class: 'item-slot', title: label, onclick: event => openPicker(side, index, slot, event.currentTarget),
            }));
            parts.items = h('div', { class: 'items' }, parts.slots);           // 由外到内，鞋子在最外；红方由 CSS 镜像
            const order = [parts.items, parts.icon, parts.name, parts.gold, h('span', { class: 'kda' }, parts.kills, '/', parts.deaths, '/', parts.assists), parts.alive];
            return { parts, node: h('div', { class: 'player ' + side }, side === 'blue' ? order : order.reverse()) };
        };
        nodes.players.replaceChildren(
            h('h3', null, '逐人数据 ', h('span', { class: 'dim' }, '按上野中下辅；经济填齐后自动算对位差和团队经济；装备点格子改，最外面一格是鞋子')),
            ...WROB.roles.map((role, index) => {
                const blue = cell('blue', index);
                const red = cell('red', index);
                const diff = h('span', { class: 'lane-diff num' });
                nodes.playerRows.push({ blue: blue.parts, red: red.parts, diff });
                return h('div', { class: 'player-row' }, blue.node,
                    h('div', { class: 'lane' }, h('span', { class: 'dim' }, WROB.roleNames[role]), diff), red.node);
            }));
    }

    function signed(value) {
        return value == null ? '–' : (value > 0 ? '+' : '') + value.toLocaleString('en-US');
    }

    function paint() {
        const game = state.game;
        nodes.status.textContent = { idle: '未开始', live: '进行中', ended: '已结束' }[game.status];
        nodes.toggle.textContent = game.clock.running ? '暂停' : '继续';
        nodes.clockLock.checked = game.locks.includes('clock');
        nodes.objectivesLock.checked = game.locks.includes('objectives');
        const manual = game.locks.filter(path => !WHOLE_LOCKS.includes(path));
        nodes.unlock.hidden = !manual.length;
        nodes.unlock.textContent = '解除 ' + manual.length + ' 个手动锁定';
        SIDES.forEach(side => ['kills', 'towers'].forEach(field =>
            nodes.counters[side + '.' + field].classList.toggle('is-locked', manual.includes(side + '.' + field))));
        if (document.activeElement !== nodes.uiMode) nodes.uiMode.value = state.ui.mode;
        const preview = document.querySelector('#tab-game .preview-frame');
        if (preview) preview.classList.toggle('phone', state.ui.mode === 'single');     // 双设备的画布是 16:9
        nodes.uiBuffs.checked = state.ui.buffs;
        nodes.uiEffects.checked = state.ui.effects;
        nodes.chartToggle.querySelector('.label').textContent = state.ui.chart ? '收起经济曲线' : '插入经济曲线';
        nodes.chartToggle.classList.toggle('is-armed', Boolean(state.ui.chart));
        const dual = state.ui.mode === 'dual';
        nodes.fightToggle.querySelector('.label').textContent = state.ui.teamfight ? '切出团战 UI' : '切入团战 UI';
        nodes.fightToggle.classList.toggle('is-armed', Boolean(state.ui.teamfight));
        nodes.fightToggle.disabled = !dual;
        nodes.fightToggle.title = dual ? FIGHT_TIP : '团战 UI 只用于双设备模式';
        nodes.loadoutToggle.querySelector('.label').textContent = state.ui.loadout ? '收起装备价值' : '显示装备价值';
        nodes.loadoutToggle.classList.toggle('is-armed', Boolean(state.ui.loadout));
        nodes.loadoutToggle.disabled = !dual;
        nodes.loadoutToggle.title = dual ? LOADOUT_TIP : '装备价值只用于双设备模式';
        const worth = SIDES.map(side => {
            const view = game.loadout[side];
            return WROB.sideNames[side] + ' ' + view.value.toLocaleString('en-US') + (view.share == null ? '' : '（' + view.share + '%）');
        });
        nodes.loadoutStatus.textContent = '装备价值：' + worth.join(' · ')
            + (!dual ? '' : state.ui.loadout || state.ui.teamfight ? ' · 面板上正在显示' + (state.ui.loadout ? '' : '（团战 UI 自动）') : ' · 面板上没显示');
        nodes.uiMatchups.checked = state.ui.matchups;
        SIDES.forEach(side => {
            const team = teamOn(state, side);
            nodes.scoreTeams[side].textContent = team.short || team.name;
            nodes.counters[side + '.kills'].textContent = game.teams[side].kills;
            nodes.counters[side + '.towers'].textContent = game.teams[side].towers;
            setValue(nodes.golds[side], game.teams[side].gold);
        });
        nodes.diff.textContent = '经济差 ' + signed(game.diff);
        nodes.diff.dataset.lead = game.lead || '';
        nodes.playerRows.forEach((row, index) => {
            SIDES.forEach(side => {
                const player = game.players[side][index];
                const parts = row[side];
                if (parts.icon.dataset.champion !== (player.champion || '')) {
                    parts.icon.dataset.champion = player.champion || '';
                    parts.icon.replaceChildren();
                    if (player.champion) parts.icon.append(WROB.champions.icon(player.champion));
                }
                setValue(parts.name, player.name);
                setValue(parts.gold, player.gold);
                ['kills', 'deaths', 'assists'].forEach(field => setValue(parts[field], player[field]));
                ['gold', 'kills', 'deaths', 'assists'].forEach(field =>
                    parts[field].classList.toggle('is-locked', manual.includes(side + '.' + index + '.' + field)));
                parts.alive.textContent = player.alive ? '存活' : '阵亡' + (player.respawn ? ' ' + player.respawn : '');
                parts.alive.classList.toggle('is-dead', !player.alive);
                parts.alive.classList.toggle('is-locked', ['alive', 'respawn'].some(field => manual.includes(side + '.' + index + '.' + field)));
                parts.items.classList.toggle('is-locked', manual.includes(side + '.' + index + '.items'));
                player.items.forEach((name, slot) => {
                    const node = parts.slots[slot];
                    if (node.dataset.item === (name || '')) return;
                    node.dataset.item = name || '';
                    node.title = ITEM_SLOT_NAMES[slot] + (name ? '：' + name : '（空）');
                    node.replaceChildren(...(name ? [h('img', { src: itemIcon(name), alt: name })] : []));
                });
            });
            row.diff.textContent = signed(game.lanes[index].diff);
            row.diff.dataset.lead = game.lanes[index].lead || '';
        });
        paintEvents();
        paintFinish();
    }

    // 识别服务每 2 秒报一次到；超过 6 秒没消息就当它没在跑。
    function paintVision() {
        const vision = state.game.vision;
        const alive = vision && WROB.now() - vision.at < 6000;
        const ended = state.game.status === 'ended';
        const text = !vision ? '画面识别：没有接入，全部手动'
            : !alive ? '画面识别：断开了（最后一次报到在 ' + Math.round((WROB.now() - vision.at) / 1000) + ' 秒前）'
            : ended ? '画面识别：本局已结束，局内数据不再读' + (state.postgame && !state.postgame.stats
                ? (vision.screen === 'stats' ? ' · 正在读结算页' : ' · 等数据机打开结算页“统计 · 数据对比”') : '')
            : '画面识别：运行中' + (vision.panel ? '' : ' · 数据机的底部面板收起了，逐人数据已冻结')
                + (vision.clock ? '' : ' · 时钟读不出');
        nodes.vision.textContent = text;
        nodes.vision.dataset.state = !vision ? 'off' : !alive ? 'lost' : ended || (vision.panel && vision.clock) ? 'ok' : 'warn';
    }

    function tick() {
        if (state && Director.tab === 'game') {
            nodes.time.textContent = clock(gameTime());
            paintObjectives();
            paintVision();
            if (state.postgame && !state.postgame.stats) paintFinish();     // “识别正在读结算页”跟着心跳变
        }
        requestAnimationFrame(tick);
    }

    Director.register({
        init(catalog) {
            rules = catalog.rules;
            ['clock', 'score', 'objectives', 'fx', 'finish', 'players'].forEach(name => { nodes[name] = document.getElementById('game-' + name); });
            legendary = new Set(catalog.legendary || []);
            buildClock();
            buildScore();
            buildObjectives();
            buildPlayers();
            buildPicker(catalog.items || []);
            buildFx();
            buildFinish();
            document.addEventListener('keydown', onShortcut);
            requestAnimationFrame(tick);
        },
        render(next) {
            state = next;
            paint();
        },
    });
})();
