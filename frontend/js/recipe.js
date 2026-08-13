/* recipe.js — 레시피 표(procs) + System Setup 모달 */
// 수집 단계 파서 — validateSetupInputs/셀 검증 '게이트 뒤'에서만 쓴다(값은 이미 숫자 보장).
// parseFloat 부분 해석을 코드베이스에서 없애기 위한 일원화이며 동작은 불변이다.
const numOr = (raw, d) => { const v = window.strictNum(raw); return v === null ? d : v; };
// ★ Math.trunc 는 이전 parseInt 와 같은 결과를 내기 위한 것이다(동작 불변):
//   parseInt('150.25')=150 이므로 정수 정규식으로 거르면 0 이 되어 값이 바뀐다.
//   게이트를 통과하는 입력(=전체가 숫자)에서 이전 식과 결과가 완전히 같다.
const intOr = (raw, d) => { const v = window.strictNum(raw); return v === null ? d : Math.trunc(v); };
// from 행을 '삽입선 위치 ins(0..length)'로 옮긴다. 이동이 있었으면 true.
// ★ 먼저 뽑으면 뒤쪽 인덱스가 한 칸 당겨진다 — ins 를 보정하고 시작한다(커밋 29 결함).
function reorderProcs(arr, from, ins){
  if(ins>from) ins--;
  if(ins===from) return false;
  const [mv]=arr.splice(from,1);
  arr.splice(ins,0,mv);
  return true;
}
/* ===== System Setup modal ===== */
const setupOverlay=document.getElementById('setupOverlay');
function buildSetupRows(){
  const tb=document.getElementById('setupRows'); tb.innerHTML='';
  channels.forEach((c,i)=>{
    const gv = c.grp==='gas'?'gas':(c.route==='pure'?'pure-air':'mix-air');
    const tr=document.createElement('tr');
    tr.className=c.en?'':'dis';
    tr.innerHTML=`
      <td class="chid">${c.id}</td>
      <td><input type="checkbox" ${c.en?'checked':''} data-sen="${i}"></td>
      <td><select data-sgrp="${i}" title="희석 계산에 포함되는지를 결정합니다 — 배관 실물과 일치해야 합니다">
        <option value="pure-air" ${gv==='pure-air'?'selected':''}>Air · 단독(4-way 직행)</option>
        <option value="mix-air" ${gv==='mix-air'?'selected':''}>Air · 혼합(희석)</option>
        <option value="gas" ${gv==='gas'?'selected':''}>Gas</option>
      </select></td>
      <td><input type="text" value="${c.max}" data-smax="${i}"></td>`;
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
    // 밸브 열은 편집 대상이 아니다 — 래더의 이름표를 그대로 보여줘 XG5000 없이도 배선을 읽게 한다.
    const pout=(cat.valve_out||{})[c.id];
    const mAddr=(coil!=null)?('M00'+(100+(coil-160))):null;   // 160→M00100 … 167→M00107
    const valveCell=(coil==null)?'—'
      : pout ? `${c.id}_CMD (${mAddr}) → ${pout}`
             : `${c.id}_CMD (${mAddr}) — <span class="dim">미배선</span>`;
    const noSv=!p.sv_out;
    const tr=document.createElement('tr');
    tr.className=noSv?'dis':'';
    tr.innerHTML=`
      <td class="chid">${c.id}</td>
      <td class="mono">${valveCell}</td>
      <td><select data-svout="${i}">${opts(cat.dac,p.sv_out,mods)}</select></td>
      <td><select data-pvin="${i}">${opts(cat.adc,p.pv_in,0)}</select></td>
      <td data-mapst="${c.id}">${mapStatusHtml(c)}</td>`;
    tb.appendChild(tr);
  });
}
/* Setup 검증 메시지 — [취소][적용] 줄의 제일 좌측(#setupMsg).
   ★ 사유를 PLC 섹션 하단(plcNote)에 띄우면 표 위쪽 필드의 문제인지 알 수 없다.
     plcNote 는 정적 안내문 전용으로 되돌렸다 — 여기 말고 오류를 쓰지 말 것. */
