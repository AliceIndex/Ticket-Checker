/**
 * 学祭ライブ 入場データ統計ダッシュボード スクリプト (app-data.js)
 *
 * 機能:
 * 1. GET /admissions から全座席の入場ステータスを取得
 * 2. 全体・ブロック別 (A/B) の入場率計算とプログレスバー描画
 * 3. 入場タイムスタンプを用いた時間帯別 (15分/30分/60分刻み) の入場数集計とグラフ描画
 * 4. ブロック別タブ切替・状態フィルタ・キーワード検索・ソート対応の入場者リスト表示
 * 5. 30秒自動更新ポーリングおよびBOM付きCSVエクスポート
 */

(() => {
  "use strict";

  const REQUEST_TIMEOUT_MS = 5000;
  const AUTO_REFRESH_INTERVAL_MS = 30000;

  const urlParams = new URLSearchParams(window.location.search);
  let apiBaseUrl = (urlParams.get("api") || window.TICKET_API_BASE_URL || "").replace(/\/+$/, "");

  // ステート
  let allItems = [];
  let selectedBlockFilter = "ALL"; // ALL | A | B
  let selectedStatusFilter = "ADMITTED"; // ADMITTED | ALL | UNADMITTED
  let selectedSort = "time_desc"; // time_desc | time_asc | seat_asc
  let searchQuery = "";
  let bucketMinutes = 15;
  let refreshTimerId = null;
  let isFetching = false;

  // ローカルモックデータ (API未接続時のプレビュー用)
  let mockItems = [];
  function initMockItems(withSampleAdmissions = true) {
    const items = [];
    const operators = ["田中", "佐藤", "鈴木", "高橋", "渡辺", "伊藤"];
    const baseTime = new Date();
    baseTime.setMinutes(0, 0, 0);
    baseTime.setHours(baseTime.getHours() - 1);

    const blocks = ["A", "B"];
    for (const block of blocks) {
      for (let i = 1; i <= 400; i++) {
        const seatId = `${block}${String(i).padStart(3, "0")}`;
        let status = "UNADMITTED";
        let admittedAt = null;
        let admittedBy = null;

        // サンプル生成時は約35%の座席に入場時刻を分散付与
        if (withSampleAdmissions && ((i * 7 + (block === "A" ? 3 : 11)) % 20 < 7)) {
          status = "ADMITTED";
          const offsetMinutes = ((i * 13 + (block === "B" ? 17 : 5)) % 75);
          const offsetSeconds = (i * 29) % 60;
          const dt = new Date(baseTime.getTime() + (offsetMinutes * 60 + offsetSeconds) * 1000);
          admittedAt = dt.toISOString();
          admittedBy = operators[(i + (block === "A" ? 0 : 2)) % operators.length];
        }

        items.push({
          seat_id: seatId,
          status,
          admitted_at: admittedAt,
          admitted_by: admittedBy,
        });
      }
    }
    mockItems = items;
  }
  initMockItems(true);

  // DOM要素参照
  const dom = {
    lastUpdatedText: document.getElementById("last-updated-text"),
    btnRefreshNow: document.getElementById("btn-refresh-now"),
    modeBanner: document.getElementById("mode-banner"),
    modeBannerText: document.getElementById("mode-banner-text"),
    btnMockSample: document.getElementById("btn-mock-sample"),
    openConfigBtn: document.getElementById("open-config-btn"),
    footerConfigBtn: document.getElementById("footer-config-btn"),
    configModal: document.getElementById("config-modal"),
    apiUrlInput: document.getElementById("api-url-input"),
    saveConfigBtn: document.getElementById("save-config-btn"),
    closeConfigBtn: document.getElementById("close-config-btn"),

    autoRefreshCheckbox: document.getElementById("auto-refresh-checkbox"),
    loadingIndicator: document.getElementById("loading-indicator"),
    btnExportCsv: document.getElementById("btn-export-csv"),
    dataErrorBanner: document.getElementById("data-error-banner"),
    dataErrorMsg: document.getElementById("data-error-msg"),

    kpiTotalRate: document.getElementById("kpi-total-rate"),
    kpiTotalCount: document.getElementById("kpi-total-count"),
    kpiTotalRemain: document.getElementById("kpi-total-remain"),
    kpiTotalBar: document.getElementById("kpi-total-bar"),

    kpiARate: document.getElementById("kpi-a-rate"),
    kpiACount: document.getElementById("kpi-a-count"),
    kpiARemain: document.getElementById("kpi-a-remain"),
    kpiABar: document.getElementById("kpi-a-bar"),

    kpiBRate: document.getElementById("kpi-b-rate"),
    kpiBCount: document.getElementById("kpi-b-count"),
    kpiBRemain: document.getElementById("kpi-b-remain"),
    kpiBBar: document.getElementById("kpi-b-bar"),

    bucketIntervalSelect: document.getElementById("bucket-interval-select"),
    timeslotEmpty: document.getElementById("timeslot-empty"),
    timeslotContainer: document.getElementById("timeslot-container"),
    timeslotTbody: document.getElementById("timeslot-tbody"),

    filteredCountBadge: document.getElementById("filtered-count-badge"),
    tabBtns: document.querySelectorAll(".tab-btn[data-block-filter]"),
    statusFilterSelect: document.getElementById("status-filter-select"),
    sortSelect: document.getElementById("sort-select"),
    searchInput: document.getElementById("search-input"),
    attendeeTbody: document.getElementById("attendee-tbody"),

    toastContainer: document.getElementById("toast-container"),
  };

  function showToast(message, type = "success", durationMs = 2600) {
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

  function formatDateTimeJST(isoStr) {
    if (!isoStr) return "--";
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return isoStr;
    return d.toLocaleTimeString("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function updateModeBanner() {
    dom.modeBanner.hidden = false;
    if (apiBaseUrl) {
      dom.modeBannerText.textContent = `API接続中: ${apiBaseUrl}`;
      dom.btnMockSample.hidden = true;
    } else {
      dom.modeBannerText.textContent = "ローカル検証モード (デモデータ表示中)";
      dom.btnMockSample.hidden = false;
    }
    if (window.TicketCommon) {
      window.TicketCommon.setApiBaseUrl(apiBaseUrl);
    }
  }

  // ============================================================
  // API 取得
  // ============================================================

  async function fetchAdmissionsData() {
    if (isFetching) return;
    isFetching = true;
    dom.loadingIndicator.hidden = false;
    dom.btnRefreshNow.disabled = true;

    try {
      let items = [];
      if (!apiBaseUrl) {
        await new Promise((r) => setTimeout(r, 120));
        items = mockItems.map((item) => ({ ...item }));
      } else {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
        const res = await fetch(`${apiBaseUrl}/admissions`, {
          method: "GET",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (!res.ok) {
          throw new Error(`HTTP_${res.status}`);
        }
        const payload = await res.json();
        items = Array.isArray(payload.items) ? payload.items : [];
      }

      allItems = items;
      dom.dataErrorBanner.hidden = true;

      const nowStr = new Date().toLocaleTimeString("ja-JP", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
      dom.lastUpdatedText.textContent = `最終取得: ${nowStr}`;

      renderDashboard();
    } catch (err) {
      dom.dataErrorBanner.hidden = false;
      dom.dataErrorMsg.textContent =
        err && err.name === "AbortError"
          ? "データ取得がタイムアウトしました。通信環境を確認して「今すぐ更新」を押してください。"
          : "サーバーから入場データを取得できませんでした。API接続設定を確認してください。";
    } finally {
      isFetching = false;
      dom.loadingIndicator.hidden = true;
      dom.btnRefreshNow.disabled = false;
    }
  }

  // ============================================================
  // 1. 入場率サマリー (KPI) 描画
  // ============================================================

  function renderKpiSummary() {
    const totalSeats = allItems.length || 800;
    const admittedAll = allItems.filter((x) => x.status === "ADMITTED").length;

    const aItems = allItems.filter((x) => x.seat_id.startsWith("A"));
    const aTotal = aItems.length || 400;
    const aAdmitted = aItems.filter((x) => x.status === "ADMITTED").length;

    const bItems = allItems.filter((x) => x.seat_id.startsWith("B"));
    const bTotal = bItems.length || 400;
    const bAdmitted = bItems.filter((x) => x.status === "ADMITTED").length;

    const totalRate = totalSeats > 0 ? ((admittedAll / totalSeats) * 100).toFixed(1) : "0.0";
    const aRate = aTotal > 0 ? ((aAdmitted / aTotal) * 100).toFixed(1) : "0.0";
    const bRate = bTotal > 0 ? ((bAdmitted / bTotal) * 100).toFixed(1) : "0.0";

    dom.kpiTotalRate.textContent = `${totalRate}%`;
    dom.kpiTotalCount.textContent = `${admittedAll} / ${totalSeats} 席`;
    dom.kpiTotalRemain.textContent = `未入場: ${Math.max(0, totalSeats - admittedAll)}席`;
    dom.kpiTotalBar.style.width = `${Math.min(100, parseFloat(totalRate))}%`;

    dom.kpiARate.textContent = `${aRate}%`;
    dom.kpiACount.textContent = `${aAdmitted} / ${aTotal} 席`;
    dom.kpiARemain.textContent = `未入場: ${Math.max(0, aTotal - aAdmitted)}席`;
    dom.kpiABar.style.width = `${Math.min(100, parseFloat(aRate))}%`;

    dom.kpiBRate.textContent = `${bRate}%`;
    dom.kpiBCount.textContent = `${bAdmitted} / ${bTotal} 席`;
    dom.kpiBRemain.textContent = `未入場: ${Math.max(0, bTotal - bAdmitted)}席`;
    dom.kpiBBar.style.width = `${Math.min(100, parseFloat(bRate))}%`;
  }

  // ============================================================
  // 2. 時間帯別入場数 集計・描画
  // ============================================================

  function getBucketKeyAndLabel(isoStr, intervalMin) {
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return null;

    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const hours = d.getHours();
    const mins = d.getMinutes();
    const bucketStartMin = Math.floor(mins / intervalMin) * intervalMin;

    const startDate = new Date(year, d.getMonth(), d.getDate(), hours, bucketStartMin, 0);
    const endDate = new Date(startDate.getTime() + intervalMin * 60 * 1000);

    const pad2 = (n) => String(n).padStart(2, "0");
    const startHM = `${pad2(startDate.getHours())}:${pad2(startDate.getMinutes())}`;
    const endHM = `${pad2(endDate.getHours())}:${pad2(endDate.getMinutes())}`;

    return {
      sortKey: startDate.getTime(),
      label: `${startHM} 〜 ${endHM}`,
      datePrefix: `${month}/${day}`,
    };
  }

  function renderTimeslotTable() {
    const admittedItems = allItems.filter((x) => x.status === "ADMITTED" && x.admitted_at);

    if (admittedItems.length === 0) {
      dom.timeslotEmpty.hidden = false;
      dom.timeslotContainer.hidden = true;
      dom.timeslotTbody.innerHTML = "";
      return;
    }

    const bucketsMap = new Map();
    for (const item of admittedItems) {
      const info = getBucketKeyAndLabel(item.admitted_at, bucketMinutes);
      if (!info) continue;

      if (!bucketsMap.has(info.sortKey)) {
        bucketsMap.set(info.sortKey, {
          sortKey: info.sortKey,
          label: info.label,
          countA: 0,
          countB: 0,
          countOther: 0,
          total: 0,
        });
      }
      const bucket = bucketsMap.get(info.sortKey);
      if (item.seat_id.startsWith("A")) {
        bucket.countA += 1;
      } else if (item.seat_id.startsWith("B")) {
        bucket.countB += 1;
      } else {
        bucket.countOther += 1;
      }
      bucket.total += 1;
    }

    const buckets = Array.from(bucketsMap.values()).sort((a, b) => a.sortKey - b.sortKey);
    const maxTotal = Math.max(...buckets.map((b) => b.total), 1);

    dom.timeslotEmpty.hidden = true;
    dom.timeslotContainer.hidden = false;
    dom.timeslotTbody.innerHTML = "";

    buckets.forEach((b) => {
      const tr = document.createElement("tr");
      const widthPct = Math.round((b.total / maxTotal) * 100);
      const isPeak = b.total === maxTotal && maxTotal >= 5;

      const tdLabel = document.createElement("td");
      tdLabel.className = "timeslot-label-cell";
      tdLabel.textContent = b.label;
      if (isPeak) {
        const peakBadge = document.createElement("span");
        peakBadge.className = "peak-badge";
        peakBadge.textContent = "PEAK";
        tdLabel.appendChild(peakBadge);
      }

      const tdA = document.createElement("td");
      tdA.className = "num-col";
      tdA.textContent = `${b.countA} 名`;

      const tdB = document.createElement("td");
      tdB.className = "num-col";
      tdB.textContent = `${b.countB} 名`;

      const tdTotal = document.createElement("td");
      tdTotal.className = "num-col font-bold";
      tdTotal.textContent = `${b.total} 名`;

      const tdBar = document.createElement("td");
      tdBar.className = "bar-col";
      const barTrack = document.createElement("div");
      barTrack.className = "bar-track";
      const barFill = document.createElement("div");
      barFill.className = isPeak ? "bar-fill bar-fill-peak" : "bar-fill";
      barFill.style.width = `${widthPct}%`;
      barTrack.appendChild(barFill);
      tdBar.appendChild(barTrack);

      tr.append(tdLabel, tdA, tdB, tdTotal, tdBar);
      dom.timeslotTbody.appendChild(tr);
    });
  }

  // ============================================================
  // 3. ブロック別入場者リスト 描画
  // ============================================================

  function getFilteredAndSortedItems() {
    const q = searchQuery.trim().toUpperCase();

    const filtered = allItems.filter((item) => {
      // ブロックフィルタ
      if (selectedBlockFilter !== "ALL" && !item.seat_id.startsWith(selectedBlockFilter)) {
        return false;
      }
      // ステータスフィルタ
      if (selectedStatusFilter !== "ALL" && item.status !== selectedStatusFilter) {
        return false;
      }
      // 検索クエリ（座席番号 または 担当者名）
      if (q) {
        const seatMatch = item.seat_id.toUpperCase().includes(q);
        const operatorMatch = (item.admitted_by || "").toUpperCase().includes(q);
        if (!seatMatch && !operatorMatch) return false;
      }
      return true;
    });

    filtered.sort((a, b) => {
      if (selectedSort === "seat_asc") {
        return a.seat_id.localeCompare(b.seat_id);
      }
      const tA = a.admitted_at ? new Date(a.admitted_at).getTime() : 0;
      const tB = b.admitted_at ? new Date(b.admitted_at).getTime() : 0;
      if (tA !== tB) {
        return selectedSort === "time_desc" ? tB - tA : tA - tB;
      }
      return a.seat_id.localeCompare(b.seat_id);
    });

    return filtered;
  }

  function renderAttendeeList() {
    const items = getFilteredAndSortedItems();
    dom.filteredCountBadge.textContent = `表示: ${items.length} 件`;
    dom.attendeeTbody.innerHTML = "";

    if (items.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5;
      td.className = "empty-row-cell";
      td.textContent = "条件に一致する座席データがありません。";
      tr.appendChild(td);
      dom.attendeeTbody.appendChild(tr);
      return;
    }

    const fragment = document.createDocumentFragment();
    items.forEach((item) => {
      const tr = document.createElement("tr");
      const blockChar = item.seat_id.charAt(0);
      const isAdmitted = item.status === "ADMITTED";

      const tdSeat = document.createElement("td");
      tdSeat.className = "seat-cell";
      tdSeat.textContent = item.seat_id;

      const tdBlock = document.createElement("td");
      const blockBadge = document.createElement("span");
      blockBadge.className = `block-tag block-tag-${blockChar}`;
      blockBadge.textContent = `${blockChar}ブロック`;
      tdBlock.appendChild(blockBadge);

      const tdStatus = document.createElement("td");
      const statusPill = document.createElement("span");
      statusPill.className = isAdmitted ? "table-badge badge-admitted" : "table-badge badge-unadmitted";
      statusPill.textContent = isAdmitted ? "入場済み" : "未入場";
      tdStatus.appendChild(statusPill);

      const tdTime = document.createElement("td");
      tdTime.textContent = isAdmitted ? formatDateTimeJST(item.admitted_at) : "--";

      const tdBy = document.createElement("td");
      tdBy.textContent = isAdmitted ? item.admitted_by || "--" : "--";

      tr.append(tdSeat, tdBlock, tdStatus, tdTime, tdBy);
      fragment.appendChild(tr);
    });

    dom.attendeeTbody.appendChild(fragment);
  }

  function renderDashboard() {
    renderKpiSummary();
    renderTimeslotTable();
    renderAttendeeList();
  }

  // ============================================================
  // CSV エクスポート
  // ============================================================

  function exportCsv() {
    const items = getFilteredAndSortedItems();
    if (items.length === 0) {
      showToast("出力対象のデータがありません", "warning");
      return;
    }

    const headers = ["座席番号", "ブロック", "入場ステータス", "入場時刻(ISO8601)", "入場時刻(JST)", "受付担当者"];
    const rows = items.map((item) => [
      item.seat_id,
      item.seat_id.charAt(0),
      item.status,
      item.admitted_at || "",
      item.admitted_at ? formatDateTimeJST(item.admitted_at) : "",
      item.admitted_by || "",
    ]);

    const escapeCsv = (val) => `"${String(val).replace(/"/g, '""')}"`;
    const csvContent =
      "\uFEFF" +
      [headers.map(escapeCsv).join(","), ...rows.map((r) => r.map(escapeCsv).join(","))].join("\r\n");

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    link.href = url;
    link.download = `admissions_${selectedBlockFilter}_${timestamp}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    showToast(`CSVファイルを出力しました (${items.length}件)`, "success");
  }

  // ============================================================
  // 自動更新タイマー制御
  // ============================================================

  function updateAutoRefreshTimer() {
    if (refreshTimerId) {
      clearInterval(refreshTimerId);
      refreshTimerId = null;
    }
    if (dom.autoRefreshCheckbox.checked) {
      refreshTimerId = setInterval(() => {
        fetchAdmissionsData();
      }, AUTO_REFRESH_INTERVAL_MS);
    }
  }

  // ============================================================
  // イベントリスナー登録
  // ============================================================

  dom.btnRefreshNow.addEventListener("click", () => fetchAdmissionsData());
  dom.autoRefreshCheckbox.addEventListener("change", updateAutoRefreshTimer);
  dom.btnExportCsv.addEventListener("click", exportCsv);

  dom.bucketIntervalSelect.addEventListener("change", () => {
    bucketMinutes = parseInt(dom.bucketIntervalSelect.value, 10) || 15;
    renderTimeslotTable();
  });

  dom.tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedBlockFilter = btn.dataset.blockFilter;
      dom.tabBtns.forEach((b) => b.classList.toggle("is-active", b === btn));
      renderAttendeeList();
    });
  });

  dom.statusFilterSelect.addEventListener("change", () => {
    selectedStatusFilter = dom.statusFilterSelect.value;
    renderAttendeeList();
  });

  dom.sortSelect.addEventListener("change", () => {
    selectedSort = dom.sortSelect.value;
    renderAttendeeList();
  });

  dom.searchInput.addEventListener("input", () => {
    searchQuery = dom.searchInput.value;
    renderAttendeeList();
  });

  dom.btnMockSample.addEventListener("click", () => {
    initMockItems(true);
    fetchAdmissionsData();
    showToast("サンプル入場データを再生成しました", "success");
  });

  // API設定モーダル制御
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
    fetchAdmissionsData();
    showToast(
      apiBaseUrl ? "API接続先を更新しました" : "ローカル検証モードに切り替えました",
      "success"
    );
  });

  // 初期起動
  updateModeBanner();
  fetchAdmissionsData();
  updateAutoRefreshTimer();
})();

