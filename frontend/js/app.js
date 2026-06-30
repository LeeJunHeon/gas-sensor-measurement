/* ============================================================
 * app.js — 화면(index.html) ↔ 서버(server.py) 연동
 *
 *  - WebSocket(ws://host/ws)으로 서버에 연결한다.
 *  - 사용자 동작은 명령(cmd*)으로 서버에 보낸다. 화면은 서버 state가 와야 갱신된다.
 *  - 서버가 telemetry를 push하면 가볍게 화면에 반영한다(applyTelemetry).
 *  - 서버가 끊기면 "연결 끊김" 표시 + 마지막 값 유지 + 시뮬레이션 대체 + 2초마다 재연결.
 *
 * 통신 약속은 INTERFACE.md 참고. index.html이 노출하는 함수만 사용한다:
 *   applyState, applyTelemetry, logMsg, collectRecipe, collectSetup,
 *   renderLanes, renderRecipe, drawBuses, updateSystem, window.channels/procs
 * ============================================================ */
(function () {
  'use strict';

  let ws = null;
  let connected = false;
  let connState = null;          // 마지막으로 표시한 연결상태(로그 중복 방지)
  let reconnectTimer = null;
  let lastSave = null;           // 덮어쓰기 확인용 마지막 저장 요청

  // ---- 로컬 상태 미러: 서버 끊김 시 시뮬레이션/낙관적 갱신의 기준 ----
  let mirror = buildInitialMirror();

  function buildInitialMirror() {
    const chans = (window.channels || []).map(c => Object.assign({}, c));
    return {
      channels: chans,
      system: {
        running: false, routeOut: 'sensor',
        loop: { current: 0, total: 1 },
        elapsed: 0, rh: null, smu: null,
        connected: false, safeStop: false,
      },
      recipe: {
        name: '', useHumidity: true, loopCount: 1, procs: [],
        params: {
          vStart: 0, vEnd: 0, vStep: 0, grafInterval: 1,
          smuMode: 'Source V, Measure I', smuSource: 0,
          smuCompliance: 1.0, chFrom: 1, chTo: 1,
        },
      },
    };
  }

  function deepCopy(o) { return JSON.parse(JSON.stringify(o)); }

  // ===================== WebSocket =====================
  function connect() {
    try {
      ws = new WebSocket(`ws://${location.host}/ws`);
    } catch (e) {
      scheduleReconnect();
      return;
    }
    ws.onopen = () => {
      connected = true;
      setConn(true);
      stopSim();
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleMessage(msg);
    };
    ws.onclose = () => { onDisconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  function onDisconnect() {
    connected = false;
    setConn(false);
    startSim();
    scheduleReconnect();
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 2000);
  }

  function setConn(c) {
    const pill = document.getElementById('connStatus');
    if (pill) {
      pill.classList.toggle('conn', c);
      pill.classList.toggle('disc', !c);
      const txt = pill.querySelector('.ctxt');
      if (txt) txt.textContent = c ? '연결됨' : '연결 끊김';
    }
    if (connState !== c) {
      connState = c;
      if (c) window.logMsg('서버 연결됨', 'ok');
      else window.logMsg('서버 연결 끊김 — 시뮬레이션 모드', 'warn');
    }
  }

  function send(obj) {
    if (ws && connected) {
      try { ws.send(JSON.stringify(obj)); return true; }
      catch (e) { return false; }
    }
    return false;
  }

  // ===================== 서버 → 화면 메시지 처리 =====================
  function handleMessage(msg) {
    switch (msg && msg.type) {
      case 'state':
        mirror = { channels: deepCopy(msg.channels || []),
                   system: deepCopy(msg.system || mirror.system),
                   recipe: deepCopy(msg.recipe || mirror.recipe) };
        window.applyState(msg);
        break;
      case 'telemetry':
        // 미러에도 빠른 값 반영(시뮬레이션 전환 시 연속성 유지)
        if (Array.isArray(msg.pv)) msg.pv.forEach((v, i) => { if (mirror.channels[i]) mirror.channels[i].pv = v; });
        if (msg.elapsed != null) mirror.system.elapsed = msg.elapsed;
        window.applyTelemetry(msg);
        break;
      case 'log':
        window.logMsg(msg.msg, msg.level);
        break;
      case 'recipe_list':
        showRecipePicker(msg.names || []);
        break;
      case 'ack':
        handleAck(msg);
        break;
    }
  }

  function handleAck(msg) {
    if (msg.of === 'run' && !msg.ok) {
      // AUTO RUN 시작 불가(계산/MAX 검증 실패) → 사유 팝업(서버가 로그는 별도로 push)
      const probs = msg.problems || [];
      window.appAlert((probs.length ? probs.join('\n') : (msg.reason || '알 수 없는 오류')), 'AUTO RUN 시작 불가');
      return;
    }
    if (msg.of !== 'recipe_save') return;
    if (msg.ok) return;                       // 성공 로그는 서버가 push
    if (msg.reason === 'exists') {
      window.appConfirm('같은 이름이 있습니다. 덮어쓸까요?', () => {
        if (lastSave) send({ cmd: 'recipe_save', name: lastSave.name, overwrite: true, recipe: lastSave.recipe });
      }, '덮어쓰기 확인');
    } else if (msg.reason === 'invalid') {
      window.logMsg('레시피 저장 실패 — 잘못된 이름', 'err');
    } else {
      window.logMsg('레시피 저장 실패', 'err');
    }
  }

  // ===================== 명령(화면 → 서버) =====================
  // 연결 시: 서버로 전송(요청). 끊김 시: 미러를 낙관적으로 갱신 후 applyState로 재렌더.
  // withRecipe=false(기본): 레시피(초안)는 건드리지 않는다 — 서버의 일상 state push와 동일하게,
  //   HMI 동작(밸브/4-way/RUN 등)으로 편집 중인 레시피가 사라지지 않도록 한다.
  function localApply(mutator, withRecipe) {
    try { mutator(mirror); } catch (e) {}
    const snap = deepCopy(mirror);
    if (!withRecipe) delete snap.recipe;
    window.applyState(snap);
  }

  window.cmdSetValve = function (ch, open) {
    if (send({ cmd: 'set_valve', ch: ch, open: open })) return;
    localApply(m => {
      const c = m.channels[ch];
      if (!c || !c.en) return;
      c.valveIn = open;
    });
  };
  window.cmdSetSv = function (ch, value) {
    if (send({ cmd: 'set_sv', ch: ch, value: value })) return;
    localApply(m => { const c = m.channels[ch]; if (c) c.sv = Math.max(0, Math.min(+value || 0, +c.max || 0)); });
  };
  window.cmdSetMax = function (ch, value) {
    if (send({ cmd: 'set_max', ch: ch, value: value })) return;
    localApply(m => { const c = m.channels[ch]; if (c) c.max = Math.max(0, +value || 0); });
  };
  window.cmdSet4way = function (route) {
    if (send({ cmd: 'set_4way', route: route })) return;
    localApply(m => { m.system.routeOut = route; });
  };
  window.cmdRun = function () {
    if (send({ cmd: 'run' })) return;
    localApply(m => { m.system.running = true; m.system.elapsed = 0; m.system.loop.current = 0; });
    simElapsed = 0;
  };
  window.cmdStop = function () {
    if (window.flashHdrStatus) window.flashHdrStatus('정지됨', 'stop');
    if (send({ cmd: 'stop' })) return;
    localApply(m => { m.system.running = false; });
  };
  window.cmdPurge = function () {
    if (window.flashHdrStatus) window.flashHdrStatus('퍼지 중', 'purge');
    if (send({ cmd: 'purge' })) return;
    window.logMsg('PURGE — 순수 Air로 라인 청소 (시뮬레이션)', 'info');
  };
  window.cmdExit = function () {
    // 브라우저 기본 confirm 대신 앱 내부 모달(window.confirmExit) 사용.
    var doExit = function () {
      // 서버가 pywebview 창을 닫아 프로세스를 종료한다.
      if (!send({ cmd: 'exit' })) {
        window.logMsg('오프라인 — 서버에 연결되어야 종료할 수 있습니다', 'warn');
        return;
      }
      window.logMsg('프로그램 종료 중...', 'warn');
    };
    if (typeof window.confirmExit === 'function') window.confirmExit(doExit);
    else window.appConfirm('프로그램을 종료하시겠습니까?', doExit, '프로그램 종료');   // 폴백
  };
  // 창 우상단 X → 서버(_on_closing)가 호출. PROGRAM END와 동일한 종료확인 모달을 띄운다.
  window.requestExitConfirm = function () { window.cmdExit(); };
  window.cmdApplySetup = function (channels, params) {
    if (send({ cmd: 'apply_setup', channels: channels, params: params })) return;
    localApply(m => {
      (channels || []).forEach(item => {
        const c = m.channels[item.ch];
        if (!c) return;
        const wasEn = c.en;
        c.en = !!item.en; c.grp = item.grp; c.route = item.route;
        c.max = item.max; c.sv = item.sv;
        if (c.en && !wasEn) { c.valveIn = true; }
        else if (!c.en) { c.valveIn = false; }
      });
      if (params) m.recipe.params = Object.assign({}, m.recipe.params, params);
    });
    window.logMsg('System Setup 적용 (시뮬레이션 — 서버 연결 시 config.json 저장)', 'warn');
  };
  window.cmdRecipeNew = function () {
    if (send({ cmd: 'recipe_new' })) return;
    localApply(m => { m.recipe = Object.assign({}, m.recipe, { name: '', procs: [] }); }, true);
  };
  window.cmdRecipeSave = function (name, recipe, overwrite) {
    lastSave = { name: name, recipe: recipe };
    if (send({ cmd: 'recipe_save', name: name, overwrite: !!overwrite, recipe: recipe })) return;
    window.logMsg('오프라인 — 서버에 연결되면 레시피를 저장할 수 있습니다', 'warn');
  };
  window.cmdRecipeLoad = function (name) {
    if (send({ cmd: 'recipe_load', name: name })) return;
    window.logMsg('오프라인 — 서버에 연결되면 레시피를 불러올 수 있습니다', 'warn');
  };
  window.cmdRecipeList = function () {
    if (send({ cmd: 'recipe_list' })) { return; }
    window.logMsg('오프라인 — 저장된 레시피 목록은 서버 연결 시 표시됩니다', 'warn');
    showRecipePicker([]);
  };

  // ===================== 레시피 선택창 =====================
  function showRecipePicker(names) {
    const overlay = document.getElementById('recipePicker');
    const list = document.getElementById('recipePickerList');
    if (!overlay || !list) return;
    list.innerHTML = '';
    if (!names.length) {
      const e = document.createElement('div');
      e.className = 'rpickempty';
      e.textContent = '저장된 레시피가 없습니다.';
      list.appendChild(e);
    } else {
      names.forEach(name => {
        const it = document.createElement('div');
        it.className = 'rpickitem';
        it.innerHTML = `<span>${name}</span><span style="color:#2a5bd0;font-weight:700">불러오기 ▸</span>`;
        it.addEventListener('click', () => {
          window.cmdRecipeLoad(name);
          overlay.classList.remove('on');
        });
        list.appendChild(it);
      });
    }
    overlay.classList.add('on');
  }
  function bindPicker() {
    const overlay = document.getElementById('recipePicker');
    const close = document.getElementById('recipePickerClose');
    if (close) close.addEventListener('click', () => overlay.classList.remove('on'));
    if (overlay) overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('on'); });
  }

  // ===================== 레시피 저장 이름 모달 (Save as) =====================
  window.openSaveName = function (preset) {
    const ov = document.getElementById('saveNameModal');
    const inp = document.getElementById('saveNameInput');
    const warn = document.getElementById('saveNameWarn');
    if (!ov || !inp) return;
    warn.textContent = '';
    inp.value = preset || '';
    ov.classList.add('on');
    setTimeout(() => { inp.focus(); inp.select(); }, 30);
  };
  function bindSaveName() {
    const ov = document.getElementById('saveNameModal');
    const inp = document.getElementById('saveNameInput');
    const warn = document.getElementById('saveNameWarn');
    const close = () => ov && ov.classList.remove('on');
    const submit = () => {
      const name = (inp.value || '').trim();
      if (!name) { warn.textContent = '이름을 입력하세요.'; return; }
      if (/[\\/:*?"<>|]/.test(name)) { warn.textContent = '사용할 수 없는 문자가 있습니다.'; return; }
      // 이름을 표 상단 입력칸에도 반영 후 저장(중복이면 서버 ack로 덮어쓰기 확인)
      const rn = document.getElementById('recname'); if (rn) rn.value = name;
      const r = (typeof collectRecipe === 'function') ? collectRecipe() : window.collectRecipe();
      r.name = name;
      window.cmdRecipeSave(name, r, false);
      close();
    };
    document.getElementById('saveNameOk')?.addEventListener('click', submit);
    document.getElementById('saveNameCancel')?.addEventListener('click', close);
    document.getElementById('saveNameClose')?.addEventListener('click', close);
    if (ov) ov.addEventListener('click', e => { if (e.target === ov) close(); });
    if (inp) inp.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') close(); });
  }

  // ===================== 공용 알림/확인 모달 =====================
  window.appAlert = function (message, title) {
    const ov = document.getElementById('alertModal');
    if (!ov) { window.alert(message); return; }   // 안전 폴백
    document.getElementById('alertModalTitle').textContent = title || '알림';
    document.getElementById('alertModalBody').textContent = message || '';
    ov.classList.add('on');
  };
  let _confirmCb = null;
  window.appConfirm = function (message, onOk, title) {
    const ov = document.getElementById('confirmModal');
    if (!ov) { if (window.confirm(message)) onOk && onOk(); return; }
    document.getElementById('confirmModalTitle').textContent = title || '확인';
    document.getElementById('confirmModalBody').textContent = message || '';
    _confirmCb = onOk || null;
    ov.classList.add('on');
  };
  function bindCommonModals() {
    const aOv = document.getElementById('alertModal');
    const aClose = () => aOv && aOv.classList.remove('on');
    document.getElementById('alertModalOk')?.addEventListener('click', aClose);
    document.getElementById('alertModalClose')?.addEventListener('click', aClose);
    if (aOv) aOv.addEventListener('click', e => { if (e.target === aOv) aClose(); });

    const cOv = document.getElementById('confirmModal');
    const cClose = () => cOv && cOv.classList.remove('on');
    document.getElementById('confirmModalOk')?.addEventListener('click', () => { cClose(); const cb = _confirmCb; _confirmCb = null; if (cb) cb(); });
    document.getElementById('confirmModalCancel')?.addEventListener('click', () => { _confirmCb = null; cClose(); });
    document.getElementById('confirmModalClose')?.addEventListener('click', () => { _confirmCb = null; cClose(); });
    if (cOv) cOv.addEventListener('click', e => { if (e.target === cOv) { _confirmCb = null; cClose(); } });
  }

  // ===================== 시뮬레이션 대체(서버 끊김 시) =====================
  let simTimer = null;
  let simElapsed = 0;
  let simLast = 0;

  function startSim() {
    if (simTimer) return;
    simElapsed = mirror.system.elapsed || 0;
    simLast = Date.now();
    simTimer = setInterval(simTick, 200);   // 5 Hz
  }
  function stopSim() {
    if (simTimer) { clearInterval(simTimer); simTimer = null; }
  }
  function simTick() {
    const now = Date.now();
    const dt = (now - simLast) / 1000;
    simLast = now;
    const running = mirror.system.running;
    if (running) simElapsed += dt;

    const pv = mirror.channels.map(c => {
      const flowing = c.en && c.valveIn;
      if (!flowing) return 0;
      const target = +c.sv || 0;
      const amp = target > 0 ? 1.6 : 0.4;
      return Math.max(0, target + (Math.random() - 0.5) * amp);
    });
    // rh·smu(측정값)는 하드웨어가 없으므로 시뮬레이션하지 않는다(화면엔 "—" 유지). PV(MFC 유량)만 시뮬.
    const total = +mirror.recipe.loopCount || 1;
    const current = running ? Math.min(total, 1 + Math.floor(simElapsed / 10)) : (mirror.system.loop.current || 0);

    window.applyTelemetry({
      pv: pv, rh: null, smu: null,
      elapsed: Math.floor(simElapsed), running: running,
      loop: { current: current, total: total },
    });
  }

  // 비상정지 — 확인 없이 즉시 전송(비상이므로). 서버 engine.emergency()가 전 채널 차단.
  document.getElementById('btnEstop')?.addEventListener('click', () => send({ cmd: 'emergency' }));

  // ===================== 시작 =====================
  bindPicker();
  bindSaveName();
  bindCommonModals();
  // 초기엔 로그 없이 pill만 "연결 끊김"으로 표시(첫 연결/실패 시 로그가 남는다).
  const pill0 = document.getElementById('connStatus');
  if (pill0) { pill0.classList.add('disc'); pill0.classList.remove('conn'); }
  connect();
})();
