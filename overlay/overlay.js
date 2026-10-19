// 两个面板共用的小工具。
(function () {
    'use strict';

    const stage = document.getElementById('stage');
    // 画布的设计尺寸写在 <div class="stage" data-width data-height> 上：
    // BP 面板是 1920×1080；局内面板单设备时和手机画面一样大（2800×1264），双设备时是 1920×1080。
    let WIDTH = Number(stage.dataset.width) || 1920;
    let HEIGHT = Number(stage.dataset.height) || 1080;
    stage.style.width = WIDTH + 'px';
    stage.style.height = HEIGHT + 'px';
    let scale = 1;

    // OBS 浏览器源设成设计尺寸时缩放为 1。设成同比例的别的尺寸、或在普通浏览器里打开时，等比缩到窗口大小。
    function fit() {
        scale = Math.min(window.innerWidth / WIDTH, window.innerHeight / HEIGHT) || 1;
        stage.style.transform = scale === 1 ? '' : 'scale(' + scale + ')';
    }
    window.addEventListener('resize', fit);
    fit();

    // 换画布尺寸（局内面板切换单设备 / 双设备时）
    function resize(width, height) {
        if (width === WIDTH && height === HEIGHT) return;
        WIDTH = width;
        HEIGHT = height;
        stage.dataset.width = width;
        stage.dataset.height = height;
        stage.style.width = width + 'px';
        stage.style.height = height + 'px';
        fit();
    }

    function setLogo(holder, url) {
        const img = holder.querySelector('img');
        holder.classList.toggle('is-empty', !url);
        if (url && img.getAttribute('src') !== url) img.src = url;
        if (!url) img.removeAttribute('src');
    }

    function setText(element, text) {
        if (element.textContent !== text) element.textContent = text;
    }

    function clock(seconds) {
        seconds = Math.max(0, Math.floor(seconds));
        return String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
    }

    WROB.overlay = {
        stage,
        setLogo,
        setText,
        resize,
        clock,
        get scale() { return scale; },
        params: new URLSearchParams(location.search),
    };
})();
