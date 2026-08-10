/* schematic.js — 배관도(채널/밸브) 렌더 + 이벤트 + drawBuses.
   다른 파일 함수는 window.* 노출분을 사용. 전역 노출/초기화는 core.js가 담당. */
/* ===================== channel model ===================== */
// 초기값은 모두 0 / 비어 있음. 실제 값은 서버 state(또는 config 로드)가 채운다.
// en은 전부 false로 시작한다 — 서버 state가 오기 전 첫 화면이 '채널이 켜진 것처럼'
// 보이면 안 된다. pv도 null(=화면 '—')로 두고 실측이 올 때만 숫자를 그린다.
let channels = [
  // 실배관: VA1=가스 매니폴드에 합류하는 혼합(희석) 라인, VA3=4-way로 직행하는 단독 라인.
  //   물 라인은 위치 기준 **잠정** 페어링 — VA2=단독측 가습, VA4=혼합측 가습(배관 확정 시 재검토).
  //   route 값은 백엔드(state.py/config.json)와 반드시 같아야 한다 — 배관도 선이 이걸 따라 그려진다.
  {grp:'air', route:'mix',  max:2000, sv:0, pv:null, en:false},  // VA1
  {grp:'air', route:'pure', max:2000, sv:0, pv:null, en:false},  // VA2 (물1, 잠정)
  {grp:'air', route:'pure', max:2000, sv:0, pv:null, en:false},  // VA3
  {grp:'air', route:'mix',  max:2000, sv:0, pv:null, en:false},  // VA4 (물2, 잠정)
  {grp:'gas', route:'mix',  max:2000, sv:0, pv:null, en:false},  // VA5
  {grp:'gas', route:'mix',  max:200,  sv:0, pv:null, en:false},  // VA6
  {grp:'gas', route:'mix',  max:200,  sv:0, pv:null, en:false},  // VA7
  {grp:'gas', route:'mix',  max:100,  sv:0, pv:null, en:false},  // VA8
];
// derive display fields (label/color/sub) from group — does NOT reorder.
// 서버가 채널 인덱스/순서/id의 주인이므로 화면은 받은 순서를 그대로 쓴다.
function deriveDisplay(){
  let gasN=0;
  channels.forEach((c,i)=>{
    if(!c.id) c.id='VA'+(i+1);
    c.color = c.grp==='gas' ? 'var(--g1)' : 'var(--air)';
    if(c.grp==='gas'){ gasN++; c.label='Gas '+gasN; c.sub=''; }
    else { c.label='Air'; c.sub = c.route==='pure'?'단독 (4-way 직행)':'혼합 (희석)'; }
  });
}
// 초기 로컬 기본값만 그룹 순으로 정렬(서버 연결 전 한 번). 서버 state 반영 시엔 정렬하지 않는다.
function relabel(){
  // ★ air 안에서는 순서를 바꾸지 않는다 — route로 정렬하면 단독 라인(VA3)이 맨 앞으로
  //   올라가 id가 VA1로 재부여된다(서버 state 도착 전 첫 화면이 실물과 어긋난다).
  const rank=c=> c.grp==='gas' ? 1 : 0;
  channels.sort((a,b)=>rank(a)-rank(b));
  channels.forEach((c,i)=>{ c.id='VA'+(i+1); });
  deriveDisplay();
}
relabel();
channels.forEach(c=>{c.valveIn=c.en;});
// PLC 안전정지 여부(연결 + SAFETY_STOP). PLC 래더의 RUN_PERMIT=!SAFETY_STOP와 대응.
function plcSafeStop(){ const L=window.plcLive; return !!(L&&L.connected&&L.status&&L.status.SAFETY_STOP===true); }
// 유효 열림 = 명령(valveIn) ON 이고 안전정지 아님 → 래더의 'valve = CMD AND RUN_PERMIT'와 동일.
const eff=c=>c.en&&c.valveIn&&!plcSafeStop();
const flowing=c=>eff(c);

const valveSvg = `<svg width="34" height="22" viewBox="0 0 34 22">
  <line class="vstem" x1="17" y1="11" x2="17" y2="4"/><rect class="vact" x="12" y="0" width="10" height="5" rx="1"/>
  <path class="vb" d="M4 5 L17 11 L4 17 Z"/><path class="vb" d="M30 5 L17 11 L30 17 Z"/></svg>`;
const mfcSvg = `<svg width="40" height="34" viewBox="0 0 40 34">
  <rect class="mb" x="2" y="3" width="36" height="22" rx="3" transform="rotate(-9 20 14)"/>
  <rect class="mt" x="7" y="6" width="26" height="8" rx="1.5" transform="rotate(-9 20 10)"/>
  <text class="mtxt" x="11" y="12" transform="rotate(-9 20 10)">Tylan</text>
  <path class="ar" d="M9 22 H29 M25 18 L29 22 L25 26"/></svg>`;