function setSetupMsg(t){const el=document.getElementById('setupMsg'); if(el) el.textContent=t||'';}
// 배정 배너 표시/숨김 — 거부 사유와 사전 중복 경고를 모달 안에서 보여준다.
function showMapErr(text){
  const el=document.getElementById('mapErr'); if(!el) return;
  el.textContent=text; el.style.display='';
  setSetupMsg((text||'').split('\n')[0]);   // 푸터엔 첫 줄 요약(상세는 배너에 그대로)
}
function hideMapErr(){
  const el=document.getElementById('mapErr'); if(!el) return;
  el.textContent=''; el.style.display='none';
  setSetupMsg('');
}
// 폼 값만으로 중복을 미리 잡는다 — 서버 왕복 전에 보이게 해서 헛수고를 줄인다.
function checkMapDup(){
  const groups=[['[data-svout]','SV 출력'],['[data-pvin]','PV 입력']];
  const msgs=[];
  document.querySelectorAll('#setupMapRows select').forEach(s=>s.classList.remove('dup'));
  groups.forEach(([sel])=>{
    const seen={};
    document.querySelectorAll('#setupMapRows '+sel).forEach(el=>{
      const v=el.value; if(!v) return;
      (seen[v]=seen[v]||[]).push(el);
    });
    Object.keys(seen).forEach(name=>{
      if(seen[name].length<2) return;
      const ids=seen[name].map(el=>{
        const i=+(el.dataset.svout!==undefined?el.dataset.svout:el.dataset.pvin);
        return (channels[i]||{}).id||('CH'+(i+1));
      });
      seen[name].forEach(el=>el.classList.add('dup'));
      msgs.push(`${name}: ${ids.join('·')} 중복 — 적용 시 거부됩니다`);
    });
  });
  _mapHasDup = msgs.length>0;
  if(msgs.length) showMapErr(msgs.join('\n')); else hideMapErr();
  updateApplyGate();
}
/* ── 적용 버튼 게이트 ───────────────────────────────────────────────
   바뀐 게 없거나 중복 배정이 남아 있으면 누를 수 없게 한다.
   서버의 거부·배너는 백스톱으로 그대로 둔다(프론트 판정이 틀려도 안전하게). */
let _setupSnap=null, _mapHasDup=false;
/* 스냅샷은 '사용자가 바꿀 수 있는 값'만 명시적으로 모은다.
   collectSetup() 전체를 직렬화하면 상태 열·PV 같은 동적 표시가 섞여 들어와
   0.7초 상태 푸시마다 dirty 로 잡힌다(오탐). 여기 나열한 필드만 비교한다. */
