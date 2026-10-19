// 局内面板用到的小图标，都是自己画的矢量图，放大缩小不糊。
// 元素龙的三个矢量图标只是备用的示意（火焰、山、水滴）：assets/dragons/ 里有文件名以 infernal / mountain / ocean
// 开头的图片时用图片（服务端在 /api/catalog 的 dragonIcons 里告诉我们有哪几张）。
(function () {
    'use strict';

    const PATHS = {
        // 防御塔
        tower: '<path d="M7 3h2.2v2h1.7V3h2.200v2h1.700V3H17v4.200l-1.600 1.600v6.400l2.100 2.300V21h-11v-3.500l2.100-2.300V8.800L7 7.200z"/>',
        // 金币
        coin: '<ellipse cx="12" cy="8" rx="8" ry="4.200"/><path d="M4 11.200c0 2.300 3.600 4.200 8 4.200s8-1.900 8-4.200v2.600c0 2.300-3.600 4.200-8 4.200s-8-1.900-8-4.200z" opacity=".75"/>',
        // 火龙
        infernal: '<path d="M12.700 1.800c.600 3-.700 5-2.500 7C8.400 10.800 6.500 12.700 6.500 15.800a5.700 5.700 0 0 0 11.400 0c0-2.100-.900-3.700-2-5.200-.300 1.300-1 2.200-1.900 2.900.300-3.800-.200-8-1.300-11.700zM12.200 14.200c1.300 1 2 2.100 2 3.300a2.300 2.300 0 0 1-4.600 0c0-1.300 1-2.200 2.600-3.300z"/>',
        // 山龙（土龙）
        mountain: '<path d="M1.800 20.500 9 6.500l3.100 5.300 2.200-3.600 7.900 12.300zM9 10.300l-1.500 3 1.500 1 1.500-1.200z"/>',
        // 海龙（水龙）
        ocean: '<path d="M12 2c3.400 4.300 6.800 8 6.800 11.900a6.800 6.800 0 0 1-13.600 0C5.200 10 8.600 6.300 12 2zm-2.900 11.300c-.300 2.400 1 4.300 3.300 4.700-1.200-1.100-1.800-2.600-1.500-4.700z"/>',
        // 纳什男爵（占位）
        baron: '<path d="M12 2.500 9.300 6 5 4.500l1.300 4.300L3 11.500l3.800 1.600-.600 4.400L10 15.300 12 21.500l2-6.200 3.800 2.200-.600-4.400 3.800-1.600-3.300-2.700L19 4.500 14.700 6zM9.600 10.400h1.700v1.700H9.600zm3.100 0h1.700v1.700h-1.700z"/>',
        // 峡谷先锋（占位）
        herald: '<path d="M12 3a8 8 0 0 0-8 8v2.500c0 1 .600 1.900 1.500 2.300V19h3v-2h7v2h3v-3.200c.900-.400 1.500-1.300 1.500-2.300V11a8 8 0 0 0-8-8zm0 4.200a3.300 3.300 0 1 1 0 6.600 3.300 3.300 0 0 1 0-6.600zm0 1.800a1.500 1.500 0 1 0 0 3 1.500 1.500 0 0 0 0-3z"/>',
        // 远古巨龙（占位）
        elder: '<path d="M12 2 9.500 7.200 4 5.500l2.300 5.200L2.500 14l5.200.800L8.500 21 12 17.500 15.500 21l.800-6.200 5.200-.800-3.800-3.300L20 5.500l-5.500 1.700zM12 9.500l1.400 2.500-1.400 2.500-1.400-2.500z"/>',
        // 属性没记的小龙
        dragon: '<path d="M12 3l2.600 5.300 5.900.900-4.300 4.100 1 5.800L12 16.400 6.800 19.100l1-5.800L3.500 9.200l5.900-.900z"/>',
    };

    function svg(name) {
        return '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' + (PATHS[name] || PATHS.dragon) + '</svg>';
    }

    // 页面里写着 <i class="icon tower"> 的地方填上图
    document.querySelectorAll('i.icon').forEach(node => {
        const name = Array.from(node.classList).find(item => PATHS[item]);
        if (name) node.innerHTML = svg(name);
    });

    // 一条元素龙的图标：assets/dragons/ 里有图片就用图片，没有就用上面画的
    const images = {};
    function dragon(element) {
        const node = document.createElement('i');
        node.className = 'dragon ' + (element || 'unknown');
        if (images[element]) {
            const image = new Image();
            image.alt = '';
            image.src = images[element];
            node.append(image);
            node.classList.add('has-image');
        } else {
            node.innerHTML = svg(element || 'dragon');
        }
        return node;
    }

    WROB.icons = { svg, dragon, useImages(urls) { Object.assign(images, urls || {}); } };
})();
