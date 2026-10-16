/* 共用即時更新(docs/ui-standard.md 之外的行為標準):
   定期抓 HTML 片段換進容器;分頁隱藏時暫停、回到前景立刻補一次;內容沒變就不換;保留各 .wrap 的橫向捲動位置。
   用法:LiveFrag({el: 容器, url: 片段網址, interval: 毫秒, onSwap: 換完後的回呼});另有 LiveClock(選取器) 讓時鐘在本機每秒走。 */
(function () {
  window.LiveFrag = function (opts) {
    var last = null, busy = false;
    async function tick() {
      if (document.hidden || busy) return;
      busy = true;
      try {
        var sc = [].map.call(opts.el.querySelectorAll('.wrap'), function (w) { return w.scrollLeft; });
        var r = await fetch(opts.url + (opts.url.indexOf('?') < 0 ? '?' : '&') + '_=' + Date.now());
        if (!r.ok) return;
        var h = await r.text();
        if (h === last) return;
        last = h;
        opts.el.innerHTML = h;
        opts.el.querySelectorAll('.wrap').forEach(function (w, i) { if (sc[i]) w.scrollLeft = sc[i]; });
        if (opts.onSwap) opts.onSwap();
      } catch (e) { /* 網路暫時失敗:下一輪再試 */ } finally { busy = false; }
    }
    var timer = setInterval(tick, opts.interval || 1000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
    return { stop: function () { clearInterval(timer); }, tick: tick };
  };
  window.LiveClock = function (sel) {
    function go() { var t = new Date().toTimeString().slice(0, 8); document.querySelectorAll(sel).forEach(function (n) { n.textContent = t; }); }
    go(); return setInterval(go, 1000);
  };
})();