const tankSvg = c => `<svg width="30" height="40" viewBox="0 0 24 32">
  <rect x="4" y="5" width="16" height="25" rx="4" style="fill:var(--bg2)"/>
  <rect x="7" y="2" width="10" height="3" rx="1.5" fill="${c}"/>
  <rect x="4" y="5" width="16" height="25" rx="4" fill="none" stroke="${c}" stroke-width="1.5"/>
  <path d="M5 17 q3 -2 6 0 t6 0 v9 a1.5 1.5 0 0 1 -1.5 1.5 h-9 a1.5 1.5 0 0 1 -1.5 -1.5 z" fill="${c}" opacity="0.28"/>
  <path d="M5 17 q3 -2 6 0 t6 0" fill="none" stroke="${c}" stroke-width="1.2" opacity="0.75"/>
  <circle cx="9" cy="22" r="1.1" fill="${c}" opacity="0.6"/>
  <circle cx="13" cy="25" r="0.9" fill="${c}" opacity="0.5"/>
</svg>`;

const lanesEl = document.getElementById('lanes');
function dec(c){return c.max<=100?1:0;}
/* 배관도의 '세로 표시 순서'. 실물 재배관(VA1=혼합, VA3=단독)에서 교차선이 생기지 않도록
   단독(VA3)은 위, 혼합(VA1)은 가스군 바로 위에 둔다.
   ★ 표시 순서일 뿐이다 — 채널 id·내부 인덱스·data-* 매핑은 그대로다(카드 조작은 원래 인덱스로 간다).
     Setup 표·레시피·서버 데이터는 계속 ID 순서를 쓴다. */
const LANE_ORDER=['VA2','VA3','VA1','VA4','VA5','VA6','VA7','VA8'];
// 표시 순서 → [원래 채널 인덱스, 채널] 목록. 목록에 없는 채널은 뒤에 원래 순서대로 붙인다.
function lanesInDisplayOrder(){
  const used=new Set(), out=[];
  LANE_ORDER.forEach(id=>{
    const i=channels.findIndex((c,n)=>c.id===id&&!used.has(n));
    if(i>=0){ used.add(i); out.push([i, channels[i]]); }
  });
  channels.forEach((c,i)=>{ if(!used.has(i)) out.push([i,c]); });
  return out;
}
function renderLanes(){
  lanesEl.innerHTML='';
  lanesInDisplayOrder().forEach(([idx,c])=>{
    const d=dec(c);
    const lane=document.createElement('div');
    lane.className='lane'+(flowing(c)&&c.pv>0?' lit':'')+(c.en?'':' off');
    lane.dataset.grp=c.grp; lane.dataset.idx=idx;
    const showLabel = '';
    // 물탱크(가습기): VA2·VA4 레인에만 그리고, 나머지는 같은 폭의 빈 자리로 둬 MFC 정렬을 맞춘다.
    const hasTank = (c.id==='VA2'||c.id==='VA4');
    // SV 출력 미배정 경고 배지: 밸브는 열려도 유량 지령이 나갈 곳이 없다(config.json의 sv_out).
    // 역할 문구: deriveDisplay()가 만든 label/sub와 hasTank(물탱크)를 그대로 쓴다.
    //   새 표를 만들면 핀맵·그룹이 바뀔 때 어긋나므로 파생값만 조합한다.
    const roleLabel = c.label + (c.sub ? ' · ' + c.sub.replace(/\s*\(.*\)$/, '') : '')
                              + (hasTank ? ' · 가습' : '');
    const noSv = !!(c.plc && !c.plc.sv_out && c.en);
    // ★ 항상 렌더하고 표시/숨김만 토글한다 — 배정을 바꿔도 레인 구조키가 그대로라
    //   재렌더가 일어나지 않는다. 뱃지를 조건부로 만들면 밸브를 건드릴 때까지 안 바뀐다.
    const noSvBadge =
      `<span data-nosv="${idx}" title="sv_out 미배정 — 유량 지령이 나가지 않습니다"
             style="font-size:9px;color:#8b8b8b;border:1px solid #8b8b8b;border-radius:3px;
                    padding:0 3px;margin-left:3px;white-space:nowrap;${noSv?'':'display:none;'}">SV 없음</span>`;
    lane.innerHTML=`
      <div class="n-src">
        <span class="srclbl">${showLabel}</span><span class="tap"></span>
      </div>
      <i class="pipe ${c.en?'on':''}" data-seg="pre" style="--c:${c.color}"></i>
      <div class="n-valve ${eff(c)?'open':'closed'}${c.en?'':' dis'}" data-v="${idx}-in" title="MFC 밸브 (VA)">${valveSvg}<span class="vlbl">${c.id}${noSvBadge}</span></div>
      <div class="midpipe">
        <i class="pipe ${eff(c)?'on':''}" data-seg="mid" style="--c:${c.color}"></i>
        ${hasTank?`<div class="tank-ov" title="물탱크 (가습기)">${tankSvg('#3a9fe0')}</div>`:''}
      </div>
      <div class="n-mfc ${eff(c)?'on':''}${c.en?'':' dis'}">
        <div class="mfc-read">
          <div class="mfchd"><span class="mfcid">${c.id} · MFC</span><span class="mfcrole" title="${roleLabel}">${roleLabel}</span></div>
          <div class="pvrow"><span class="rlbl">PV</span><span class="pvb" data-pv="${idx}">${fmtPv(c)}</span><span class="un">sccm</span><button class="svzero" data-svzero="${idx}" ${c.en?'':'disabled'} title="SV를 0으로 즉시 적용">초기화</button></div>
          <div class="svrow"><span class="rlbl">SV</span><input class="svi" size="4" value="${c.sv.toFixed(d)}" data-sv="${idx}" title="MAX ${c.max} sccm — 변경은 System Setup" ${c.en?'':'disabled'}><span class="un">sccm</span><button class="svgo" data-svgo="${idx}" ${c.en?'':'disabled'} title="입력한 SV를 PLC로 보냅니다 (Enter도 동일)">적용</button></div>
        </div>
      </div>
      <i class="pipe grow ${eff(c)?'on':''}" data-seg="post" style="--c:${c.color}"></i>
      <span class="endcap"></span>`;
    lanesEl.appendChild(lane);
  });
  bindLaneEvents();
  drawBuses();
  updateSystem();
}

