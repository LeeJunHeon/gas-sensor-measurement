/* recipe.js — 레시피 표(procs) + System Setup 모달 */
/* ===== System Setup modal ===== */
const setupOverlay=document.getElementById('setupOverlay');
function buildSetupRows(){
  const tb=document.getElementById('setupRows'); tb.innerHTML='';
  channels.forEach((c,i)=>{
    const gv = c.grp==='gas'?'gas':(c.route==='pure'?'pure-air':'mix-air');
    const dec=c.max<=100?1:0;
    const tr=document.createElement('tr');
    tr.className=c.en?'':'dis';
    tr.innerHTML=`
      <td class="chid">${c.id}</td>
      <td><input type="checkbox" ${c.en?'checked':''} data-sen="${i}"></td>
      <td><select data-sgrp="${i}">
        <option value="pure-air" ${gv==='pure-air'?'selected':''}>Air · 순수</option>
        <option value="mix-air" ${gv==='mix-air'?'selected':''}>Air · 혼합</option>
        <option value="gas" ${gv==='gas'?'selected':''}>Gas</option>
      </select></td>
      <td><input type="text" value="${c.max}" data-smax="${i}"></td>
      <td><input type="text" value="${c.sv.toFixed(dec)}" data-ssv="${i}"></td>`;
    tb.appendChild(tr);
  });
  // dim row toggle on checkbox
  tb.querySelectorAll('[data-sen]').forEach(cb=>cb.addEventListener('change',e=>{
    e.target.closest('tr').classList.toggle('dis',!e.target.checked);
  }));
}
function openSetup(){
  buildSetupRows();
  if(window.cmdPlcPorts) window.cmdPlcPorts();   // 사용 가능한 시리얼 포트 목록 요청(드롭다운 채우기)
  setupOverlay.classList.add('on');
}
function closeSetup(){ setupOverlay.classList.remove('on'); }
// 서버가 보낸 포트 목록으로 datalist를 채운다(app.js가 plc_ports 메시지 수신 시 호출).
window.applyPlcPorts=function(ports){
  const dl=document.getElementById('plcPortList'); if(!dl) return;
  dl.innerHTML='';
  (ports||[]).forEach(p=>{
    const o=document.createElement('option');
    o.value=p.device||''; if(p.desc) o.label=p.desc;
    dl.appendChild(o);
  });
};
// PLC 통신 설정 검증(저장 전). 문제 있으면 {ok:false, msg}.
function validatePlc(plc){
  if(!(plc.heartbeat_s < 3.0))
    return {ok:false, msg:'Heartbeat는 3초 미만이어야 합니다 — PLC COMM_TMR(3초) 때문에 통신두절로 트립됩니다.'};
  if(!(plc.unit_id>=1 && plc.unit_id<=247))
    return {ok:false, msg:'국번(Unit ID)은 1~247 사이여야 합니다 (0 금지).'};
  return {ok:true};
}
// 설정 모달의 입력을 읽어 명령 페이로드로 변환(서버 INTERFACE 4.2 apply_setup 형식).
function collectSetup(){
  const chans=[];
  channels.forEach((c,i)=>{
    const enEl=document.querySelector(`[data-sen="${i}"]`);
    const gvEl=document.querySelector(`[data-sgrp="${i}"]`);
    const mxEl=document.querySelector(`[data-smax="${i}"]`);
    const svEl=document.querySelector(`[data-ssv="${i}"]`);
    if(!enEl||!gvEl||!mxEl||!svEl) return;
    const gv=gvEl.value;
    let grp='air', route='pure';
    if(gv==='gas'){grp='gas';route='mix';}
    else if(gv==='mix-air'){grp='air';route='mix';}
    else {grp='air';route='pure';}
    chans.push({ch:i, en:enEl.checked, grp, route,
      max:parseFloat(mxEl.value)||0, sv:parseFloat(svEl.value)||0});
  });
  const num=id=>parseFloat(document.getElementById(id)?.value)||0;
  const params={vStart:num('setVStart'), vEnd:num('setVEnd'),
    grafInterval:num('setGraf'), smuCompliance:num('setComp')};
  const settings={
    logEnabled: !!document.getElementById('logEnabled')?.checked,
    logDir: (document.getElementById('logDir')?.value || 'logs').trim(),
    logLevel: document.getElementById('logLevel')?.value || 'info',
    logKeepDays: parseInt(document.getElementById('logKeepDays')?.value, 10) || 30,
  };
  const pnum=(id,d)=>{const v=parseFloat(document.getElementById(id)?.value); return isNaN(v)?d:v;};
  const pint=(id,d)=>{const v=parseInt(document.getElementById(id)?.value,10); return isNaN(v)?d:v;};
  const plc={
    port: (document.getElementById('plcPort')?.value || '').trim(),
    baudrate: pint('plcBaud', 115200),
    bytesize: pint('plcBytesize', 8),
    stopbits: pint('plcStopbits', 1),
    parity: document.getElementById('plcParity')?.value || 'N',
    unit_id: pint('plcUnitId', 1),
    timeout_s: pnum('plcTimeout', 1.5),
    inter_cmd_gap_s: pnum('plcGap', 0.1),
    heartbeat_s: pnum('plcHeartbeat', 1.0),
    reconnect_delay_s: pnum('plcReconnect', 1.0),
  };
  return {channels:chans, params, settings, plc};
}
function applySetup(){
  const {channels:chans, params, settings, plc}=collectSetup();
  // PLC 검증 실패 시 저장 막고 경고 표시(모달 유지)
  const v=validatePlc(plc);
  const note=document.getElementById('plcNote');
  if(!v.ok){
    if(note){ note.textContent=v.msg; note.classList.add('warn'); }
    return;
  }
  if(note){ note.textContent='설정 변경은 저장 후 재연결해야 적용됩니다.'; note.classList.remove('warn'); }
  window.cmdApplySetup(chans, params, settings, plc);
  // sync a few process params into the Auto Process panel inputs for immediate feedback
  const set=(id,el)=>{const a=document.getElementById(id),b=document.getElementById(el);if(a&&b)b.value=a.value;};
  set('setVStart','vStart'); set('setVEnd','vEnd'); set('setGraf','grafInt'); set('setLoop','loopCount'); set('setComp','smuComp');
  closeSetup();
}
document.getElementById('openSetup').addEventListener('click',openSetup);
document.getElementById('closeSetup').addEventListener('click',closeSetup);
document.getElementById('cancelSetup').addEventListener('click',closeSetup);
document.getElementById('applySetup').addEventListener('click',applySetup);
document.getElementById('plcPortsRefresh')?.addEventListener('click',()=>{ if(window.cmdPlcPorts) window.cmdPlcPorts(); });
setupOverlay.addEventListener('click',e=>{if(e.target===setupOverlay)closeSetup();});

