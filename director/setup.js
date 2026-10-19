// 导播台 · 赛事设置页：赛事信息、两支队伍、红蓝方、比分、新系列赛。
(function () {
    'use strict';

    const { h, toast, send, armed, setValue } = Director;
    const FEARLESS = [['global', '双边无畏（任何一方选过，双方都不能再选）'], ['team', '无畏征召（只锁本队选过的）'], ['off', '不启用']];

    const nodes = { teams: {} };
    let state = null;
    let catalog = null;

    const update = patch => send({ type: 'series.update', patch });

    function field(label, input) {
        return h('label', { class: 'field' }, h('span', null, label), input);
    }

    function textField(label, key, placeholder) {
        const input = h('input', {
            type: 'text', placeholder: placeholder || '', autocomplete: 'off',
            onchange: event => update({ [key]: event.target.value }),
        });
        nodes[key] = input;
        return field(label, input);
    }

    function buildSeries() {
        nodes.bestOf = h('select', { onchange: event => update({ bestOf: Number(event.target.value) }) },
            [1, 3, 5, 7].map(n => h('option', { value: n }, 'BO' + n)));
        nodes.fearless = h('select', { onchange: event => update({ fearless: event.target.value }) },
            FEARLESS.map(([value, label]) => h('option', { value }, label)));
        nodes.series.replaceChildren(
            h('h3', null, '赛事'),
            textField('赛事名称', 'title', '面板中间上方的小字'),
            textField('阶段 / 轮次', 'stage', '如：小组赛 第 2 轮（双设备时显示在局内面板右上角）'),
            textField('角标文字', 'brand', '局内面板角上的赛事标识，如 SUFE LOLM 26（单设备右上角、双设备左上角）'),
            textField('角标图片', 'brandLogo', '可不填；放进 assets/brand/ 后写 /assets/brand/文件名，有图就显示图'),
            textField('比赛版本', 'patch', '如：7.3a'),
            field('赛制', nodes.bestOf),
            field('无畏征召', nodes.fearless));
    }

    function buildTeam(key) {
        const parts = { players: {} };
        const teamPatch = fields => update({ teams: { [key]: fields } });
        parts.preset = h('select', {
            onchange: event => {
                const team = catalog.teams.find(item => item.id === event.target.value);
                if (team) teamPatch({ id: team.id, name: team.name, short: team.short, logo: team.logo, players: team.players });
                event.target.value = '';
            },
        }, h('option', { value: '' }, '套用预设队伍…'),
            catalog.teams.map(team => h('option', { value: team.id }, team.short + (team.name !== team.short ? ' · ' + team.name : ''))));
        const text = (name, placeholder) => {
            parts[name] = h('input', {
                type: 'text', placeholder, autocomplete: 'off', onchange: event => teamPatch({ [name]: event.target.value }),
            });
            return parts[name];
        };
        parts.logoPreview = h('span', { class: 'logo-preview' }, h('img', { alt: '' }));
        parts.side = h('span', { class: 'side-tag' });
        parts.score = h('b', { class: 'num' });
        const score = delta => () => update({ score: { [key]: Math.max(0, state.series.score[key] + delta) } });
        const roster = h('datalist', { id: 'roster-' + key });
        parts.roster = roster;
        nodes.teams[key].replaceChildren(
            h('h3', null, '队伍 ' + key.toUpperCase() + ' ', parts.side),
            parts.preset,
            field('全称', text('name', '完整队名')),
            field('简称', text('short', '面板上的大字，2–4 个字符最好')),
            h('div', { class: 'field' }, h('span', null, '队标'), h('div', { class: 'row grow' },
                text('logo', '图片地址；放进 assets/teams/ 后写 /assets/teams/文件名'), parts.logoPreview)),
            h('div', { class: 'field' }, h('span', null, '比分'), h('div', { class: 'row' },
                h('button', { type: 'button', onclick: score(-1) }, '−'), parts.score,
                h('button', { type: 'button', onclick: score(1) }, '+'))),
            h('div', { class: 'players' }, WROB.roles.map(role => {
                parts.players[role] = h('input', {
                    type: 'text', list: roster.id, placeholder: WROB.roleNames[role], autocomplete: 'off',
                    onchange: event => teamPatch({ players: { [role]: event.target.value } }),
                });
                return h('label', { class: 'field compact' }, h('span', null, WROB.roleShort[role]), parts.players[role]);
            })),
            roster);
        nodes.teams[key].parts = parts;
    }

    function buildMisc() {
        const origin = location.origin;
        const link = (label, path, note) => h('div', { class: 'url' }, h('span', { class: 'dim' }, label),
            h('code', null, origin + path), h('button', {
                type: 'button', onclick: () => navigator.clipboard.writeText(origin + path).then(() => toast('已复制')),
            }, '复制'), note ? h('span', { class: 'dim' }, note) : null);
        nodes.misc.replaceChildren(
            h('h3', null, '系列赛'),
            h('div', { class: 'row' },
                h('button', { type: 'button', onclick: () => send({ type: 'series.swap_sides' }) }, '交换红蓝方'),
                armed(h('button', { type: 'button', class: 'danger' }, '新系列赛（保留两支队伍）'),
                    () => send({ type: 'series.reset', keepTeams: true })),
                armed(h('button', { type: 'button', class: 'danger' }, '新系列赛（清空队伍）'),
                    () => send({ type: 'series.reset' })),
                h('span', { class: 'dim' }, '比分、已打小局和无畏记录会清零；点错了可以回 BP 页按“撤销”')),
            h('h3', null, 'OBS 浏览器源'),
            link('BP 面板', '/overlay/bp.html', '宽 1920 高 1080；下方 340px 是面板，上方透明'),
            link('BP 面板（带背景）', '/overlay/bp.html?bg=1', '没有别的画面可垫时用'),
            link('局内面板', '/overlay/game.html', '宽 2800 高 1264，和手机画面一样大，叠在观战画面上'));
    }

    function render(next) {
        state = next;
        const series = state.series;
        ['title', 'stage', 'patch', 'brand', 'brandLogo'].forEach(key => setValue(nodes[key], series[key]));
        setValue(nodes.bestOf, series.bestOf);
        setValue(nodes.fearless, series.fearless);
        ['a', 'b'].forEach(key => {
            const team = series.teams[key];
            const parts = nodes.teams[key].parts;
            const side = series.sides.blue === key ? 'blue' : 'red';
            ['name', 'short', 'logo'].forEach(name => setValue(parts[name], team[name]));
            WROB.roles.forEach(role => setValue(parts.players[role], team.players[role]));
            parts.side.textContent = '本局' + WROB.sideNames[side];
            parts.side.className = 'side-tag ' + side;
            parts.score.textContent = series.score[key];
            const img = parts.logoPreview.querySelector('img');
            parts.logoPreview.hidden = !team.logo;
            if (team.logo && img.getAttribute('src') !== team.logo) img.src = team.logo;
            const preset = catalog.teams.find(item => item.id === team.id);
            if (parts.roster.dataset.team !== team.id) {
                parts.roster.dataset.team = team.id;
                parts.roster.replaceChildren(...(preset ? preset.roster : []).map(name => h('option', { value: name })));
            }
        });
    }

    Director.register({
        init(data) {
            catalog = data;
            nodes.series = document.getElementById('setup-series');
            nodes.teams.a = document.getElementById('setup-team-a');
            nodes.teams.b = document.getElementById('setup-team-b');
            nodes.misc = document.getElementById('setup-misc');
            buildSeries();
            buildTeam('a');
            buildTeam('b');
            buildMisc();
        },
        render,
    });
})();
