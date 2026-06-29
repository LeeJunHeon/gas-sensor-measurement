/* schematic.js — 배관도(채널/밸브) 렌더 + 이벤트 + drawBuses.
   다른 파일 함수는 window.* 노출분을 사용. 전역 노출/초기화는 core.js가 담당. */
/* ===================== channel model ===================== */
// 초기값은 모두 0 / 비어 있음. 실제 값은 서버 state(또는 config 로드)가 채운다.
let channels = [
  {grp:'air', route:'pure', max:2000, sv:0, pv:0, en:true},   // VA1
  {grp:'air', route:'pure', max:2000, sv:0, pv:0, en:false},  // VA2
  {grp:'air', route:'mix',  max:2000, sv:0, pv:0, en:true},   // VA3
  {grp:'air', route:'mix',  max:2000, sv:0, pv:0, en:false},  // VA4
  {grp:'gas', route:'mix',  max:2000, sv:0, pv:0, en:true},   // VA5
  {grp:'gas', route:'mix',  max:200,  sv:0, pv:0, en:true},   // VA6
  {grp:'gas', route:'mix',  max:200,  sv:0, pv:0, en:false},  // VA7
  {grp:'gas', route:'mix',  max:100,  sv:0, pv:0, en:false},  // VA8
];
// derive display fields (label/color/sub) from group — does NOT reorder.
// 서버가 채널 인덱스/순서/id의 주인이므로 화면은 받은 순서를 그대로 쓴다.
function deriveDisplay(){
  let gasN=0;
  channels.forEach((c,i)=>{
    if(!c.id) c.id='VA'+(i+1);
    c.color = c.grp==='gas' ? 'var(--g1)' : 'var(--air)';
    if(c.grp==='gas'){ gasN++; c.label='Gas '+gasN; c.sub=''; }
    else { c.label='Air'; c.sub = c.route==='pure'?'순수 (pure)':'혼합 (mix)'; }
  });
}
// 초기 로컬 기본값만 그룹 순으로 정렬(서버 연결 전 한 번). 서버 state 반영 시엔 정렬하지 않는다.
function relabel(){
  const rank=c=> c.grp==='gas' ? 2 : (c.route==='pure' ? 0 : 1);
  channels.sort((a,b)=>rank(a)-rank(b));
  channels.forEach((c,i)=>{ c.id='VA'+(i+1); });
  deriveDisplay();
}
relabel();
channels.forEach(c=>{c.valveIn=c.en;});
const flowing=c=>c.en&&c.valveIn;

const valveSvg = `<svg width="34" height="22" viewBox="0 0 34 22">
  <line class="vstem" x1="17" y1="11" x2="17" y2="4"/><rect class="vact" x="12" y="0" width="10" height="5" rx="1"/>
  <path class="vb" d="M4 5 L17 11 L4 17 Z"/><path class="vb" d="M30 5 L17 11 L30 17 Z"/></svg>`;
const mfcSvg = `<svg width="40" height="34" viewBox="0 0 40 34">
  <rect class="mb" x="2" y="3" width="36" height="22" rx="3" transform="rotate(-9 20 14)"/>
  <rect class="mt" x="7" y="6" width="26" height="8" rx="1.5" transform="rotate(-9 20 10)"/>
  <text class="mtxt" x="11" y="12" transform="rotate(-9 20 10)">Tylan</text>
  <path class="ar" d="M9 22 H29 M25 18 L29 22 L25 26"/></svg>`;
const tankSvg = c => `<svg width="20" height="28" viewBox="0 0 20 28">
  <rect x="7" y="0" width="6" height="4" rx="1" fill="${c}"/><rect x="4" y="3" width="12" height="5" rx="2" fill="${c}"/>
  <rect x="2" y="7" width="16" height="20" rx="6" fill="${c}" opacity=".18" stroke="${c}" stroke-width="1.6"/></svg>`;

