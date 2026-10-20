/**
 * 学祭ライブ 学生証確認・入場管理サービス 共通ユーティリティ (app-common.js)
 *
 * - API ベースURLの解決 (?api=... クエリまたは window.TICKET_API_BASE_URL)
 * - ページ間ナビゲーションリンクへの ?api=... パラメータ引き継ぎ
 * - 5秒タイムアウト & 自動1回リトライ付き fetch ラッパー
 * - 日時フォーマット (JST表示)
 * - トースト通知
 */

(() => {
  "use strict";

  const SEAT_REGEX = /^[A-Z][0-9]{3}$/;
  const REQUEST_TIMEOUT_MS = 5000;
  const MAX_AUTO_RETRIES = 1;

  const urlParams = new URLSearchParams(window.location.search);
  let apiBaseUrl = (urlParams.get("api") || window.TICKET_API_BASE_URL || "").replace(/\/+$/, "");

  function getApiBaseUrl() {
    return apiBaseUrl;
  }

  function setApiBaseUrl(newUrl) {
    apiBaseUrl = (newUrl || "").trim().replace(/\/+$/, "");
    syncNavLinks();
    return apiBaseUrl;
  }

  /**
   * ページ内の data-nav-link 属性を持つリンクに ?api=... クエリを引き継ぐ
   */
  function syncNavLinks() {
    const links = document.querySelectorAll("a[data-nav-link]");
    links.forEach((link) => {
      const baseHref = link.getAttribute("data-base-href") || link.getAttribute("href").split("?")[0];
      link.setAttribute("data-base-href", baseHref);
      if (apiBaseUrl) {
        link.setAttribute("href", `${baseHref}?api=${encodeURIComponent(apiBaseUrl)}`);
      } else {
        link.setAttribute("href", baseHref);
      }
    });
  }

  /**
   * ISO8601 日時文字列を見やすい時刻形式 (HH:MM:SS) にフォーマットする
   */
  function formatTimestamp(isoStr, includeDate = false) {
    if (!isoStr) return "--:--";
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return isoStr;
    if (includeDate) {
      return date.toLocaleString("ja-JP", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }
    return date.toLocaleTimeString("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  /**
   * トースト通知を表示する
   */
  function showToast(message, type = "success", durationMs = 2800) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.2s ease";
      setTimeout(() => toast.remove(), 220);
    }, durationMs);
  }

  /**
   * タイムアウト & 自動リトライ付き fetch
   */
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

  window.TicketCommon = {
    SEAT_REGEX,
    REQUEST_TIMEOUT_MS,
    MAX_AUTO_RETRIES,
    getApiBaseUrl,
    setApiBaseUrl,
    syncNavLinks,
    formatTimestamp,
    showToast,
    fetchWithTimeoutAndRetry,
  };
})();