function snapSetup(){
  try{
    const v=sel=>{const e=document.querySelector(sel); return e?(e.type==='checkbox'?!!e.checked:String(e.value)):null;};
    const id=x=>{const e=document.getElementById(x); return e?(e.type==='checkbox'?!!e.checked:String(e.value)):null;};
    const chans=channels.map((c,i)=>({
      id:c.id,
      en:v(`[data-sen="${i}"]`), grp:v(`[data-sgrp="${i}"]`), max:v(`[data-smax="${i}"]`),
      sv_out:v(`[data-svout="${i}"]`), pv_in:v(`[data-pvin="${i}"]`),
      fs:v(`[data-sfs="${i}"]`), svfull:v(`[data-svfull="${i}"]`),
      pvzero:v(`[data-pvzero="${i}"]`), pvfull:v(`[data-pvfull="${i}"]`),
    }));
    return JSON.stringify({chans,
      log:[id('logEnabled'),id('logDir'),id('logLevel'),id('logKeepDays')],
      plc:['plcMode','plcHost','plcTcpPort','plcPort','plcBaud','plcBytesize','plcStopbits',
           'plcParity','plcUnitId','plcTimeout','plcGap','plcHeartbeat','plcReconnect'].map(id)});
  }catch(e){ return null; }
}
function updateApplyGate(){
  const b=document.getElementById('applySetup'); if(!b) return;
  const dirty = _setupSnap!==null && snapSetup()!==_setupSnap;
  b.disabled = !(dirty && !_mapHasDup);
  b.title = _mapHasDup ? '중복 배정을 해소하세요'
          : (!dirty ? '변경 사항 없음'
                    : '변경한 설정을 저장합니다 — 통신 설정은 저장 후 재연결해야 적용됩니다');
  // 시각·접근성만 반영(위 disabled 판정은 그대로).
  //  · 감싼 span 이 같은 문구를 들어 비활성일 때도 툴팁이 뜬다(disabled 버튼은 툴팁이 안 뜬다).
  //  · aria-disabled 로 스크린리더에도 '누를 수 없음'을 알린다.
  const w=b.parentElement;
  if(w&&w.classList.contains('applywrap')){
    w.title=b.title;
    w.classList.toggle('off', b.disabled);
  }
  b.setAttribute('aria-disabled', b.disabled ? 'true' : 'false');
}
document.addEventListener('change', e=>{
  if(e.target && e.target.closest && e.target.closest('#setupMapRows')) checkMapDup();
});
// 모달 안의 모든 입력 변화 → 게이트 재계산(위임이라 표가 다시 그려져도 유효하다).
// 사용자의 입력만 게이트를 다시 계산한다(서버 상태 푸시는 input/change 를 발생시키지 않는다).
['input','change'].forEach(t=>setupOverlay.addEventListener(t, e=>{
  if(e.target&&/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) updateApplyGate();
}));
// 서버 판정(ack) 처리 — ok면 닫고, 거부면 모달을 유지한 채 사유를 보여준다.
window.onSetupAck=function(msg){
  clearTimeout(window._setupPending);
  if(msg && msg.ok){ setSetupMsg(''); closeSetup(); return; }
  const probs=(msg&&msg.problems)||[];
  showMapErr(probs.length?probs.join('\n'):'설정이 거부되었습니다 — System Log 를 확인하세요');
  buildMapRows();      // 드롭다운을 서버 상태(원래 값)로 되돌린다
  checkMapDup();       // 되돌린 값 기준으로 중복·게이트 재판정
};
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
/* Setup 모달 입력의 브라우저 자동완성을 끈다.
   ★ 실제 사고: PLC Port 칸이 저절로 502 → 5024 → 5025 로 바뀌었다. 서버가 덮어쓴 게 아니라
     브라우저가 과거 입력 이력을 후보로 띄우고, 그게 확정되면서 저장까지 된 것이다.
   ★ 크로미움은 autocomplete="off" 를 무시할 때가 있다 — 이력 매칭의 열쇠인 name 을
     'setup-<id>' 같은 고유값으로 바꿔 매칭 자체를 끊는 것이 핵심이다.
   ★ 동적으로 그려지는 배정·스케일 표에도 적용되도록 표를 만든 뒤에도 부른다. */