const lanesEl = document.getElementById('lanes');
function dec(c){return c.max<=100?1:0;}
function renderLanes(){
  lanesEl.innerHTML='';
  channels.forEach((c,idx)=>{
    const d=dec(c);
    const lane=document.createElement('div');
    lane.className='lane'+(flowing(c)&&c.pv>0?' lit':'')+(c.en?'':' off');
    lane.dataset.grp=c.grp; lane.dataset.idx=idx;
    const showLabel = '';
    // 물탱크(가습기): VA2·VA4 레인에만 그리고, 나머지는 같은 폭의 빈 자리로 둬 MFC 정렬을 맞춘다.
    const hasTank = (c.id==='VA2'||c.id==='VA4');
    lane.innerHTML=`
      <div class="n-src">
        <span class="srclbl">${showLabel}</span><span class="tap"></span>
      </div>
      <i class="pipe ${c.en?'on':''}" data-seg="pre" style="--c:${c.color}"></i>
      <div class="n-valve ${c.valveIn?'open':'closed'}${c.en?'':' dis'}" data-v="${idx}-in" title="MFC 밸브 (VA)">${valveSvg}<span class="vlbl">${c.id}</span></div>
      <i class="pipe ${c.en&&c.valveIn?'on':''}" data-seg="mid" style="--c:${c.color}"></i>
      <div class="n-tank" title="${hasTank?'물탱크 (가습기)':''}">${hasTank?tankSvg('#3a9fe0'):''}</div>
      <div class="n-mfc ${c.en&&c.valveIn?'on':''}${c.en?'':' dis'}">
        <div class="mfc-read">
          <span class="vid">${c.id} · MFC</span>
          <div class="pvrow"><span class="rlbl">PV</span><span class="pvb" data-pv="${idx}">${c.pv.toFixed(d)}</span><span class="un" style="visibility:hidden">sccm</span></div>
          <span class="maxwrap">MAX<input value="${c.max}" size="4" data-max="${idx}" title="MFC 최대 용량" ${c.en?'':'disabled'}></span>
          <div class="svrow"><span class="rlbl">SV</span><input class="svi" size="4" value="${c.sv.toFixed(d)}" data-sv="${idx}" ${c.en?'':'disabled'}><span class="un">sccm</span></div>
        </div>
      </div>
      <i class="pipe grow ${c.en&&c.valveIn?'on':''}" data-seg="post" style="--c:${c.color}"></i>
      <span class="endcap"></span>`;
    lanesEl.appendChild(lane);
  });
  bindLaneEvents();
  drawBuses();
  updateSystem();
}

