// Скрипт страницы вынесен из HTML 19.10.2026: корпоративный прокси методистов
// обрывает ответ, внутри которого есть <script> с загрузкой файлов. Тот же код
// отдельным файлом он пропускает. Подробности — docs/agent-memory/facts/corporate-tls-proxy.md
(function(){
  var runId=(document.body.dataset||{}).runId||'';
  var started=Date.now(),t=document.getElementById('rpd-wait-time'),
      note=document.getElementById('rpd-wait-note'),err=document.getElementById('rpd-wait-error');
  function tick(){var s=Math.floor((Date.now()-started)/1000);
    t.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}
  setInterval(tick,1000);
  function poll(){
    fetch('/app/rpd/'+encodeURIComponent(runId)+'/status',{headers:{'Accept':'application/json'}})
      .then(function(r){return r.json();})
      .then(function(d){
        if(d&&d.state==='done'&&d.redirect){window.location.href=d.redirect;return;}
        if(d&&d.state==='error'){note.textContent='Сборка не удалась.';
          err.hidden=false;err.textContent=d.error||'Причина неизвестна';return;}
        setTimeout(poll,2000);
      }).catch(function(){setTimeout(poll,4000);});
  }
  setTimeout(poll,2000);
})();
