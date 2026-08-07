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
/* 하드웨어 배정 표(편집 가능). 드롭다운이라 오타가 불가능하고, 중복·미장착 배정은
   적용 시 서버가 통째로 거부한다. 밸브 코일은 카탈로그가 채널 id로 결정 — 편집 대상 아님.
   레지스터 번호를 함께 표시하려면 카탈로그가 필요해 서버에서 한 번 받아 캐시한다. */
let _plcCatalog=null;
let _lastLive=null;   // 마지막 plc_live (state push 시 갱신, telemetry 때는 유지)
// 상태 열은 config가 아니라 '지금 실제로 살아있는가'를 보여준다.
function mapStatusHtml(c){
  const p=c.plc||{};
  const live=_lastLive;
  if(!live || !live.connected) return '<span class="dim">PLC 미연결</span>';
  if(!p.sv_out && !p.pv_in)    return '<span class="warn">미배정</span>';
  if(p.pv_in){
    const v=(live.pv||{})[c.id];
    if(v!=null) return '<span class="ok">통신 중 · PV '+(+v).toFixed(1)+'</span>';
    return '<span class="warn">PV 수신 없음</span>';   // 연결됐는데 값이 안 옴(채널 정지·배선 확인)
  }
  return '<span class="dim">SV만 배정 (PV 없음)</span>';
}
// live를 넘기면 캐시 갱신, undefined면 캐시 유지(telemetry 경로).
window.refreshMapStatus=function(live){
  if(live!==undefined) _lastLive=live;
  if(!setupOverlay || !setupOverlay.classList.contains('on')) return;   // 모달 닫혀 있으면 무시
  document.querySelectorAll('[data-mapst]').forEach(td=>{
    const c=channels.find(x=>x.id===td.dataset.mapst);
    if(c) td.innerHTML=mapStatusHtml(c);
  });
};
function loadPlcCatalog(){
  if(_plcCatalog) return Promise.resolve(_plcCatalog);
  return fetch('/plc_catalog').then(r=>{
    if(!r.ok) throw new Error('HTTP '+r.status);
    return r.json();
  }).then(j=>{_plcCatalog=j; return j;});
  // ★ 실패를 여기서 삼키지 않는다 — 빈 카탈로그로 폴백하면 통신 문제가
  //   '배정 오류'처럼 보여 현장에서 원인을 구분할 수 없다. buildMapRows가 안내를 띄운다.
}
// 카탈로그를 못 받았을 때: 배정이 잘못된 것으로 오인하지 않도록 분명히 알린다.
function showMapRowsError(){
  const tb=document.getElementById('setupMapRows'); if(!tb) return;
  tb.innerHTML='<tr class="dis"><td colspan="5">'
    +'채널 정보를 불러오지 못했습니다 — 서버 연결을 확인하세요'
    +' (배정이 잘못된 것이 아닙니다)</td></tr>';
}
function buildMapRows(){
  const tb=document.getElementById('setupMapRows'); if(!tb) return;
  const cat=_plcCatalog||{valve:{},dac:{},adc:{}};
  // 드롭다운 옵션: 미배정 + 카탈로그 이름(라벨에 레지스터 번호 병기).
  const opts=(map,cur,limit)=>{
    let h=`<option value=""${cur?'':' selected'}>— 미배정 —</option>`;
    Object.keys(map||{}).forEach(name=>{
      const m=map[name];
      if(limit && m.module && m.module>limit) return;   // 미장착 모듈 채널은 목록에서 제외
      h+=`<option value="${name}"${cur===name?' selected':''}>${name} (D${m.reg})</option>`;
    });
    return h;
  };
  const mods=cat.dac_modules||1;
  tb.innerHTML='';
  channels.forEach((c,i)=>{
    const p=c.plc; if(!p) return;
    const coil=(cat.valve||{})[c.id];
    const noSv=!p.sv_out;
    const tr=document.createElement('tr');
    tr.className=noSv?'dis':'';
    tr.innerHTML=`
      <td class="chid">${c.id}</td>
      <td>${coil!=null?('코일 '+coil):'—'}</td>
      <td><select data-svout="${i}">${opts(cat.dac,p.sv_out,mods)}</select></td>
      <td><select data-pvin="${i}">${opts(cat.adc,p.pv_in,0)}</select></td>
      <td data-mapst="${c.id}">${mapStatusHtml(c)}</td>`;
    tb.appendChild(tr);
  });
}
/* 아날로그 스케일 표(MFC ↔ PLC). plc 매핑 없는 채널은 행을 만들지 않는다. */
function buildScaleRows(){
  const tb=document.getElementById('setupScaleRows'); if(!tb) return;
  tb.innerHTML='';
  channels.forEach((c,i)=>{
    const p=c.plc; if(!p) return;
    const tr=document.createElement('tr');
    tr.className=c.en?'':'dis';
    tr.innerHTML=`
      <td class="chid">${c.id}</td>
      <td><input type="text" value="${p.fs_sccm??''}" data-sfs="${i}"></td>
      <td><input type="text" value="${p.sv_full??''}" data-svfull="${i}"></td>
      <td><input type="text" value="${p.pv_zero??''}" data-pvzero="${i}"></td>
      <td><input type="text" value="${p.pv_full??''}" data-pvfull="${i}"></td>`;
    tb.appendChild(tr);
  });
}
function openSetup(){
  buildSetupRows();
  // 카탈로그 도착 후 배정 표 렌더(드롭다운). 실패는 표에 명시한다(조용히 넘기지 않는다).
  loadPlcCatalog().then(()=>{ buildMapRows(); window.refreshMapStatus(undefined); }).catch(e=>{
    console.error('[plc_catalog] 조회 실패 — 배정 표를 표시할 수 없습니다', e);
    showMapRowsError();
  });
  buildScaleRows();
  if(window.cmdPlcPorts) window.cmdPlcPorts();   // 사용 가능한 시리얼 포트 목록 요청(드롭다운 채우기)
  window.plcSyncModeFields();                     // 연결 방식에 맞는 필드만 표시
  setupOverlay.classList.add('on');
}
// 연결 방식(시리얼/TCP)에 따라 관련 필드만 보이게 토글.
window.plcSyncModeFields=function(){
  const tcp=(document.getElementById('plcMode')?.value||'serial')==='tcp';
  document.querySelectorAll('.plc-serial-only').forEach(el=>el.style.display=tcp?'none':'');
  document.querySelectorAll('.plc-tcp-only').forEach(el=>el.style.display=tcp?'':'none');
};
document.getElementById('plcMode')?.addEventListener('change',()=>window.plcSyncModeFields());
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
// 채널 아날로그 스케일 검증(저장 전). 0으로 나누기·스케일 무효화·SV 포화를 막는다.
// collectSetup()이 만든 채널 배열을 그대로 받는다(scale은 plc 매핑 있는 채널에만 있다).
function validateScales(chans){
  for(const ch of chans){
    const sc=ch.scale; if(!sc) continue;
    const id=channels[ch.ch]?.id || ('CH'+(ch.ch+1));
    if(!(sc.fs_sccm>0))
      return {ok:false, msg:`${id}: 풀스케일(sccm)은 0보다 커야 합니다.`};
    if(!(sc.sv_full>0))
      return {ok:false, msg:`${id}: SV 풀카운트는 0보다 커야 합니다.`};
    if(!(sc.pv_full>0))
      return {ok:false, msg:`${id}: PV 풀카운트는 0보다 커야 합니다.`};
    if(!(sc.pv_full>sc.pv_zero))
      return {ok:false, msg:`${id}: PV 풀카운트(${sc.pv_full})는 영점카운트(${sc.pv_zero})보다 커야 합니다.`};
    if(ch.max>sc.fs_sccm)
      return {ok:false, msg:`${id}: MAX ${ch.max}이 풀스케일 ${sc.fs_sccm}을 초과합니다. `
        +'SV가 풀스케일에서 포화되어 화면 값과 실제 유량이 달라집니다.'};
  }
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
    const row={ch:i, en:enEl.checked, grp, route,
      max:parseFloat(mxEl.value)||0, sv:parseFloat(svEl.value)||0};
    // 스케일·배정은 plc 매핑이 있는 채널만. 밸브 코일은 서버(카탈로그)가 결정하므로 안 보낸다.
    if(c.plc){
      row.id=c.id;
      const svEl2=document.querySelector(`[data-svout="${i}"]`);
      const pvEl2=document.querySelector(`[data-pvin="${i}"]`);
      if(svEl2) row.sv_out=svEl2.value;
      if(pvEl2) row.pv_in=pvEl2.value;
      row.scale={
        fs_sccm: parseFloat(document.querySelector(`[data-sfs="${i}"]`)?.value) || 0,
        sv_full: parseInt(document.querySelector(`[data-svfull="${i}"]`)?.value) || 0,
        pv_zero: parseInt(document.querySelector(`[data-pvzero="${i}"]`)?.value) || 0,
        pv_full: parseInt(document.querySelector(`[data-pvfull="${i}"]`)?.value) || 0,
      };
    }
    chans.push(row);
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
    mode: document.getElementById('plcMode')?.value || 'serial',
    host: (document.getElementById('plcHost')?.value || '127.0.0.1').trim(),
    tcp_port: pint('plcTcpPort', 502),
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
  // PLC 통신·채널 스케일 검증 실패 시 저장 막고 경고 표시(모달 유지)
  const pv=validatePlc(plc);
  const v=pv.ok ? validateScales(chans) : pv;
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
