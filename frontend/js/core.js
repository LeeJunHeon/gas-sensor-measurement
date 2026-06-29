/* core.js — 헤더/상태/로그, 서버상태 반영(applyState/applyTelemetry),
   fit/도크/종료모달, 전역 노출, 초기화. schematic.js·recipe.js 다음에 로드. */
/* ===== 프로그램 종료 확인 모달 (System Setup과 동일한 스타일) ===== */
const exitOverlay=document.getElementById('exitConfirm');
let _pendingExit=null;
function closeExit(){ exitOverlay.classList.remove('on'); _pendingExit=null; }
// app.js의 cmdExit가 window.confirm 대신 호출: 모달 "종료" 클릭 시 onConfirm 실행
window.confirmExit=function(onConfirm){
  if(!exitOverlay){ if(onConfirm) onConfirm(); return; }   // 모달 없으면 바로 진행(폴백)
  _pendingExit=(typeof onConfirm==='function')?onConfirm:null;
  exitOverlay.classList.add('on');
};
document.getElementById('exitConfirmClose').addEventListener('click',closeExit);
document.getElementById('exitConfirmCancel').addEventListener('click',closeExit);
exitOverlay.addEventListener('click',e=>{if(e.target===exitOverlay)closeExit();});
document.getElementById('exitConfirmOk').addEventListener('click',()=>{
  const fn=_pendingExit; closeExit(); if(fn) fn();
});

function updateSystem(){
  const act=channels.filter(c=>c.en).length;
  const total=channels.filter(c=>c.en).reduce((s,c)=>s+c.pv,0);
  document.getElementById('activeCh').textContent=act+' / 8';
  document.getElementById('totalFlow').textContent=Math.round(total)+' sccm';
}

/* ===================== Auto Process dock ===================== */
const viewProc=document.getElementById('viewProc');
const dockToggle=document.getElementById('dockToggle');
const viewsEl=document.querySelector('.views');
const viewSchemEl=document.getElementById('viewSchem');
function setDock(open){
  viewsEl.classList.toggle('docked',open);
  viewProc.classList.toggle('dock',open);
  // 전체화면 모드: #app은 뷰포트를 채움. 패널 표시 여부만 제어.
  viewProc.style.setProperty('display', open ? 'flex' : 'none', 'important');
  fit();
  dockToggle.innerHTML = open ? 'Auto Process View ◂' : 'Auto Process View ▸';
  setTimeout(()=>{fit();drawBuses();},30);
  setTimeout(()=>{fit();drawBuses();},320);
}
dockToggle.addEventListener('click',()=>setDock(!viewsEl.classList.contains('docked')));
document.getElementById('dockClose').addEventListener('click',()=>setDock(false));
window.addEventListener('resize',()=>setTimeout(drawBuses,50));

/* ===================== run state ===================== */
let running=false;
/* 헤더 현재 상태 표시 — 지금은 running 기준. 추후 서버가 더 구체적인 상태 문자열을
   내려주면 setHdrStatus()로 그대로 표시하도록 확장 가능. */
