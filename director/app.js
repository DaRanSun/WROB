// 导播台外壳：页签、顶栏、提示条，以及各页签共用的小工具。
(function () {
    'use strict';

    const modules = [];
    let catalog = null;

    function h(tag, props, ...children) {
        const node = document.createElement(tag);
        Object.entries(props || {}).forEach(([key, value]) => {
            if (key === 'class') node.className = value;
            else if (key === 'dataset') Object.assign(node.dataset, value);
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else if (key === 'value') node.value = value;
            else if (value === true) node.setAttribute(key, '');
            else if (value !== false && value != null) node.setAttribute(key, value);
        });
        node.append(...children.flat().filter(child => child != null && child !== false));
        return node;
    }

    // ---- 提示条 ----
    const toastNode = document.getElementById('toast');
    let toastTimer = 0;
    function toast(text, kind) {
        toastNode.textContent = text;
        toastNode.className = 'toast' + (kind ? ' ' + kind : '');
        toastNode.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toastNode.hidden = true; }, 3200);
    }

    // 发命令；被拒绝时把原因显示出来。
    function send(command) {
        if (!WROB.state) {
            toast('还没连上服务，稍等一下', 'error');
            return Promise.resolve({ ok: false, error: '还没连上服务' });
        }
        return WROB.send(command).then(reply => {
            if (!reply.ok) toast(reply.error, 'error');
            return reply;
        });
    }

    // 危险按钮：第一下变成“再点一次确认”，3 秒内再点才执行。
    function armed(button, action) {
        const label = button.textContent;
        let timer = 0;
        button.addEventListener('click', () => {
            if (button.classList.contains('is-armed')) {
                clearTimeout(timer);
                button.classList.remove('is-armed');
                button.textContent = button.dataset.label || label;
                action();
                return;
            }
            button.dataset.label = button.textContent;
            button.classList.add('is-armed');
            button.textContent = '再点一次确认';
            timer = setTimeout(() => {
                button.classList.remove('is-armed');
                button.textContent = button.dataset.label;
            }, 3000);
        });
        return button;
    }

    // 状态刷新时不要覆盖正在输入的框。
    function setValue(input, value) {
        value = value == null ? '' : String(value);
        if (document.activeElement !== input && input.value !== value) input.value = value;
    }

    function clock(seconds) {
        seconds = Math.max(0, Math.floor(seconds));
        return String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
    }

    function teamOn(state, side) {
        return state.series.teams[state.series.sides[side]];
    }

    // ---- 页签 ----
    const tabs = document.getElementById('tabs');
    function showTab(name) {
        tabs.querySelectorAll('button').forEach(button => button.classList.toggle('is-on', button.dataset.tab === name));
        document.querySelectorAll('main > .tab').forEach(section => {
            const on = section.id === 'tab-' + name;
            section.hidden = !on;
            // 预览用的 iframe 等到页签第一次打开再加载
            if (on) section.querySelectorAll('iframe[data-src]').forEach(frame => {
                frame.src = frame.dataset.src;
                frame.removeAttribute('data-src');
            });
        });
        Director.tab = name;
        modules.forEach(module => { if (module.tab === name && module.shown) module.shown(); });
        try { localStorage.setItem('wrob-tab', name); } catch (error) { /* 无痕模式下存不了，不影响使用 */ }
    }
    tabs.addEventListener('click', event => {
        const button = event.target.closest('button[data-tab]');
        if (button) showTab(button.dataset.tab);
    });

    // ---- 顶栏 ----
    const matchline = document.getElementById('matchline');
    function renderMatchline(state) {
        const series = state.series;
        const blue = teamOn(state, 'blue');
        const red = teamOn(state, 'red');
        const winner = series.decided ? series.teams[series.decided] : null;
        matchline.replaceChildren(...[
            h('b', { class: 'blue' }, blue.short || blue.name),
            h('span', { class: 'num score' }, series.score[series.sides.blue] + ' : ' + series.score[series.sides.red]),
            h('b', { class: 'red' }, red.short || red.name),
            h('span', { class: 'dim' }, '第 ' + series.gameNumber + ' 局 · BO' + series.bestOf),
            winner ? h('span', { class: 'done' }, '系列赛已决出：' + (winner.short || winner.name) + ' 胜') : null,
        ].filter(Boolean));
    }

    const conn = document.getElementById('conn');
    WROB.onStatus(connected => {
        conn.textContent = connected ? '已连接' : '连接断开，正在重连…';
        conn.classList.toggle('is-off', !connected);
    });

    window.Director = {
        h, toast, send, armed, setValue, clock, teamOn, showTab,
        tab: 'bp',
        get catalog() { return catalog; },
        register(module) { modules.push(module); },
    };

    window.addEventListener('DOMContentLoaded', () => {
        WROB.catalog().then(data => {
            catalog = data;
            WROB.champions.load(data);
            modules.forEach(module => module.init && module.init(data));
            let saved = null;
            try { saved = localStorage.getItem('wrob-tab'); } catch (error) { /* 同上 */ }
            showTab(['bp', 'game', 'setup'].includes(saved) ? saved : 'bp');
            let first = true;
            WROB.onState(state => {
                renderMatchline(state);
                modules.forEach(module => module.render(state));
                if (first) {            // 第一份状态到了之后，再给当前页签一次“刚打开”的机会（比如把光标放好）
                    first = false;
                    modules.forEach(module => { if (module.tab === Director.tab && module.shown) module.shown(); });
                }
            });
            WROB.connect();
        });
    });
})();
