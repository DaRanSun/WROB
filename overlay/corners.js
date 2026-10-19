// 局内面板的两个上角。
//   单设备：右上角是赛事标识（盖住原生的观战按钮），左上角是小地图，不放东西。
//   双设备：都在顶部那条底色里，不压画面——左上角赛事标识，右上角比赛轮次（赛事设置里的“阶段 / 轮次”）。
// 野怪 / 龙魂播报在右上角那一块里替换显示（config/effects.yaml 的 hold.objective 秒），然后淡出、换回原来的内容。
// 赛事标识的文字和图片在导播台“赛事设置”里改（series.brand / brandLogo）。
(function () {
    'use strict';

    const FADE_MS = 450;
    const corners = {};
    ['left', 'right'].forEach(side => {
        const root = document.getElementById('corner-' + side);
        corners[side] = { root, face: root.querySelector('.corner-face'), key: '' };
    });
    const queue = [];
    let busy = false;

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function content(kind, series) {
        if (kind === 'brand') {
            if (!series.brand && !series.brandLogo) return null;
            return { kind, text: series.brand || '', logo: series.brandLogo || '' };
        }
        const text = series.stage || '';
        return text ? { kind, text } : null;
    }

    function paint(corner, item) {
        const key = JSON.stringify(item);
        if (key === corner.key) return;
        corner.key = key;
        corner.root.classList.toggle('is-empty', !item);
        corner.root.dataset.kind = item ? item.kind : '';
        corner.face.replaceChildren();
        if (!item) return;
        if (item.logo) {
            const holder = element('span', 'corner-logo');
            const img = new Image();
            img.alt = '';
            img.src = item.logo;
            holder.append(img);
            corner.face.append(holder);
        }
        if (item.text) corner.face.append(element('b', 'corner-text', item.text));
    }

    function render(state) {
        const dual = state.ui.mode === 'dual';
        paint(corners.left, dual ? content('brand', state.series) : null);
        paint(corners.right, content(dual ? 'round' : 'brand', state.series));
    }

    // 右上角换成一条播报，hold 秒后淡出、换回原来的内容；接连来的排队
    function notify(node, hold) {
        queue.push({ node, hold });
        if (!busy) next();
    }
    function next() {
        const item = queue.shift();
        busy = Boolean(item);
        const corner = corners.right;
        if (!item) {
            corner.root.classList.remove('is-noting');
            return;
        }
        const note = element('div', 'corner-note');
        note.append(item.node);
        corner.root.append(note);
        corner.root.classList.add('is-noting');
        setTimeout(() => {
            note.classList.add('is-out');
            if (!queue.length) corner.root.classList.remove('is-noting');   // 原来的内容和播报的淡出同时淡回来
            setTimeout(() => { note.remove(); next(); }, FADE_MS);
        }, Math.max(0.5, item.hold) * 1000 - FADE_MS);
    }

    WROB.corners = { notify };
    WROB.catalog().then(() => WROB.onState(render));
})();
