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
function openSetup(){ buildSetupRows(); setupOverlay.classList.add('on'); }
function closeSetup(){ setupOverlay.classList.remove('on'); }
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
  return {channels:chans, params, settings};
}
function applySetup(){
  const {channels:chans, params, settings}=collectSetup();
  window.cmdApplySetup(chans, params, settings);
  // sync a few process params into the Auto Process panel inputs for immediate feedback
  const set=(id,el)=>{const a=document.getElementById(id),b=document.getElementById(el);if(a&&b)b.value=a.value;};
  set('setVStart','vStart'); set('setVEnd','vEnd'); set('setGraf','grafInt'); set('setLoop','loopCount'); set('setComp','smuComp');
  closeSetup();
}
document.getElementById('openSetup').addEventListener('click',openSetup);
document.getElementById('closeSetup').addEventListener('click',closeSetup);
document.getElementById('cancelSetup').addEventListener('click',closeSetup);
document.getElementById('applySetup').addEventListener('click',applySetup);
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