let _hdrTransient=false, _hdrTimer=null;
function setHdrStatus(text, kind){   // kind: 'idle' | 'run' | 'purge' | 'stop'
  const e=document.getElementById('hdrStatus'); if(!e) return;
  const t=e.querySelector('.htxt'); if(t) t.textContent=text;
  e.classList.remove('run','purge','stop');
  if(kind && kind!=='idle') e.classList.add(kind);
}
function refreshHdrStatus(){
  if(_hdrTransient) return;   // 임시 표시(퍼지/정지) 유지 중이면 건드리지 않음
  setHdrStatus(running?'자동 실행 중':'대기 중', running?'run':'idle');
}
// 퍼지/정지처럼 잠깐 보여줄 상태(기본 상태로 자동 복귀)
window.flashHdrStatus=function(text, kind, ms){
  _hdrTransient=true; setHdrStatus(text, kind);
  clearTimeout(_hdrTimer);
  _hdrTimer=setTimeout(()=>{ _hdrTransient=false; refreshHdrStatus(); }, ms||2500);
};
window.setHdrStatus=setHdrStatus;
function uiSetRunning(on){
  running=on;
  const pill=document.getElementById('runpill');
  if(pill) pill.classList.toggle('idle',!on);
  const rt=document.getElementById('runtxt'); if(rt) rt.textContent=on?'AUTO RUN':'IDLE';
  if(on){ _hdrTransient=false; clearTimeout(_hdrTimer); }  // 실행 시작은 즉시 반영
  refreshHdrStatus();
}
document.querySelectorAll('.hbtn.run, .pbtn.runbig').forEach(b=>b.addEventListener('click',()=>window.cmdRun()));
// AUTO STOP(푸터 신설) + 도크 AUTO STOP → 시퀀스 정지
document.querySelectorAll('.hbtn.stop, .pbtn.stopbig').forEach(b=>b.addEventListener('click',()=>window.cmdStop()));
document.querySelector('.hbtn.purge')?.addEventListener('click',()=>window.cmdPurge());
// PROGRAM END → 프로그램 실제 종료
document.querySelector('.hbtn.end')?.addEventListener('click',()=>window.cmdExit());
document.getElementById('recname').addEventListener('change',e=>{document.getElementById('hdrRecipe').textContent=(e.target.value||'').trim()||'\u2014';});