function bindLaneEvents(){
  // \uc0ac\uc6a9\uc790 \ub3d9\uc791 = \uc694\uccad. \uc9c1\uc811 \uc0c1\ud0dc\ub97c \ubc14\uafb8\uc9c0 \uc54a\uace0 app.js \uba85\ub839 \ud568\uc218\ub85c \ubcf4\ub0b8\ub2e4.
  // \ud654\uba74\uc740 \uc11c\ubc84 state(\ub610\ub294 \ub04a\uae40 \uc2dc \uc2dc\ubbac\ub808\uc774\uc158 \ub300\uccb4)\uac00 \uc640\uc57c \uac31\uc2e0\ub41c\ub2e4.
  document.querySelectorAll('[data-max]').forEach(inp=>inp.addEventListener('change',e=>{
    window.cmdSetMax(+e.target.dataset.max, +e.target.value||0);
  }));
  document.querySelectorAll('[data-sv]').forEach(inp=>inp.addEventListener('change',e=>{
    window.cmdSetSv(+e.target.dataset.sv, +e.target.value||0);
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

// 4-Way 카드 내부 십자(크로스) 라우팅을 인라인 SVG로 그린다(카드 위에 항상 보이게).
// 위=Air 입력, 아래=Gas 입력, 오른쪽=Sensor 출력, 왼쪽=Vent 출력. routeOut에 따라 ㄱㄴ 경로 반전.
// 활성(흐름 있음) 경로는 색+움직이는 흰 줄무늬, 비활성은 회색. (fL/fP와 동일한 룩)
function renderVcCross(airFlow, gasFlow){
  const el=document.getElementById('vcCross'); if(!el) return;
  const sen=routeOut==='sensor';
  const BLUE='#2f72c4', RED='#c8384c', GREY='#cdd5e0', VENT='#e0851f', PV='#0d7a6a', OFF='#9aa6b6';
  // local viewBox 188×92 — center(94,46), Air(top 94,12) Gas(bot 94,80) Vent(left 44,46) Sensor(right 144,46)
  const airOutX=sen?144:44, gasOutX=sen?44:144;
  const airD=`M94 12 V46 H${airOutX}`;     // 위(Air) → 중앙 → 좌/우 출력
  const gasD=`M94 80 V46 H${gasOutX}`;     // 아래(Gas) → 중앙 → 좌/우 출력
  const airCol=airFlow?BLUE:GREY, gasCol=gasFlow?RED:GREY;
  let s=`<svg viewBox="0 0 188 92" preserveAspectRatio="xMidYMid meet">`;
  s+=`<path class="base" d="${airD}" stroke="${airCol}"/>`;
  s+=`<path class="base" d="${gasD}" stroke="${gasCol}"/>`;
  if(airFlow) s+=`<path class="stripe" d="${airD}"/>`;
  if(gasFlow) s+=`<path class="stripe" d="${gasD}"/>`;
  const dot=(x,y,col)=>`<circle cx="${x}" cy="${y}" r="4.5" fill="${col}"/>`;
  s+=dot(94,12, airFlow?BLUE:OFF);
  s+=dot(94,80, gasFlow?RED:OFF);
  s+=dot(144,46, (sen?airFlow:gasFlow)?PV:OFF);    // Sensor 포트
  s+=dot(44,46,  (sen?gasFlow:airFlow)?VENT:OFF);  // Vent 포트
  s+=`<text class="plabel" x="94" y="8" text-anchor="middle">Air</text>`;
  s+=`<text class="plabel" x="94" y="91" text-anchor="middle">Gas</text>`;
  s+=`<text class="plabel" x="38" y="49" text-anchor="end">Vent</text>`;
  s+=`<text class="plabel" x="150" y="49" text-anchor="start">Sensor</text>`;
  s+=`</svg>`;
  el.innerHTML=s;
}

/* ===================== manifold buses ===================== */
let routeOut='sensor';
function drawBuses(){
  const svg=document.getElementById('wires'); if(!svg) return;
  const S=svg.parentElement.getBoundingClientRect();
  const cx=el=>{const r=el.getBoundingClientRect();return r.left-S.left+r.width/2;};
  const cy=el=>{const r=el.getBoundingClientRect();return r.top-S.top+r.height/2;};
  const probe=document.querySelector('.lane .endcap');
  if(!S.width||!probe||probe.getBoundingClientRect().height===0){setTimeout(drawBuses,100);return;}
  svg.setAttribute('viewBox',`0 0 ${S.width} ${S.height}`);

  const BLUE='#2f72c4', RED='#c8384c', MIX='#c8384c', GREY='#b6c4d6', PV='#0d7a6a', VENT='#e0851f';
  // pipe look = solid colored base + white moving stripes (matches horizontal CSS pipes)
  let p=`<style>
    .stripe{stroke:rgba(255,255,255,.7);stroke-width:4;stroke-dasharray:5 17;stroke-linecap:butt;fill:none}
    .sdn{animation:sdn 1.1s linear infinite}
    .sup{animation:sup 1.1s linear infinite}
    @keyframes sdn{to{stroke-dashoffset:-22}}
    @keyframes sup{to{stroke-dashoffset:22}}
  </style>`;
  // flow line: base + white stripe overlay; dir: 'dn' (toward 2nd point) or 'up' (toward 1st)
  const fL=(x1,y1,x2,y2,col,dir,on)=> on
    ? `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="4" stroke-linecap="round"/><line class="stripe ${dir==='up'?'sup':'sdn'}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`
    : `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="4" stroke-linecap="round"/>`;
  const fP=(d,col,on)=> on
    ? `<path d="${d}" fill="none" stroke="${col}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><path class="stripe sdn" d="${d}" stroke-linejoin="round"/>`
    : `<path d="${d}" fill="none" stroke="${col}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`;
  const Bbox=(x,y)=>`<rect x="${x-13}" y="${y-13}" width="26" height="26" rx="6" fill="#f0ece2" stroke="#b9ad8e" stroke-width="1.6"/><text x="${x}" y="${y+4}" text-anchor="middle" font-size="11" font-weight="700" fill="#8a7c55">B</text>`;

  /* ── Air supply left manifold ── */
  const airTaps=[...document.querySelectorAll('.lane[data-grp="air"] .tap')];
  const airChs=channels.filter(c=>c.grp==='air');
  if(airTaps.length){
    const ax=cx(airTaps[0]); const ays=airTaps.map(cy);
    const enAirYs=ays.filter((_,i)=>airChs[i]&&airChs[i].en);
    const has=enAirYs.length>0;
    const topY=has?Math.min(...enAirYs):Math.min(...ays);
    const botY=has?Math.max(...enAirYs):Math.max(...ays);
    const xIn=ax-42;
    // left inlet pipe + inlet cap (label sits at its left end)
    p+=fL(xIn,topY,ax,topY,BLUE,'dn',has);
    p+=`<circle cx="${xIn}" cy="${topY}" r="4.5" fill="#fff" stroke="${BLUE}" stroke-width="2.4"/>`;
    // vertical manifold: flow only across the enabled air span
    if(has) p+=fL(ax,topY,ax,botY,BLUE,'dn',true);
    {const sc=(typeof lastScale==='number'&&lastScale>0)?lastScale:1; const al=document.getElementById('airsupply'); al.style.left=((xIn-8)/sc)+'px'; al.style.top=(topY/sc)+'px';}
    airTaps.forEach((t,i)=>{const on=airChs[i]&&airChs[i].en;p+=`<circle cx="${ax}" cy="${ays[i]}" r="3.5" fill="${on?BLUE:GREY}" opacity="${on?1:0.45}"/>`;});
  }

  /* ── Gas inlets: each lane = ONE continuous line from inlet cap to VA valve ── */
  const gasTaps=[...document.querySelectorAll('.lane[data-grp="gas"] .tap')];
  const gasChs=channels.filter(c=>c.grp==='gas');
  const flowC=c=>c.en&&c.valveIn;
  const glayer=document.getElementById('gaslabels'); if(glayer) glayer.innerHTML='';
  const scG=(typeof lastScale==='number'&&lastScale>0)?lastScale:1;
  gasTaps.forEach((t,i)=>{
    const lane=t.closest('.lane');
    const valve=lane.querySelector('.n-valve');
    const gx=cx(t), gy=cy(t); const ch=gasChs[i]; const on=ch&&ch.en;
    const xIn=gx-42;
    const vx=valve?(valve.getBoundingClientRect().left-S.left):gx+120;
    const col=on?RED:'#bcc6d3';
    // hide the HTML pre-pipe so this is a single SVG line
    const pre=lane.querySelector('.pipe[data-seg="pre"]'); if(pre) pre.style.visibility='hidden';
    // pre-segment flows whenever enabled (supply reaches the valve), like air
    let seg=fL(xIn,gy,vx,gy,col,'dn',on)+`<circle cx="${xIn}" cy="${gy}" r="4.5" fill="#fff" stroke="${col}" stroke-width="2.4"/>`;
    if(!on) seg=`<g opacity="0.42">${seg}</g>`;   // match disabled air lanes (.lane.off opacity:.42)
    p+=seg;
    if(glayer&&ch){
      const d=document.createElement('div'); d.className='gaslbl'+(ch.en?'':' off');
      d.textContent=ch.label; d.style.left=((xIn-8)/scG)+'px'; d.style.top=(gy/scG)+'px';
      glayer.appendChild(d);
    }
  });

  /* ── Right collection (flow only where valves pass) ── */
  const caps=[...document.querySelectorAll('.lane .endcap')];
  if(!caps.length){svg.innerHTML=p;return;}
  const bx=cx(caps[0]); const bys=caps.map(cy);
  const flow=c=>c.en&&c.valveIn;
  const pureRows=channels.map((c,i)=>c.route==='pure'?bys[i]:null).filter(v=>v!=null);
  const mixRows=channels.map((c,i)=>c.route==='mix'?bys[i]:null).filter(v=>v!=null);
  const pureF=channels.map((c,i)=>c.route==='pure'&&flow(c)?bys[i]:null).filter(v=>v!=null);
  const mixF=channels.map((c,i)=>c.route==='mix'&&flow(c)?bys[i]:null).filter(v=>v!=null);
  const sc=(typeof lastScale==='number'&&lastScale>0)?lastScale:1;
  const vcR=24, vcX=bx+138*sc, jx=bx+93*sc;
  const pureMidRow=pureRows.length?(Math.min(...pureRows)+Math.max(...pureRows))/2:S.height*0.25;
  const mixMidRow=mixRows.length?(Math.min(...mixRows)+Math.max(...mixRows))/2:S.height*0.6;
  const vcY=S.height*0.34;   // 4-way moved up to leave room for the log panel bottom-right

  // card (4-way) geometry — measured then scaled to SVG units (= visual px)
  const vcEl=document.getElementById('vcControl');
  const cardW=(vcEl?vcEl.offsetWidth:150)*sc;
  const cardH=(vcEl?vcEl.offsetHeight:120)*sc;
  const cLeft=vcX, cCx=vcX+cardW/2, cRight=vcX+cardW, cTop=vcY-cardH/2, cBot=vcY+cardH/2;
  const senOn=routeOut==='sensor';

  // AIR (pure) bus — FIXED grey structure across ALL rows + coloured flow only on the FLOWING span.
  // 4-way 신배치: Air 버스는 카드 위(top-center)로 들어간다(ㄱ 모양: 가로 → 아래로 꺾여 top 포트).
  if(pureRows.length>0){
    const ptop=Math.min(...pureRows), pbot=Math.max(...pureRows);
    const airFeed=`M${bx} ${pureMidRow} H${cCx} V${cTop}`;
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
    const gas1Idx=channels.findIndex(c=>c.grp==='gas');
    const gas1Y=(gas1Idx>=0)?bys[gas1Idx]:mtop;
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
    p+=fP(`M${bx} ${gas1Y} H${cCx} V${cBot}`,feeding?feedColor:GREY,feeding);
  }

  // endcap joints (coloured only where that channel actually flows)
  caps.forEach((c,i)=>{
    const ech=channels[i]; const col=ech.grp==='gas'?RED:BLUE;
    p+=`<rect x="${bx-6}" y="${bys[i]-4}" width="12" height="8" rx="3" fill="#cfd8e3" stroke="${flow(ech)?col:GREY}" stroke-width="1.2"/>`;
  });

  /* ── 4-way valve = card. Ports: Air(top in) · Gas(bottom in) · Sensor(right out) · Vent(left out).
       ㄱㄴ 내부 라우팅은 카드 인라인 SVG(renderVcCross)가 그린다. 여기선 외부 출력 스텁만. ── */
  const airFlow=pureF.length>0, gasFlow=mixF.length>0;
  // 현재 라우팅에 따라 각 출력이 받는 소스가 흐르는지: sensor 모드면 Sensor←Air, Vent←Gas / vent 모드면 반전.
  const senFlow = senOn ? airFlow : gasFlow;
  const ventFlow = senOn ? gasFlow : airFlow;
  // Sensor output stub (right from right-center)
  p+=fL(cRight,vcY,cRight+41*sc,vcY,(senFlow?PV:GREY),'dn',senFlow);
  // Vent output stub (left from left-center)
  p+=fL(cLeft,vcY,cLeft-41*sc,vcY,(ventFlow?VENT:GREY),'dn',ventFlow);

  renderVcCross(airFlow, gasFlow);
  updateWayToggle();

  svg.innerHTML=p;
  // position card so LEFT edge sits at the air inlet (convert scaled px → layout px)
  if(vcEl){vcEl.style.right='auto'; vcEl.style.left=(vcX/sc)+'px'; vcEl.style.top=(vcY/sc)+'px';}
}
