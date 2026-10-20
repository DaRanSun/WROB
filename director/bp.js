// 导播台 · BP 页。操作方式照 WRPedia 录入后台：
//
//   · 队名、选手 ID 直接在页面上方填。
//   · 每个禁用位 / 选用位是一个输入框：打字检索（中文、英文、拼音、首字母、昵称），↑↓ 选，回车填入；
//     也可以点开下拉点选，或者点中间的英雄格子。填完自动跳到 BP 流程里的下一个槽位。
//   · 任何槽位随时可以回头改：点进去重新选，或者删空文字后离开（= 清空）。
//   · 已被禁、已被选、被无畏征召锁住的英雄排在下拉最后并注明原因，按住 Shift 可以强行填。
(function () {
    'use strict';

    const { h, toast, send, armed, setValue, teamOn } = Director;
    const SIDES = ['blue', 'red'];
    const KIND_NAMES = { ban: '禁用', pick: '选用' };
    const STATUS_TEXT = { idle: 'BP 还没开始', lineup: '阵容调整：核对双方分路', done: '阵容已确认' };
    // 自选模式：没有禁用、不读秒（OB 排位、巅峰对决）。按顺序填，填完再对着游戏里的实际分路调整
    const FREE_TEXT = { idle: '自选：按选人顺序填双方英雄', lineup: '自选：对着游戏里的实际分路调整，再确认阵容',
                        done: '阵容已确认（开局后发现分路不对，照样可以在这里改）' };
    const EMPTY_BAN_TERMS = ['空', '空ban', 'kong', 'kongban', 'none', 'empty', '无'];

    const nodes = { forms: {} };
    const cells = new Map();        // slug → 中间格子里的英雄
    const rows = {};                // "blue-ban-0" → { root, input, icon, roles, ref }
    let state = null;
    let catalog = null;
    let layoutKey = '';
    let dragged = null;
    let P = null;                   // 当前获得焦点的英雄输入框：{ input, ref, original, open, items, active, query, fresh }

    const refKey = ref => ref.side + '-' + ref.type + '-' + ref.index;
    const sameRef = (a, b) => Boolean(a && b) && refKey(a) === refKey(b);
    const slotOf = ref => state.draft.slots[ref.side][ref.type][ref.index];
    const slotText = slot => slot.champion ? WROB.champions.name(slot.champion) : slot.locked ? '空 Ban' : '';
    const slotLabel = ref => WROB.sideNames[ref.side] + ' ' + KIND_NAMES[ref.type] + ' ' + (ref.index + 1);
    const isHover = slot => Boolean(slot.champion) && !slot.locked;

    // 点格子、按“锁定 / 空 Ban”时作用在哪个槽位：有输入框在编辑就是它，否则是流程轮到的那个。
    function targetRef() {
        return P ? P.ref : state.draft.active[0] || null;
    }

    function blockedReason(slug, ref) {
        const draft = state.draft;
        const slot = slotOf(ref);
        if (draft.blindBans && ref.type === 'ban') {        // 暗禁：两边可以禁同一个，只有本方禁过的不行
            const own = draft.slots[ref.side].ban.some((other, index) => index !== ref.index && other.locked && other.champion === slug);
            if (own) return '本方已禁用';
        } else if (draft.used[slug] && !(slot.locked && slot.champion === slug)) {
            return draft.used[slug];
        }
        if (ref.type === 'pick' || draft.fearless.bans) return draft.fearless.sides[ref.side][slug] || null;
        return null;
    }

    // ---- 填槽位 ----

    // 像 WRPedia 后台一样随时可以直接填：BP 还没开始时，第一次填会顺手把它开始。
    function ensureStarted() {
        if (state.draft.status !== 'idle') return Promise.resolve(true);
        return send({ type: 'draft.start', format: nodes.format.value || undefined }).then(reply => reply.ok);
    }

    // 命令成功之后：收起下拉，把焦点交给流程里的下一个槽位。状态广播比回执先到，这时 state 已经是新的。
    function settled(ref, stay) {
        closeMenu();
        // 先把这一格的文字换成填好的英雄名。否则焦点一走，“空着离开 = 清空”会把刚填的又清掉。
        const row = rows[refKey(ref)];
        row.input.value = slotText(slotOf(ref));
        if (P && P.input === row.input) P.original = row.input.value;
        if (stay) {
            if (P && P.input === row.input) row.input.select();
            return render(state);
        }
        const next = state.draft.active[0];
        if (next) rows[refKey(next)].input.focus();
        else if (P) P.input.blur();
        render(state);
    }

    function fill(ref, slug, force) {
        ensureStarted().then(started => {
            if (!started) return;
            ref = ref || state.draft.active[0];
            if (!ref) return toast('没有轮到的槽位：先点一下要填的那一格');
            const reason = blockedReason(slug, ref);
            if (reason && !force) return toast(WROB.champions.name(slug) + '：' + reason + '（按住 Shift 可以强行填）', 'error');
            const preview = nodes.twoStep.checked && !slotOf(ref).locked;
            const command = preview
                ? { type: 'draft.hover', champion: slug, slot: ref }
                : { type: 'draft.lock', champion: slug, slot: ref, force: Boolean(force) };
            send(command).then(reply => reply.ok && settled(ref, preview));
        });
    }

    function skip(ref) {
        ensureStarted().then(started => {
            ref = ref || state.draft.active[0];
            if (started && ref) send({ type: 'draft.skip', slot: ref }).then(reply => reply.ok && settled(ref, false));
        });
    }

    function lockPreview(ref, force) {
        send({ type: 'draft.lock', slot: ref, force: Boolean(force) }).then(reply => reply.ok && settled(ref, false));
    }

    // ---- 英雄下拉（挂在 body 上，跟着输入框定位） ----

    const menu = h('div', { class: 'champ-menu', role: 'listbox', hidden: true });
    document.body.append(menu);

    function menuItems(ref, query) {
        const term = WROB.champions.normalize(query);
        const items = WROB.champions.search(query, WROB.champions.popular)
            .map(champion => ({ champion, reason: blockedReason(champion.slug, ref) }));
        const sorted = items.filter(item => !item.reason).concat(items.filter(item => item.reason));
        if (ref.type === 'ban' && (!term || EMPTY_BAN_TERMS.some(word => word.startsWith(term)))) {
            sorted.unshift({ champion: null, reason: '' });
        }
        return sorted;
    }

    function openMenu(query) {
        if (!P) return;
        const term = WROB.champions.normalize(query);
        P.open = true;
        P.query = query;
        P.items = menuItems(P.ref, query);
        const first = P.items.findIndex(item => item.champion && !item.reason);
        P.active = term && EMPTY_BAN_TERMS.includes(term) && P.items[0] && !P.items[0].champion ? 0 : Math.max(0, first);
        menu.scrollTop = 0;
        drawMenu();
    }

    function drawMenu() {
        if (!P || !P.open) return;
        menu.replaceChildren(...(P.items.length ? P.items.map((item, index) => {
            const champion = item.champion;
            return h('div', {
                class: 'champ-opt' + (index === P.active ? ' active' : '') + (item.reason ? ' blocked' : ''),
                role: 'option', dataset: { index },
            },
                h('span', { class: 'slot-icon sm' }, champion ? WROB.champions.icon(champion.slug) : '—'),
                h('b', null, champion ? champion.name : '空 Ban'),
                h('small', null, champion ? champion.en + (champion.title ? ' · ' + champion.title : '') : '这一手不禁英雄'),
                item.reason ? h('em', null, item.reason) : null);
        }) : [h('div', { class: 'champ-none' }, '没有匹配的英雄')]));
        placeMenu();
        const active = menu.querySelector('.champ-opt.active');
        if (active && active.offsetTop < menu.scrollTop) menu.scrollTop = active.offsetTop;
        else if (active && active.offsetTop + active.offsetHeight > menu.scrollTop + menu.clientHeight) {
            menu.scrollTop = active.offsetTop + active.offsetHeight - menu.clientHeight;
        }
    }

    function placeMenu() {
        if (!P || !P.open) return;
        const rect = P.input.getBoundingClientRect();
        const height = Math.min(360, window.innerHeight * 0.6);
        menu.hidden = false;
        menu.style.left = Math.max(4, Math.min(rect.left, window.innerWidth - 324)) + 'px';
        menu.style.minWidth = Math.max(rect.width, 300) + 'px';
        menu.style.maxHeight = height + 'px';
        const below = window.innerHeight - rect.bottom;
        menu.style.top = (below < height && rect.top > below
            ? Math.max(4, rect.top - Math.min(height, menu.scrollHeight) - 2) : rect.bottom + 2) + 'px';
    }

    function closeMenu() {
        if (P) P.open = false;
        menu.hidden = true;
        menu.replaceChildren();
    }

    function chooseItem(item, force) {
        if (!item || !P) return;
        if (!item.champion) return skip(P.ref);
        fill(P.ref, item.champion.slug, force);
    }

    menu.addEventListener('mousedown', event => event.preventDefault());      // 点下拉时焦点留在输入框
    menu.addEventListener('click', event => {
        const option = event.target.closest('.champ-opt');
        if (option && P) chooseItem(P.items[Number(option.dataset.index)], event.shiftKey);
    });
    window.addEventListener('resize', placeMenu);
    window.addEventListener('scroll', placeMenu, true);

    // ---- 槽位输入框的事件 ----

    function onFocusIn(event) {
        const input = event.target;
        if (!input.classList.contains('champ-input')) return;
        P = { input, ref: input.closest('.slot').ref, original: input.value, open: false, items: [], active: 0, query: '', fresh: true };
        input.select();
        render(state);
    }

    function onFocusOut(event) {
        if (!P || P.input !== event.target) return;
        const ref = P.ref;
        const slot = slotOf(ref);
        const cleared = !event.target.value.trim() && (slot.champion || slot.locked);
        event.target.value = slotText(slot);            // 没选就离开：恢复原样
        closeMenu();
        P = null;
        if (cleared) send({ type: 'draft.clear', slot: ref });      // 删空再离开 = 清空这一格
        render(state);
    }

    function onInput(event) {
        if (P && P.input === event.target) openMenu(event.target.value);
    }

    function onInputKey(event) {
        if (!P || P.input !== event.target) return;
        const typed = event.target.value !== P.original;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!P.open) return openMenu(typed ? event.target.value : '');
            const count = P.items.length;
            if (count) { P.active = (P.active + (event.key === 'ArrowDown' ? 1 : count - 1)) % count; drawMenu(); }
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (P.open) return chooseItem(P.items[P.active], event.shiftKey);
            if (isHover(slotOf(P.ref))) return lockPreview(P.ref, event.shiftKey);      // 预选着的：回车锁定
            openMenu('');
        } else if (event.key === 'Escape') {
            event.preventDefault();
            event.target.value = P.original;
            closeMenu();
            event.target.select();
        } else if (event.key === 'Tab' && !event.shiftKey && P.open && typed && event.target.value.trim() && P.items.length) {
            event.preventDefault();
            chooseItem(P.items[P.active], false);
        }
    }

    // ---- 队伍表单 ----

    function buildTeamForm(side) {
        const parts = { players: {} };
        const teamKey = () => state.series.sides[side];
        const patch = fields => state && send({ type: 'series.update', patch: { teams: { [teamKey()]: fields } } });
        const text = (name, placeholder, className) => {
            parts[name] = h('input', {
                type: 'text', class: className, placeholder, title: placeholder, spellcheck: 'false', autocomplete: 'off',
                onchange: event => patch({ [name]: event.target.value }),
            });
            return parts[name];
        };
        parts.preset = h('select', {
            class: 'preset', title: '从预设队伍里套用队名、队标和首发',
            onchange: event => {
                const team = catalog.teams.find(item => item.id === event.target.value);
                if (team) patch({ id: team.id, name: team.name, short: team.short, logo: team.logo, players: team.players });
                event.target.value = '';
            },
        }, h('option', { value: '' }, '套用预设…'), catalog.teams.map(team => h('option', { value: team.id }, team.short)));
        parts.score = h('b', { class: 'num' });
        const score = delta => () => send({
            type: 'series.update', patch: { score: { [teamKey()]: Math.max(0, state.series.score[teamKey()] + delta) } },
        });
        parts.roster = h('datalist', { id: 'bp-roster-' + side });
        parts.logo = h('span', { class: 'logo-preview', hidden: true }, h('img', { alt: '' }));
        nodes.forms[side].replaceChildren(
            h('div', { class: 'form-row' },
                h('span', { class: 'side-tag' }, WROB.sideNames[side]),
                parts.logo,
                text('short', '简称', 'short'),
                text('name', '队伍全称', 'grow'),
                parts.preset,
                h('span', { class: 'form-score' },
                    h('button', { type: 'button', onclick: score(-1), title: '比分 −1' }, '−'), parts.score,
                    h('button', { type: 'button', onclick: score(1), title: '比分 +1' }, '+'))),
            h('div', { class: 'form-row players' }, WROB.roles.map(role => {
                parts.players[role] = h('input', {
                    type: 'text', list: parts.roster.id, placeholder: WROB.roleNames[role] + ' ID', spellcheck: 'false', autocomplete: 'off',
                    onchange: event => patch({ players: { [role]: event.target.value } }),
                });
                return h('label', { class: 'player-field', title: WROB.roleNames[role] },
                    h('span', null, WROB.roleShort[role]), parts.players[role]);
            })),
            parts.roster);
        nodes.forms[side].parts = parts;
    }

    function paintTeamForm(side) {
        const team = teamOn(state, side);
        const parts = nodes.forms[side].parts;
        setValue(parts.short, team.short);
        setValue(parts.name, team.name);
        WROB.roles.forEach(role => setValue(parts.players[role], team.players[role]));
        parts.score.textContent = state.series.score[state.series.sides[side]];
        const img = parts.logo.querySelector('img');
        parts.logo.hidden = !team.logo;
        if (team.logo && img.getAttribute('src') !== team.logo) img.src = team.logo;
        if (parts.roster.dataset.team !== team.id) {        // 选手 ID 的输入提示：这支预设队伍的全部队员
            parts.roster.dataset.team = team.id;
            const preset = catalog.teams.find(item => item.id === team.id);
            parts.roster.replaceChildren(...(preset ? preset.roster : []).map(name => h('option', { value: name })));
        }
    }

    // ---- 两侧的槽位 ----

    function buildSides() {
        const draft = state.draft;
        const key = draft.format + ':' + draft.turns.length + ':' + Boolean(draft.free);
        if (key === layoutKey) return;
        layoutKey = key;
        SIDES.forEach(side => {
            const column = nodes[side];
            column.replaceChildren();
            ['ban', 'pick'].forEach(kind => {
                if (!draft.slots[side][kind].length) return;            // 自选模式没有禁用位
                const note = kind === 'ban' && draft.blindBans ? '（双方同时暗禁，可以重复）'
                    : draft.free && !draft.slots[side].ban.length ? '（自选，不禁用）' : draft.free ? '（按顺序选）' : '';
                column.append(h('div', { class: 'side-label' }, KIND_NAMES[kind] + note));
                draft.slots[side][kind].forEach((_, index) => {
                    const ref = { side, type: kind, index };
                    const input = h('input', {
                        type: 'text', class: 'champ-input',
                        placeholder: KIND_NAMES[kind] + ' ' + (index + 1),
                        autocomplete: 'off', spellcheck: 'false', 'aria-label': slotLabel(ref),
                    });
                    const icon = h('span', { class: 'slot-icon' });
                    const roles = kind === 'pick' ? h('span', { class: 'slot-roles', hidden: true }, WROB.roles.map(role => h('button', {
                        type: 'button', tabindex: '-1', dataset: { role }, title: WROB.roleNames[role],
                        onclick: () => send({ type: 'draft.role', side, index, role }),
                    }, WROB.roleShort[role]))) : null;
                    // 只有左边的序号和图标能拖（拖到同类槽位上 = 交换），输入框里拖动仍然是选文字。
                    const handle = h('span', {
                        class: 'slot-handle', draggable: 'true', title: '拖到同一方的同类槽位上：交换',
                        ondragstart: event => { dragged = ref; event.dataTransfer.effectAllowed = 'move'; },
                        ondragend: () => { dragged = null; },
                    }, h('span', { class: 'slot-no num' }, (side === 'blue' ? 'B' : 'R') + (index + 1)), icon);
                    const root = h('div', {
                        class: 'slot ' + kind,
                        ondragover: event => {
                            if (dragged && dragged.side === side && dragged.type === kind && dragged.index !== index) event.preventDefault();
                        },
                        ondrop: event => {
                            event.preventDefault();
                            if (dragged) send({ type: 'draft.swap', a: dragged, b: ref });
                            dragged = null;
                        },
                    }, handle, input, roles, h('button', {
                        type: 'button', class: 'slot-clear', tabindex: '-1', title: '清空这个槽位',
                        onclick: () => send({ type: 'draft.clear', slot: ref }),
                    }, '×'));
                    root.ref = ref;
                    rows[refKey(ref)] = { root, input, icon, roles, ref };
                    column.append(root);
                });
            });
        });
    }

    function paintSides() {
        const draft = state.draft;
        const active = new Set(draft.active.map(refKey));
        const aim = targetRef();
        const showRoles = draft.status === 'lineup' || draft.status === 'done';
        SIDES.forEach(side => {
            const team = teamOn(state, side);
            ['ban', 'pick'].forEach(kind => draft.slots[side][kind].forEach((slot, index) => {
                const row = rows[refKey({ side, type: kind, index })];
                const champion = slot.champion || '';
                if (row.root.dataset.champion !== champion) {
                    row.root.dataset.champion = champion;
                    row.icon.replaceChildren();
                    if (champion) row.icon.append(WROB.champions.icon(champion));
                }
                const text = slotText(slot);
                const editing = P && P.input === row.input;
                // 正在这一格里打字时不动它的文字，只记下“原样”是什么
                if (!editing || row.input.value === P.original) row.input.value = text;
                if (editing) P.original = text;
                row.root.dataset.state = slot.locked ? (champion ? 'locked' : 'skipped') : champion ? 'hover' : 'empty';
                row.root.classList.toggle('is-turn', active.has(refKey(row.ref)));
                row.root.classList.toggle('is-target', sameRef(aim, row.ref));
                if (row.roles) {
                    row.roles.hidden = !showRoles;
                    row.roles.querySelectorAll('button').forEach(button =>
                        button.classList.toggle('is-on', button.dataset.role === slot.role));
                    row.input.title = slot.role ? WROB.roleNames[slot.role] + ' · ' + (team.players[slot.role] || '选手未填') : '';
                }
            }));
        });
    }

    // ---- 中间的点选格子 ----

    function buildGrid() {
        nodes.grid.replaceChildren(...WROB.champions.list.map(champion => {
            const cell = h('button', { type: 'button', class: 'champ', tabindex: '-1', dataset: { slug: champion.slug } },
                h('span', { class: 'champ-icon' }, WROB.champions.icon(champion.slug)),
                h('span', { class: 'champ-name' }, champion.name),
                h('span', { class: 'champ-flag' }));
            cells.set(champion.slug, cell);
            return cell;
        }));
        nodes.grid.addEventListener('mousedown', event => event.preventDefault());     // 点格子时焦点留在槽位输入框
        nodes.grid.addEventListener('click', event => {
            const cell = event.target.closest('.champ');
            if (cell) fill(targetRef(), cell.dataset.slug, event.shiftKey);
        });
    }

    function paintGrid() {
        const ref = targetRef();
        const shown = ref && isHover(slotOf(ref)) ? slotOf(ref).champion : null;
        cells.forEach((cell, slug) => {
            const reason = ref ? blockedReason(slug, ref) : state.draft.used[slug] || null;
            cell.classList.toggle('is-blocked', Boolean(reason));
            cell.classList.toggle('is-hover', slug === shown);
            cell.title = reason || '';
            cell.querySelector('.champ-flag').textContent = !reason ? ''
                : reason.includes('无畏') ? '无畏' : reason.includes('禁') ? '已禁' : '已选';
        });
    }

    // ---- 流程栏 ----

    function buildFlow() {
        const button = (label, onclick, className, title) =>
            h('button', { type: 'button', class: className || '', title: title || '', onclick }, label);
        const formats = Object.entries(catalog.formats);
        nodes.format = h('select', { title: 'BP 流程（config/draft.yaml）' },
            formats.map(([key, name]) => h('option', { value: key }, name)));
        nodes.format.value = catalog.defaultFormat;
        // 还没开始时换流程：马上换成那种流程的空白槽位（比如换成“自选”，禁用位就没了）
        nodes.format.addEventListener('change', () => {
            if (state && state.draft.status === 'idle') send({ type: 'draft.reset', format: nodes.format.value });
        });
        nodes.start = button('开始 BP', () => send({ type: 'draft.start', format: nodes.format.value }), 'primary',
            '开始读秒。不点也行：直接填第一个槽位会自动开始');
        nodes.lock = button('锁定 ⏎', event => { const ref = targetRef(); if (ref) lockPreview(ref, event.shiftKey); }, 'primary',
            '把预选的英雄锁定');
        nodes.skip = button('空 Ban', () => skip(targetRef()), '', '这一手不禁英雄');
        nodes.confirm = button('确认阵容', () => send({ type: 'draft.confirm' }), 'primary', '面板按上野中下辅重排并显示选手');
        nodes.enter = button('进入局内 →', () =>
            send({ type: 'game.new' }).then(reply => reply.ok && Director.showTab('game')), 'primary', '把阵容带进局内面板');
        // 画面识别自动填 BP：数据机停在选人界面时，禁用、亮出、选定都由识别填；导播锁过的格子识别不动
        nodes.autoBp = h('input', { type: 'checkbox', onchange: event => send({ type: 'ui.set', values: { autoBp: event.target.checked } }) });
        nodes.autoLabel = h('label', { class: 'check', title: '数据机停在游戏的选人界面时，识别自动填禁用、亮出（预选）和选定。导播自己锁过的格子识别不会改；识别填错了直接改就行' },
            nodes.autoBp, ' 识别自动填 BP');
        nodes.visionBp = h('span', { class: 'dim vision-bp' });
        nodes.actions.replaceChildren(nodes.format, nodes.start, nodes.lock, nodes.skip, nodes.confirm, nodes.enter, nodes.autoLabel, nodes.visionBp);
        nodes.actions.addEventListener('mousedown', event => {      // 点这些按钮时别让槽位输入框失焦
            if (event.target.closest('button')) event.preventDefault();
        });
    }

    function paintFlow() {
        const draft = state.draft;
        const turn = draft.status === 'running' ? draft.turns[draft.turn] : null;
        const ref = targetRef();
        const slot = ref ? slotOf(ref) : null;
        nodes.phase.textContent = draft.free && FREE_TEXT[draft.status]
            ? (draft.status === 'idle' && draft.blindBans ? '暗禁：先填双方的禁用（哪边先都行），再按选人顺序填英雄' : FREE_TEXT[draft.status])
            : turn && turn.both ? turn.phaseName + ' · 双方同时' + KIND_NAMES[turn.type] + '（暗禁，可以重复）'
            : turn ? turn.phaseName + ' · ' + WROB.sideNames[turn.side] + KIND_NAMES[turn.type]
            : STATUS_TEXT[draft.status];
        nodes.phase.dataset.side = turn && !turn.both ? turn.side : '';
        const manual = P && !draft.active.some(item => sameRef(item, P.ref));
        nodes.target.textContent = !ref ? (draft.status !== 'idle' ? '' : draft.free ? '直接填第一格就开始' : '点“开始 BP”开始读秒，或者直接填第一个槽位')
            : (manual ? '正在改：' : '轮到：') + slotLabel(ref) + (isHover(slot) ? '（预选 ' + WROB.champions.name(slot.champion) + '，回车锁定）' : '');
        nodes.target.classList.toggle('is-manual', Boolean(manual));
        nodes.hint.textContent = ref ? '点英雄填入 ' + slotLabel(ref)
            : draft.status === 'idle' ? '点英雄填入第一个槽位（会自动开始 BP）' : '要改哪一格，先点那一格的输入框';

        nodes.format.hidden = draft.status !== 'idle' || nodes.format.options.length < 2;
        if (draft.status === 'idle' && document.activeElement !== nodes.format) nodes.format.value = draft.format;
        nodes.start.hidden = draft.status !== 'idle';
        nodes.lock.hidden = !(slot && isHover(slot));
        nodes.skip.hidden = !(ref && ref.type === 'ban' && draft.status !== 'idle' && !slot.locked);
        nodes.confirm.hidden = draft.status !== 'lineup';
        nodes.enter.hidden = draft.status !== 'done';

        const counting = draft.timer.totalMs > 0 && (draft.status === 'running' || draft.status === 'lineup');
        nodes.timerToggle.textContent = draft.timer.running ? '暂停' : '继续';
        nodes.timerToggle.disabled = nodes.timerReset.disabled = !counting;
        nodes.undo.disabled = !state.canUndo;
        nodes.autoBp.checked = state.ui.autoBp !== false;
        const vision = state.game.vision;
        const reading = vision && vision.screen === 'bp' && WROB.now() - vision.at < 6000;
        nodes.visionBp.textContent = reading ? (state.ui.autoBp !== false ? '识别正在读选人界面' : '识别看到了选人界面（自动填已关）') : '';
    }

    function paintResult() {
        const series = state.series;
        const key = JSON.stringify([series.teams.a.short, series.teams.b.short, series.gameNumber, series.sides]);
        if (nodes.result.dataset.key === key) return;
        nodes.result.dataset.key = key;
        const length = h('input', { type: 'text', class: 'length', placeholder: '时长 如 1832', title: '本局时长，可不填（不填就用比赛时钟）' });
        nodes.result.replaceChildren(
            h('span', { class: 'dim' }, '第 ' + series.gameNumber + ' 局：'),
            ...SIDES.map(side => {
                const team = series.sides[side];
                return armed(
                    h('button', { type: 'button', class: 'win ' + side }, '结束本局 · ' + (series.teams[team].short || series.teams[team].name) + ' 胜'),
                    () => send({ type: 'game.finish', winner: team, length: length.value }).then(reply => {
                        if (reply.ok) toast('已记录，OBS 放胜利画面。下一局默认败方在蓝方，不对的话点两队中间的 ⇄ 换边');
                    }));
            }),
            length);
    }

    function render(next) {
        state = next;
        if (!state) return;
        buildSides();
        SIDES.forEach(paintTeamForm);
        paintSides();
        paintGrid();
        paintFlow();
        paintResult();
        if (P && P.open) {                  // 下拉开着时状态变了（比如别的槽位刚填上）：重算一遍可选项
            const active = P.active;
            P.items = menuItems(P.ref, P.query);
            P.active = Math.min(active, Math.max(0, P.items.length - 1));
            drawMenu();
        }
    }

    function tickTimer() {
        if (state) {
            const timer = state.draft.timer;
            const counting = timer.totalMs > 0 && (state.draft.status === 'running' || state.draft.status === 'lineup');
            const remaining = timer.running ? Math.max(0, timer.endsAt - WROB.now()) : timer.remainingMs;
            nodes.timer.textContent = counting ? String(Math.max(0, Math.ceil(remaining / 1000) - 1)) : '–';     // 和游戏一样 29 → 0
            nodes.timer.classList.toggle('is-low', counting && remaining <= 5000);
        }
        requestAnimationFrame(tickTimer);
    }

    // Ctrl/⌘ + Z：撤销上一步 BP 操作。在别的输入框里、或者槽位里正打着字时，留给浏览器自己的文字撤销。
    function onGlobalKey(event) {
        if (Director.tab !== 'bp' || !state) return;
        if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return;
        const field = event.target.closest('input, select, textarea');
        if (field && !(P && P.input === field && field.value === P.original)) return;
        event.preventDefault();
        send({ type: 'undo' });
    }

    Director.register({
        tab: 'bp',
        init(data) {
            catalog = data;
            SIDES.forEach(side => {
                nodes[side] = document.getElementById('bp-' + side);
                nodes.forms[side] = document.getElementById('bp-team-' + side);
            });
            Object.assign(nodes, {
                phase: document.getElementById('bp-phase'),
                target: document.getElementById('bp-target'),
                hint: document.getElementById('bp-palette-hint'),
                timer: document.getElementById('bp-timer'),
                timerToggle: document.getElementById('bp-timer-toggle'),
                timerReset: document.getElementById('bp-timer-reset'),
                actions: document.getElementById('bp-actions'),
                twoStep: document.getElementById('bp-two-step'),
                grid: document.getElementById('bp-grid'),
                result: document.getElementById('bp-result'),
                undo: document.getElementById('bp-undo'),
            });
            SIDES.forEach(buildTeamForm);
            buildGrid();
            buildFlow();

            const tab = document.getElementById('tab-bp');
            tab.addEventListener('focusin', onFocusIn);
            tab.addEventListener('focusout', onFocusOut);
            tab.addEventListener('input', onInput);
            tab.addEventListener('keydown', onInputKey);
            tab.addEventListener('mouseup', event => {
                if (!P || P.input !== event.target) return;
                // 刚点进输入框时，浏览器会在 mouseup 把“全选”取消掉，拦一下；每次点输入框都展开下拉。
                if (P.fresh) { P.fresh = false; event.preventDefault(); }
                if (!P.open) openMenu(event.target.value === P.original ? '' : event.target.value);
            });
            document.addEventListener('keydown', onGlobalKey);

            nodes.timerToggle.addEventListener('click', () =>
                send({ type: 'draft.timer', action: state.draft.timer.running ? 'pause' : 'resume' }));
            nodes.timerReset.addEventListener('click', () => send({ type: 'draft.timer', action: 'reset' }));
            nodes.undo.addEventListener('click', () => send({ type: 'undo' }));
            armed(document.getElementById('bp-reset'), () => send({ type: 'draft.reset' }));
            document.getElementById('bp-swap-sides').addEventListener('click', () => send({ type: 'series.swap_sides' }));
            requestAnimationFrame(tickTimer);
        },
        render,
        // 切到 BP 页时，把光标放进轮到的那个槽位
        shown() {
            const next = state && state.draft.active[0];
            if (next && rows[refKey(next)]) rows[refKey(next)].input.focus();
        },
    });
})();