/* ===================== system log ===================== */
function logMsg(msg, level){
  const body=document.getElementById('logBody'); if(!body) return;
  const d=new Date();
  const ts=`${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  const el=document.createElement('div');
  el.className='le '+(level||'info');
  el.innerHTML=`<span class="lt">${ts}</span><span class="lv"></span>`;
  el.querySelector('.lv').textContent=msg;
  body.appendChild(el);
  while(body.children.length>200) body.removeChild(body.firstChild);
  body.scrollTop=body.scrollHeight;
  // \uc911\uc694\ub85c\uadf8(warn/err)\uac00 \ubaa8\ub2ec \ub2eb\ud78c \uc0c1\ud0dc\uc5d0\uc11c \ubc1c\uc0dd\ud558\uba74 "\ub85c\uadf8" \ubc84\ud2bc\uc5d0 \ubc30\uc9c0 \ud45c\uc2dc
  if(level==='warn'||level==='err'){
    const lm=document.getElementById('logModal'), ob=document.getElementById('openLog');
    if(lm&&ob&&!lm.classList.contains('on')) ob.classList.add('hasalert');
  }
}
document.getElementById('logClear')?.addEventListener('click',()=>{document.getElementById('logBody').innerHTML='';logMsg('\ub85c\uadf8 \uc9c0\uc6c0','info');});

/* ===== System Log \ubaa8\ub2ec (System Setup\uacfc \ub3d9\uc77c\ud55c \uad6c\uc870) ===== */
const logModal=document.getElementById('logModal');
const openLogBtn=document.getElementById('openLog');
function openLog(){
  if(!logModal) return;
  logModal.classList.add('on');
  if(openLogBtn) openLogBtn.classList.remove('hasalert');   // \uc5f4\uba74 \ubc30\uc9c0 \uc81c\uac70
}
function closeLog(){ if(logModal) logModal.classList.remove('on'); }
openLogBtn?.addEventListener('click',openLog);
document.getElementById('logModalClose')?.addEventListener('click',closeLog);
logModal?.addEventListener('click',e=>{if(e.target===logModal)closeLog();});
logMsg('\ud654\uba74 \uc900\ube44 \uc644\ub8cc \u2014 \uc11c\ubc84 \uc5f0\uacb0 \ub300\uae30','info');
// \uce21\uc815\uac12 \uc2dc\ubbac\ub808\uc774\uc158\uc740 \ub354 \uc774\uc0c1 \ud654\uba74\uc5d0 \ub450\uc9c0 \uc54a\ub294\ub2e4.
// \uc5f0\uacb0 \uc2dc: \uc11c\ubc84\uac00 telemetry\ub97c push. \ub04a\uae40 \uc2dc: app.js\uac00 \uc2dc\ubbac\ub808\uc774\uc158\uc73c\ub85c \ub300\uccb4\ud55c\ub2e4.

/* ===================== \uc11c\ubc84 \uc5f0\ub3d9 \ube0c\ub9ac\uc9c0 (app.js\uac00 \ud638\ucd9c) ===================== */
function fmtElapsed(sec){
  sec=Math.max(0,Math.floor(sec||0));
  const h=String(Math.floor(sec/3600)).padStart(2,'0');
  const m=String(Math.floor(sec%3600/60)).padStart(2,'0');
  const s=String(sec%60).padStart(2,'0');
  return `${h}:${m}:${s}`;
}
function applyParams(p){
  if(!p) return;
  const set=(id,v)=>{const e=document.getElementById(id); if(e&&v!=null) e.value=v;};
  set('vStart',p.vStart); set('vEnd',p.vEnd); set('vStep',p.vStep);
  set('grafInt',p.grafInterval); set('smuSrc',p.smuSource); set('smuComp',p.smuCompliance);
  set('chFrom',p.chFrom); set('chTo',p.chTo);
  const sm=document.getElementById('smuMode'); if(sm&&p.smuMode) sm.value=p.smuMode;
}
// \uc11c\ubc84 state \uba54\uc2dc\uc9c0 \u2192 \ub0b4\ubd80 \uc0c1\ud0dc \ubc18\uc601 \ud6c4 \uc7ac\ub80c\ub354
function applyState(s){
  if(!s) return;
  if(s.channels){
    channels.length=0;
    s.channels.forEach(c=>channels.push(Object.assign({}, c)));
    deriveDisplay();   // 정렬 없이 표시 필드만 derive (서버 인덱스 유지)
  }
  if(s.recipe){
    procs.length=0;
    (s.recipe.procs||[]).forEach(p=>procs.push(Object.assign({}, p, {g:(p.g||[0,0,0,0]).slice()})));
    const rn=document.getElementById('recname'); if(rn) rn.value=s.recipe.name||'';
    const hdr=document.getElementById('hdrRecipe'); if(hdr) hdr.textContent=s.recipe.name||'\u2014';
    const uh=document.getElementById('useHumidity'); if(uh) uh.checked=!!s.recipe.useHumidity;
    const lc=document.getElementById('loopCount'); if(lc&&s.recipe.loopCount!=null) lc.value=s.recipe.loopCount;
    (s.recipe.bottle||[]).forEach((v,i)=>{const el=document.getElementById('b'+i); if(el) el.value=(v||v===0)?v:'';});
    applyParams(s.recipe.params);
  }
  if(s.system){
    if(s.system.routeOut) routeOut=s.system.routeOut;
    uiSetRunning(!!s.system.running);
    if(typeof updateWayToggle==='function') updateWayToggle();   // 4-Way 토글 버튼 모드 표시 갱신
    const hl=document.getElementById('hdrLoop');
    if(hl&&s.system.loop) hl.textContent=`${s.system.loop.current} / ${s.system.loop.total}`;
    const rh=document.getElementById('rh'); if(rh&&s.system.rh!=null) rh.textContent=(+s.system.rh).toFixed(1);
    const mv=document.getElementById('measVal'); if(mv&&s.system.smu) mv.textContent=s.system.smu;
  }
  renderLanes();   // \ubc30\uad00\ub3c4 \uc7ac\ub80c\ub354
  renderRecipe();  // \ub808\uc2dc\ud53c \ud45c \uc7ac\ub80c\ub354
  updateSystem();  // \uc0c1\ub2e8 \ud1b5\uacc4
}
// \ube60\ub978 \uce21\uc815\uac12\ub9cc \uac00\ubccd\uac8c \ubc18\uc601 \u2014 \ubc30\uad00 SVG/\ub808\uc2dc\ud53c\ub97c \uc7ac\ub80c\ub354\ud558\uc9c0 \uc54a\ub294\ub2e4.
function applyTelemetry(tl){
  if(!tl) return;
  if(Array.isArray(tl.pv)){
    tl.pv.forEach((v,i)=>{
      const el=document.querySelector(`[data-pv="${i}"]`);
      if(el) el.textContent=(+v).toFixed(dec(channels[i]||{max:2000}));
      if(channels[i]) channels[i].pv=+v;
    });
  }
  if(tl.rh!=null){
    const rh=document.getElementById('rh'); if(rh) rh.textContent=(+tl.rh).toFixed(1);
    const rhp=document.getElementById('rhProc'); if(rhp) rhp.textContent=(+tl.rh).toFixed(1);
  }
  if(tl.smu!=null){ const mv=document.getElementById('measVal'); if(mv) mv.textContent=tl.smu; }
  if(tl.elapsed!=null){ const c=document.getElementById('clk'); if(c) c.textContent=fmtElapsed(tl.elapsed); }
  if(tl.loop){ const hl=document.getElementById('hdrLoop'); if(hl) hl.textContent=`${tl.loop.current} / ${tl.loop.total}`; }
  updateSystem();  // activeCh / totalFlow \ud14d\uc2a4\ud2b8\ub9cc \uac31\uc2e0(\uac00\ubcbc\uc6c0)
}

/* app.js\uac00 \ucc38\uc870\ud560 \uc804\uc5ed \ub178\ucd9c */
window.channels=channels; window.procs=procs;
window.renderLanes=renderLanes; window.renderRecipe=renderRecipe;
window.drawBuses=drawBuses; window.updateSystem=updateSystem; window.logMsg=logMsg;
window.applyState=applyState; window.applyTelemetry=applyTelemetry;
window.collectRecipe=collectRecipe; window.collectSetup=collectSetup;

/* ===================== fit ===================== */
let lastScale=0;
let contentW=2040;                      // #app 고정 폭(배관도 1320 + 도크 720)
const contentH=1010;                    // #app 기준 높이(세로 비율 판단용)
function fit(){
  // 가로·세로 중 빡빡한 쪽 기준으로 균일 축소(왜곡 없음). 세로 늘림은 1.15배까지만,
  // 넘으면 위아래 여백으로 처리(전체화면 1920×1080은 기존처럼 꽉 참).
  const w=window.innerWidth, h=window.innerHeight; if(!w||!h){requestAnimationFrame(fit);return;}
  const app=document.getElementById('app');
  const s=Math.min(w/contentW, h/contentH);
  lastScale=s;
  app.style.height=Math.min(h/s, contentH*1.15)+'px';
  app.style.zoom=s;
}
window.addEventListener('resize',()=>{fit();drawBuses();});
window.addEventListener('load',()=>{fit();drawBuses();});
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>{fit();drawBuses();});
if(window.ResizeObserver){const ro=new ResizeObserver(()=>drawBuses());ro.observe(document.querySelector('.schem'));const vo=new ResizeObserver(()=>{fit();drawBuses();});vo.observe(document.documentElement);}
if(window.visualViewport)window.visualViewport.addEventListener('resize',()=>{fit();drawBuses();});
setInterval(()=>{const p=lastScale;fit();if(lastScale!==p||document.getElementById('wires').innerHTML.length===0)drawBuses();},300);

/* init */
renderLanes(); renderRecipe(); uiSetRunning(false);
setDock(true);                          // 통합 화면: Auto Process 사이드를 처음부터 표시
fit(); setTimeout(()=>{fit();drawBuses();},60);
requestAnimationFrame(()=>requestAnimationFrame(()=>{fit();drawBuses();}));