function hardenSetupInputs(){
  if(!setupOverlay) return;
  setupOverlay.querySelectorAll('input,textarea').forEach(el=>{
    el.setAttribute('autocomplete','off');
    el.setAttribute('autocorrect','off');
    el.setAttribute('autocapitalize','off');
    el.setAttribute('spellcheck','false');
    if(!el.name || !el.name.startsWith('setup-'))
      el.name = 'setup-' + (el.id || el.getAttribute('data-sfs') || Math.random().toString(36).slice(2));
  });
  // 숫자 필드: 모바일/터치 키패드 + 입력 의도 명시(값 검증은 collectSetup 이 한다).
  ['plcUnitId','plcTcpPort','plcTimeout','plcGap','plcHeartbeat','plcReconnect','logKeepDays']
    .forEach(id=>{const e=document.getElementById(id); if(e) e.setAttribute('inputmode','numeric');});
}
function openSetup(){
  // 모달이 열려 있는 동안에는 applyState 가 폼을 덮지 않는다(편집 롤백 방지) —
  //   대신 여는 순간 1회만 최신 서버값으로 채운다. 게이트 스냅샷은 이 뒤에 찍힌다.
  if(window.fillSetupForms && window._lastStateForSetup)
    window.fillSetupForms(window._lastStateForSetup);
  buildSetupRows();
  // 카탈로그 도착 후 배정 표 렌더(드롭다운). 실패는 표에 명시한다(조용히 넘기지 않는다).
  hideMapErr();
  setSetupMsg('');            // 지난 번 사유가 남아 있지 않게
  // 스냅샷은 표가 모두 그려진 뒤에 찍는다 — 그 전에 찍으면 렌더 자체가 '변경'으로 잡힌다.
  _setupSnap=null; _mapHasDup=false; updateApplyGate();
  loadPlcCatalog().then(()=>{
    buildMapRows(); window.refreshMapStatus(undefined);
    hardenSetupInputs();                        // 표가 그려진 뒤 새 입력에도 적용
    _setupSnap=snapSetup(); checkMapDup();      // checkMapDup 안에서 게이트가 갱신된다
  }).catch(e=>{
    console.error('[plc_catalog] 조회 실패 — 배정 표를 표시할 수 없습니다', e);
    showMapRowsError();
    _setupSnap=snapSetup(); updateApplyGate();
  });
  buildScaleRows();
  hardenSetupInputs();
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
    if(!enEl||!gvEl||!mxEl) return;
    const gv=gvEl.value;
    let grp='air', route='pure';
    if(gv==='gas'){grp='gas';route='mix';}
    else if(gv==='mix-air'){grp='air';route='mix';}
    else {grp='air';route='pure';}
    // ★ sv 는 보내지 않는다 — 현재 유량은 배관도 카드에서 조작한다(Setup의 SV 칸은 레거시).
    //   서버 apply_setup 은 키가 없으면 기존 값을 유지한다.
    const row={ch:i, en:enEl.checked, grp, route,
      max:numOr(mxEl.value, 0)};
    // 스케일·배정은 plc 매핑이 있는 채널만. 밸브 코일은 서버(카탈로그)가 결정하므로 안 보낸다.
    if(c.plc){
      row.id=c.id;
      const svEl2=document.querySelector(`[data-svout="${i}"]`);
      const pvEl2=document.querySelector(`[data-pvin="${i}"]`);
      if(svEl2) row.sv_out=svEl2.value;
      if(pvEl2) row.pv_in=pvEl2.value;
      row.scale={
        fs_sccm: numOr(document.querySelector(`[data-sfs="${i}"]`)?.value, 0),
        sv_full: intOr(document.querySelector(`[data-svfull="${i}"]`)?.value, 0),
        pv_zero: intOr(document.querySelector(`[data-pvzero="${i}"]`)?.value, 0),
        pv_full: intOr(document.querySelector(`[data-pvfull="${i}"]`)?.value, 0),
      };
    }
    chans.push(row);
  });
  // 측정 관련 파라미터(vStart/vEnd/grafInterval/smuCompliance)는 소비처가 없어 수집하지 않는다
  // — 죽은 값을 config에 저장하면 나중에 '설정했는데 왜 안 되나' 오해를 부른다.
  //   backend의 params 키 자체는 스냅샷 하위호환으로 남아 있다.
  const params={};
  /* 숫자 파서 — 범위 밖/해석 불가 값을 '조용히' 기본값으로 되돌리지 않고 경고를 남긴다.
     조용히 되돌리면 사용자는 자기가 넣은 값이 적용된 줄 알고, 나중에 통신이 안 되는 이유를
     찾지 못한다(자동완성이 엉뚱한 포트를 넣었던 사고와 같은 계열의 문제다).
     빈 칸은 '지우고 기본값으로' 라는 의도로 보고 경고하지 않는다. */
  const _fix=(label,raw,d)=>{
    if(window.logMsg) window.logMsg(`${label} 값이 올바르지 않습니다 — ${d} 로 되돌림 (입력: "${raw}")`,'warn');
    return d;
  };
  const _pick=(id,d,min,max,label,parse)=>{
    const el=document.getElementById(id);
    const raw=(el?.value ?? '').trim();
    if(raw==='') return d;                       // 빈 칸 → 기본값(경고 없음)
    const v=parse(raw);
    if(isNaN(v)) return _fix(label,raw,d);
    if(min!=null && v<min) return _fix(label,raw,d);
    if(max!=null && v>max) return _fix(label,raw,d);
    return v;
  };
  // ★ 전체가 숫자일 때만 받는다 — parseFloat('1.5x')=1.5 처럼 부분 해석되면
  //   운전자가 넣지 않은 값이 조용히 저장된다.
  const pnum=(id,d,min,max,label)=>_pick(id,d,min,max,label||id,
    s=>{const v=window.strictNum(s); return v===null?NaN:v;});
  const pint=(id,d,min,max,label)=>_pick(id,d,min,max,label||id,
    s=>/^[+-]?\d+$/.test(s.trim())?parseInt(s,10):NaN);
  const settings={
    logEnabled: !!document.getElementById('logEnabled')?.checked,
    logDir: (document.getElementById('logDir')?.value || 'logs').trim(),
    logLevel: document.getElementById('logLevel')?.value || 'info',
    logKeepDays: pint('logKeepDays', 30, 1, 3650, '로그 보관일수'),
  };
  const plc={
    mode: document.getElementById('plcMode')?.value || 'serial',
    host: (document.getElementById('plcHost')?.value || '127.0.0.1').trim(),
    tcp_port: pint('plcTcpPort', 502, 1, 65535, '포트'),
    port: (document.getElementById('plcPort')?.value || '').trim(),
    baudrate: pint('plcBaud', 115200, 1200, 921600, '통신 속도'),
    bytesize: pint('plcBytesize', 8, 5, 8, '데이터 비트'),
    stopbits: pint('plcStopbits', 1, 1, 2, '정지 비트'),
    parity: document.getElementById('plcParity')?.value || 'N',
    unit_id: pint('plcUnitId', 1, 1, 247, '국번'),
    // ★ 요청 1건이 락을 (timeout + gap) 만큼 잡는다 — 그동안 하트비트가 밀리면
    //   PLC COMM_TMR(3초) 트립이 난다. 상한을 그 안쪽으로 묶는다.
    timeout_s: pnum('plcTimeout', 1.5, 0.1, 2.5, 'Timeout(s)'),
    inter_cmd_gap_s: pnum('plcGap', 0.1, 0, 1.0, 'Cmd Gap(s)'),
    // ★ 하트비트는 PLC COMM_TMR(3초) 미만이어야 통신두절 트립을 막는다.
    heartbeat_s: pnum('plcHeartbeat', 1.0, 0.1, 2.5, 'Heartbeat(s)'),
    reconnect_delay_s: pnum('plcReconnect', 1.0, 0.1, 60, 'Reconnect(s)'),
  };
  return {channels:chans, params, settings, plc};
}
/* 입력 '원문' 검증 — 숫자 여부 + 범위까지 여기서 본다.
   ★ collectSetup 의 pnum/pint 는 범위를 벗어나면 기본값으로 조용히 대체한다(백스톱).
     그대로 두면 Heartbeat=5 가 1.0 으로 바뀐 뒤 검증(<3)을 통과해 '적용은 됐는데
     다시 열면 1' 이 된다 — 사용자는 모달 밖 경고를 보지 못한다. 원문 단계에서 거부한다.
   ★ 빈칸은 허용(기본값 의도), 숨겨진 필드(연결 방식에 따라 감춰진 TCP/시리얼 섹션)는 건너뛴다. */
