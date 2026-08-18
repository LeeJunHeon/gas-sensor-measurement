/* ============================================================
 * app.js — 화면(index.html) ↔ 서버(server.py) 연동
 *
 *  - WebSocket(ws://host/ws)으로 서버에 연결한다.
 *  - 사용자 동작은 명령(cmd*)으로 서버에 보낸다. 화면은 서버 state가 와야 갱신된다.
 *  - 서버가 telemetry를 push하면 가볍게 화면에 반영한다(applyTelemetry).
 *  - 서버가 끊기면 "연결 끊김" 표시 + 모든 값 '—' + 2초마다 재연결(가짜 값을 만들지 않는다).
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
    if (window.clearLiveValues) window.clearLiveValues();
    // 서버가 없으면 PLC도 조작할 수 없다 — 컨트롤을 잠가 오조작을 막는다.
    if (window.applyPlcLock) window.applyPlcLock(false);
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
      if (txt) txt.textContent = c ? 'Server Connected' : 'Server Disconnected';
    }
    if (connState !== c) {
      connState = c;
      if (c) window.logMsg('서버 연결됨', 'ok');
      else window.logMsg('서버 연결 끊김', 'warn');
    }
    // 끊김은 헤더 상태줄에도 크게 남긴다(로그를 안 보고 있을 수 있다).
    if (window.setHdrStatus) {
      if (c) { if (window.refreshHdrStatus) window.refreshHdrStatus(); }
      else window.setHdrStatus('서버 연결 끊김', 'stop');
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
        window.applyState(msg);
        break;
      case 'telemetry':
        window.applyTelemetry(msg);
        break;
      case 'log':
        window.logMsg(msg.msg, msg.level);
        break;
      case 'recipe_list':
        showRecipePicker(msg.names || []);
        break;
      case 'plc_ports':
        if (window.applyPlcPorts) window.applyPlcPorts(msg.ports || []);
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
    if (msg.of === 'apply_setup') {
      if (window.onSetupAck) window.onSetupAck(msg);   // 모달 유지/닫기·사유 표시는 recipe.js가 담당
      if (msg.ok) return;                     // 성공 로그는 서버가 push
      (msg.problems || []).forEach(p => window.logMsg(p, 'err'));
      window.logMsg('설정이 적용되지 않았습니다 — 로그를 확인하세요', 'err');
      return;
    }
    if (msg.of !== 'recipe_save') return;
    if (msg.ok) return;                       // 성공 로그는 서버가 push
    if (msg.reason === 'exists') {
      window.appConfirm('같은 이름이 있습니다. 덮어쓸까요?', () => {
        if (lastSave) send({ cmd: 'recipe_save', name: lastSave.name, overwrite: true, recipe: lastSave.recipe });
      }, '덮어쓰기 확인');
    } else if (msg.reason === 'invalid') {
      // 서버가 사유를 보내면 그대로 보여준다(무엇이 잘못됐는지 알 수 없으면 고칠 수 없다).
      window.logMsg(msg.msg ? ('레시피 저장 실패 — ' + msg.msg) : '레시피 저장 실패 — 잘못된 이름', 'err');
    } else {
      // exists/invalid 외의 사유(io 오류 등)도 반드시 사유와 함께 남긴다 — 무음 실패 금지.
      const why = msg.msg || msg.reason || '알 수 없는 오류';
      window.logMsg('레시피 저장 실패 — ' + why, 'err');
    }
  }

  // ===================== 명령(화면 → 서버) =====================
  // 연결 시: 서버로 전송(요청). 끊김 시: 로컬로 흉내내지 않고 경고만 한다 —
  //   화면이 서버와 다른 상태를 보여주면 운전자가 오인한다(가짜 상태 금지).

  window.cmdSetValve = function (ch, open) {
    if (send({ cmd: 'set_valve', ch: ch, open: open })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  window.cmdSetSv = function (ch, value) {
    if (send({ cmd: 'set_sv', ch: ch, value: value })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  // 봄베 농도 저장(장비에 물린 실물이라 config.json 에 남는다) — 실패해도 조용히 넘긴다.
  window.cmdSetBottle = function (values) {
    send({ cmd: 'set_bottle', values: values });
  };
  window.cmdSet4way = function (route) {
    if (send({ cmd: 'set_4way', route: route })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  window.cmdRun = function () {
    // 현재 화면 표의 레시피(bottle/procs/params/loopCount)를 함께 보내 서버가 그걸로 검증·실행.
    // 저장/불러오기 전에도 표에 입력한 그대로 동작(저장은 Save as 때만 파일에 기록).
    const recipe = (typeof collectRecipe === 'function') ? collectRecipe() : (window.collectRecipe ? window.collectRecipe() : null);
    if (send({ cmd: 'run', recipe: recipe })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  window.cmdStop = function () {
    if (window.flashHdrStatus) window.flashHdrStatus('정지됨', 'stop');
    if (send({ cmd: 'stop' })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  window.cmdPurge = function () {
    if (window.flashHdrStatus) window.flashHdrStatus('퍼지 중', 'purge');
    if (send({ cmd: 'purge' })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  window.cmdExit = function () {
    // 브라우저 기본 confirm 대신 앱 내부 모달(window.confirmExit) 사용.
    var doExit = function () {
      // 서버가 pywebview 창을 닫아 프로세스를 종료한다.
      if (!send({ cmd: 'exit' })) {
        // 서버가 죽어도 창은 닫혀야 한다. pywebview 브리지가 있으면 프로세스를 직접 종료한다.
        // (차단 프레임은 못 보낸다 — 하트비트 두절 시 PLC 래더가 트립되어 NC 밸브를 닫는다)
        var api = window.pywebview && window.pywebview.api;
        if (api && api.force_close) {
          window.logMsg('서버 응답 없음 — 프로그램을 강제 종료합니다 (밸브는 PLC 안전로직이 닫습니다)', 'warn');
          api.force_close();
          return;
        }
        // 순수 브라우저 접속에는 브리지가 없다 — 탭은 사용자가 닫는다.
        window.logMsg('오프라인 — 서버에 연결되어야 종료할 수 있습니다', 'warn');
        return;
      }
      window.logMsg('프로그램 종료 중...', 'warn');
    };
    if (typeof window.confirmExit === 'function') window.confirmExit(doExit);
    else window.appConfirm('프로그램을 종료하시겠습니까? 종료하면 모든 밸브가 닫히고 가스 공급이 차단됩니다.', doExit, '프로그램 종료');   // 폴백
  };
  // 창 우상단 X → 서버(_on_closing)가 호출. PROGRAM END와 동일한 종료확인 모달을 띄운다.
  window.requestExitConfirm = function () { window.cmdExit(); };
  window.cmdApplySetup = function (channels, params, settings, plc) {
    if (send({ cmd: 'apply_setup', channels: channels, params: params, settings: settings, plc: plc })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
  };
  // System Setup 모달의 포트 드롭다운 채우기 — 서버에 목록 요청(오프라인이면 빈 목록=텍스트 입력).
  window.cmdPlcPorts = function () {
    if (send({ cmd: 'plc_ports' })) return;
    if (window.applyPlcPorts) window.applyPlcPorts([]);
  };
  // 안전리셋(M112 펄스) — 서버가 PLC로 순간 펄스 전송. 오프라인이면 안내만.
  window.cmdPlcReset = function () {
    if (send({ cmd: 'plc_reset' })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 안전리셋을 보낼 수 있습니다', 'warn');
  };
  // PLC 재연결 — 서버가 PLC 연결 루프를 끊고 새 설정으로 재시작. 오프라인이면 안내만.
  window.cmdPlcReconnect = function () {
    if (send({ cmd: 'plc_reconnect' })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 PLC 재연결을 보낼 수 있습니다', 'warn');
  };
  window.cmdRecipeNew = function () {
    if (send({ cmd: 'recipe_new' })) return;
    window.logMsg('오프라인 — 서버에 연결되어야 합니다', 'warn');
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

  // ===================== 입력 창(appPrompt) =====================
  // appConfirm 과 같은 오버레이/버튼 스타일을 재사용한다. 확인=onOk(값), 취소/X=무동작.
  // validator(value) → 문자열이면 창을 닫지 않고 입력 아래 인라인 에러로 보여준다(null=통과).
  let _promptCb = null, _promptVal = null;
  window.appPrompt = function (message, defaultValue, onOk, title, validator) {
    const ov = document.getElementById('saveNameModal');
    const inp = document.getElementById('saveNameInput');
    if (!ov || !inp) {                       // 폴백: 모달이 없으면 브라우저 prompt
      let v = window.prompt(message, defaultValue || '');
      while (v !== null && validator) {
        const err = validator(v);
        if (!err) break;
        v = window.prompt(err + ' / ' + message, v);
      }
      if (v !== null && onOk) onOk(v);
      return;
    }
    const t = document.getElementById('promptTitle'); if (t) t.textContent = title || '입력';
    const m = document.getElementById('promptMsg'); if (m) m.textContent = message || '';
    const w = document.getElementById('saveNameWarn'); if (w) w.textContent = '';
    inp.value = defaultValue || '';
    _promptCb = onOk || null;
    _promptVal = validator || null;
    ov.classList.add('on');
    setTimeout(() => inp.focus(), 30);       // 전역 focusin 위임이 전체선택까지 처리
  };
  function bindPrompt() {
    const ov = document.getElementById('saveNameModal');
    const inp = document.getElementById('saveNameInput');
    const warn = document.getElementById('saveNameWarn');
    const close = () => { _promptCb = null; _promptVal = null; ov && ov.classList.remove('on'); };
    const submit = () => {
      const cb = _promptCb, v = (inp.value || '');
      if (_promptVal) {                      // 검증 실패면 창을 유지하고 사유만 보여준다
        const err = _promptVal(v);
        if (err) { if (warn) warn.textContent = err; inp.focus(); inp.select(); return; }
      }
      if (warn) warn.textContent = '';
      ov && ov.classList.remove('on'); _promptCb = null; _promptVal = null;
      if (cb) cb(v);
    };
    if (inp && warn) inp.addEventListener('input', () => { warn.textContent = ''; });
    document.getElementById('saveNameOk')?.addEventListener('click', submit);
    document.getElementById('saveNameCancel')?.addEventListener('click', close);
    document.getElementById('saveNameClose')?.addEventListener('click', close);
    if (ov) ov.addEventListener('click', e => { if (e.target === ov) close(); });
    if (inp) inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); submit(); }
      if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
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

  // 비상정지 — 확인 없이 즉시 전송(비상이므로). 서버 engine.emergency()가 전 채널 차단.
  // 래치 토글 — 눌려 있으면 해제, 아니면 비상정지. 상태는 서버 system.safeStop 이 진실.
  document.getElementById('btnEstop')?.addEventListener('click', () => {
    const on = document.getElementById('btnEstop')?.classList.contains('active');
    send({ cmd: on ? 'clear_emergency' : 'emergency' });
    if (on && window.flashHdrStatus) window.flashHdrStatus('비상정지 해제 — 밸브는 닫혀 있음', 'stop');
  });
  // 안전리셋(운전 준비) — M112 순간 펄스 요청.
  document.getElementById('plcReset')?.addEventListener('click', () => window.cmdPlcReset());
  // PLC 재연결 요청.
  document.getElementById('plcReconnectBtn')?.addEventListener('click', () => window.cmdPlcReconnect());

  // 측정 프로그램 런처 — 경로/자동실행은 디바운스 저장, [실행]은 즉시 전송.
  // ★ 가스 제어와 무관한 부가 기능이다. 실패는 서버가 로그로만 알린다.
  {
    const mp = document.getElementById('measPath');
    const mc = document.getElementById('measAuto');
    let t = null;
    const saveSoon = () => {
      clearTimeout(t);
      t = setTimeout(() => send({
        cmd: 'set_measure_app',
        path: mp ? mp.value : '',
        autoLaunch: !!(mc && mc.checked),
      }), 500);
    };
    mp?.addEventListener('input', saveSoon);
    mc?.addEventListener('change', saveSoon);
    // 파일 선택 창은 서버(pywebview)가 연다. 결과 경로는 push_state 로 입력칸에 반영된다.
    document.getElementById('measPick')?.addEventListener('click', () => {
      // 직접 입력하던 값이 있으면 먼저 확정 저장한다(취소해도 타이핑이 사라지지 않게).
      clearTimeout(t);
      send({ cmd: 'set_measure_app', path: mp ? mp.value : '', autoLaunch: !!(mc && mc.checked) });
      send({ cmd: 'pick_measure_app' });
    });
    document.getElementById('measRun')?.addEventListener('click', () => {
      clearTimeout(t);                       // 경로를 막 고쳤다면 저장부터 하고 실행
      send({ cmd: 'set_measure_app', path: mp ? mp.value : '', autoLaunch: !!(mc && mc.checked) });
      send({ cmd: 'launch_measure' });
    });
  }

  // ===================== 시작 =====================
  bindPicker();
  bindPrompt();
  bindCommonModals();
  // 초기엔 로그 없이 pill만 "연결 끊김"으로 표시(첫 연결/실패 시 로그가 남는다).
  const pill0 = document.getElementById('connStatus');
  if (pill0) { pill0.classList.add('disc'); pill0.classList.remove('conn'); }
  connect();
})();