/* 폴링처럼 '값만' 바뀔 때 레인을 통째로 재생성하지 않고 값만 in-place 갱신한다.
   → .pipe.on 흐름 애니메이션이 리셋되지 않는다. 구조/흐름 변경은 renderLanes()로 전체 렌더.
   구조키: DOM 구조에 영향(채널 수/구성/소수자리). 흐름키: 흐름 클래스에 영향(밸브 개폐·4way). */
function lanesStructKey(){
  return channels.map(c=>`${c.id}|${c.en?1:0}|${c.grp}|${c.route}|${c.max<=100?1:0}`).join(',');
}
function lanesFlowKey(){
  return (plcSafeStop()?'S':'-')+';'+routeOut+';'+channels.map(c=>eff(c)?1:0).join('');
}
function updateLaneValues(){
  channels.forEach((c,idx)=>{
    const lane=lanesEl.querySelector(`.lane[data-idx="${idx}"]`);
    if(!lane) return;
    const d=dec(c);
    lane.classList.toggle('lit', flowing(c)&&c.pv>0);   // 발광(글로우)은 파이프 애니메이션과 무관
    const pv=lane.querySelector(`[data-pv="${idx}"]`); if(pv) pv.textContent=fmtPv(c);
    // 'SV 없음' 뱃지 — 배정 변경은 구조키를 바꾸지 않으므로 여기서 직접 갱신한다.
    const nb=lane.querySelector(`[data-nosv="${idx}"]`);
    if(nb) nb.style.display = (c.plc && !c.plc.sv_out && c.en) ? '' : 'none';
    const sv=lane.querySelector(`[data-sv="${idx}"]`);
    if(sv){
      // 포커스 중(편집 중)이면 값을 덮지 않는다 — 타이핑이 사라지지 않도록.
      if(document.activeElement!==sv){
        sv.value=c.sv.toFixed(d); sv.classList.remove('pending','over');
        const go=lane.querySelector(`[data-svgo="${idx}"]`);   // 초과 해제 → [적용] 복구
        if(go&&c.en) go.disabled=false;
      }
      sv.title=`MAX ${c.max} sccm — 변경은 System Setup`;   // MAX는 Setup에서만 바뀐다
    }
  });
}

