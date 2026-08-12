/* schematic.js — 배관도(채널/밸브) 렌더 + 이벤트 + drawBuses.
   다른 파일 함수는 window.* 노출분을 사용. 전역 노출/초기화는 core.js가 담당. */
/* 전체 문자열이 숫자일 때만 값을 돌려준다. parseFloat('10O0')=10 같은 부분 해석과
   +('12a')||0 = 0 같은 조용한 대체가, 운전자가 의도하지 않은 유량·스케일을 만든 사고의
   원인이었다(자동완성 사고와 같은 계열 — DEC-041 "입력한 값 그대로가 아니면 나가지 않는다").
   ★ 이 파일이 첫 스크립트라 여기서 정의한다(recipe.js·core.js·app.js가 뒤따라 로드된다). */
window.strictNum = function(s){
  s = String(s==null?'':s).trim();
  return /^[+-]?(\d+\.?\d*|\.\d+)$/.test(s) ? parseFloat(s) : null;
};
/* ── PIPE DESIGN TOKENS — style.css 의 .pipe 계열과 동기(★한쪽만 바꾸지 말 것) ──
   .pipe{height:5px;background:#bcc6d3}
   .pipe.on{background:var(--c)} / .pipe.on::after{ 흰 5px + 17px 간격(주기 22px), 1.1s linear }
   레인은 CSS, 커넥터·트렁크·버스는 SVG로 그리므로 값이 갈라지면 한 화면에 두 언어가 섞인다. */
const PIPE_W = 5;                 // .pipe height:5px
const PIPE_DASH = '5 17';         // 흰 5px / 주기 22px (.pipe.on::after 와 동일)
const PIPE_PERIOD = '1.1s';       // .pipe.on::after animation 주기
// ★ 색은 CSS 변수를 그대로 참조한다 — inline SVG 는 문서의 CSS 변수를 상속하므로
//   style.css 한 곳만 고치면 레인과 배관이 함께 바뀐다(값 복제 금지).
const COL_OFF = 'var(--pipe-off)';   // = .pipe 기본(비활성) 배경
// 그 구간을 지나는 채널이 하나도 없을 때(전부 en=false). 닫힘(COL_OFF)보다 연하다.
const COL_UNUSED = 'var(--pipe-unused)';
const COL_AIR = 'var(--air)';        // 레인 카드가 --c 로 쓰는 값
// 가스 레인은 deriveDisplay()가 전부 --g1 을 준다. 채널별로 색을 나누게 되면 여기만 고치면 된다.
const COL_GAS = { VA5:'var(--g1)', VA6:'var(--g1)', VA7:'var(--g1)', VA8:'var(--g1)' };
const COL_GAS_DEFAULT = 'var(--g1)';
const COL_BLEND = '#8a4f9e';      // 공기+가스가 함께 흐르는 합류 구간(레인에는 없는 색)
const DOT_R = 4.5, DOT_SW = 2.4;  // 소스점·정션·탭 공통 규격
const gasCol = id => COL_GAS[id] || COL_GAS_DEFAULT;

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
// 밸브 앞(en 상시)·밸브→MFC(eff) 전용. MFC 이후 표시는 svOn 을 쓴다.
const flowing=c=>eff(c);
// MFC 이후 구간(레인 후단·트렁크·버스·4-way 입력·연결점·발광)의 단일 판정.
//  · 밸브 닫힘/미준비(eff=false) → 즉시 꺼짐 (지령 해제 즉시 반영 — 유지)
//  · SV>0 인데 PV 아직 0     → 표시 (상승 중 끊겨 보이지 않게 — 유지)
//  · SV=0(초기화)·밸브 열림   → 잔류 PV 가 PV_ON_MIN 밑으로 빠질 때까지 표시 ★변경
//  · PV 미가용(미배정·두절)   → SV 기준 폴백 (유지)
// ★ 파이프와 점·발광을 두 규칙으로 그리면 초기화 순간 한쪽만 꺼져 불일치가 보인다 —
//   점의 방식(PV 추종)으로 통일한다.
const PV_ON_MIN = 1;                      // sccm — 잡음 무시 문턱
const svOn = c => {
  if (!eff(c)) return false;
  const pv = (c && c.pv != null && isFinite(+c.pv)) ? +c.pv : null;
  if (pv == null) return +c.sv > 0;
  return pv >= PV_ON_MIN || +c.sv > 0;
};

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
     Setup 표·레시피·서버 데이터는 계속 ID 순서를 쓴다.
   ★ 채널 셋은 하드웨어 계약(8채널 + 4way)으로 고정 — 이 배열은 '표시 순서'만 정의한다.
     오타·누락이 있어도 카드가 사라지지 않도록 아래에서 방어한다. */