function validateSetupInputs(){
  // 1) 채널 표: 비숫자 거부(값이 채널 동작에 직결)
  const fields=[['data-smax','MAX'],['data-sfs','풀스케일(sccm)'],
    ['data-svfull','SV 풀카운트'],['data-pvzero','PV 영점'],['data-pvfull','PV 풀카운트']];
  for(const [attr,label] of fields){
    for(const el of document.querySelectorAll(`[${attr}]`)){
      const raw=(el.value||'').trim(); if(raw==='') continue;   // 빈칸=기본/기존값 의도
      if(window.strictNum(raw)===null){
        const i=+el.getAttribute(attr);
        const id=(channels[i]||{}).id||('CH'+(i+1));
        return {ok:false, msg:`${id} ${label}: "${raw}" 은(는) 숫자가 아닙니다`};
      }
    }
  }
  // 2) 통신·로그 숫자 필드: 숫자 여부 + 범위
  // ★ backend plc.PLC_COMM_LIMITS 와 페어 — 바꾸면 양쪽 함께
  const nums=[
    ['plcTcpPort',   true,  1,   65535, '포트',        ''],
    ['plcUnitId',    true,  1,   247,   '국번',        ''],
    ['plcTimeout',   false, 0.1, 2.5,   'Timeout(s)',  ''],
    ['plcGap',       false, 0,   1.0,   'Cmd Gap(s)',  ''],
    ['plcHeartbeat', false, 0.1, 2.5,   'Heartbeat(s)','(PLC 통신감시 3초 미만)'],
    ['plcReconnect', false, 0.1, 60,    'Reconnect(s)',''],
    ['logKeepDays',  true,  1,   3650,  '로그 보관일수',''],
  ];
  for(const [id, isInt, min, max, label, extra] of nums){
    const el=document.getElementById(id);
    if(!el || el.offsetParent===null) continue;      // 없거나 숨겨진 필드는 건너뛴다
    const raw=(el.value||'').trim(); if(raw==='') continue;
    const v=isInt ? (/^[+-]?\d+$/.test(raw) ? parseInt(raw,10) : null) : window.strictNum(raw);
    if(v===null) return {ok:false,
      msg:`${label}: "${raw}" 은(는) ${isInt?'정수가':'숫자가'} 아닙니다`};
    if(v<min || v>max) return {ok:false,
      msg:`${label}: "${raw}" — ${min}~${max} 범위여야 합니다${extra}`};
  }
  return {ok:true};
}
function applySetup(){
  setSetupMsg('');                        // 이전 사유 지우고 시작
  // ★ collectSetup 전에 원문을 본다 — 숫자·범위가 어긋나면 아예 수집하지 않는다
  //   (pnum/pint 의 조용한 기본값 대체가 끼어들 자리를 없앤다).
  const nv=validateSetupInputs();
  if(!nv.ok){ setSetupMsg(nv.msg); return; }
  const {channels:chans, params, settings, plc}=collectSetup();
  // PLC 통신·채널 스케일 검증(백스톱) 실패 시 저장 막고 사유 표시(모달 유지)
  const pv=validatePlc(plc);
  const v=pv.ok ? validateScales(chans) : pv;
  if(!v.ok){ setSetupMsg(v.msg); return; }
  hideMapErr();
  window.cmdApplySetup(chans, params, settings, plc);
  // sync a few process params into the Auto Process panel inputs for immediate feedback
  const set=(id,el)=>{const a=document.getElementById(id),b=document.getElementById(el);if(a&&b)b.value=a.value;};
  set('setVStart','vStart'); set('setVEnd','vEnd'); set('setGraf','grafInt'); set('setLoop','loopCount'); set('setComp','smuComp');
  // ★ 즉시 닫지 않는다 — 서버가 거부하면 모달 안에서 사유를 봐야 한다(onSetupAck가 닫는다).
  clearTimeout(window._setupPending);
  window._setupPending=setTimeout(()=>{
    showMapErr('서버 응답 없음 — System Log 를 확인하세요');
  }, 2000);
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
    tr.dataset.row=i;
    // 퍼지 단계는 G1~G4·RH 를 쓰지 않는다 — 값은 지우지 않고 흐림+입력 잠금만
    // (종류를 가스로 되돌리면 그대로 복원된다).
    // ★ 준비 칸은 퍼지에서도 활성이다 — 엔진이 준비+측정을 합산해 한 구간으로 돈다.
    const pg = r.type==='purge';
    const off = pg ? ' cell-off' : '';
    const dis = pg ? ' disabled' : '';
    const gcells=r.g.map((v,gi)=>`<td class="${v===0?'zero':''}${off}"><input class="ci" value="${v}" data-g="${i}-${gi}"${dis}></td>`).join('');
    tr.innerHTML=`
      <td class="pcol" draggable="true" data-drag="${i}" title="드래그해서 순서 변경">P${i+1}</td>
      <td><select class="ptype" data-type="${i}">
        <option value="gas"${!pg?' selected':''}>Gas→Sensor</option>
        <option value="purge"${pg?' selected':''}>Air→Sensor</option>
      </select></td>
      <td><input class="ci" value="${r.flow}" data-f="flow-${i}"${pg?' title="퍼지 단계: 단독 에어 유량(sccm)"':''}></td>
      <td class="humcol${off}" ${useHum?'':'style="display:none"'}><input class="ci" value="${r.rh}" data-f="rh-${i}"${dis}></td>
      ${gcells}
      <td><input class="ci" value="${r.prep}" data-f="prep-${i}"></td>
      <td><input class="ci" value="${r.meas}" data-f="meas-${i}"></td>
      <td><button class="delrow" data-del="${i}">×</button></td>`;
    recipeBody.appendChild(tr);
  });
  bindRecipe();
  window.markRunningStep(_curStep);   // 실행 중 재렌더에도 하이라이트 유지
}
function bindRecipe(){
  // ★ 셀에 비숫자가 들어오면 조용히 0 으로 바꾸지 않는다 — '5OO' 이 0 이 되면
  //   그 단계의 가스가 통째로 빠진 채 실행된다. 경고하고 이전 값으로 되돌린다.
  const cellReject=(el,label,prev)=>{
    window.logMsg(`${label}: "${el.value}" — 숫자가 아니므로 되돌렸습니다`, 'warn');
    el.value=prev;
  };
  recipeBody.querySelectorAll('[data-g]').forEach(inp=>inp.addEventListener('change',e=>{
    const [i,gi]=e.target.dataset.g.split('-').map(Number);
    const v=window.strictNum(e.target.value);
    if(v===null){ cellReject(e.target, `P${i+1} 가스${gi+1}`, procs[i].g[gi]); return; }
    procs[i].g[gi]=v;
    e.target.parentElement.classList.toggle('zero', v===0);
  }));
  recipeBody.querySelectorAll('[data-f]').forEach(inp=>inp.addEventListener('change',e=>{
    const [k,i]=e.target.dataset.f.split('-');
    const v=window.strictNum(e.target.value);
    if(v===null){ cellReject(e.target, `P${(+i)+1} ${k}`, procs[+i][k]); return; }
    procs[+i][k]=v;
  }));
  recipeBody.querySelectorAll('[data-type]').forEach(s=>s.addEventListener('change',e=>{
    procs[+e.target.dataset.type].type=e.target.value;
    renderRecipe();      // 퍼지↔가스에 따라 흐림·잠금이 달라지므로 행을 다시 그린다
  }));
  recipeBody.querySelectorAll('[data-del]').forEach(b=>b.addEventListener('click',e=>{procs.splice(+e.target.dataset.del,1);renderRecipe();}));
  // P열을 잡아 행 순서를 바꾼다(HTML5 DnD). 번호(P1·P2…)는 재렌더로 자동 재부여된다.
  let dragFrom=null;
  const clearDropMarks=()=>recipeBody.querySelectorAll('tr').forEach(t=>
    t.classList.remove('drop-before','drop-after'));
  recipeBody.querySelectorAll('td.pcol').forEach(h=>{
    h.addEventListener('dragstart',e=>{
      if(window._sys&&window._sys.running){e.preventDefault();return;}   // 실행 중 금지
      dragFrom=+h.dataset.drag; e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain',String(dragFrom));
    });
    h.addEventListener('dragend',()=>{clearDropMarks(); dragFrom=null;}); // 취소·이탈 정리
  });
  recipeBody.querySelectorAll('tr').forEach(tr=>{
    tr.addEventListener('dragover',e=>{
      e.preventDefault();
      const r=tr.getBoundingClientRect();
      const after=e.clientY > r.top + r.height/2;      // 행 중점 아래면 '뒤에 삽입'
      clearDropMarks();
      tr.classList.add(after?'drop-after':'drop-before');
    });
    tr.addEventListener('drop',e=>{
      e.preventDefault();
      const after=tr.classList.contains('drop-after');
      const ins=+tr.dataset.row + (after?1:0);
      clearDropMarks();
      if(dragFrom==null){return;}
      const moved=reorderProcs(procs, dragFrom, ins);
      dragFrom=null;
      if(moved) renderRecipe();
    });
  });
}
/* 실행 중인 단계 하이라이트 — telemetry 의 stepIndex(1-base)를 받는다. 0 이면 해제. */
let _curStep=0;
window.markRunningStep=idx=>{
  _curStep=idx|0;
  recipeBody.querySelectorAll('tr').forEach((tr,k)=>
    tr.classList.toggle('runrow',_curStep>0&&k===_curStep-1));
};
// 봄베 농도·Loop Count 도 같은 규칙 — 비숫자는 조용히 0/1 로 바뀌지 않게 한다.
//   봄베가 0 이 되면 희석 계산이 통째로 어긋나고, Loop 가 0 이 되면 실행이 즉시 끝난다.
[0,1,2,3].forEach(i=>{
  const el=document.getElementById('b'+i);
  el?.addEventListener('change',()=>{
    const raw=(el.value||'').trim();
    if(raw!=='' && window.strictNum(raw)===null){
      window.logMsg(`봄베 ${i+1} 농도: "${raw}" — 숫자가 아니므로 지웠습니다`, 'warn');
      el.value='';
    }
    // 봄베는 '장비에 물린 실물' 이라 레시피와 별개로 서버(config.json)에 남긴다 —
    // 재기동해도 괄호 안 값이 유지된다. 검증을 통과한 값만 보낸다(비숫자는 위에서 비움).
    if(window.cmdSetBottle){
      window.cmdSetBottle([0,1,2,3].map(k=>numOr(document.getElementById('b'+k)?.value, 0)));
    }
  });
});
(()=>{
  const el=document.getElementById('loopCount');
  let prev=el?el.value:'1';
  el?.addEventListener('change',()=>{
    const raw=(el.value||'').trim();
    if(/^\d+$/.test(raw)){ prev=raw; return; }   // 정수만 허용
    window.logMsg(`Loop Count: "${raw}" — 정수가 아니므로 되돌렸습니다`, 'warn');
    el.value=prev;
  });
})();
document.getElementById('addProc').addEventListener('click',()=>{
  // 표 편집은 로컬 초안(draft). 저장(Save as) 시 서버로 레시피 전체를 보낸다.
  procs.push({type:'gas', flow:1000, rh:40, g:[0,0,0,0], prep:600, meas:300}); renderRecipe();
});
document.getElementById('useHumidity').addEventListener('change',renderRecipe);

