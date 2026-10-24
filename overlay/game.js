// 局内面板：顶部的战队面板 + 底部的对位面板。
// 数值来自状态快照；时钟、倒计时、buff 剩余时间按服务器给的游戏时间锚点每帧自己算。
(function () {
    'use strict';

    const { setLogo, setText, clock, params } = WROB.overlay;
    const SIDES = ['blue', 'red'];
    const OTHER = { blue: 'red', red: 'blue' };
    const BUFFS = [['baron', '男爵'], ['elder', '远古']];
    const RESULT_SECONDS = 8;       // buff 结束后 Power play 结果再停留几秒
    const TWEEN_MS = 300;
    const ITEM_SLOTS = 6;
    // 画布尺寸：单设备盖在手机画面上，和手机一样大；双设备是 16:9，画面夹在上下两条面板之间
    const CANVAS = { single: [2800, 1264], dual: [1920, 1080] };
    const VISION_ALIVE_MS = 6000;   // 识别服务每 2 秒报一次到；超过这么久没消息就当它没在跑

    document.getElementById('fake-game').hidden = params.get('bg') !== '1';

    const bar = {};
    SIDES.forEach(side => {
        const root = document.querySelector('.tb-team.' + side);
        bar[side] = {
            logo: root.querySelector('.logo'),
            name: root.querySelector('.tb-name'),
            pips: root.querySelector('.tb-pips'),
            towers: root.querySelector('.tb-towers'),
            gold: root.querySelector('.tb-gold-value b'),
            lead: root.querySelector('.tb-lead'),
            kills: document.querySelector('.tb-kills.' + side),
            dragons: document.querySelector('.tb-dragons.' + side),
        };
    });
    const clockNode = document.getElementById('clock');
    const markGame = document.getElementById('mark-game');
    const markFormat = document.getElementById('mark-format');
    const timers = document.getElementById('timers');
    const matchups = document.getElementById('matchups');
    const stage = document.getElementById('stage');

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    // ---- 数字补间：数值变化时用 300ms 滚过去，而不是直接跳 ----

    const tweens = new Map();
    function tween(node, value, format) {
        if (value == null) {
            tweens.delete(node);
            node.shown = node.target = null;
            return setText(node, '');
        }
        if (node.target === value) return;
        node.target = value;
        if (node.shown == null) {
            node.shown = value;
            return setText(node, format(value));
        }
        tweens.set(node, { from: node.shown, to: value, start: performance.now(), format });
    }
    function runTweens(now) {
        tweens.forEach((item, node) => {
            const k = Math.min(1, (now - item.start) / TWEEN_MS);
            node.shown = item.from + (item.to - item.from) * (1 - (1 - k) * (1 - k));
            setText(node, item.format(Math.round(node.shown)));
            if (k >= 1) { node.shown = item.to; tweens.delete(node); }
        });
    }

    const exact = value => value.toLocaleString('en-US');
    const compact = value => Math.abs(value) >= 1000 ? (value / 1000).toFixed(1) + 'K' : String(value);

    // ---- 男爵 / 远古龙 buff 的小牌子。野怪的刷新倒计时原生界面都有，这里不再显示 ----

    const buffChips = {};
    SIDES.forEach(side => BUFFS.forEach(([what, label]) => {
        const chip = element('div', 'chip buff');
        chip.append(element('span', 'what', label), element('b'), element('span', 'gain'));
        chip.hidden = true;
        buffChips[side + what] = chip;
        document.getElementById('buffs-' + side).append(chip);
    }));

    function paintTimers(game, t) {
        const view = game.objectives;
        SIDES.forEach(side => BUFFS.forEach(([what, label]) => {
            const chip = buffChips[side + what];
            const buff = view[what].buff;
            const mine = buff && buff.side === side;
            const running = mine && t < buff.endsAt;
            const showResult = mine && !running && t < buff.endsAt + RESULT_SECONDS && buff.goldStart[side] != null;
            chip.hidden = !(running || showResult);
            if (chip.hidden) return;
            chip.classList.toggle('is-over', !running);
            const holders = buff.holders ? ' ×' + buff.holders.length : '';
            setText(chip.querySelector('.what'), running ? label + ' BUFF' + holders : label + ' BUFF 结束');
            setText(chip.querySelector('b'), running ? clock(Math.ceil(buff.endsAt - t)) : '');
            // Power play：buff 期间这一方比对面多赚了多少
            const end = running || !buff.goldEnd ? { blue: game.teams.blue.gold, red: game.teams.red.gold } : buff.goldEnd;
            const start = buff.goldStart;
            const known = [start.blue, start.red, end.blue, end.red].every(value => value != null);
            const gain = known ? (end[side] - start[side]) - (end[OTHER[side]] - start[OTHER[side]]) : null;
            setText(chip.querySelector('.gain'), gain == null ? '' : (gain >= 0 ? '+' : '−') + compact(Math.abs(gain)));
            chip.querySelector('.gain').hidden = gain == null;
        }));
    }

    // ---- 底部：对位面板 ----

    // 血条和资源条（对位面板、团战 UI 都有）：血条里叠着掉血时停一下的浅色一截、实际血量、护盾
    function makeBars(className) {
        const bars = { root: element('div', className), hp: element('span', 'bar-hp'), res: element('span', 'bar-res') };
        ['lag', 'fill', 'shield'].forEach(part => { bars[part] = bars.hp.appendChild(element('i', 'bar-' + part)); });
        bars.mana = bars.res.appendChild(element('i', 'bar-fill'));
        bars.root.append(bars.hp, bars.res);
        return bars;
    }

    const ultSlots = [];                // 对位面板和团战 UI 里所有的大招图标，每帧往识别给的进度靠
    const laneRows = WROB.roles.map(() => {
        const row = element('div', 'lane-row');
        const parts = {};
        SIDES.forEach(side => {
            const box = element('div', 'lane-side ' + side);
            const items = element('div', 'lane-items');
            const slots = Array.from({ length: ITEM_SLOTS }, () => items.appendChild(element('span', 'lane-item')));
            const rune = element('span', 'lane-rune');
            const ult = element('span', 'lane-ult');
            ult.append(new Image(), element('i', 'lane-ult-shade'));
            ult.hidden = true;
            ultSlots.push(ult);
            const spellBox = element('div', 'lane-spells');
            const spells = [0, 1].map(() => spellBox.appendChild(element('span', 'lane-spell')));
            const portrait = element('div', 'lane-portrait');
            const face = element('span', 'lane-face');
            const respawn = element('span', 'lane-respawn');
            const level = element('span', 'lane-level num');
            portrait.append(face, respawn, level);
            const text = element('div', 'lane-text');
            const name = element('span', 'lane-name');
            const gold = element('span', 'lane-gold num');
            const kda = element('span', 'lane-kda num');
            const bars = makeBars('lane-bars');
            text.append(name, gold, kda, bars.root);
            box.append(items, rune, ult, spellBox, portrait, text);     // 从外到内；红方由 CSS 镜像
            parts[side] = { box, slots, rune, ult, spells, portrait, face, respawn, level, name, gold, kda, bars };
        });
        parts.mid = element('div', 'lane-mid num');
        row.append(parts.blue.box, parts.mid, parts.red.box);
        matchups.append(row);
        return parts;
    });

    // 一个图标格子（装备、召唤师技能、符文）：图标 + 冷却时压在上面的秒数
    function paintIcon(slot, folder, name) {
        if (slot.dataset.item === (name || '')) return;
        slot.dataset.item = name || '';
        slot.replaceChildren();
        if (!name) return;
        const img = new Image();
        img.alt = '';
        img.src = '/assets/' + folder + '/' + encodeURIComponent(name) + '.png';
        slot.append(img, element('b', 'cd num'));
    }

    // 大招：用英雄的大招图标做底。五级之前不显示；转好了是亮的；用掉之后整个压暗，再像表针一样一圈圈亮回来。
    // 识别给的是“转好了多少”（0–1），每变化 5% 报一次，这里每帧往目标值靠一点，看起来是连续的。
    const ULT_LEVEL = 5;
    function paintUlt(slot, player) {
        const learned = player.level != null ? player.level >= ULT_LEVEL : player.ult > 0;
        slot.hidden = !(learned && player.champion && player.ult != null);
        if (slot.dataset.champion !== (player.champion || '')) {
            slot.dataset.champion = player.champion || '';
            slot.classList.remove('no-icon');
            const img = slot.querySelector('img');
            img.onerror = () => slot.classList.add('no-icon');           // 没有这个英雄的大招图标（比如诺拉）：显示一个 R
            img.src = player.champion ? '/assets/ults/' + player.champion + '.png' : '';
        }
        slot.target = player.ult == null ? 1 : player.ult;
        if (slot.shown == null || slot.hidden) slot.shown = slot.target;
        slot.classList.toggle('is-ready', slot.target >= 1);
    }
    function runUlts() {
        ultSlots.forEach(slot => {
            if (slot.hidden || slot.shown == null) return;
            // 用掉的那一下直接变暗；往回转的时候慢慢追
            slot.shown = slot.target < slot.shown ? slot.target : Math.min(slot.target, slot.shown + 0.004);
            slot.style.setProperty('--done', slot.shown.toFixed(3));
        });
    }

    // 冷却：识别服务给的是 {"spell.0": [开始, 结束], "item.3": [开始, null]}（游戏秒）。
    // 没到结束时间的压暗并倒数；结束时间是 null 的（惩戒、不知道冷却多久的）只压暗。
    function paintCooling(slot, span, t) {
        const cooling = Boolean(span) && slot.firstChild != null && (span[1] == null || t < span[1]);
        slot.classList.toggle('is-cooling', cooling);
        const label = slot.querySelector('.cd');
        if (label) setText(label, cooling && span[1] != null ? String(Math.ceil(span[1] - t)) : '');
    }
    function paintCooldowns(game, t) {
        laneRows.forEach((parts, index) => SIDES.forEach(side => {
            const running = game.players[side][index].cooldowns || {};
            const cell = parts[side];
            cell.spells.forEach((slot, number) => paintCooling(slot, running['spell.' + number], t));
            cell.slots.forEach((slot, number) => paintCooling(slot, running['item.' + number], t));
            if (!fightOn) return;
            const col = fightCols[side][index];         // 团战 UI 的装备格记着它在装备栏里是第几格
            col.spells.forEach((slot, number) => paintCooling(slot, running['spell.' + number], t));
            col.items.forEach(slot => paintCooling(slot, slot.hidden ? null : running['item.' + slot.dataset.slot], t));
        }));
    }

    // 血条和资源条（只在双设备显示）：识别给的是占整条的比例。阵亡的按空的画；识别没在跑、还没读到的不画。
    // 掉血时浅色的那一截（bar-lag）停一下再追上去，和游戏里一样；回血时它在血条下面，看不见。
    const percent = value => (Math.max(0, Math.min(1, value)) * 100).toFixed(1) + '%';
    function paintBars(view, player) {
        const bars = player.bars;
        view.root.classList.toggle('is-unknown', !bars);
        if (!bars) return;
        const hp = player.alive ? bars.hp : 0;
        view.fill.style.width = view.lag.style.width = percent(hp);
        view.shield.style.left = percent(hp);
        view.shield.style.width = percent(player.alive ? Math.min(bars.shield, 1 - hp) : 0);
        view.res.dataset.kind = bars.resource || '';
        view.mana.style.width = percent(player.alive ? bars.mana : 0);
    }

    function paintFace(node, champion) {
        if (node.dataset.champion === (champion || '')) return;
        node.dataset.champion = champion || '';
        node.replaceChildren();
        if (champion) node.append(WROB.champions.icon(champion));
    }

    function paintLanes(game) {
        laneRows.forEach((parts, index) => {
            SIDES.forEach(side => {
                const player = game.players[side][index];
                const cell = parts[side];
                setText(cell.name, player.name || WROB.roleNames[player.role]);
                paintFace(cell.face, player.champion);
                setText(cell.kda, player.kills + '/' + player.deaths + '/' + player.assists);
                tween(cell.gold, player.gold, exact);
                (player.items || []).forEach((name, slot) => { if (cell.slots[slot]) paintIcon(cell.slots[slot], 'items', name); });
                (player.spells || []).forEach((name, slot) => { if (cell.spells[slot]) paintIcon(cell.spells[slot], 'spells', name); });
                paintIcon(cell.rune, 'runes', player.rune);
                setText(cell.level, player.level ? String(player.level) : '');
                paintUlt(cell.ult, player);
                paintBars(cell.bars, player);
                cell.box.classList.toggle('is-dead', !player.alive);
                setText(cell.respawn, !player.alive && player.respawn ? String(player.respawn) : '');
            });
            const lane = game.lanes[index];
            tween(parts.mid, lane.diff == null ? null : Math.abs(lane.diff), compact);
            parts.mid.dataset.lead = lane.lead || '';
        });
    }

    // ---- 底部：团战 UI（只用于双设备）。导播切入时顶替对位面板，照端游观赛的团战界面：十个人一人一栏，
    //      从上到下是 召唤师技能 · 大招 · 召唤师技能、关键装备、头像、等级和血条资源条。名字、经济、KDA 打团时不看，不放 ----

    const fight = document.getElementById('teamfight');
    const fightLogo = fight.querySelector('.tf-center .logo');
    let fightOn = false;
    let keyItems = new Set();           // 关键装备：会读冷却条的那些，去掉卢登（config/cooldowns.yaml）
    const fightCols = {};
    SIDES.forEach(side => {
        const half = fight.querySelector('.tf-half.' + side);
        fightCols[side] = WROB.roles.map(() => {
            const box = element('div', 'tf-col');
            const spells = [0, 1].map(() => element('span', 'tf-spell'));
            const ult = element('span', 'lane-ult tf-ult');
            ult.append(new Image(), element('i', 'lane-ult-shade'));
            ult.hidden = true;
            ultSlots.push(ult);
            const skills = element('div', 'tf-skills');
            skills.append(spells[0], ult, spells[1]);
            const itemBox = element('div', 'tf-items');
            const items = Array.from({ length: ITEM_SLOTS }, () => itemBox.appendChild(element('span', 'tf-item')));
            const portrait = element('div', 'tf-portrait');
            const face = element('span', 'tf-face');
            const respawn = element('span', 'tf-respawn');
            portrait.append(face, respawn);
            const level = element('b', 'tf-level num');
            const bars = makeBars('tf-bars');
            const status = element('div', 'tf-status');
            status.append(level, bars.root);
            box.append(skills, itemBox, portrait, status);
            half.append(box);
            return { box, spells, ult, itemBox, items, portrait, face, respawn, level, bars };
        });
    });

    function paintFight(game) {
        SIDES.forEach(side => fightCols[side].forEach((col, index) => {
            const player = game.players[side][index];
            paintFace(col.face, player.champion);
            (player.spells || []).forEach((name, slot) => { if (col.spells[slot]) paintIcon(col.spells[slot], 'spells', name); });
            // 关键装备按装备栏的顺序排（鞋子在最外）；格子记着自己是装备栏的第几格，冷却按那一格查
            const owned = (player.items || []).map((name, slot) => [slot, name]).filter(([, name]) => keyItems.has(name));
            col.itemBox.dataset.count = owned.length;
            col.items.forEach((node, k) => {
                const [slot, name] = owned[k] || [];
                node.hidden = name == null;
                node.dataset.slot = slot == null ? '' : String(slot);
                paintIcon(node, 'items', name);
            });
            paintUlt(col.ult, player);
            setText(col.level, player.level ? String(player.level) : '');
            paintBars(col.bars, player);
            col.box.classList.toggle('is-dead', !player.alive);
            setText(col.respawn, !player.alive && player.respawn ? String(player.respawn) : '');
        }));
    }

    // ---- 底部两侧：装备价值（只用于双设备）。导播按 L 显示 / 收起；团战 UI 开着时自动显示。数由状态中心算好（game.loadout） ----

    const loadoutBox = document.getElementById('loadout');
    const loadoutSides = {};
    SIDES.forEach(side => {
        const root = loadoutBox.querySelector('.lv-side.' + side);
        loadoutSides[side] = { root, value: root.querySelector('.lv-value'), share: root.querySelector('.lv-share'), bar: root.querySelector('.lv-bar i') };
    });

    function paintLoadout(game, ui) {
        loadoutBox.classList.toggle('is-shown', ui.mode === 'dual' && Boolean(ui.loadout || ui.teamfight));
        SIDES.forEach(side => {
            const view = game.loadout[side];
            const parts = loadoutSides[side];
            tween(parts.value, view.value, compact);
            setText(parts.share, view.share == null ? '–' : view.share + '%');
            parts.bar.style.width = (view.share == null ? 50 : view.share) + '%';
            parts.root.classList.toggle('is-ahead', view.share != null && view.share > 50);
        });
    }

    // ---- 顶部：战队面板 ----

    function paintPips(holder, won, needed) {
        const key = won + '/' + needed;
        if (holder.dataset.key === key) return;
        holder.dataset.key = key;
        // BO1 不需要点；BO3 两格、BO5 三格，赢一场亮一格
        holder.replaceChildren(...Array.from({ length: needed > 1 ? needed : 0 }, (_, index) => {
            const pip = document.createElement('i');
            if (index < won) pip.className = 'is-won';
            return pip;
        }));
    }

    function paintDragons(holder, taken, soul) {
        const key = taken.join(',') + '|' + soul;
        if (holder.dataset.key === key) return;
        holder.dataset.key = key;
        holder.classList.toggle('has-soul', soul);
        holder.replaceChildren(...taken.map(kind => WROB.icons.dragon(kind)));
    }

    function render(state) {
        const { series, game, ui } = state;
        SIDES.forEach(side => {
            const key = series.sides[side];
            const team = series.teams[key];
            const view = game.teams[side];
            setLogo(bar[side].logo, team.logo);
            setText(bar[side].name, team.short || team.name);
            paintPips(bar[side].pips, series.score[key], series.winsNeeded);
            setText(bar[side].towers, String(view.towers));
            setText(bar[side].kills, String(view.kills));
            tween(bar[side].gold, view.gold, compact);
            // 经济差只标在领先的那一方下面
            tween(bar[side].lead, game.lead === side && game.diff != null ? Math.abs(game.diff) : null,
                value => '+' + compact(value));
            paintDragons(bar[side].dragons, view.dragons, view.soul);
        });
        setText(markGame, 'G' + series.gameNumber);
        setText(markFormat, 'BO' + series.bestOf);
        clockNode.classList.toggle('is-paused', !game.clock.running);
        if (stage.dataset.mode !== ui.mode) {
            stage.dataset.mode = ui.mode;
            WROB.overlay.resize(...(CANVAS[ui.mode] || CANVAS.single));
        }
        timers.classList.toggle('is-off', !ui.buffs);
        matchups.hidden = !ui.matchups;
        fightOn = ui.mode === 'dual' && Boolean(ui.teamfight);
        fight.classList.toggle('is-shown', fightOn);
        matchups.classList.toggle('is-replaced', Boolean(ui.chart) || fightOn);       // 经济曲线、团战 UI 插入时让开
        setLogo(fightLogo, series.brandLogo || '');                                     // 团战 UI 中间：赛事标识，没有就写 VS
        paintLanes(game);
        paintFight(game);
        paintLoadout(game, ui);
    }

    function frame(now) {
        const state = WROB.state;
        if (state) {
            const c = state.game.clock;
            const t = c.baseSec + (c.running ? (WROB.now() - c.baseAt) / 1000 : 0);
            setText(clockNode, clock(t));
            paintTimers(state.game, t);
            paintCooldowns(state.game, t);
            // 单设备模式：数据机上原生的底部面板收起了（打团时为了视野），我们的也跟着收起，展开时再出来
            const vision = state.game.vision;
            const folded = state.ui.mode === 'single' && vision && vision.panel === false && WROB.now() - vision.at < VISION_ALIVE_MS;
            matchups.classList.toggle('is-collapsed', Boolean(folded));
            runUlts();
        }
        runTweens(now);
        requestAnimationFrame(frame);
    }

    // 特效层（effects.js）要用的：舞台；底部现在显示的那块（对位面板或团战 UI），每名选手在里面的那一格和头像
    WROB.panel = {
        stage,
        teamfight: () => fightOn,
        bottom: () => fightOn ? fight : matchups,
        cell: (side, index) => fightOn ? fightCols[side][index].box : laneRows[index][side].box,
        portrait: (side, index) => fightOn ? fightCols[side][index].portrait : laneRows[index][side].portrait,
    };

    WROB.catalog().then(catalog => {
        keyItems = new Set(catalog.keyItems || []);
        WROB.champions.load(catalog);
        WROB.icons.useImages(catalog.dragonIcons);
        WROB.onState(render);
        WROB.connect();
        requestAnimationFrame(frame);
    });
})();