const LANE_ORDER=['VA2','VA3','VA1','VA4','VA5','VA6','VA7','VA8'];
// 표시 순서 → [원래 채널 인덱스, 채널] 목록. 목록에 없는 채널은 뒤에 원래 순서대로 붙인다.
function lanesInDisplayOrder(){
  const used=new Set(), out=[], missing=[];
  LANE_ORDER.forEach(id=>{
    const i=channels.findIndex((c,n)=>c.id===id&&!used.has(n));
    if(i>=0){ used.add(i); out.push([i, channels[i]]); }
    else missing.push(id);      // 오타·구성 변경 → 그 항목만 건너뛴다
  });
  if(missing.length)
    console.warn('[LANE_ORDER] 존재하지 않는 채널 id — 건너뜁니다:', missing.join(', '));
  // LANE_ORDER 에 없는 채널은 맨 뒤에 원래 순서대로 이어 그린다(카드 누락 금지).
  const extra=[];
  channels.forEach((c,i)=>{ if(!used.has(i)){ out.push([i,c]); extra.push(c.id); } });
  if(extra.length)
    console.warn('[LANE_ORDER] 순서에 없는 채널 — 맨 뒤에 표시합니다:', extra.join(', '));
  return out;
}
function renderLanes(){
  lanesEl.innerHTML='';
  lanesInDisplayOrder().forEach(([idx,c])=>{
    const d=dec(c);
    const lane=document.createElement('div');
    lane.className='lane'+(svOn(c)?' lit':'')+(c.en?'':' off');
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
      <i class="pipe grow ${svOn(c)?'on':''}" data-seg="post" style="--c:${c.color}"></i>
      <span class="endcap"></span>`;
    lanesEl.appendChild(lane);
  });
  bindLaneEvents();
  drawBuses();
  updateSystem();
  _lastFlowKey=lanesFlowKey();   // 방금 그린 상태를 기준선으로 (telemetry 재렌더 중복 방지)
}

/* 폴링처럼 '값만' 바뀔 때 레인을 통째로 재생성하지 않고 값만 in-place 갱신한다.
   → .pipe.on 흐름 애니메이션이 리셋되지 않는다. 구조/흐름 변경은 renderLanes()로 전체 렌더.
   구조키: DOM 구조에 영향(채널 수/구성/소수자리). 흐름키: 흐름 클래스에 영향(밸브 개폐·4way). */
function lanesStructKey(){
  return channels.map(c=>`${c.id}|${c.en?1:0}|${c.grp}|${c.route}|${c.max<=100?1:0}`).join(',');
}
function lanesFlowKey(){
  // ★ 후단(svOn)도 흐름키에 넣는다 — PV/SV 값만 바뀌어도 표시가 달라지므로,
  //   값 변화만으로도 흐름 표시를 다시 계산해야 한다(판정 규칙 자체는 그대로).
  //   발광(lit)은 이제 svOn 과 같은 판정이라 별도 자리가 필요 없다.
  return (plcSafeStop()?'S':'-')+';'+routeOut+';'
       + channels.map(c=>(eff(c)?1:0)+''+(svOn(c)?1:0)).join('');
}
/* 흐름 클래스만 in-place 로 갈아끼운다(DOM 교체 없음).
   → 입력에 포커스가 있어 renderLanes()를 보류하는 동안에도 파이프 표시는 최신이 된다.
   구간 규칙은 renderLanes()의 템플릿과 같은 판정을 쓴다(en 상시 / eff / svOn). */
function updateLaneFlowClasses(){
  channels.forEach((c,idx)=>{
    const lane=lanesEl.querySelector(`.lane[data-idx="${idx}"]`);
    if(!lane) return;
    const seg=s=>lane.querySelector(`.pipe[data-seg="${s}"]`);
    const pre=seg('pre'),  mid=seg('mid'),  post=seg('post');
    if(pre)  pre.classList.toggle('on', !!c.en);      // 밸브 앞 = en 상시
    if(mid)  mid.classList.toggle('on', eff(c));      // 밸브 → MFC = 유효 열림
    if(post) post.classList.toggle('on', svOn(c));    // MFC 이후 = 실측 PV 기준
    const v=lane.querySelector('.n-valve');
    if(v){ v.classList.toggle('open', eff(c)); v.classList.toggle('closed', !eff(c)); }
    const m=lane.querySelector('.n-mfc');
    if(m) m.classList.toggle('on', eff(c));
  });
}
/* PV는 state push가 아니라 telemetry(초당 5회)로 들어온다. 후단 표시가 실측 PV 기준이 된
   이상, telemetry 틱에서도 흐름키가 바뀌면 다시 그려야 한다 — 안 그리면 PV가 올라와도
   다음 state push 때까지 후단이 꺼진 채로 남는다. 키가 그대로면 아무 것도 하지 않으므로
   흐름 애니메이션은 리셋되지 않는다(0↔1 전이에서만 재렌더). */
let _lastFlowKey='';
function refreshLaneFlow(){
  if(!lanesEl.querySelector('.lane')) return;
  const k=lanesFlowKey();
  if(k===_lastFlowKey) return;    // 바뀐 게 없으면 아무 것도 하지 않는다(흐름 애니메이션 보존)
  _lastFlowKey=k;
  // ★ 보류하는 것은 '재렌더(DOM 교체)'뿐이다 — 편집 중이라고 흐름 표시까지 멈추면
  //   SV 칸에 커서를 둔 채로는 PV 가 올라와도 파이프가 안 켜진다(바깥을 클릭해야 반영).
  const editing = lanesEl.contains(document.activeElement)
    && /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
  if(!editing){
    renderLanes();                // 내부에서 updateLaneValues 없이 새 DOM + drawBuses 까지 수행
    return;                       // 중복 호출 방지
  }
  updateLaneFlowClasses();        // 레인 파이프·밸브·MFC 클래스만 교체
  updateLaneValues();             // 값 갱신 — 포커스 중인 SV 입력은 내부에서 스킵된다
  drawBuses();                    // 커넥터·트렁크·버스는 항상 최신으로
}
function updateLaneValues(){
  channels.forEach((c,idx)=>{
    const lane=lanesEl.querySelector(`.lane[data-idx="${idx}"]`);
    if(!lane) return;
    const d=dec(c);
    lane.classList.toggle('lit', svOn(c));   // 발광(글로우)은 파이프 애니메이션과 무관
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
    if(raw===''){ svRevert(idx); return; }               // 빈값 = 입력 취소(경고 없음)
    const v=window.strictNum(raw);
    if(v===null){    // '10O0' 같은 부분 숫자 차단 — 조용히 10 이 적용되던 자리다
      window.logMsg(`${c.id}: "${raw}" — 숫자가 아니므로 적용하지 않았습니다`, 'err');
      svRevert(idx); return;
    }
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
      // 비숫자는 초과도 미적용도 아니다 — 표식 없이 두고 [적용] 시점에 거부한다.
      const v=window.strictNum(e.target.value);
      const over=v!==null && v > (+c.max||0);
      e.target.classList.toggle('over', over);                    // 초과(빨강)가 우선
      e.target.classList.toggle('pending', v!==null && !over && v!==c.sv);   // 미적용(주황)
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
  const sen=routeOut==='sensor';          // sen = 혼합(가스)이 센서로 간다
  b.classList.toggle('vent', !sen);
  b.title = sen ? 'Gas→Sensor / Air→Vent  (클릭: Gas를 Vent로)'
                : 'Gas→Vent / Air→Sensor · 무전원 기본 위치  (클릭: Gas를 Sensor로)';
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
  // ★ sc(=lastScale)를 곱하는 이유 — fit()은 #app 에 CSS **zoom** 을 건다.
  //   zoom 아래에서 getBoundingClientRect()는 '화면 px'을 돌려주므로 viewBox 단위 = 화면 px 이고,
  //   CSS px(.pipe 의 5px)은 화면에서 5*sc 로 보인다. 따라서 SVG 쪽에 sc 를 곱해야 둘이 같아진다
  //   (sc 를 빼면 축소할수록 SVG 선만 굵어 보인다). 좌표를 HTML 라벨로 넘길 때 /sc 하는 것과 짝이다.
  const SW=(PIPE_W*sc).toFixed(2);
  const BLUE=COL_AIR, RED=COL_GAS_DEFAULT, GREY=COL_OFF;   // 토큰 별칭(아래 식들을 짧게 유지)
  // pipe look = solid colored base + white moving stripes (matches horizontal CSS pipes)
  let p=`<style>
    .stripe{stroke:rgba(255,255,255,.7);stroke-width:${SW};stroke-dasharray:${PIPE_DASH};stroke-linecap:butt;fill:none}
    /* 주기 22px(=5+17)·1.1s — .pipe.on::after 의 background-size 22px / animation flow 1.1s 와 동일 */
    .sdn{animation:sdn ${PIPE_PERIOD} linear infinite}
    .sup{animation:sup ${PIPE_PERIOD} linear infinite}
    /* PLC 미준비: .lane.notready .pipe.on::after{animation:none} 와 같은 룩(흰 틱은 남고 정지) */
    .nostripe .stripe{animation:none}
    @keyframes sdn{to{stroke-dashoffset:-22}}
    @keyframes sup{to{stroke-dashoffset:22}}
  </style>`;
  // flow line: base + white stripe overlay; dir: 'dn' (toward 2nd point) or 'up' (toward 1st)
  const fL=(x1,y1,x2,y2,col,dir,on)=> on
    ? `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="${SW}" stroke-linecap="butt"/><line class="stripe ${dir==='up'?'sup':'sdn'}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`
    : `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="${SW}" stroke-linecap="butt"/>`;
  const fP=(d,col,on)=> on
    ? `<path d="${d}" fill="none" stroke="${col}" stroke-width="${SW}" stroke-linecap="butt" stroke-linejoin="round"/><path class="stripe sdn" d="${d}" stroke-linejoin="round"/>`
    : `<path d="${d}" fill="none" stroke="${col}" stroke-width="${SW}" stroke-linecap="butt" stroke-linejoin="round"/>`;
  /* 정션·소스 접점 공통 점(흰 속 + 색 테두리). 규격은 DOT_R·DOT_SW 단일 출처.
     ★ 같은 모양을 세 군데서 인라인으로 복제하고 있던 것을 이 헬퍼로 모았다 — 규격이 갈라지면
       한 화면에서 점 크기가 달라 보인다(4-way 포트의 dotC 는 '속 채운' 다른 종류다). */
  const fDot=(x,y,col,op)=>`<circle cx="${x}" cy="${y}" r="${(DOT_R*sc).toFixed(2)}"`
    +` fill="#fff" stroke="${col}" stroke-width="${(DOT_SW*sc).toFixed(2)}"`
    +`${(op==null||op===1)?'':` opacity="${op}"`}/>`;
  const Bbox=(x,y)=>`<rect x="${x-13*sc}" y="${y-13*sc}" width="${26*sc}" height="${26*sc}" rx="${6*sc}" fill="#f0ece2" stroke="#b9ad8e" stroke-width="${(1.6*sc).toFixed(2)}"/><text x="${x}" y="${y+4*sc}" text-anchor="middle" font-size="${(11*sc).toFixed(1)}" font-weight="700" fill="#8a7c55">B</text>`;

  // PLC 준비 여부 — 레인의 .lane.notready(투명도 .45 + 애니 정지)와 같은 표현을 SVG에도 적용한다.
  //   plcSafeStop() 은 '연결 + SAFETY_STOP' 을 보므로 미연결까지 포함하려면 connected 도 함께 본다.
  const ready = !!(window.plcLive && window.plcLive.connected) && !plcSafeStop();
  //   공급측(en 기반 상시) 세그먼트만 감싼다 — eff 기반 구간은 미준비면 어차피 비활성 회색이다.
  const dim = seg => ready ? seg : `<g opacity="0.45" class="nostripe">${seg}</g>`;

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
    let airSeg=fL(xIn,topY,ax,topY,BLUE,'dn',has)+fDot(xIn,topY,BLUE);
    // vertical manifold: flow across the enabled air span (밸브 개폐와 무관)
    if(has) airSeg+=fL(ax,topY,ax,botY,BLUE,'dn',true);
    p+=dim(airSeg);
    {const al=document.getElementById('airsupply'); al.style.left=((xIn-8)/sc)+'px'; al.style.top=(topY/sc)+'px';}
    // 소스 접점: 가스·버스 정션과 같은 규격(흰 속 + 색 테두리)으로 그린다.
    //   HTML .tap 은 CSS로 숨겨져 있고 여기가 유일한 표시다 — 규격은 DOT_R·DOT_SW로 통일.
    airTaps.forEach((t,i)=>{const on=!!(airChs[i]&&airChs[i].en);
      p+=dim(fDot(ax,ays[i],on?BLUE:GREY,on?1:0.45));});
  }

  /* ── Gas inlets: each lane = ONE continuous line from inlet cap to VA valve ── */
  const gasLanes=[...document.querySelectorAll('.lane[data-grp="gas"]')];
  const gasTaps=gasLanes.map(l=>l.querySelector('.tap')).filter(Boolean);
  const gasChs=gasLanes.map(l=>channels[+l.dataset.idx]).filter(Boolean);
  const glayer=document.getElementById('gaslabels'); if(glayer) glayer.innerHTML='';
  const scG=(typeof lastScale==='number'&&lastScale>0)?lastScale:1;
  gasTaps.forEach((t,i)=>{
    const lane=t.closest('.lane');
    const valve=lane.querySelector('.n-valve');
    const gx=cx(t), gy=cy(t); const ch=gasChs[i]; const on=ch&&ch.en;
    const xIn=SRC_X;
    const vx=valve?(valve.getBoundingClientRect().left-S.left):gx+120;
    const col=on?gasCol(ch&&ch.id):COL_OFF;
    // hide the HTML pre-pipe so this is a single SVG line
    const pre=lane.querySelector('.pipe[data-seg="pre"]'); if(pre) pre.style.visibility='hidden';
    // pre-segment flows whenever enabled (supply reaches the valve), like air
    let seg=fL(xIn,gy,vx,gy,col,'dn',on)+fDot(xIn,gy,col);
    if(!on) seg=`<g opacity="0.42">${seg}</g>`;   // match disabled air lanes (.lane.off opacity:.42)
    p+=dim(seg);   // PLC 미준비면 레인과 같은 45%·정지 표현
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
  /* ── 세로 버스 구조선을 '행 사이 구간'으로 쪼개 그린다 ─────────────────
     한 줄로 그리면 아무것도 지나지 않는 구간(예: 미배선 VA2 행 ~ 아래 합류점)까지
     '닫힘' 색으로 진하게 보여 쓰는 배관처럼 읽힌다.
     구간에 기여하는 채널 = 그 구간을 실제로 지나 합류점(merge)으로 가는 행.
       · 구간이 합류점보다 위  → 위쪽(작은 y) 행들이 내려오며 지난다
       · 구간이 합류점보다 아래 → 아래쪽(큰 y) 행들이 올라가며 지난다
       · 구간이 합류점을 걸치면 → 양쪽 모두 지난다
     기여 채널이 전부 en=false 면 --pipe-unused(가장 연함), 아니면 기존 닫힘색.
     ★ '기여 채널이 흐르는 중'인 구간은 아래 흐름 오버레이가 채널색으로 덮어 그린다 —
       구조선에서 색을 또 계산하면 두 곳이 갈라진다(색 판정 단일 출처 유지). */
  // 경계 점은 여기 모아 두었다가 흐름 오버레이보다 '뒤에' 붙인다 —
  // 같은 자리에 색 선이 지나가도 점이 가려지지 않아야 한다(z-order).
  let dots='';
  // 채널색(이음새 점·조인트 공용). 아래 endcap 조인트도 같은 함수를 쓴다 — 색 규칙 단일 출처.
  const chCol=ch=>ch.grp==='gas'?gasCol(ch.id):COL_AIR;
  // 점(y)을 '지나가는' 흐름의 색 — 단일 출처.
  //   합류점(merge) 위쪽 행은 아래로, 아래쪽 행은 위로 이동해 merge 에서 꺾인다.
  //   경로가 y 를 덮는 조건: (r.y<=y && y<=merge) || (r.y>=y && y>=merge)
  //   (y==merge 면 양쪽 모두 — 기존 경계점 규칙은 위쪽만 세서 아래서 오는 흐름을 놓쳤다)
  //   흐르는 채널 있음 → 채널색 / 배선만 됨 → GREY / 아무도 안 지남 → COL_UNUSED
  const passCol=(rows,merge,y)=>{
    const via=rows.filter(r=>(r.y<=y&&y<=merge)||(r.y>=y&&y>=merge));
    const f=via.find(r=>r.ch&&svOn(r.ch));
    if(f) return chCol(f.ch);
    return via.some(r=>r.ch&&r.ch.en)?GREY:COL_UNUSED;
  };
  const busStruct=(rows, merge)=>{
    const ys0=rows.map(r=>r.y);
    if(ys0.length<2) return '';
    // ★ 합류점도 경계로 넣는다 — 안 넣으면 행이 2개일 때 구간 하나가 합류점을 걸쳐
    //   양쪽 채널을 모두 세고, 위쪽이 미배선이어도 '사용 중'으로 잡힌다.
    const ys=[...new Set(
      (merge>Math.min(...ys0)&&merge<Math.max(...ys0)) ? ys0.concat(merge) : ys0
    )].sort((a,b)=>a-b);
    if(ys.length<2) return '';
    let out='';
    for(let i=0;i<ys.length-1;i++){
      const y1=ys[i], y2=ys[i+1];
      const via=rows.filter(r => (y2<=merge) ? (r.y<=y1)
                               : ((y1>=merge) ? (r.y>=y2) : true));
      const used=via.some(r=>r.ch&&r.ch.en);
      out+=fL(bx,y1,bx,y2,used?GREY:COL_UNUSED,'dn',false);
      // 구간 경계 = 실제 이음새 → 다른 정션과 같은 규격·같은 색 규칙(passCol)의 점을 찍는다.
      // ★ 양 끝(맨 위·맨 아래)과 '행 위치'는 제외한다 — 행에는 아래 조인트 점이 따로 찍히므로
      //   겹쳐 그리면 표식이 두 개가 된다. 남는 건 합류점 같은 중간 경계뿐이다.
      if(i>0 && !ys0.includes(y1)) dots+=fDot(bx,y1,passCol(rows,merge,y1));
    }
    return out;
  };
  const busRows=route=>channels
    .map((c,i)=>(c.route===route&&bys[i]!=null)?{y:bys[i],ch:c}:null).filter(Boolean);
  // ★ 수집 버스·정션·4-way 입력은 전부 MFC '이후' 구간이다 — 판정은 svOn 하나로 통일한다.
  //   (별칭을 두면 한쪽만 고쳐져 선은 회색인데 탭만 색이 남는 어긋남이 생긴다.)
  const pureRows=channels.map((c,i)=>c.route==='pure'?bys[i]:null).filter(v=>v!=null);
  const mixRows=channels.map((c,i)=>c.route==='mix'?bys[i]:null).filter(v=>v!=null);
  const pureF=channels.map((c,i)=>c.route==='pure'&&svOn(c)?bys[i]:null).filter(v=>v!=null);
  const mixF=channels.map((c,i)=>c.route==='mix'&&svOn(c)?bys[i]:null).filter(v=>v!=null);
  // ★ mix 블록 안에서만 쓰던 값들을 바깥으로 올린다 — 행 접점(passCol)과 4-way 입력색이
  //   같은 값을 참조해야 한다. 아래에서 재계산하던 중복 블록은 삭제했다(계산은 한 곳).
  let gas1Y=null, airFlowY=[], gasFlowY=[];
  const BLEND=COL_BLEND;
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
    const airFeed=`M${bx} ${pureMidRow} H${cCx} V${cCy-r}`;   // 밸브 위쪽 입력점까지
    // fixed structural line (always, no flow) — 구간별로 쪼개 미사용 구간을 흐리게
    p+=busStruct(busRows('pure'), pureMidRow);
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
    const mtop=Math.min(...mixRows);
    // 합류점 = '표시상 가장 위에 있는 가스 행'. 채널 인덱스 순서가 아니라 y로 고른다
    //   — 레인 표시 순서를 바꿔도 공기 구간/가스 구간 경계가 따라온다.
    const gasRowYs=channels.map((c,i)=>c.grp==='gas'?bys[i]:null).filter(v=>v!=null);
    gas1Y=gasRowYs.length?Math.min(...gasRowYs):mtop;
    // Y's of channels that are ACTUALLY flowing (valves open), per group
    airFlowY=channels.map((c,i)=>c.grp==='air'&&c.route==='mix'&&svOn(c)?bys[i]:null).filter(v=>v!=null);
    gasFlowY=channels.map((c,i)=>c.grp==='gas'&&c.route==='mix'&&svOn(c)?bys[i]:null).filter(v=>v!=null);
    const airMixFlow=airFlowY.length>0, gasFlow=gasFlowY.length>0;
    const feedColor=(airMixFlow&&gasFlow)?BLEND:(airMixFlow?BLUE:(gasFlow?RED:GREY));
    // grey structural bus — 구간별로 쪼개 미사용 구간을 흐리게(합류점 = gas1Y)
    p+=busStruct(busRows('mix'), gas1Y);
    // air-dilution flow: only from the topmost FLOWING air tap down to the junction
    if(airMixFlow) p+=fL(bx,Math.min(...airFlowY),bx,gas1Y,BLUE,'dn',true);
    // gas flow: only from the junction down to the deepest FLOWING gas tap
    if(gasFlow) p+=fL(bx,gas1Y,bx,Math.max(...gasFlowY),RED,'up',true);
    // combined feeder gas1 row → card bottom
    const feeding=airMixFlow||gasFlow;
    p+=fP(`M${bx} ${gas1Y} H${cCx} V${cCy+r}`,feeding?feedColor:GREY,feeding);   // 밸브 아래쪽 입력점까지
  }

  // 구간 경계 점 — 두 버스의 흐름 오버레이를 모두 그린 뒤에 붙인다(점이 색 선에 가리지 않게).
  p+=dots;

  // endcap joints — 색은 '이 점을 지나는 흐름'(passCol)이다.
  // ★ 자기 채널만 보면, 꺼진 행(예: VA4)의 회색 링이 그 위를 지나가는 파란 흐름 위에
  //   구멍처럼 얹힌다. 점도 선과 같은 통과 규칙을 써야 이어져 보인다.
  //   단독(pure) 라인은 4-way로 직행하므로 혼합 버스 위를 '점 없이 통과'해야 한다 —
  //   점을 찍으면 합류하는 것처럼 보인다(표시 순서가 바뀌며 pure 행이 버스 한가운데 온다).
  channels.forEach((ech,i)=>{
    if(bys[i]==null) return;
    const onMixBus=ech.route==='mix', onPureBus=ech.route==='pure'&&pureRows.length>1;
    if(!onMixBus&&!onPureBus) return;
    // 규격은 다른 이음새(소스점·합류점·구간 경계)와 같은 원(DOT_R·DOT_SW) — fDot 단일 렌더러.
    const col = onMixBus ? passCol(busRows('mix'), gas1Y, bys[i])
                         : passCol(busRows('pure'), pureMidRow, bys[i]);
    p+=fDot(bx,bys[i],col);
  });

  /* ── 4-way 밸브 = 둥근 테두리 박스 + 스타일 C 대각선(반대 삼각형, 가운데 빔). 출력색 = 들어온 입력색. ── */
  // 입력 색: 위=순수공기(파랑), 아래=mix(실제 흐름 기준: 공기희석=파랑/가스=빨강/둘다=보라)
  // ★ airFlowY/gasFlowY/BLEND 는 위 mix 블록에서 이미 구했다 — 재계산하지 않는다(단일 출처).
  const topFlow=pureF.length>0, topCol=BLUE;
  const botFlow=mixF.length>0;
  const botCol=(airFlowY.length>0&&gasFlowY.length>0)?BLEND:(airFlowY.length>0?BLUE:(gasFlowY.length>0?RED:GREY));
  const vTop=[cCx,cCy-r], vBot=[cCx,cCy+r], vRight=[cCx+r,cCy], vLeft=[cCx-r,cCy];
  /* ★ routeOut = '혼합(mix) 라인이 가는 곳' (백엔드 단일 정의).
       위 포트 = 단독(pure) 에어, 아래 포트 = 혼합(mix) 매니폴드,
       오른쪽 = Sensor, 왼쪽 = Vent.
     실물 밸브의 무전원(코일 OFF) 위치 = gas→Vent / air→Sensor 이고,
     이때 백엔드 routeOut 은 'vent' 다(want_4w=False). 즉 아래 포트(mix)가
     routeOut 을 그대로 따라가고, 위 포트(에어)는 항상 그 반대편으로 간다.
     ※ 이전 코드는 위 포트를 routeOut 에 연결해 두 상태 모두 반대로 그렸다. */
  const topTo=senOn?vLeft:vRight, botTo=senOn?vRight:vLeft;
  // 테두리 박스(입·출력 파이프가 4변 통과, 안쪽 배경색) — 대각선보다 먼저 그려 대각선이 위로.
  p+=`<rect x="${cCx-r}" y="${cCy-r}" width="${r*2}" height="${r*2}" rx="${6*sc}" style="fill:var(--bg2)" stroke="#6b7686" stroke-width="${(1.6*sc).toFixed(2)}"/>`;
  p+=fL(vTop[0],vTop[1],topTo[0],topTo[1],topFlow?topCol:GREY,'dn',topFlow);
  p+=fL(vBot[0],vBot[1],botTo[0],botTo[1],botFlow?botCol:GREY,'dn',botFlow);
  const L=46*sc;
  // 출력 파이프 색 = '그 출구로 들어온 입력'의 색 — 위 대각선 반전과 짝이다.
  const senSrcFlow=senOn?botFlow:topFlow, senSrcCol=senOn?botCol:topCol;
  const venSrcFlow=senOn?topFlow:botFlow, venSrcCol=senOn?topCol:botCol;
  p+=fL(vRight[0],vRight[1],cCx+r+L,cCy,senSrcFlow?senSrcCol:GREY,'dn',senSrcFlow);
  p+=fL(vLeft[0],vLeft[1],cCx-r-L,cCy,venSrcFlow?venSrcCol:GREY,'dn',venSrcFlow);
  // 4-way 4포트도 다른 이음새와 같은 렌더러·규격(fDot) — 속 채운 dotC 별종을 없앴다.
  p+=fDot(vTop[0],vTop[1],topFlow?topCol:GREY);
  p+=fDot(vBot[0],vBot[1],botFlow?botCol:GREY);
  p+=fDot(vRight[0],vRight[1],senSrcFlow?senSrcCol:GREY);
  p+=fDot(vLeft[0],vLeft[1],venSrcFlow?venSrcCol:GREY);
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
