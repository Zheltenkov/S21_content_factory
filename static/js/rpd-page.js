// Скрипт страницы вынесен из HTML 19.10.2026: корпоративный прокси методистов
// обрывает ответ, внутри которого есть <script> с загрузкой файлов. Тот же код
// отдельным файлом он пропускает. Подробности — docs/agent-memory/facts/corporate-tls-proxy.md
(function(){
  var form=document.getElementById('rpd-form'),file=document.getElementById('rpd-file'),
      nameEl=document.getElementById('rpd-name'),okEl=document.getElementById('rpd-ok'),
      hid=document.getElementById('rpd-upload-id'),prog=document.getElementById('rpd-progress'),
      lbl=document.getElementById('rpd-plabel'),fill=document.getElementById('rpd-fill'),
      go=document.getElementById('rpd-go'),defName=nameEl.textContent;
  function ready(r){go.classList.toggle('is-ready',!!r);}
  file.addEventListener('change',function(){
    hid.value='';ready(false);okEl.hidden=true;
    if(!file.files||!file.files.length){nameEl.textContent=defName;prog.hidden=true;return;}
    var f=file.files[0];nameEl.textContent=f.name;prog.hidden=false;fill.style.width='0%';lbl.textContent='Загрузка: 0%';
    var body=new FormData();body.append('program',f);
    var xhr=new XMLHttpRequest();xhr.open('POST','/app/rpd/upload',true);
    xhr.upload.onprogress=function(e){if(!e.lengthComputable)return;var p=Math.round(e.loaded/e.total*100);
      fill.style.width=p+'%';lbl.textContent=p<100?('Загрузка: '+p+'%'):'Обработка…';};
    xhr.onload=function(){var id='';if(xhr.status>=200&&xhr.status<300){try{id=(JSON.parse(xhr.responseText)||{}).upload_id||'';}catch(e){}}
      if(id){hid.value=id;fill.style.width='100%';lbl.textContent='Файл на сервере ✓';okEl.hidden=false;ready(true);}
      else{lbl.textContent='Ошибка загрузки ('+xhr.status+')';}};
    xhr.onerror=function(){lbl.textContent='Ошибка сети';};xhr.send(body);
  });
  form.addEventListener('submit',function(e){
    e.preventDefault();if(!hid.value){lbl.textContent='Сначала загрузите УП (.csv)';prog.hidden=false;return;}
    go.disabled=true;go.textContent='Собираю РПД (генерю прозу)…';prog.hidden=false;
    var titleEl=document.getElementById('rpd-title');
    var body=new URLSearchParams();body.append('upload_id',hid.value);body.append('title',titleEl?titleEl.value:'');
    fetch('/app/rpd/generate',{method:'POST',body:body,headers:{'Content-Type':'application/x-www-form-urlencoded'}})
      .then(function(r){return r.json();}).then(function(d){
        if(!d||!d.run_id){throw 0;}
        watch(d.run_id);
      })
      .catch(function(){go.disabled=false;go.textContent='Сформировать РПД →';lbl.textContent='Не удалось собрать РПД';});
  });
  // Сборка идёт на сервере; запрос уже отвечен, поэтому ждём по статусу, а не держим соединение.
  function watch(runId){
    var started=Date.now();
    function elapsed(){var s=Math.floor((Date.now()-started)/1000);
      return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}
    function step(){
      fetch('/app/rpd/'+encodeURIComponent(runId)+'/status')
        .then(function(r){return r.json();})
        .then(function(d){
          if(d&&d.state==='done'&&d.redirect){window.location.href=d.redirect;return;}
          if(d&&d.state==='error'){go.disabled=false;go.textContent='Сформировать РПД →';
            lbl.textContent='Сборка не удалась: '+(d.error||'причина неизвестна');return;}
          lbl.textContent='Собираю РПД — '+elapsed()+'. Обычно две-три минуты, не перезагружайте страницу.';
          setTimeout(step,2000);
        }).catch(function(){setTimeout(step,4000);});
    }
    lbl.textContent='Собираю РПД — 0:00. Обычно две-три минуты, не перезагружайте страницу.';
    setTimeout(step,2000);
  }
})();
(function(){
  var select=document.getElementById('rpd-registry-select'),go=document.getElementById('rpd-registry-go'),
      state=document.getElementById('rpd-registry-state');
  if(!select||!go||!state) return;
  function ready(value){go.disabled=!value;go.classList.toggle('is-ready',!!value);}
  fetch('/app/rpd/registry').then(function(r){return r.json();}).then(function(d){
    var plans=(d&&d.plans)||[];
    select.innerHTML='';
    var placeholder=document.createElement('option');placeholder.value='';
    placeholder.textContent=plans.length?'Выберите учебный план':'В Реестре нет доступных УП';
    select.appendChild(placeholder);
    if(!plans.length){state.textContent='Можно загрузить УП в формате CSV.';return;}
    plans.forEach(function(p){
      var option=document.createElement('option');option.value=String(p.id);
      option.textContent=p.title+(p.hours?(' · '+p.hours+' ч'):'');
      select.appendChild(option);
    });
    select.disabled=false;state.textContent='Доступно УП: '+plans.length;
  }).catch(function(){select.innerHTML='<option value="">Не удалось загрузить список</option>';
    state.textContent='Проверьте доступность Реестра.';});
  select.addEventListener('change',function(){ready(select.value);state.textContent=select.value?'УП выбран. Можно формировать РПД.':'';});
  go.addEventListener('click',function(){
    var planId=select.value;if(!planId)return;
    select.disabled=true;go.disabled=true;go.textContent='Собираю РПД…';state.textContent='Формируется документ из выбранного УП.';
    fetch('/app/rpd/registry/'+encodeURIComponent(planId),{method:'POST'}).then(function(r){return r.json();})
      .then(function(x){
        if(!x||!x.run_id){throw 0;}
        var started=Date.now();
        function step(){
          fetch('/app/rpd/'+encodeURIComponent(x.run_id)+'/status').then(function(r){return r.json();})
            .then(function(d){
              if(d&&d.state==='done'&&d.redirect){window.location.href=d.redirect;return;}
              if(d&&d.state==='error'){select.disabled=false;go.textContent='Повторить →';ready(select.value);
                state.textContent='Сборка не удалась: '+(d.error||'причина неизвестна');return;}
              var s=Math.floor((Date.now()-started)/1000);
              state.textContent='Собираю РПД — '+Math.floor(s/60)+':'+String(s%60).padStart(2,'0')
                +'. Обычно две-три минуты, не перезагружайте страницу.';
              setTimeout(step,2000);
            }).catch(function(){setTimeout(step,4000);});
        }
        state.textContent='Собираю РПД — 0:00. Обычно две-три минуты, не перезагружайте страницу.';
        setTimeout(step,2000);
      })
      .catch(function(){select.disabled=false;go.textContent='Повторить →';ready(select.value);
        state.textContent='Не удалось собрать РПД. Попробуйте ещё раз.';});
  });
})();