function bindLaneEvents(){
  // \uc0ac\uc6a9\uc790 \ub3d9\uc791 = \uc694\uccad. \uc9c1\uc811 \uc0c1\ud0dc\ub97c \ubc14\uafb8\uc9c0 \uc54a\uace0 app.js \uba85\ub839 \ud568\uc218\ub85c \ubcf4\ub0b8\ub2e4.
  // \ud654\uba74\uc740 \uc11c\ubc84 state(\ub610\ub294 \ub04a\uae40 \uc2dc \uc2dc\ubbac\ub808\uc774\uc158 \ub300\uccb4)\uac00 \uc640\uc57c \uac31\uc2e0\ub41c\ub2e4.
  // SV는 '명시적 적용'이다 — 타이핑만으로는 나가지 않고 [적용]/Enter 로만 전송한다.
  const svInput=idx=>lanesEl.querySelector(`[data-sv="${idx}"]`);
  const svRevert=idx=>{
    const el=svInput(idx), c=channels[idx]; if(!el||!c) return;
    el.value=c.sv.toFixed(dec(c));
    el.classList.remove('pending','over');
    svSyncGo(idx, false);
  };
  const svApply=idx=>{
    const el=svInput(idx), c=channels[idx]; if(!el||!c) return;
    const raw=(el.value||'').trim();
    const v=parseFloat(raw);
    if(raw==='' || isNaN(v)){ svRevert(idx); return; }   // 빈값·오타는 원값 복귀
    // ★ MAX 초과는 전송하지 않는다 — 자동 클램프(예: 400→200)는 운전자가 의도하지 않은
    //   값을 조용히 흘려보내므로 금지. 서버의 클램프는 백스톱으로만 남는다.
    if(v > (+c.max||0)){
      window.logMsg(`${c.id}: ${v} sccm — MAX ${c.max} 초과, 적용되지 않았습니다`, 'err');
      return;
    }
    el.classList.remove('pending');
    window.cmdSetSv(idx, v);
  };
  // 초과 여부에 따라 [적용] 버튼을 잠근다(잠금 상태의 disabled는 _applyLocks가 따로 관리).
  const svSyncGo=(idx,over)=>{
    const b=lanesEl.querySelector(`[data-svgo="${idx}"]`), c=channels[idx];
    if(b&&c&&c.en) b.disabled=!!over;
  };
  document.querySelectorAll('[data-sv]').forEach(inp=>{
    inp.addEventListener('input',e=>{
      const idx=+e.target.dataset.sv, c=channels[idx]; if(!c) return;
      const v=+e.target.value||0;
      const over=v > (+c.max||0);
      e.target.classList.toggle('over', over);                    // 초과(빨강)가 우선
      e.target.classList.toggle('pending', !over && v!==c.sv);    // 미적용(주황)
      svSyncGo(idx, over);   // 초과 동안 [적용] 비활성 → 해제되면 즉시 복구
    });
    inp.addEventListener('keydown',e=>{
      if(e.key==='Enter'){ e.preventDefault(); svApply(+e.target.dataset.sv); }
      else if(e.key==='Escape'){ e.preventDefault(); svRevert(+e.target.dataset.sv); e.target.blur(); }
    });
    inp.addEventListener('blur',e=>svRevert(+e.target.dataset.sv));
  });
  // ★ click이 아니라 mousedown + preventDefault — click이면 input의 blur(revert)가 먼저
  //   실행돼 입력값이 사라진다. mousedown에서 포커스를 뺏지 않으면 그 경쟁이 없다.
  document.querySelectorAll('[data-svgo]').forEach(btn=>btn.addEventListener('mousedown',e=>{
    e.preventDefault();
    if(e.currentTarget.disabled) return;   // MAX 초과 중에는 눌러도 전송하지 않는다
    svApply(+e.currentTarget.dataset.svgo);
  }));
  // [초기화] — SV 0을 즉시 적용. 적용 버튼과 같은 이유로 mousedown + preventDefault.
  document.querySelectorAll('[data-svzero]').forEach(btn=>btn.addEventListener('mousedown',e=>{
    e.preventDefault();
    if(e.currentTarget.disabled) return;
    const idx=+e.currentTarget.dataset.svzero, el=svInput(idx);
    if(el){ el.classList.remove('pending','over'); }
    svSyncGo(idx, false);
    window.cmdSetSv(idx, 0);   // 값은 서버 에코로 0이 되어 돌아온다
  }));
  document.querySelectorAll('[data-v]').forEach(v=>v.addEventListener('click',()=>{
    const idx=+v.dataset.v.split('-')[0]; const c=channels[idx];
    if(!c||!c.en) return;   // disabled channel: valve locked
    window.cmdSetValve(idx, !c.valveIn);
  }));
}
// 4-Way "방향 전환" 토글: 누르면 기본(sensor)↔전환(vent) 반전. 명령 이름·상태값은 그대로.
document.getElementById('wayToggle')?.addEventListener('click',()=>{
  window.cmdSet4way(routeOut==='sensor' ? 'vent' : 'sensor');
});

// 토글 버튼의 현재 모드 표시(틴트/툴팁) 갱신. core.js applyState와 drawBuses에서 호출.
function updateWayToggle(){
  const b=document.getElementById('wayToggle'); if(!b) return;
  const sen=routeOut==='sensor';
  b.classList.toggle('vent', !sen);
  b.title = sen ? 'Air→Sensor / Gas→Vent (클릭: 전환)' : 'Air→Vent / Gas→Sensor (클릭: 전환)';
}

