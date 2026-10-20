/**
 * 学祭ライブ 実入場受付モード フロントエンドスクリプト (app-admission.js)
 *
 * 要件・仕様対応:
 * - 担当者名は JavaScript メモリ変数 (operatorName) にのみ保持。
 *   localStorage / sessionStorage / Cookie 等には一切保存せず、リロード・再起動で確実にリセット。
 * - 座席番号はオンスクリーンキーボード（ブロック選択ボタン＋0〜9テンキー）で入力。
 * - 3桁入力完了時に GET /admissions/{seat_id} を自動照会。
 * - 未入場時は「入場を許可する」ボタンで PUT /admissions/{seat_id} (status: ADMITTED) を送信。
 * - 入場済み時は赤色警告バナー（二重入場防止）と入場時刻・担当者名を表示し、取消 (UNADMITTED) も可能。
 */

(() => {
  "use strict";

  const SEAT_REGEX = /^[A-Z][0-9]{3}$/;
  const REQUEST_TIMEOUT_MS = 5000;
  const MAX_AUTO_RETRIES = 1;

  const urlParams = new URLSearchParams(window.location.search);
  let apiBaseUrl = (urlParams.get("api") || window.TICKET_API_BASE_URL || "").replace(/\/+$/, "");

  // ============================================================
  // 揮発性ステート (ストレージ保存厳禁・メモリ内のみ保持)
  // ============================================================
  let operatorName = ""; // 入場ゲート担当者名（リロードで必ず消える）
  let selectedBlock = "A";
  let inputDigits = "";
  let currentSeatId = "";
  let isRequestInFlight = false;
  let lastFailedAction = null;

  // ============================================================
  // ローカル・インメモリモックDB (A001〜A100, B001〜B310, C001〜C390 計800席)
  // ============================================================
  const mockAdmissionsDb = new Map();
  function initMockDb() {
    mockAdmissionsDb.clear();
    const blockCounts = { A: 100, B: 310, C: 390 };
    for (const [block, maxNum] of Object.entries(blockCounts)) {
      for (let i = 1; i <= maxNum; i++) {
        const seatId = `${block}${String(i).padStart(3, "0")}`;
        mockAdmissionsDb.set(seatId, {
          seat_id: seatId,
          status: "UNADMITTED",
          admitted_at: null,
          admitted_by: null,
        });
      }
    }
  }
  initMockDb();

  // ============================================================
  // DOM要素参照
  // ============================================================
  const dom = {
    operatorModal: document.getElementById("operator-modal"),
    operatorForm: document.getElementById("operator-form"),
    operatorInput: document.getElementById("operator-input"),
    operatorError: document.getElementById("operator-error"),
    appContainer: document.getElementById("app-container"),
    currentOperatorName: document.getElementById("current-operator-name"),
    changeOperatorBtn: document.getElementById("change-operator-btn"),

    modeBanner: document.getElementById("mode-banner"),
    modeBannerText: document.getElementById("mode-banner-text"),
    openConfigBtn: document.getElementById("open-config-btn"),
    footerConfigBtn: document.getElementById("footer-config-btn"),
    configModal: document.getElementById("config-modal"),
    apiUrlInput: document.getElementById("api-url-input"),
    saveConfigBtn: document.getElementById("save-config-btn"),
    closeConfigBtn: document.getElementById("close-config-btn"),

    keyboardSection: document.getElementById("keyboard-section"),
    seatInput: document.getElementById("seat-input"),
    seatPrefixDisplay: document.getElementById("seat-prefix-display"),
    seatDigitsDisplay: document.getElementById("seat-digits-display"),
    blockBtns: document.querySelectorAll(".block-btn"),
    numpadBtns: document.querySelectorAll(".numpad-btn"),

    statusSection: document.getElementById("status-section"),
    viewIdle: document.getElementById("view-idle"),
    viewLoading: document.getElementById("view-loading"),
    loadingText: document.getElementById("loading-text"),
    viewInvalid: document.getElementById("view-invalid"),
    viewNotFound: document.getElementById("view-not-found"),
    nfSeatId: document.getElementById("nf-seat-id"),
    viewUnadmitted: document.getElementById("view-unadmitted"),
    unadmittedSeatId: document.getElementById("unadmitted-seat-id"),
    btnAdmit: document.getElementById("btn-admit"),
    viewAdmitted: document.getElementById("view-admitted"),
    admittedBadge: document.getElementById("admitted-badge"),
    admittedSeatId: document.getElementById("admitted-seat-id"),
    admittedAlertBanner: document.getElementById("admitted-alert-banner"),
    admittedAlertIcon: document.getElementById("admitted-alert-icon"),
    admittedAlertTitle: document.getElementById("admitted-alert-title"),
    admittedMeta: document.getElementById("admitted-meta"),
    btnUndoAdmit: document.getElementById("btn-undo-admit"),
    viewNetworkError: document.getElementById("view-network-error"),
    netErrorTitle: document.getElementById("net-error-title"),
    netErrorDesc: document.getElementById("net-error-desc"),
    btnRetry: document.getElementById("btn-retry"),
    nextSeatBtns: document.querySelectorAll(".next-seat-btn"),

    toastContainer: document.getElementById("toast-container"),
  };

  function formatTimestamp(isoStr) {
    if (!isoStr) return "--:--";
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return isoStr;
    return date.toLocaleTimeString("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function showToast(message, type = "success", durationMs = 2800) {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.2s ease";
      setTimeout(() => toast.remove(), 220);
    }, durationMs);
  }

  function updateSeatDisplay() {
    dom.seatPrefixDisplay.textContent = selectedBlock;
    const paddedDigits = inputDigits.padEnd(3, "_");
    dom.seatDigitsDisplay.textContent = paddedDigits;
    dom.seatDigitsDisplay.classList.toggle("is-placeholder", inputDigits.length === 0);

    dom.blockBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.prefix === selectedBlock);
    });
  }

  function showKeyboardSection() {
    dom.keyboardSection.hidden = false;
    dom.statusSection.hidden = true;
  }

  function showStatusSection() {
    dom.keyboardSection.hidden = true;
    dom.statusSection.hidden = false;
  }

  function switchStatusView(viewName, sectionStateClass = "state-idle") {
    const views = {
      idle: dom.viewIdle,
      loading: dom.viewLoading,
      invalid: dom.viewInvalid,
      notFound: dom.viewNotFound,
      unadmitted: dom.viewUnadmitted,
      admitted: dom.viewAdmitted,
      networkError: dom.viewNetworkError,
    };

    Object.entries(views).forEach(([key, el]) => {
      el.hidden = key !== viewName;
    });

    dom.statusSection.className = `card status-section ${sectionStateClass}`;
  }

  function updateModeBanner() {
    dom.modeBanner.hidden = false;
    if (apiBaseUrl) {
      dom.modeBannerText.textContent = `API接続中: ${apiBaseUrl}`;
    } else {
      dom.modeBannerText.textContent = "ローカル検証モード (入場DB: A001〜A100, B001〜B310, C001〜C390)";
    }
    if (window.TicketCommon) {
      window.TicketCommon.setApiBaseUrl(apiBaseUrl);
    }
  }

  // ============================================================
  // タイムアウト & 自動リトライ付き API 通信
  // ============================================================

  async function fetchWithTimeoutAndRetry(url, options = {}, retriesLeft = MAX_AUTO_RETRIES) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      return response;
    } catch (err) {
      clearTimeout(timeoutId);
      if (retriesLeft > 0) {
        await new Promise((r) => setTimeout(r, 350));
        return fetchWithTimeoutAndRetry(url, options, retriesLeft - 1);
      }
      throw err;
    }
  }

  async function apiGetAdmission(seatId) {
    if (!apiBaseUrl) {
      await new Promise((r) => setTimeout(r, 120));
      if (!mockAdmissionsDb.has(seatId)) {
        const notFoundErr = new Error("SEAT_NOT_FOUND");
        notFoundErr.status = 404;
        throw notFoundErr;
      }
      return { ...mockAdmissionsDb.get(seatId) };
    }

    const res = await fetchWithTimeoutAndRetry(`${apiBaseUrl}/admissions/${encodeURIComponent(seatId)}`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });

    if (res.status === 404) {
      const notFoundErr = new Error("SEAT_NOT_FOUND");
      notFoundErr.status = 404;
      throw notFoundErr;
    }

    if (!res.ok) {
      const serverErr = new Error(`HTTP_${res.status}`);
      serverErr.status = res.status;
      throw serverErr;
    }

    return await res.json();
  }

  async function apiPutAdmission(seatId, newStatus, operator) {
    if (!apiBaseUrl) {
      await new Promise((r) => setTimeout(r, 150));
      if (!mockAdmissionsDb.has(seatId)) {
        const notFoundErr = new Error("SEAT_NOT_FOUND");
        notFoundErr.status = 404;
        throw notFoundErr;
      }
      const isAdmitted = newStatus === "ADMITTED";
      const record = {
        seat_id: seatId,
        status: newStatus,
        admitted_at: isAdmitted ? new Date().toISOString() : null,
        admitted_by: isAdmitted ? operator : null,
      };
      mockAdmissionsDb.set(seatId, record);
      return { ...record };
    }

    const res = await fetchWithTimeoutAndRetry(`${apiBaseUrl}/admissions/${encodeURIComponent(seatId)}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        status: newStatus,
        operator_name: operator,
      }),
    });

    if (res.status === 404) {
      const notFoundErr = new Error("SEAT_NOT_FOUND");
      notFoundErr.status = 404;
      throw notFoundErr;
    }

    if (!res.ok) {
      const serverErr = new Error(`HTTP_${res.status}`);
      serverErr.status = res.status;
      throw serverErr;
    }

    return await res.json();
  }

  // ============================================================
  // ステータス描画・更新制御
  // ============================================================

  function renderAdmissionData(data, isNewlyAdmitted = false) {
    currentSeatId = data.seat_id;

    if (data.status === "ADMITTED") {
      dom.admittedSeatId.textContent = data.seat_id;
      const timeStr = formatTimestamp(data.admitted_at);
      const byStr = data.admitted_by || "不明";
      dom.admittedMeta.textContent = `入場時刻: ${timeStr} ／ 受付担当: ${byStr}`;

      if (isNewlyAdmitted) {
        dom.admittedBadge.className = "status-badge badge-unchecked";
        dom.admittedBadge.textContent = "入場完了 (ADMITTED)";
        dom.admittedAlertBanner.className = "alert-banner alert-success-row";
        dom.admittedAlertIcon.textContent = "✅";
        dom.admittedAlertTitle.textContent = "入場処理が完了しました。どうぞお進みください。";
        switchStatusView("admitted", "state-unchecked");
      } else {
        dom.admittedBadge.className = "status-badge badge-danger";
        dom.admittedBadge.textContent = "入場済み (ADMITTED)";
        dom.admittedAlertBanner.className = "alert-banner alert-danger-row";
        dom.admittedAlertIcon.textContent = "⚠️";
        dom.admittedAlertTitle.textContent = "この座席はすでに入場済みです！";
        switchStatusView("admitted", "state-danger");
      }
    } else {
      dom.unadmittedSeatId.textContent = data.seat_id;
      switchStatusView("unadmitted", "state-unchecked");
    }
  }

  async function lookupSeat(seatId) {
    if (isRequestInFlight) return;
    if (!operatorName) {
      openOperatorModal();
      return;
    }

    showStatusSection();

    if (!SEAT_REGEX.test(seatId)) {
      switchStatusView("invalid", "state-danger");
      return;
    }

    isRequestInFlight = true;
    currentSeatId = seatId;
    lastFailedAction = () => lookupSeat(seatId);

    dom.loadingText.textContent = `${seatId} の入場状態を照会中...`;
    switchStatusView("loading", "state-idle");

    try {
      const data = await apiGetAdmission(seatId);
      renderAdmissionData(data, false);
    } catch (err) {
      if (err && err.status === 404) {
        dom.nfSeatId.textContent = seatId;
        switchStatusView("notFound", "state-danger");
      } else {
        dom.netErrorTitle.textContent = `${seatId} の照会に失敗しました`;
        dom.netErrorDesc.textContent =
          err && err.name === "AbortError"
            ? "通信がタイムアウトしました（5秒経過）。電波状況を確認して「再試行」を押してください。"
            : "ネットワーク接続エラーが発生しました。「再試行」を押してください。";
        switchStatusView("networkError", "state-danger");
      }
    } finally {
      isRequestInFlight = false;
    }
  }

  async function updateAdmissionStatus(seatId, targetStatus) {
    if (isRequestInFlight || !seatId) return;
    if (!operatorName) {
      openOperatorModal();
      return;
    }

    isRequestInFlight = true;
    lastFailedAction = () => updateAdmissionStatus(seatId, targetStatus);

    dom.btnAdmit.disabled = true;
    dom.btnUndoAdmit.disabled = true;
    dom.loadingText.textContent =
      targetStatus === "ADMITTED" ? `${seatId} の入場を記録中...` : `${seatId} の入場を取り消し中...`;
    switchStatusView("loading", "state-idle");

    try {
      const updated = await apiPutAdmission(seatId, targetStatus, operatorName);
      renderAdmissionData(updated, targetStatus === "ADMITTED");

      if (targetStatus === "ADMITTED") {
        showToast(`✓ ${seatId} の入場を記録しました（担当: ${operatorName}）`, "success");
      } else {
        showToast(`↩ ${seatId} を「未入場」に戻しました`, "warning");
      }
    } catch (err) {
      if (err && err.status === 404) {
        dom.nfSeatId.textContent = seatId;
        switchStatusView("notFound", "state-danger");
      } else {
        dom.netErrorTitle.textContent = `${seatId} の更新に失敗しました`;
        dom.netErrorDesc.textContent =
          "更新リクエストが完了しませんでした。下の「再試行」を押して再度実行してください。";
        switchStatusView("networkError", "state-danger");
        showToast("通信エラーが発生しました。再試行してください。", "error");
      }
    } finally {
      isRequestInFlight = false;
      dom.btnAdmit.disabled = false;
      dom.btnUndoAdmit.disabled = false;
    }
  }

  function resetSeatInput() {
    inputDigits = "";
    currentSeatId = "";
    updateSeatDisplay();
    switchStatusView("idle", "state-idle");
    showKeyboardSection();
  }

  function handleDigitInput(digit) {
    if (isRequestInFlight) return;
    if (!operatorName) {
      openOperatorModal();
      return;
    }
    if (inputDigits.length >= 3) return;

    inputDigits += digit;
    updateSeatDisplay();

    if (inputDigits.length === 3) {
      const seatId = `${selectedBlock}${inputDigits}`;
      lookupSeat(seatId);
    }
  }

  // ============================================================
  // 担当者名モーダル制御 (メモリ変数のみ保持)
  // ============================================================

  function openOperatorModal() {
    dom.operatorModal.hidden = false;
    dom.appContainer.classList.add("is-locked");
    dom.appContainer.setAttribute("aria-hidden", "true");
    dom.operatorInput.value = operatorName;
    dom.operatorError.hidden = true;
    setTimeout(() => dom.operatorInput.focus(), 50);
  }

  function handleOperatorSubmit(e) {
    e.preventDefault();
    const trimmed = dom.operatorInput.value.trim();
    if (!trimmed) {
      dom.operatorError.hidden = false;
      dom.operatorInput.focus();
      return;
    }

    // メモリ変数にのみ保持（localStorage / sessionStorage には一切書き込まない）
    operatorName = trimmed;
    dom.currentOperatorName.textContent = operatorName;
    dom.operatorError.hidden = true;
    dom.operatorModal.hidden = true;
    dom.appContainer.classList.remove("is-locked");
    dom.appContainer.setAttribute("aria-hidden", "false");

    showToast(`入場ゲート担当「${operatorName}」で受付を開始しました`, "success", 2200);
  }

  // ============================================================
  // イベントリスナー登録
  // ============================================================

  dom.operatorForm.addEventListener("submit", handleOperatorSubmit);
  dom.changeOperatorBtn.addEventListener("click", openOperatorModal);

  dom.blockBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (isRequestInFlight) return;
      selectedBlock = btn.dataset.prefix;
      updateSeatDisplay();
    });
  });

  dom.numpadBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const digit = btn.dataset.digit;
      if (digit !== undefined) {
        handleDigitInput(digit);
      }
    });
  });

  dom.nextSeatBtns.forEach((btn) => {
    btn.addEventListener("click", () => resetSeatInput());
  });

  dom.btnAdmit.addEventListener("click", () => updateAdmissionStatus(currentSeatId, "ADMITTED"));
  dom.btnUndoAdmit.addEventListener("click", () => updateAdmissionStatus(currentSeatId, "UNADMITTED"));
  dom.btnRetry.addEventListener("click", () => {
    if (typeof lastFailedAction === "function") {
      lastFailedAction();
    }
  });

  function openConfigModal() {
    dom.apiUrlInput.value = apiBaseUrl;
    dom.configModal.hidden = false;
  }
  function closeConfigModal() {
    dom.configModal.hidden = true;
  }

  dom.openConfigBtn.addEventListener("click", openConfigModal);
  dom.footerConfigBtn.addEventListener("click", openConfigModal);
  dom.closeConfigBtn.addEventListener("click", closeConfigModal);
  dom.saveConfigBtn.addEventListener("click", () => {
    apiBaseUrl = dom.apiUrlInput.value.trim().replace(/\/+$/, "");
    updateModeBanner();
    closeConfigModal();
    showToast(
      apiBaseUrl ? `API接続先を更新しました` : "ローカル検証モードに切り替えました",
      "success"
    );
    if (currentSeatId && SEAT_REGEX.test(currentSeatId)) {
      lookupSeat(currentSeatId);
    }
  });

  // 初期起動処理
  updateModeBanner();
  updateSeatDisplay();
  showKeyboardSection();
  openOperatorModal();
})();

