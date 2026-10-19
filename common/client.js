// 和状态中心的连接：WebSocket 自动重连、服务器时间校准、发命令、读资料。
// 面板和导播台都用它：WROB.onState(fn) 订阅状态，WROB.send({type: …}) 发命令。
(function () {
    'use strict';

    const listeners = new Set();
    const statusListeners = new Set();
    const pending = new Map();
    let socket = null;
    let state = null;
    let offset = 0;             // 服务器时间 − 本机时间（导播台可能开在另一台设备上）
    let offsetKnown = false;
    let sequence = 0;
    let retry = 0;
    let lastMessageAt = 0;
    let catalogPromise = null;
    const SILENCE_MS = 13000;   // 服务端每 4 秒发一次心跳；这么久没动静说明连接已经死了

    function setStatus(connected) {
        statusListeners.forEach(fn => fn(connected));
    }

    function connect() {
        const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
        socket = new WebSocket(scheme + '://' + location.host + '/ws');
        socket.onopen = () => {
            retry = 0;
            offsetKnown = false;
            lastMessageAt = Date.now();
            setStatus(true);
        };
        socket.onmessage = event => {
            lastMessageAt = Date.now();
            const message = JSON.parse(event.data);
            if (message.type === 'state') {
                // 收到时刻 = 发出时刻 + 网络延迟，所以延迟最小的那次样本算出的差值最大，也最准。
                const sample = message.state.now - Date.now();
                offset = offsetKnown ? Math.max(offset, sample) : sample;
                offsetKnown = true;
                state = message.state;
                listeners.forEach(fn => fn(state));
            } else if (message.type === 'ack' && pending.has(message.id)) {
                pending.get(message.id)(message);
                pending.delete(message.id);
            }
        };
        socket.onclose = () => {
            setStatus(false);
            pending.forEach(resolve => resolve({ ok: false, error: '和服务的连接断开了' }));
            pending.clear();
            retry = Math.min(retry + 1, 10);
            setTimeout(connect, Math.min(250 * retry, 2000));
        };
    }

    // 网线被拔、电脑睡眠这类情况浏览器不一定会触发 onclose，所以自己盯着：太久没收到消息就主动断开重连。
    setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN && Date.now() - lastMessageAt > SILENCE_MS) socket.close();
    }, 2000);

    function send(command) {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return Promise.resolve({ ok: false, error: '还没连上服务' });
        }
        const id = ++sequence;
        return new Promise(resolve => {
            pending.set(id, resolve);
            socket.send(JSON.stringify({ type: 'cmd', id, cmd: command }));
        });
    }

    function catalog() {
        if (!catalogPromise) catalogPromise = fetch('/api/catalog').then(response => response.json());
        return catalogPromise;
    }

    window.WROB = {
        connect,
        send,
        catalog,
        onState(fn) { listeners.add(fn); if (state) fn(state); },
        onStatus(fn) { statusListeners.add(fn); },
        get state() { return state; },
        now() { return Date.now() + offset; },
        roles: ['top', 'jungle', 'mid', 'bot', 'support'],
        roleNames: { top: '上单', jungle: '打野', mid: '中路', bot: '下路', support: '辅助' },
        roleShort: { top: '上', jungle: '野', mid: '中', bot: '下', support: '辅' },
        sideNames: { blue: '蓝方', red: '红方' },
    };
})();