/* ===================== manifold buses ===================== */
let routeOut='vent';   // 기본은 vent — 서버 state가 오면 그 값으로 덮인다
function drawBuses(){
  const svg=document.getElementById('wires'); if(!svg) return;
  const S=svg.parentElement.getBoundingClientRect();
  const cx=el=>{const r=el.getBoundingClientRect();return r.left-S.left+r.width/2;};
  const cy=el=>{const r=el.getBoundingClientRect();return r.top-S.top+r.height/2;};
  const probe=document.querySelector('.lane .endcap');
  if(!S.width||!probe||probe.getBoundingClientRect().height===0){setTimeout(drawBuses,100);return;}
  svg.setAttribute('viewBox',`0 0 ${S.width} ${S.height}`);

  // 화면 축소 비율(sc): 모든 SVG 선 두께·점·글자를 이 비율로 줄여 작은 창에서도 비율 유지.
  const sc=(typeof lastScale==='number'&&lastScale>0)?lastScale:1;
  // ★ 레인 파이프(.pipe)와 픽셀 파라미터를 맞춘다 — 한 화면에 두 언어가 섞이지 않게.
  //   .pipe        : height 5px / 비활성 #bcc6d3 / 활성 var(--c)
  //   .pipe.on::after: 흰 스트라이프 rgba(255,255,255,.7) 5px + 17px 간격(주기 22px), 1.1s linear
  //   아래 SW·GREY·.stripe 값은 그 CSS와 1:1로 대응한다. 한쪽만 바꾸면 굵기·명도가 어긋난다.
  const SW=(5*sc).toFixed(2);   // = .pipe 의 height 5px
  const BLUE='#2f72c4', RED='#c8384c', GREY='#bcc6d3';   // GREY = .pipe 비활성 배경색
  // pipe look = solid colored base + white moving stripes (matches horizontal CSS pipes)
  let p=`<style>
    .stripe{stroke:rgba(255,255,255,.7);stroke-width:${SW};stroke-dasharray:5 17;stroke-linecap:butt;fill:none}
    /* 주기 22px(=5+17)·1.1s — .pipe.on::after 의 background-size 22px / animation flow 1.1s 와 동일 */
    .sdn{animation:sdn 1.1s linear infinite}
    .sup{animation:sup 1.1s linear infinite}
    @keyframes sdn{to{stroke-dashoffset:-22}}
    @keyframes sup{to{stroke-dashoffset:22}}
  </style>`;
  // flow line: base + white stripe overlay; dir: 'dn' (toward 2nd point) or 'up' (toward 1st)
  const fL=(x1,y1,x2,y2,col,dir,on)=> on
    ? `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="${SW}" stroke-linecap="round"/><line class="stripe ${dir==='up'?'sup':'sdn'}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`
    : `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="${SW}" stroke-linecap="round"/>`;
  const fP=(d,col,on)=> on
    ? `<path d="${d}" fill="none" stroke="${col}" stroke-width="${SW}" stroke-linecap="round" stroke-linejoin="round"/><path class="stripe sdn" d="${d}" stroke-linejoin="round"/>`
    : `<path d="${d}" fill="none" stroke="${col}" stroke-width="${SW}" stroke-linecap="round" stroke-linejoin="round"/>`;
  const Bbox=(x,y)=>`<rect x="${x-13*sc}" y="${y-13*sc}" width="${26*sc}" height="${26*sc}" rx="${6*sc}" fill="#f0ece2" stroke="#b9ad8e" stroke-width="${(1.6*sc).toFixed(2)}"/><text x="${x}" y="${y+4*sc}" text-anchor="middle" font-size="${(11*sc).toFixed(1)}" font-weight="700" fill="#8a7c55">B</text>`;

  /* ── 좌측 소스 열(라벨·시작점·수평선 시작 x) ──
     Air 와 Gas 가 각자 자기 탭에서 -42 하던 것을 한 값으로 묶는다. 탭 위치가 조금이라도
     다르면 라벨 열이 어긋난다 — Gas 쪽 기준으로 통일하고, 가스 레인이 없으면 Air 기준. */
  const _allTaps=[...document.querySelectorAll('.lane .tap')];
  const _gasTap0=document.querySelector('.lane[data-grp="gas"] .tap');
  const SRC_X=(_gasTap0?cx(_gasTap0):(_allTaps.length?cx(_allTaps[0]):0))-42;

  /* ── Air supply left manifold ── */
  // ★ 레인 표시 순서와 채널 인덱스가 다르므로 DOM 위치가 아니라 data-idx로 짝짓는다.
  const airLanes=[...document.querySelectorAll('.lane[data-grp="air"]')];
  const airTaps=airLanes.map(l=>l.querySelector('.tap')).filter(Boolean);
  const airChs=airLanes.map(l=>channels[+l.dataset.idx]).filter(Boolean);
  if(airTaps.length){
    const ax=cx(airTaps[0]); const ays=airTaps.map(cy);
    const enAirYs=ays.filter((_,i)=>airChs[i]&&airChs[i].en);
    const has=enAirYs.length>0;
    const topY=has?Math.min(...enAirYs):Math.min(...ays);
    const botY=has?Math.max(...enAirYs):Math.max(...ays);
    const xIn=SRC_X;
    // ★ 공급측(밸브 앞)은 밸브 개폐와 무관하게 '상시' 흐름으로 그린다 —
    //   소스는 항상 가압돼 있고 공기는 밸브 앞까지 이미 도달해 있다.
    //   b17aab9의 pre 구간 규칙("pre-segment flows whenever enabled")을 커넥터에 일반화한 것.
    //   구간 활성 = 그 구간을 경유해 도달하는 가지 중 en=true 가 존재(=enAirYs 범위).
    //   밸브 '뒤'부터(트렁크·버스·4-way)는 유효 열림(eff) 기준을 그대로 쓴다.
    // left inlet pipe + inlet cap (label sits at its left end)
    p+=fL(xIn,topY,ax,topY,BLUE,'dn',has);
    p+=`<circle cx="${xIn}" cy="${topY}" r="${(4.5*sc).toFixed(2)}" fill="#fff" stroke="${BLUE}" stroke-width="${(2.4*sc).toFixed(2)}"/>`;
    // vertical manifold: flow across the enabled air span (밸브 개폐와 무관)
    if(has) p+=fL(ax,topY,ax,botY,BLUE,'dn',true);
    {const al=document.getElementById('airsupply'); al.style.left=((xIn-8)/sc)+'px'; al.style.top=(topY/sc)+'px';}
    airTaps.forEach((t,i)=>{const on=!!(airChs[i]&&airChs[i].en);
      p+=`<circle cx="${ax}" cy="${ays[i]}" r="${(3.5*sc).toFixed(2)}" fill="${on?BLUE:GREY}" opacity="${on?1:0.45}"/>`;});
  }

  /* ── Gas inlets: each lane = ONE continuous line from inlet cap to VA valve ── */
  const gasLanes=[...document.querySelectorAll('.lane[data-grp="gas"]')];
  const gasTaps=gasLanes.map(l=>l.querySelector('.tap')).filter(Boolean);
  const gasChs=gasLanes.map(l=>channels[+l.dataset.idx]).filter(Boolean);
  const flowC=c=>eff(c);   // 유효 열림(안전정지면 닫힘)
  const glayer=document.getElementById('gaslabels'); if(glayer) glayer.innerHTML='';
  const scG=(typeof lastScale==='number'&&lastScale>0)?lastScale:1;
  gasTaps.forEach((t,i)=>{
    const lane=t.closest('.lane');
    const valve=lane.querySelector('.n-valve');
    const gx=cx(t), gy=cy(t); const ch=gasChs[i]; const on=ch&&ch.en;
    const xIn=SRC_X;
    const vx=valve?(valve.getBoundingClientRect().left-S.left):gx+120;
    const col=on?RED:'#bcc6d3';
    // hide the HTML pre-pipe so this is a single SVG line
    const pre=lane.querySelector('.pipe[data-seg="pre"]'); if(pre) pre.style.visibility='hidden';
    // pre-segment flows whenever enabled (supply reaches the valve), like air
    let seg=fL(xIn,gy,vx,gy,col,'dn',on)+`<circle cx="${xIn}" cy="${gy}" r="${(4.5*sc).toFixed(2)}" fill="#fff" stroke="${col}" stroke-width="${(2.4*sc).toFixed(2)}"/>`;
    if(!on) seg=`<g opacity="0.42">${seg}</g>`;   // match disabled air lanes (.lane.off opacity:.42)
    p+=seg;
    if(glayer&&ch){
      const d=document.createElement('div'); d.className='gaslbl'+(ch.en?'':' off');
      d.textContent=ch.label; d.style.left=((xIn-8)/scG)+'px'; d.style.top=(gy/scG)+'px';
      glayer.appendChild(d);
    }
  });

  /* ── Right collection (flow only where valves pass) ── */
  const capLanes=[...document.querySelectorAll('.lane')].filter(l=>l.querySelector('.endcap'));
  if(!capLanes.length){svg.innerHTML=p;return;}
  const bx=cx(capLanes[0].querySelector('.endcap'));
  // bys[채널 인덱스] = 그 채널이 '표시되고 있는 줄'의 y. 아래 계산은 전부 채널 인덱스 기준이라
  // 레인을 재배치해도 route(혼합/단독)별 버스가 그대로 따라간다.
  const bys=[];
  capLanes.forEach(l=>{ bys[+l.dataset.idx]=cy(l.querySelector('.endcap')); });
  const flow=c=>eff(c);   // 유효 열림(안전정지면 닫힘) — 수집 버스 흐름도 함께 정지
  const pureRows=channels.map((c,i)=>c.route==='pure'?bys[i]:null).filter(v=>v!=null);
  const mixRows=channels.map((c,i)=>c.route==='mix'?bys[i]:null).filter(v=>v!=null);
  const pureF=channels.map((c,i)=>c.route==='pure'&&flow(c)?bys[i]:null).filter(v=>v!=null);
  const mixF=channels.map((c,i)=>c.route==='mix'&&flow(c)?bys[i]:null).filter(v=>v!=null);
  const vcR=24, vcX=bx+186*sc, jx=bx+93*sc;
  const pureMidRow=pureRows.length?(Math.min(...pureRows)+Math.max(...pureRows))/2:S.height*0.25;
  const mixMidRow=mixRows.length?(Math.min(...mixRows)+Math.max(...mixRows))/2:S.height*0.6;
  const vcY=S.height*0.34;   // 4-way moved up to leave room for the log panel bottom-right

  // 4-way 밸브 = 박스 없이 파이프로 직접 그린다. 접합부 중심(cCx,cCy)·반경 r(밸브 크기).
  // 토글/RH(#vcControl)는 접합부 아래에 배치한다.
  const vcEl=document.getElementById('vcControl');
  const r=22*sc;
  const cCx=vcX+62*sc, cCy=vcY;
  const senOn=routeOut==='sensor';

  // AIR (pure) bus — FIXED grey structure across ALL rows + coloured flow only on the FLOWING span.
  // 4-way 신배치: Air 버스는 카드 위(top-center)로 들어간다(ㄱ 모양: 가로 → 아래로 꺾여 top 포트).
  if(pureRows.length>0){
    const ptop=Math.min(...pureRows), pbot=Math.max(...pureRows);
    const airFeed=`M${bx} ${pureMidRow} H${cCx} V${cCy-r}`;   // 밸브 위쪽 입력점까지
    // fixed structural line (always, no flow)
    if(pureRows.length>1) p+=fL(bx,ptop,bx,pbot,GREY,'dn',false);
    p+=fP(airFeed,pureF.length>0?BLUE:GREY,false);
    if(pureF.length>0){
      const colTop=Math.min(Math.min(...pureF),pureMidRow), colBot=Math.max(Math.max(...pureF),pureMidRow);
      if(colTop<pureMidRow) p+=fL(bx,colTop,bx,pureMidRow,BLUE,'dn',true);
      if(colBot>pureMidRow) p+=fL(bx,pureMidRow,bx,colBot,BLUE,'up',true);
      p+=fP(airFeed,BLUE,true);
    }
  }
  // GAS (mix) bus — air-dilution segment blue, gas segment red, combined feeder blends by what flows
  if(mixRows.length>0){
    const mtop=Math.min(...mixRows), mbot=Math.max(...mixRows);
    // 합류점 = '표시상 가장 위에 있는 가스 행'. 채널 인덱스 순서가 아니라 y로 고른다
    //   — 레인 표시 순서를 바꿔도 공기 구간/가스 구간 경계가 따라온다.
    const gasRowYs=channels.map((c,i)=>c.grp==='gas'?bys[i]:null).filter(v=>v!=null);
    const gas1Y=gasRowYs.length?Math.min(...gasRowYs):mtop;
    // Y's of channels that are ACTUALLY flowing (valves open), per group
    const airFlowY=channels.map((c,i)=>c.grp==='air'&&c.route==='mix'&&flow(c)?bys[i]:null).filter(v=>v!=null);
    const gasFlowY=channels.map((c,i)=>c.grp==='gas'&&c.route==='mix'&&flow(c)?bys[i]:null).filter(v=>v!=null);
    const airMixFlow=airFlowY.length>0, gasFlow=gasFlowY.length>0;
    const BLEND='#8a4f9e';   // blue + red mixed (both flowing)
    const feedColor=(airMixFlow&&gasFlow)?BLEND:(airMixFlow?BLUE:(gasFlow?RED:GREY));
    // grey structural bus (always, full span)
    if(mixRows.length>1) p+=fL(bx,mtop,bx,mbot,GREY,'dn',false);
    // air-dilution flow: only from the topmost FLOWING air tap down to the junction
    if(airMixFlow) p+=fL(bx,Math.min(...airFlowY),bx,gas1Y,BLUE,'dn',true);
    // gas flow: only from the junction down to the deepest FLOWING gas tap
    if(gasFlow) p+=fL(bx,gas1Y,bx,Math.max(...gasFlowY),RED,'up',true);
    // combined feeder gas1 row → card bottom
    const feeding=airMixFlow||gasFlow;
    p+=fP(`M${bx} ${gas1Y} H${cCx} V${cCy+r}`,feeding?feedColor:GREY,feeding);   // 밸브 아래쪽 입력점까지
  }

  // endcap joints (coloured only where that channel actually flows)
  // ★ 정션(탭)은 그 행의 채널이 실제로 이 버스에 합류할 때만 그린다.
  //   단독(pure) 라인은 4-way로 직행하므로 혼합 버스 위를 '점 없이 통과'해야 한다 —
  //   점을 찍으면 합류하는 것처럼 보인다(표시 순서가 바뀌며 pure 행이 버스 한가운데 온다).
  channels.forEach((ech,i)=>{
    if(bys[i]==null) return;
    const onMixBus=ech.route==='mix', onPureBus=ech.route==='pure'&&pureRows.length>1;
    if(!onMixBus&&!onPureBus) return;
    const col=ech.grp==='gas'?RED:BLUE;
    p+=`<rect x="${bx-6*sc}" y="${bys[i]-4*sc}" width="${12*sc}" height="${8*sc}" rx="${3*sc}" fill="#cfd8e3" stroke="${flow(ech)?col:GREY}" stroke-width="${(1.2*sc).toFixed(2)}"/>`;
  });

  /* ── 4-way 밸브 = 둥근 테두리 박스 + 스타일 C 대각선(반대 삼각형, 가운데 빔). 출력색 = 들어온 입력색. ── */
  // 입력 색: 위=순수공기(파랑), 아래=mix(실제 흐름 기준: 공기희석=파랑/가스=빨강/둘다=보라)
  const BLEND='#8a4f9e';
  const airMixFlowY=channels.map((c,i)=>c.grp==='air'&&c.route==='mix'&&flow(c)?bys[i]:null).filter(v=>v!=null);
  const gasMixFlowY=channels.map((c,i)=>c.grp==='gas'&&c.route==='mix'&&flow(c)?bys[i]:null).filter(v=>v!=null);
  const topFlow=pureF.length>0, topCol=BLUE;
  const botFlow=mixF.length>0;
  const botCol=(airMixFlowY.length>0&&gasMixFlowY.length>0)?BLEND:(airMixFlowY.length>0?BLUE:(gasMixFlowY.length>0?RED:GREY));
  const vTop=[cCx,cCy-r], vBot=[cCx,cCy+r], vRight=[cCx+r,cCy], vLeft=[cCx-r,cCy];
  const topTo=senOn?vRight:vLeft, botTo=senOn?vLeft:vRight;
  // 테두리 박스(입·출력 파이프가 4변 통과, 안쪽 배경색) — 대각선보다 먼저 그려 대각선이 위로.
  p+=`<rect x="${cCx-r}" y="${cCy-r}" width="${r*2}" height="${r*2}" rx="${6*sc}" style="fill:var(--bg2)" stroke="#6b7686" stroke-width="${(1.6*sc).toFixed(2)}"/>`;
  p+=fL(vTop[0],vTop[1],topTo[0],topTo[1],topFlow?topCol:GREY,'dn',topFlow);
  p+=fL(vBot[0],vBot[1],botTo[0],botTo[1],botFlow?botCol:GREY,'dn',botFlow);
  const L=46*sc;
  const senSrcFlow=senOn?topFlow:botFlow, senSrcCol=senOn?topCol:botCol;
  const venSrcFlow=senOn?botFlow:topFlow, venSrcCol=senOn?botCol:topCol;
  p+=fL(vRight[0],vRight[1],cCx+r+L,cCy,senSrcFlow?senSrcCol:GREY,'dn',senSrcFlow);
  p+=fL(vLeft[0],vLeft[1],cCx-r-L,cCy,venSrcFlow?venSrcCol:GREY,'dn',venSrcFlow);
  const dotC=(x,y,col)=>`<circle cx="${x}" cy="${y}" r="${(3.6*sc).toFixed(1)}" fill="${col}"/>`;
  p+=dotC(vTop[0],vTop[1],topFlow?topCol:GREY);
  p+=dotC(vBot[0],vBot[1],botFlow?botCol:GREY);
  p+=dotC(vRight[0],vRight[1],senSrcFlow?senSrcCol:GREY);
  p+=dotC(vLeft[0],vLeft[1],venSrcFlow?venSrcCol:GREY);
  // Vent/Sensor 라벨만 출력 파이프 끝에(배관도 라벨 톤). Air/Gas 텍스트는 넣지 않음.
  const lf=(13.5*sc).toFixed(1);
  p+=`<text x="${cCx+r+L+8*sc}" y="${cCy+4.5*sc}" text-anchor="start" font-family="inherit" font-size="${lf}" font-weight="700" fill="#2a3645">Sensor</text>`;
  p+=`<text x="${cCx-r-L-8*sc}" y="${cCy+4.5*sc}" text-anchor="end" font-family="inherit" font-size="${lf}" font-weight="700" fill="#2a3645">Vent</text>`;

  updateWayToggle();

  svg.innerHTML=p;
  // 토글+RH는 밸브에 가까운 우하단(밸브 오른쪽 옆 + Sensor 출력선 아래)에 배치 → 파이프와 안 겹침.
  if(vcEl){
    vcEl.style.right='auto';
    vcEl.style.left=((cCx+r+16*sc)/sc)+'px';   // 밸브 오른쪽 옆
    vcEl.style.top=((cCy+r+14*sc)/sc)+'px';     // Sensor 출력선 아래
    vcEl.style.transform='none';                // 좌상단 기준
  }
  // PLC 상태 패널: 스키매틱 우하단 '코너'에 붙인다(right/bottom 앵커).
  // .plc-panel은 position:absolute, 부모 .schem이 relative라 우하단에 정렬된다.
  const ppEl=document.getElementById('plcPanel');
  if(ppEl){
    ppEl.style.left='auto'; ppEl.style.top='auto'; ppEl.style.transform='none';
    ppEl.style.right='8px'; ppEl.style.bottom='10px'; ppEl.style.width='360px';
  }
}