/* ===================== recipe data ===================== */
// 시작은 빈 레시피. 서버 state(recipe_load 등)로 채워진다.
let procs=[];
const recipeBody=document.getElementById('recipeBody');

function renderRecipe(){
  const useHum=document.getElementById('useHumidity').checked;
  document.querySelectorAll('.humcol').forEach(e=>e.style.display=useHum?'':'none');
  recipeBody.innerHTML='';
  procs.forEach((r,i)=>{
    const tr=document.createElement('tr');
    if(i===0) tr.className='active';
    const gcells=r.g.map((v,gi)=>`<td class="${v===0?'zero':''}"><input class="ci" value="${v}" data-g="${i}-${gi}"></td>`).join('');
    tr.innerHTML=`
      <td class="pcol">P${i+1}</td>
      <td><input class="ci" value="${r.flow}" data-f="flow-${i}"></td>
      <td class="humcol" ${useHum?'':'style="display:none"'}><input class="ci" value="${r.rh}" data-f="rh-${i}"></td>
      ${gcells}
      <td><input class="ci" value="${r.prep}" data-f="prep-${i}"></td>
      <td><input class="ci" value="${r.meas}" data-f="meas-${i}"></td>
      <td><input type="checkbox" class="reptog" ${r.rep?'checked':''} data-rep="${i}"></td>
      <td><button class="delrow" data-del="${i}">×</button></td>`;
    recipeBody.appendChild(tr);
  });
  bindRecipe();
}
function bindRecipe(){
  recipeBody.querySelectorAll('[data-g]').forEach(inp=>inp.addEventListener('change',e=>{
    const [i,gi]=e.target.dataset.g.split('-').map(Number); procs[i].g[gi]=+e.target.value||0;
    e.target.parentElement.classList.toggle('zero',(+e.target.value||0)===0);
  }));
  recipeBody.querySelectorAll('[data-f]').forEach(inp=>inp.addEventListener('change',e=>{
    const [k,i]=e.target.dataset.f.split('-'); procs[+i][k]=+e.target.value||0;
  }));
  recipeBody.querySelectorAll('[data-rep]').forEach(cb=>cb.addEventListener('change',e=>{procs[+e.target.dataset.rep].rep=e.target.checked;}));
  recipeBody.querySelectorAll('[data-del]').forEach(b=>b.addEventListener('click',e=>{procs.splice(+e.target.dataset.del,1);renderRecipe();}));
}
document.getElementById('addProc').addEventListener('click',()=>{
  // 표 편집은 로컬 초안(draft). 저장(Save as) 시 서버로 레시피 전체를 보낸다.
  procs.push({flow:1000, rh:40, g:[0,0,0,0], prep:600, meas:300, rep:false}); renderRecipe();
});
document.getElementById('useHumidity').addEventListener('change',renderRecipe);

/* 레시피 New/Open/Save as → app.js 명령 */
document.getElementById('recNew')?.addEventListener('click',()=>window.cmdRecipeNew());
document.getElementById('recOpen')?.addEventListener('click',()=>window.cmdRecipeList());
document.getElementById('recSave')?.addEventListener('click',()=>{
  const cur=(document.getElementById('recname')?.value||'').trim();
  window.openSaveName(cur);   // app.js에 정의된 저장 이름 모달 오픈
});

/* 현재 화면의 레시피 초안을 INTERFACE 3.3 형식으로 수집 */
function collectRecipe(){
  const name=(document.getElementById('recname').value||'').trim();
  const useHumidity=document.getElementById('useHumidity').checked;
  const loopCount=+(document.getElementById('loopCount')?.value)||0;
  const num=id=>parseFloat(document.getElementById(id)?.value)||0;
  const bottle=[0,1,2,3].map(i=>parseFloat(document.getElementById('b'+i)?.value)||0);
  const params={
    vStart:num('vStart'), vEnd:num('vEnd'), vStep:num('vStep'),
    grafInterval:num('grafInt'),
    smuMode:document.getElementById('smuMode')?.value||'Source V, Measure I',
    smuSource:num('smuSrc'), smuCompliance:num('smuComp'),
    chFrom:num('chFrom'), chTo:num('chTo'),
  };
  return {name, useHumidity, loopCount, bottle,
    procs:procs.map(p=>Object.assign({},p,{g:(p.g||[0,0,0,0]).slice()})), params};
}