/* 레시피 New/Open/Save as → app.js 명령 */
document.getElementById('recNew')?.addEventListener('click',()=>window.cmdRecipeNew());
document.getElementById('recOpen')?.addEventListener('click',()=>window.cmdRecipeList());
// 이름은 저장할 때만 묻는다(기본값=현재 레시피 이름) — 상단 이름칸은 없앴다.
document.getElementById('recSave')?.addEventListener('click',()=>{
  // backend storage.valid_recipe_name 과 같은 규칙 요약 — 창을 닫기 전에 걸러
  //   '눌렀는데 아무 일도 없다'를 없앤다(서버 검증은 백스톱으로 그대로 남는다).
  const nameErr = v=>{
    const n=(v||'').trim();
    if(!n) return '이름을 입력하세요';
    if(/[<>:"|?*/\\]/.test(n)) return '다음 문자는 쓸 수 없습니다: < > : " | ? * / \\';
    if(n!==n.replace(/[ .]+$/,'')) return '이름 끝에 공백이나 점(.)을 둘 수 없습니다';
    if(/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i.test(n)) return 'Windows 예약어는 쓸 수 없습니다 (CON, PRN, AUX, NUL, COM1~9, LPT1~9)';
    if(n.length>80) return '이름이 너무 깁니다 (80자 이내)';
    return null;
  };
  window.appPrompt('레시피 이름을 입력하세요', window._recipeName || '', v=>{
    const name=(v||'').trim();
    const r=(typeof collectRecipe==='function')?collectRecipe():window.collectRecipe();
    r.name=name;
    // 이후는 기존 경로 그대로 — exists면 덮어쓰기 확인, invalid면 사유 표시(app.js ack).
    window.cmdRecipeSave(name, r, false);
  }, '레시피 저장', nameErr);
});

/* 현재 화면의 레시피 초안을 INTERFACE 3.3 형식으로 수집 */
function collectRecipe(){
  const name=(window._recipeName||'').trim();   // 이름은 Save as 에서만 정한다
  const useHumidity=document.getElementById('useHumidity').checked;
  const loopCount=+(document.getElementById('loopCount')?.value)||0;
  const num=id=>numOr(document.getElementById(id)?.value, 0);
  const bottle=[0,1,2,3].map(i=>numOr(document.getElementById('b'+i)?.value, 0));
  const params={
    vStart:num('vStart'), vEnd:num('vEnd'), vStep:num('vStep'),
    grafInterval:num('grafInt'),
    smuMode:document.getElementById('smuMode')?.value||'Source V, Measure I',
    smuSource:num('smuSrc'), smuCompliance:num('smuComp'),
    chFrom:num('chFrom'), chTo:num('chTo'),
  };
  return {name, useHumidity, loopCount, bottle,
    procs:procs.map(p=>Object.assign({},p,{type:p.type||'gas', g:(p.g||[0,0,0,0]).slice()})), params};
}

// 숫자/텍스트 입력 포커스 시 전체 선택 — 기존 값 위에 바로 타이핑해 교체할 수 있게
document.addEventListener('focusin', e=>{
  const t=e.target;
  if(t && t.tagName==='INPUT' && !t.disabled && !t.readOnly
     && t.type!=='checkbox' && t.type!=='radio'){
    setTimeout(()=>{ try{ t.select(); }catch(_){} }, 0);
  }
});
