// 英雄资料：查找、检索、出图。图片直接用 WRPedia 的素材，个别英雄按 config/art.yaml 裁切。
(function () {
    'use strict';

    const bySlug = new Map();
    let list = [];          // 按中文名拼音排，位置固定，给点选用的格子
    let popular = [];       // 按赛事里的选禁次数排，常用的在前，给下拉列表

    // 检索时忽略大小写、空格、连字符和间隔号，和 WRPedia 的习惯一致。
    function normalize(text) {
        return String(text || '').toLowerCase().replace(/[\s\-'’·.]/g, '');
    }

    function load(catalog) {
        const collator = new Intl.Collator('zh-Hans-CN');
        list = catalog.champions.slice().sort((a, b) => collator.compare(a.name, b.name));
        popular = list.slice().sort((a, b) => (b.usage || 0) - (a.usage || 0));     // 稳定排序：次数相同的仍按拼音
        list.forEach(champion => {
            champion.keys = [champion.name, champion.title, champion.en, champion.slug]
                .concat(champion.aliases || []).map(normalize).filter(Boolean);
            bySlug.set(champion.slug, champion);
        });
    }

    // 完全匹配排最前，其次是开头匹配，再其次是包含；同一档里保持 source 原来的顺序。
    function search(query, source) {
        const needle = normalize(query);
        source = source || list;
        if (!needle) return source;
        const ranked = [];
        source.forEach(champion => {
            let best = 3;
            for (const key of champion.keys) {
                if (key === needle) { best = 0; break; }
                if (key.startsWith(needle)) best = Math.min(best, 1);
                else if (best > 2 && key.includes(needle)) best = 2;
            }
            if (best < 3) ranked.push([best, champion]);
        });
        return ranked.sort((a, b) => a[0] - b[0]).map(item => item[1]);
    }

    function image(src, focus, zoom) {
        const img = document.createElement('img');
        img.src = src;
        img.alt = '';
        img.draggable = false;
        if (focus) img.style.objectPosition = focus;
        if (zoom) {
            img.style.transform = 'scale(' + zoom + ')';
            img.style.transformOrigin = focus || '50% 50%';
        }
        return img;
    }

    // 方形小图。没有小图的英雄（诺拉）用立绘按焦点裁。
    function icon(slug) {
        const champion = bySlug.get(slug);
        if (!champion) return document.createElement('span');
        const art = champion.art || {};
        return art.icon === 'portrait' ? image(champion.icon, art.icon_focus, art.icon_zoom) : image(champion.icon);
    }

    // 竖版立绘。横版原画（彗）按焦点裁成竖的。
    function portrait(slug) {
        const champion = bySlug.get(slug);
        if (!champion) return document.createElement('span');
        return image(champion.portrait || champion.icon, (champion.art || {}).portrait_focus);
    }

    WROB.champions = {
        load,
        search,
        normalize,
        get popular() { return popular; },
        icon,
        portrait,
        get(slug) { return bySlug.get(slug) || null; },
        name(slug) { return bySlug.has(slug) ? bySlug.get(slug).name : ''; },
        get list() { return list; },
    };
})();
