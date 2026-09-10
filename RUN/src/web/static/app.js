/**
 * Dashboard Client App.
 *
 * Polls REST API endpoints every 5 seconds and updates the UI dynamically.
 */

let activeErrorsList = [];
let authToken = sessionStorage.getItem("dash_password") || "";
let currentRunMode = "DRY_RUN";
let latestPortfolioData = null;
let latestOrdersList = [];
let regimePointsData = [];
let regimeChartPoints = [];

// ── Server Connection & Downtime Monitoring State ───────────────
let consecutiveConnectionFailures = 0;
let isServerOffline = false;
let isServerCrashed = false;
let offlineSinceTimestamp = 0;
let offlineCountdownSeconds = 3;
let offlineCountdownTimer = null;
let offlineProbeInterval = null;
let lastOfflineError = "";
let crashDataCached = null;
let mainPollInterval = null;

function updateConnectionBadge(online, textOverride = "") {
    const badge = document.getElementById("connBadge");
    const dot = document.getElementById("connDot");
    const text = document.getElementById("connText");
    if (!badge || !dot || !text) return;

    if (online) {
        badge.className = "status-badge conn-badge";
        text.textContent = textOverride || "ONLINE";
        dot.className = "dot pulse";
    } else {
        badge.className = "status-badge conn-badge conn-badge-offline";
        text.textContent = textOverride || "OFFLINE";
        dot.className = "dot";
    }
}

function handleConnectionSuccess() {
    consecutiveConnectionFailures = 0;
    if (isServerOffline) {
        setServerOfflineState(false);
    } else {
        updateConnectionBadge(true);
    }
}

function handleConnectionFailure(err, endpoint = "") {
    consecutiveConnectionFailures++;
    lastOfflineError = err ? (err.message || String(err)) : "Network connection failed";
    updateConnectionBadge(false, "DISCONNECTED");

    // If 2 failures occur, or if /api/status failed
    if (consecutiveConnectionFailures >= 2 || endpoint.includes("/api/status")) {
        setServerOfflineState(true, err);
    }
}

function stopMainPolling() {
    if (mainPollInterval) {
        clearInterval(mainPollInterval);
        mainPollInterval = null;
    }
}

function startMainPolling() {
    stopMainPolling();
    mainPollInterval = setInterval(fetchDashboardData, 5000);
}

function setServerOfflineState(offline, err = null) {
    const modal = document.getElementById("serverOfflineModal");
    const sinceEl = document.getElementById("offlineSinceText");
    const attemptsEl = document.getElementById("offlineAttemptsText");
    const errorEl = document.getElementById("offlineErrorText");

    if (offline) {
        if (!isServerOffline) {
            isServerOffline = true;
            offlineSinceTimestamp = Date.now();
            if (sinceEl) sinceEl.textContent = new Date(offlineSinceTimestamp).toLocaleTimeString();
        }
        updateConnectionBadge(false, "SERVER DOWN");

        // Stop main polling — reconnect cycle handles probing
        stopMainPolling();

        // Update health card to reflect offline state
        const healthVal = document.getElementById("systemHealthVal");
        const healthDetail = document.getElementById("systemHealthDetail");
        if (healthVal) {
            healthVal.textContent = "OFFLINE";
            healthVal.className = "metric-value text-danger";
        }
        if (healthDetail) {
            healthDetail.textContent = "אין תקשורת עם שרת המסחר";
            healthDetail.className = "metric-value text-danger-subtle";
            healthDetail.style.fontSize = "0.8rem";
        }

        // Update last cycle to show offline instead of stale "Never"
        const lastCycleEl = document.getElementById("lastCycleTime");
        if (lastCycleEl && lastCycleEl.textContent === "Never") {
            lastCycleEl.textContent = "Server offline";
        }

        if (attemptsEl) attemptsEl.textContent = consecutiveConnectionFailures;
        if (errorEl) errorEl.textContent = lastOfflineError || (err ? err.message : "Failed to reach server (ERR_CONNECTION_REFUSED)");

        // Show offline modal if crash modal is not already open
        if (modal && !isServerCrashed) {
            modal.style.display = "flex";
        }

        startOfflineReconnectCycle();
    } else {
        isServerOffline = false;
        offlineSinceTimestamp = 0;
        updateConnectionBadge(true, "ONLINE");

        if (modal) modal.style.display = "none";
        stopOfflineReconnectCycle();

        // Resume main polling now that server is back
        startMainPolling();

        showToast("✅ החיבור לשרת שוחזר בהצלחה! הדאשבורד פעיל.", "success");
        fetchDashboardData();
    }
}

function startOfflineReconnectCycle() {
    stopOfflineReconnectCycle();

    offlineCountdownSeconds = 3;
    updateOfflineCountdownDisplay();

    offlineCountdownTimer = setInterval(() => {
        offlineCountdownSeconds--;
        if (offlineCountdownSeconds <= 0) {
            offlineCountdownSeconds = 3;
            probeServerStatus();
        }
        updateOfflineCountdownDisplay();
    }, 1000);
}

function stopOfflineReconnectCycle() {
    if (offlineCountdownTimer) {
        clearInterval(offlineCountdownTimer);
        offlineCountdownTimer = null;
    }
    if (offlineProbeInterval) {
        clearInterval(offlineProbeInterval);
        offlineProbeInterval = null;
    }
}

function updateOfflineCountdownDisplay() {
    const countdownEl = document.getElementById("offlineCountdownText");
    if (countdownEl) {
        countdownEl.textContent = `${offlineCountdownSeconds} שניות...`;
    }
}

async function probeServerStatus() {
    try {
        const res = await fetch("/api/status", {
            headers: getAuthHeaders(),
            cache: "no-store"
        });

        if (res.status === 401 || res.status === 429) {
            setServerOfflineState(false);
            showLoginModal();
            return;
        }

        if (res.ok) {
            const data = await res.json();
            if (data.status === "CRASHED" || data.is_crashed) {
                setServerOfflineState(false);
                showServerCrashModal(data);
                return;
            }

            setServerOfflineState(false);
            hideServerCrashModal();
            return;
        } else if (res.status === 503) {
            try {
                const data = await res.json();
                if (data.status === "CRASHED" || data.is_crashed) {
                    setServerOfflineState(false);
                    showServerCrashModal(data);
                    return;
                }
            } catch (_) {}
        }
    } catch (err) {
        consecutiveConnectionFailures++;
        const attemptsEl = document.getElementById("offlineAttemptsText");
        if (attemptsEl) attemptsEl.textContent = consecutiveConnectionFailures;
        updateConnectionBadge(false, "RECONNECTING...");
    }
}

function showServerCrashModal(data) {
    isServerCrashed = true;
    crashDataCached = data;
    updateConnectionBadge(false, "CRASHED");

    const offlineModal = document.getElementById("serverOfflineModal");
    if (offlineModal) offlineModal.style.display = "none";

    const crashModal = document.getElementById("serverCrashModal");
    const exitCodeEl = document.getElementById("crashModalExitCode");
    const timeEl = document.getElementById("crashModalTime");
    const tracebackEl = document.getElementById("crashModalTraceback");

    if (exitCodeEl) exitCodeEl.textContent = data.exit_code != null ? data.exit_code : "1";
    if (timeEl) timeEl.textContent = data.crash_time || new Date().toLocaleTimeString();
    if (tracebackEl) {
        tracebackEl.textContent = data.error_summary || data.message || "Trading engine stopped or crashed unexpectedly.";
    }

    if (crashModal) crashModal.style.display = "flex";
    fetchCrashModalLogs();
}

function hideServerCrashModal() {
    isServerCrashed = false;
    const crashModal = document.getElementById("serverCrashModal");
    if (crashModal) crashModal.style.display = "none";
}

async function fetchCrashModalLogs() {
    try {
        const res = await fetch("/api/logs", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        const container = document.getElementById("crashModalLogsConsole");
        if (!container) return;

        if (data.error_summary) {
            const tbEl = document.getElementById("crashModalTraceback");
            if (tbEl) tbEl.textContent = data.error_summary;
        }

        if (data.logs && data.logs.length > 0) {
            const lines = data.logs.map(l => {
                let cls = "";
                if (l.includes("ERROR") || l.includes("CRITICAL") || l.includes("Traceback") || l.includes("Exception")) cls = "color: #f87171;";
                else if (l.includes("WARNING")) cls = "color: #fbbf24;";
                else if (l.includes("INFO")) cls = "color: #94a3b8;";
                return `<div style="${cls}">${escapeHtml(l)}</div>`;
            });
            container.innerHTML = lines.join("");
            container.scrollTop = container.scrollHeight;
        } else {
            container.textContent = "No log lines recorded yet.";
        }
    } catch (e) {
        console.error("Error fetching crash logs:", e);
    }
}

async function triggerCrashModalRestart() {
    if (!confirm("האם להפעיל מחדש את מנוע המסחר כעת? / Restart trading engine now?")) return;
    showToast("שולח פקודת הפעלה מחדש... / Sending restart command...", "info");
    try {
        const res = await fetch("/api/restart", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("✅ פקודת הפעלה מחדש התקבלה! המנוע עולה כעת... הדף יתרענן בעוד מספר שניות.", "success");
            setTimeout(() => {
                hideServerCrashModal();
                probeServerStatus();
            }, 3500);
        } else {
            showToast("שגיאה בהפעלה מחדש: " + (data.error || "Unknown"), "error");
        }
    } catch (e) {
        showToast("פקודת הפעלה נשלחה! ממתין לעליית השרת...", "info");
        setTimeout(() => {
            hideServerCrashModal();
            probeServerStatus();
        }, 3000);
    }
}

function manualReconnectAttempt() {
    showToast("בודק חיבור לשרת... (Checking connection)", "info");
    probeServerStatus();
}

function closeOfflineModal() {
    const modal = document.getElementById("serverOfflineModal");
    if (modal) modal.style.display = "none";
}

function closeCrashModal() {
    const modal = document.getElementById("serverCrashModal");
    if (modal) modal.style.display = "none";
}

function showConnectionStatusDetails() {
    if (isServerOffline) {
        const modal = document.getElementById("serverOfflineModal");
        if (modal) modal.style.display = "flex";
    } else if (isServerCrashed) {
        const modal = document.getElementById("serverCrashModal");
        if (modal) modal.style.display = "flex";
    } else {
        showToast("🟢 שרת המסחר מקוון ופעיל בפורט 8090 (Online & Healthy)", "success");
    }
}

function openEmergencyServerPage() {
    window.location.reload();
}

async function copyCrashModalTraceback() {
    const el = document.getElementById("crashModalTraceback");
    if (!el) return;
    const txt = el.innerText || el.textContent;
    if (!txt) return;
    const copied = await copyTextToClipboard(txt);
    if (copied) showToast("📋 סיבת התקלה הועתקה ללוח בהצלחה!", "success");
    else showToast("⚠️ לא ניתן להעתיק אוטומטית. לחץ Ctrl+C", "error");
}

async function copyCrashModalLogs() {
    const el = document.getElementById("crashModalLogsConsole");
    if (!el) return;
    const txt = el.innerText || el.textContent;
    if (!txt) return;
    const copied = await copyTextToClipboard(txt);
    if (copied) showToast("📋 לוגים הועתקו ללוח בהצלחה!", "success");
    else showToast("⚠️ לא ניתן להעתיק אוטומטית. לחץ Ctrl+C", "error");
}

function getAuthHeaders() {
    const headers = {};
    if (authToken) {
        headers["X-Dashboard-Password"] = authToken;
    }
    return headers;
}

async function apiFetch(url, options = {}) {
    options.headers = {
        ...getAuthHeaders(),
        ...(options.headers || {})
    };

    try {
        const res = await fetch(url, options);
        if (res.status === 401 || res.status === 429) {
            authToken = "";
            sessionStorage.removeItem("dash_password");
            showLoginModal();
        } else {
            handleConnectionSuccess();
        }
        return res;
    } catch (err) {
        handleConnectionFailure(err, url);
        throw err;
    }
}

async function parseJsonResponse(res) {
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
        const text = await res.text();
        let snippet = (text || "").trim();
        if (snippet.length > 80) snippet = snippet.substring(0, 80) + "...";
        throw new Error(`Server returned non-JSON response (Status ${res.status}): ${snippet || "Empty response"}`);
    }
    try {
        return await res.json();
    } catch (e) {
        throw new Error(`Invalid JSON response: ${e.message}`);
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    initClock();

    function safeAddListener(id, event, handler) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(event, handler);
    }

    // Login listeners
    safeAddListener("submitLoginBtn", "click", performLogin);
    const passInput = document.getElementById("dashboardPasswordInput");
    if (passInput) passInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") performLogin();
    });

    // Event listeners with instant visual feedback
    safeAddListener("refreshBtn", "click", manualRefresh);
    safeAddListener("triggerCycleBtn", "click", openTriggerCycleConfirmModal);
    safeAddListener("killSwitchBtn", "click", openKillSwitchModal);
    safeAddListener("toggleUpdaterBtn", "click", toggleUpdater);
    safeAddListener("manualPullBtn", "click", triggerManualPull);

    safeAddListener("resetStatsBtn", "click", openResetPnlModal);
    safeAddListener("pnlResetMiniBtn", "click", openResetPnlModal);

    // Reset PnL Modal listeners
    safeAddListener("closeResetPnlModal", "click", closeResetPnlModal);
    safeAddListener("cancelResetPnlBtn", "click", closeResetPnlModal);
    safeAddListener("confirmResetPnlBtn", "click", confirmResetPnlStats);
    safeAddListener("resetPnlConfirmModal", "click", (e) => {
        if (e.target.id === "resetPnlConfirmModal") closeResetPnlModal();
    });

    // Logs & Orders toolbar listeners
    safeAddListener("copyLogsBtn", "click", copyLogsToClipboard);
    safeAddListener("downloadLogsBtn", "click", downloadLogsAsFile);
    safeAddListener("clearLogsBtn", "click", openClearLogsModal);
    safeAddListener("closeClearLogsModal", "click", closeClearLogsModal);
    safeAddListener("cancelClearLogsBtn", "click", closeClearLogsModal);
    safeAddListener("confirmClearLogsBtn", "click", confirmClearSystemLogs);
    safeAddListener("clearDisplayOnlyBtn", "click", clearLogsDisplayOnly);
    safeAddListener("clearLogsConfirmModal", "click", (e) => {
        if (e.target.id === "clearLogsConfirmModal") closeClearLogsModal();
    });
    safeAddListener("copyOrdersBtn", "click", copyOrdersToClipboard);
    safeAddListener("clearOrdersBtn", "click", openClearOrdersModal);
    safeAddListener("closeClearOrdersModal", "click", closeClearOrdersModal);
    safeAddListener("cancelClearOrdersBtn", "click", closeClearOrdersModal);
    safeAddListener("confirmClearOrdersBtn", "click", confirmClearOrders);
    safeAddListener("clearOrdersConfirmModal", "click", (e) => {
        if (e.target.id === "clearOrdersConfirmModal") closeClearOrdersModal();
    });

    // Trigger Cycle Confirm Modal listeners
    safeAddListener("closeTriggerCycleConfirmModal", "click", closeTriggerCycleConfirmModal);
    safeAddListener("cancelTriggerCycleConfirmBtn", "click", closeTriggerCycleConfirmModal);
    safeAddListener("confirmTriggerCycleBtn", "click", confirmTriggerCycle);
    safeAddListener("triggerCycleConfirmModal", "click", (e) => {
        if (e.target.id === "triggerCycleConfirmModal") closeTriggerCycleConfirmModal();
    });

    // Kill Switch Modal listeners
    safeAddListener("closeKillSwitchModal", "click", closeKillSwitchModal);
    safeAddListener("cancelKillSwitchBtn", "click", closeKillSwitchModal);
    safeAddListener("confirmKillSwitchBtn", "click", confirmToggleKillSwitch);
    safeAddListener("killSwitchModal", "click", (e) => {
        if (e.target.id === "killSwitchModal") closeKillSwitchModal();
    });

    // Dry Run modal event listeners
    safeAddListener("dryRunModalBtn", "click", openDryRunModal);
    safeAddListener("closeDryRunModal", "click", closeDryRunModal);
    safeAddListener("cancelDryRunSave", "click", closeDryRunModal);
    safeAddListener("saveDryRunBalances", "click", openDryRunConfirmModal);
    safeAddListener("dryRunModal", "click", (e) => {
        if (e.target.id === "dryRunModal") closeDryRunModal();
    });

    // Dry Run Confirm modal event listeners
    safeAddListener("closeDryRunConfirmModal", "click", closeDryRunConfirmModal);
    safeAddListener("cancelDryRunConfirmBtn", "click", closeDryRunConfirmModal);
    safeAddListener("confirmDryRunSaveBtn", "click", confirmSaveDryRunBalances);
    safeAddListener("dryRunConfirmModal", "click", (e) => {
        if (e.target.id === "dryRunConfirmModal") closeDryRunConfirmModal();
    });

    // Telegram modal event listeners
    safeAddListener("telegramModalBtn", "click", openTelegramModal);
    safeAddListener("closeTelegramModal", "click", closeTelegramModal);
    safeAddListener("cancelTelegramSave", "click", closeTelegramModal);
    safeAddListener("saveTelegramConfigBtn", "click", saveTelegramConfig);
    safeAddListener("testTelegramBtn", "click", testTelegramConnection);
    safeAddListener("toggleTokenVisibilityBtn", "click", toggleTokenVisibility);
    safeAddListener("telegramModal", "click", (e) => {
        if (e.target.id === "telegramModal") closeTelegramModal();
    });

    // System Errors modal event listeners (support both healthCard and systemHealthCard)
    safeAddListener("healthCard", "click", openErrorsModal);
    safeAddListener("systemHealthCard", "click", openErrorsModal);
    safeAddListener("closeErrorsModal", "click", closeErrorsModal);
    safeAddListener("closeErrorsModalFooter", "click", closeErrorsModal);
    safeAddListener("clearErrorsBtn", "click", clearSystemErrors);
    safeAddListener("errorsModal", "click", (e) => {
        if (e.target.id === "errorsModal") closeErrorsModal();
    });

    // Strategy Conditions modal event listeners
    safeAddListener("conditionsModalBtn", "click", openConditionsModal);
    safeAddListener("closeConditionsModal", "click", closeConditionsModal);
    safeAddListener("closeConditionsModalFooter", "click", closeConditionsModal);
    // Mode Switch modal event listeners
    safeAddListener("modeBadge", "click", openSwitchModeModal);
    safeAddListener("modeSwitchModal", "click", (e) => {
        if (e.target.id === "modeSwitchModal") closeModeSwitchModal();
    });

    safeAddListener("tabBinaryTreeBtn", "click", () => switchConditionsTab("tree"));
    safeAddListener("tabLiveConditionsBtn", "click", () => switchConditionsTab("live"));
    safeAddListener("tabStrategyRulesBtn", "click", () => switchConditionsTab("rules"));

    initBinaryTreeControls();
    initDashPipelineControls();

    initPnlChart();
    initRegimeChart();
    initHealthChart();

    const isAuthed = await checkAuthStatus();
    if (isAuthed) {
        fetchDashboardData();
        loadTelegramConfig();
    }
    startMainPolling();
});

async function checkAuthStatus() {
    try {
        const res = await apiFetch("/api/auth_check");
        if (res.ok) {
            const data = await res.json();
            if (data.auth_required && !data.authenticated) {
                authToken = "";
                sessionStorage.removeItem("dash_password");
                showLoginModal();
                if (data.locked_out) {
                    const errorMsg = document.getElementById("loginErrorMsg");
                    if (errorMsg) {
                        errorMsg.textContent = `Account temporarily locked! Try again in ${data.retry_after_seconds} seconds.`;
                        errorMsg.style.display = "block";
                    }
                }
                return false;
            }
        }
    } catch (e) {
        // apiFetch already triggered handleConnectionFailure for network errors,
        // so offline detection is handled. Just log and continue.
        console.error("Auth check failed:", e);
    }
    return true;
}

function showLoginModal() {
    const modal = document.getElementById("loginModal");
    if (modal) {
        modal.classList.add("active");
        const input = document.getElementById("dashboardPasswordInput");
        if (input) {
            setTimeout(() => input.focus(), 100);
        }
    }
}

function closeLoginModal() {
    const modal = document.getElementById("loginModal");
    if (modal) modal.classList.remove("active");
}

async function performLogin() {
    const input = document.getElementById("dashboardPasswordInput");
    const errorMsg = document.getElementById("loginErrorMsg");
    const btn = document.getElementById("submitLoginBtn");

    const password = input.value.trim();
    if (!password) {
        errorMsg.textContent = "Please enter password";
        errorMsg.style.display = "block";
        return;
    }

    btn.disabled = true;
    btn.textContent = "Authenticating...";
    errorMsg.style.display = "none";

    try {
        const res = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password })
        });
        const data = await res.json();

        if (res.ok && data.success) {
            authToken = password;
            sessionStorage.setItem("dash_password", password);
            input.value = "";
            closeLoginModal();
            showToast("🔑 Successfully logged in to dashboard!", "success");
            fetchDashboardData();
        } else {
            authToken = "";
            sessionStorage.removeItem("dash_password");
            errorMsg.textContent = data.error || "Invalid password";
            errorMsg.style.display = "block";
        }
    } catch (err) {
        errorMsg.textContent = "Communication error: " + err;
        errorMsg.style.display = "block";
    } finally {
        btn.disabled = false;
        btn.textContent = "Login to Dashboard 🔑";
    }
}

// ── Toast Notifications ─────────────────────────────────────

function showToast(message, type = "info") {
    let container = document.getElementById("toastContainer");
    if (!container) {
        container = document.createElement("div");
        container.id = "toastContainer";
        container.className = "toast-container";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add("show");
    }, 10);

    setTimeout(() => {
        toast.classList.remove("show");
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

async function manualRefresh() {
    const btn = document.getElementById("refreshBtn");
    btn.classList.add("spinning");
    await fetchDashboardData();
    setTimeout(() => btn.classList.remove("spinning"), 500);
    showToast("✨ Dashboard data refreshed successfully!", "success");
}

// ── Clock ──────────────────────────────────────────────────

function initClock() {
    function updateClock() {
        const now = new Date();
        document.getElementById("utcClock").textContent = now.toUTCString().split(" ")[4] + " UTC";
    }
    updateClock();
    setInterval(updateClock, 1000);
}

// ── Main Data Fetcher ──────────────────────────────────────

async function fetchDashboardData() {
    await Promise.all([
        fetchStatus(),
        fetchPortfolio(),
        fetchOrders(),
        fetchLogs(),
        fetchUpdaterStatus(),
        fetchStrategyConditions(),
    ]);
}

// ── Git Auto-Updater Status ──────────────────────────────────

async function fetchUpdaterStatus() {
    try {
        const res = await apiFetch("/api/updater");
        if (!res.ok) return;
        const data = await res.json();

        const badge = document.getElementById("updaterBadge");
        const dot = document.getElementById("updaterDot");
        const text = document.getElementById("updaterText");
        const btn = document.getElementById("toggleUpdaterBtn");

        if (data.active) {
            if (badge) badge.className = "status-badge updater-badge";
            if (dot) dot.className = "dot pulse";
            if (text) text.textContent = "AUTO-PULL: ACTIVE";
            if (btn) {
                btn.textContent = "⏸️ Pause Auto-Pull";
                btn.style.borderColor = "rgba(16, 185, 129, 0.4)";
                btn.style.color = "#10b981";
            }
        } else {
            if (badge) badge.className = "status-badge updater-badge updater-badge-stopped";
            if (dot) dot.className = "dot";
            if (text) text.textContent = "AUTO-PULL: PAUSED";
            if (btn) {
                btn.textContent = "▶️ Resume Auto-Pull";
                btn.style.borderColor = "rgba(245, 158, 11, 0.4)";
                btn.style.color = "#f59e0b";
            }
        }
    } catch (err) {
        console.error("Failed to fetch updater status:", err);
    }
}

async function toggleUpdater() {
    const btn = document.getElementById("toggleUpdaterBtn");
    btn.disabled = true;
    btn.textContent = "⏳ Updating...";

    try {
        const res = await apiFetch("/api/updater/toggle", { method: "POST" });
        const data = await res.json();
        await fetchUpdaterStatus();

        if (data.active) {
            showToast("🔄 Git Auto-Updater activated successfully! (Active)", "success");
        } else {
            showToast("⏸️ Git Auto-Updater paused", "info");
        }
    } catch (err) {
        showToast("❌ Error updating auto-updater status: " + err, "error");
    } finally {
        btn.disabled = false;
    }
}

async function triggerManualPull() {
    const btn = document.getElementById("manualPullBtn");
    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> <span class="btn-text">Pulling...</span>`;

    try {
        const res = await apiFetch("/api/updater/pull", { method: "POST" });
        const data = await res.json();

        if (res.ok && data.success) {
            if (data.updated) {
                showToast(`🚀 ${data.message}`, "success");
            } else {
                showToast(`ℹ️ ${data.message}`, "info");
            }
            await fetchLogs();
        } else {
            showToast("❌ Error pulling code from GitHub: " + (data.error || data.message || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Communication error executing Git Pull: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<span>⬇️</span> <span class="btn-text">Git Pull</span>`;
    }
}

// ── Status & Regime ────────────────────────────────────────

async function fetchStatus() {
    const startTime = performance.now();
    try {
        const res = await apiFetch("/api/status");
        if (!res.ok) return;
        const latencyMs = performance.now() - startTime;
        if (typeof recordHealthPoint === "function") {
            recordHealthPoint(latencyMs);
        }
        const data = await res.json();

        // Detect if server is reporting CRASHED state from fallback server
        if (data.status === "CRASHED" || data.is_crashed) {
            showServerCrashModal(data);
            return;
        } else if (isServerCrashed) {
            hideServerCrashModal();
        }

        // Mode badge
        currentRunMode = data.run_mode || "DRY_RUN";
        const modeBadgeEl = document.getElementById("modeBadge");
        const modeTextEl = document.getElementById("modeText");
        if (modeTextEl) modeTextEl.textContent = currentRunMode;
        if (modeBadgeEl) {
            modeBadgeEl.className = "status-badge mode-badge";
            if (currentRunMode === "LIVE") {
                modeBadgeEl.classList.add("mode-badge-live");
                modeBadgeEl.title = "Execution Mode: LIVE (Real Money at Risk!). Click to switch mode.";
            } else if (currentRunMode === "TESTNET") {
                modeBadgeEl.classList.add("mode-badge-testnet");
                modeBadgeEl.title = "Execution Mode: TESTNET (Binance Sandbox). Click to switch mode.";
            } else {
                modeBadgeEl.title = "Execution Mode: DRY_RUN (Simulated Trading). Click to switch mode.";
            }
        }

        // Regime badge & card
        const regimeBadge = document.getElementById("regimeBadge");
        const regimeText = document.getElementById("regimeText");
        const regimeIcon = document.getElementById("regimeIcon");
        const macroRegimeVal = document.getElementById("macroRegimeVal");
        const macroRegimeSub = document.getElementById("macroRegimeSub");

        const btcMetrics = data.market_metrics && data.market_metrics.BTC ? data.market_metrics.BTC : null;
        let isBull = false;
        if (data.last_regime && data.last_regime !== "UNKNOWN") {
            isBull = data.last_regime.toLowerCase() === "bull";
        } else if (btcMetrics && typeof btcMetrics.change_sma150 === "number") {
            isBull = btcMetrics.change_sma150 >= 0;
        } else {
            isBull = true; // Safe default for bull regime
        }

        const strat = data.strategy_state || {};
        const macroState = strat.macro_state || {};
        const macro = macroState;
        const effLev = (typeof macroState.effective_leverage === "number") 
            ? macroState.effective_leverage 
            : (typeof strat.effective_leverage === "number" ? strat.effective_leverage : (isBull ? 2.4 : 0.0));

        if (isBull) {
            regimeBadge.className = "status-badge regime-badge";
            regimeIcon.textContent = "🐂";
            regimeText.textContent = "BULL REGIME";
            macroRegimeVal.textContent = "BULL MARKET";
            macroRegimeVal.className = "metric-value text-success";
            macroRegimeSub.textContent = `Dynamic Allocations Active (${Number(effLev).toFixed(1)}x Model)`;
        } else {
            regimeBadge.className = "status-badge regime-badge regime-bear";
            regimeIcon.textContent = "🐻";
            regimeText.textContent = "BEAR REGIME";
            macroRegimeVal.textContent = "BEAR MARKET";
            macroRegimeVal.className = "metric-value text-danger";
            macroRegimeSub.textContent = "Bear Protection Active (15% Short BTC + 85% USDT)";
        }

        // Record & Update Macro Regime Trend Chart
        const regimeGapPill = document.getElementById("regimeGapPill");
        const regimeLevPill = document.getElementById("regimeLevPill");

        if (btcMetrics && typeof btcMetrics.change_sma150 === "number") {
            const btcSmaGap = btcMetrics.change_sma150;
            if (regimeGapPill) {
                const gapSign = btcSmaGap >= 0 ? "+" : "";
                regimeGapPill.textContent = `${gapSign}${btcSmaGap.toFixed(2)}%`;
                regimeGapPill.className = btcSmaGap >= 0 ? "pnl-pill pnl-pill-high" : "pnl-pill pnl-pill-low";
            }
            if (regimeLevPill) {
                const isSafeHaven = !!(macro && macro.safe_haven_active);
                regimeLevPill.textContent = isBull ? (isSafeHaven ? "1.0x SAFE HAVEN" : `${Number(effLev).toFixed(1)}x BULL`) : "2.0x SHORT (35% Hedge)";
                regimeLevPill.className = isBull ? (isSafeHaven ? "pnl-pill pnl-pill-mid" : "pnl-pill pnl-pill-high") : "pnl-pill pnl-pill-low";
            }
            if (typeof recordRegimePoint === "function") {
                recordRegimePoint(btcSmaGap, isBull);
            }
        } else if (regimeLevPill) {
            const isSafeHaven = !!(macro && macro.safe_haven_active);
            regimeLevPill.textContent = isBull ? (isSafeHaven ? "1.0x SAFE HAVEN" : `${Number(effLev).toFixed(1)}x BULL`) : "2.0x SHORT (35% Hedge)";
            regimeLevPill.className = isBull ? (isSafeHaven ? "pnl-pill pnl-pill-mid" : "pnl-pill pnl-pill-high") : "pnl-pill pnl-pill-low";
        }

        // Update Market Performance Table (BTC, ETH, SOL: 4H, 24H, SMA-150)
        const macroCoinPerf = document.getElementById("macroCoinPerf");
        if (macroCoinPerf && data.market_metrics) {
            const metrics = data.market_metrics;
            const coins = ["BTC", "ETH", "SOL"];
            let rowsHtml = "";

            coins.forEach(coin => {
                const item = metrics[coin];
                if (!item) return;

                let icon = "₿";
                if (coin === "ETH") icon = "⟠";
                if (coin === "SOL") icon = "◎";

                const formatCell = (val) => {
                    const isPos = val >= 0;
                    const sign = isPos ? "+" : "";
                    const cls = isPos ? "perf-val-up" : "perf-val-down";
                    return `<span class="${cls}">${sign}${val.toFixed(2)}%</span>`;
                };

                rowsHtml += `
                    <div class="macro-perf-row">
                        <div class="col-coin">
                            <span class="coin-mini-icon">${icon}</span>
                            <span class="coin-name">${coin}</span>
                        </div>
                        <div class="col-tf" title="4-Hour Change">${formatCell(item.change_4h)}</div>
                        <div class="col-tf" title="24-Hour Change">${formatCell(item.change_24h)}</div>
                        <div class="col-tf" title="Distance from SMA-150 Trendline">${formatCell(item.change_sma150)}</div>
                    </div>
                `;
            });

            if (rowsHtml) {
                macroCoinPerf.innerHTML = `
                    <div class="macro-perf-table">
                        <div class="macro-perf-header">
                            <div class="col-coin">COIN</div>
                            <div class="col-tf" title="4-Hour Price Change">4H</div>
                            <div class="col-tf" title="24-Hour Price Change">24H</div>
                            <div class="col-tf" title="Distance from SMA-150 Trendline">SMA-150</div>
                        </div>
                        ${rowsHtml}
                    </div>
                `;
            }
        }

        // Health
        activeErrorsList = data.critical_errors || [];
        const healthVal = document.getElementById("systemHealthVal");
        const healthDetail = document.getElementById("systemHealthDetail");
        if (data.critical_errors_count > 0) {
            healthVal.textContent = `${data.critical_errors_count} ERROR${data.critical_errors_count > 1 ? 'S' : ''}`;
            healthVal.className = "metric-value text-danger";
            if (healthDetail) {
                healthDetail.textContent = data.latest_error || "System error recorded in logs";
                healthDetail.className = "metric-value text-danger-subtle";
                healthDetail.style.fontSize = "0.8rem";
                healthDetail.title = data.latest_error || "";
            }
        } else {
            healthVal.textContent = "HEALTHY";
            healthVal.className = "metric-value text-success";
            if (healthDetail) {
                healthDetail.textContent = "All systems operational";
                healthDetail.className = "metric-subtitle text-muted";
                healthDetail.style.fontSize = "0.85rem";
                healthDetail.title = "";
            }
        }

        const lastRunTs = data.last_run_ts || (data.strategy_state && data.strategy_state.last_run_ts) || (latestPortfolioData && latestPortfolioData.timestamp_ms);
        const lastCycleEl = document.getElementById("lastCycleTime");
        if (lastCycleEl) {
            if (lastRunTs && lastRunTs > 0) {
                const date = new Date(lastRunTs);
                const diffSec = Math.max(0, Math.floor((Date.now() - lastRunTs) / 1000));
                let agoStr = "Just now";
                if (diffSec >= 60) {
                    const min = Math.floor(diffSec / 60);
                    agoStr = min < 60 ? `${min}m ago` : `${Math.floor(min / 60)}h ago`;
                } else if (diffSec >= 5) {
                    agoStr = `${diffSec}s ago`;
                }
                lastCycleEl.textContent = `${date.toLocaleTimeString()} (${agoStr})`;
            } else {
                lastCycleEl.textContent = "Never";
            }
        }

        // Kill switch button style
        const ksBtn = document.getElementById("killSwitchBtn");
        if (data.kill_switch) {
            ksBtn.textContent = "⚠️ KILL SWITCH ACTIVE";
            ksBtn.style.background = "#ef4444";
            ksBtn.style.color = "#ffffff";
        } else {
            ksBtn.textContent = "🛡️ KILL SWITCH";
            ksBtn.style.background = "";
            ksBtn.style.color = "";
        }

    } catch (err) {
        console.error("Failed to fetch status:", err);
    }
}

// ── Portfolio & Allocations ────────────────────────────────

async function fetchPortfolio() {
    try {
        const res = await apiFetch("/api/portfolio");
        if (!res.ok) {
            let errorMsg = "Exchange connection unavailable";
            let serverIp = "unknown";
            try {
                const errData = await res.json();
                if (errData.error) errorMsg = errData.error;
                if (errData.server_ip) serverIp = errData.server_ip;
                if (errData.is_auth_error || errorMsg.includes("-2015") || errorMsg.includes("Invalid API-key") || errorMsg.includes("permissions")) {
                    isAuthErr = true;
                }
            } catch (_) {}

            const container = document.getElementById("allocationBars");
            if (container) {
                let detailsHtml = "";
                if (isAuthErr) {
                    detailsHtml = `
                        <div class="port-error-details" style="margin-top: 10px; font-size: 0.85rem; color: #fca5a5; line-height: 1.5; background: rgba(0,0,0,0.3); padding: 12px; border-radius: 8px; text-align: right; direction: rtl;">
                            <div style="font-weight: 600; color: #fecdd3; margin-bottom: 6px;">🔑 שגיאת אימות Binance (-2015: Invalid API-key, IP, or permissions):</div>
                            <ul style="margin: 4px 0 4px 20px; padding: 0;">
                                <li><strong>קובץ מפתחות (.env):</strong> וודא שקובץ <code>.env</code> מכיל <code>BINANCE_API_KEY</code> ו-<code>BINANCE_API_SECRET</code> תקינים.</li>
                                <li><strong>כתובת IP מורשית (IP Whitelist):</strong> אם מוגדרת הגבלת IP בבינאנס, הוסף את ה-IP של השרת: <code>${escapeHtml(serverIp)}</code>.</li>
                                <li><strong>הרשאת חוזים (Futures):</strong> בניהול ה-API בבינאנס סמן ב-V את <code>Enable Futures</code> (וחשבון USDT-M פעיל).</li>
                            </ul>
                        </div>
                    `;
                } else {
                    detailsHtml = `
                        <div style="font-size: 0.82rem; color: #fca5a5; margin-top: 6px; word-break: break-all;">${escapeHtml(errorMsg)}</div>
                    `;
                }

                container.innerHTML = `
                    <div class="empty-state" style="border: 1px solid rgba(244, 63, 94, 0.4); background: rgba(244, 63, 94, 0.1); border-radius: 10px; padding: 18px 20px; color: #fecdd3; text-align: center;">
                        <div style="font-size: 1.05rem; font-weight: 600; margin-bottom: 6px; color: #fda4af;">
                            ⚠️ לא ניתן לחשב יתרות תיק (Exchange Connection Issue)
                        </div>
                        ${detailsHtml}
                        <div style="margin-top: 14px; display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;">
                            <button class="btn btn-sm btn-primary" onclick="fetchPortfolio()">
                                <span>🔄</span> <span>נסה שוב (Retry)</span>
                            </button>
                            <button class="btn btn-sm btn-outline" onclick="openSwitchModeModal()">
                                <span>🛡️</span> <span>החלף מצב (DRY_RUN / LIVE)</span>
                            </button>
                        </div>
                    </div>
                `;
            }
            return;
        }
        const data = await res.json();
        latestPortfolioData = data;

        // Net Total Portfolio Value (after deducting estimated 0.1% sell fee on open holdings)
        const netValue = data.net_total_value_usd !== undefined ? data.net_total_value_usd : data.total_value_usd;
        document.getElementById("portfolioValue").textContent = `$${netValue.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        // Store and render historical PNL chart
        pnlHistoryData = data.pnl_history || [];
        renderPnlChart();
        updatePnlAndFeesDisplay(selectedPnlTimeframe);

        // Coin performance summary tags removed from metric card 1 (user requested PNL & FEES only)
        const coinPerfTagsEl = document.getElementById("coinPerfTags");
        if (coinPerfTagsEl) {
            coinPerfTagsEl.innerHTML = "";
        }

        // Allocation Bars
        const container = document.getElementById("allocationBars");
        container.innerHTML = "";

        if (!data.holdings || data.holdings.length === 0) {
            container.innerHTML = `<div class="empty-state">No balances recorded</div>`;
            return;
        }

        data.holdings.forEach(h => {
            const symbol = h.symbol.toUpperCase();
            let bgClass = "bg-usdt";
            let coinIcon = "💵";
            if (symbol === "BTC") { bgClass = "bg-btc"; coinIcon = "₿"; }
            else if (symbol === "ETH") { bgClass = "bg-eth"; coinIcon = "⟠"; }
            else if (symbol === "SOL") { bgClass = "bg-sol"; coinIcon = "◎"; }
            else if (symbol === "USDT") { bgClass = "bg-usdt"; coinIcon = "💵"; }

            const isZero = (h.total === 0 || h.weight_pct === 0);
            const totalStr = isZero ? "0.00" : (h.total < 1 ? h.total.toFixed(4) : h.total.toFixed(2));
            const valueStr = h.value_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            const fillWidth = isZero ? 0 : Math.min(100, Math.max(1, h.weight_pct));

            const priceVal = h.current_price || 0;
            const priceStr = priceVal > 0 ? `$${priceVal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: priceVal < 10 ? 4 : 2 })}` : "--";

            const netChangePct = h.net_change_pct !== undefined ? h.net_change_pct : (h.change_pct !== undefined ? h.change_pct : 0);
            let perfBadgeHtml = "";
            if (symbol !== "USDT" && symbol !== "USD") {
                const isPos = netChangePct >= 0;
                const sign = isPos ? "+" : "";
                const icon = isPos ? "📈" : "📉";
                const badgeClass = isPos ? "perf-pill perf-up" : "perf-pill perf-down";
                const entryPx = h.entry_price || h.initial_price;
                const baseInfo = entryPx ? `Buy Price: $${entryPx.toLocaleString("en-US", { minimumFractionDigits: 2 })} | Net PnL (Deducting Buy & Exit Fees)` : "Net PnL (Deducting Buy & Exit Fees)";
                perfBadgeHtml = `<span class="${badgeClass}" title="${baseInfo}">${icon} Net: ${sign}${netChangePct.toFixed(2)}%</span>`;
            }

            const row = document.createElement("div");
            row.className = `alloc-row ${isZero ? "alloc-row-zero" : ""}`;
            row.innerHTML = `
                <div class="alloc-info">
                    <div class="alloc-symbol-badge">
                        <span class="coin-icon">${coinIcon}</span>
                        <span class="coin-symbol-name">${symbol}</span>
                        ${perfBadgeHtml}
                    </div>
                    <div class="alloc-center">
                        <span class="alloc-balance">${totalStr} ${symbol} ($${valueStr})</span>
                        <span class="alloc-price-sub">${priceStr}</span>
                    </div>
                    <div class="alloc-right">
                        <span class="alloc-weight">${h.weight_pct}%</span>
                    </div>
                </div>
                <div class="progress-bg">
                    <div class="progress-fill ${bgClass}" style="width: ${fillWidth}%"></div>
                </div>
            `;
            container.appendChild(row);
        });

    } catch (err) {
        console.error("Failed to fetch portfolio:", err);
    }
}

// ── Orders ─────────────────────────────────────────────────

async function fetchOrders() {
    try {
        const res = await apiFetch("/api/orders");
        if (!res.ok) return;
        const data = await res.json();

        const tableBody = document.getElementById("ordersTableBody");
        tableBody.innerHTML = "";

        const allOrders = [...(data.pending || []), ...(data.completed || [])].reverse();
        latestOrdersList = data.completed || [];
        document.getElementById("orderCount").textContent = allOrders.length;
        updatePnlAndFeesDisplay(selectedPnlTimeframe);

        if (allOrders.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="8" class="empty-cell text-muted">No orders executed yet</td></tr>`;
            return;
        }

        allOrders.slice(0, 100).forEach(o => {
            const tr = document.createElement("tr");
            const side = (o.side || "BUY").toUpperCase();
            const sideClass = side === "BUY" ? "tag-buy" : "tag-sell";
            const priceVal = o.average_price || o.price || 0;
            const priceStr = priceVal > 0 ? `$${priceVal.toFixed(2)}` : "MARKET";
            const amountVal = o.filled_amount || o.amount || 0;

            const feeVal = typeof o.fees === 'number' ? o.fees : 0.0;
            const feeStr = feeVal > 0 ? `${feeVal.toFixed(4)} ${o.fee_currency || ''}`.trim() : "0.0";

            // Calculate Net Total USD (Net cash flow impact after fees)
            const grossTotalUsd = amountVal * priceVal;
            let netTotalStr = "--";
            let netStyle = "color: var(--text-muted);";

            if (grossTotalUsd > 0) {
                let feeInUsd = feeVal;
                if (o.fee_currency && o.fee_currency !== "USDT" && o.fee_currency !== "USD" && priceVal > 0) {
                    feeInUsd = feeVal * priceVal;
                }

                if (side === "BUY") {
                    const netCost = grossTotalUsd + feeInUsd;
                    netTotalStr = `-$${netCost.toFixed(2)}`;
                    netStyle = "color: var(--accent-danger, #f43f5e);";
                } else {
                    const netReceived = grossTotalUsd - feeInUsd;
                    netTotalStr = `+$${netReceived.toFixed(2)}`;
                    netStyle = "color: var(--accent-success, #10b981); font-weight: 600;";
                }
            }

            let statusRaw = (o.status || "FILLED").toUpperCase();
            let statusDisplay = statusRaw;
            let statusClass = "tag-filled";

            if (statusRaw === "FILLED") {
                statusDisplay = "DONE";
                statusClass = "tag-filled";
            } else if (statusRaw === "FAILED" || statusRaw === "REJECTED") {
                statusDisplay = "FAILED";
                statusClass = "tag-sell";
            } else if (statusRaw === "PENDING" || statusRaw === "OPEN") {
                statusDisplay = "PENDING";
                statusClass = "tag-buy";
            }

            tr.innerHTML = `
                <td><code>${(o.client_order_id || o.exchange_order_id || "N/A").substring(0, 18)}</code></td>
                <td><span class="tag ${sideClass}">${side}</span></td>
                <td><strong>${o.symbol}</strong></td>
                <td>${amountVal}</td>
                <td>${priceStr}</td>
                <td>${feeStr}</td>
                <td><span style="${netStyle}">${netTotalStr}</span></td>
                <td><span class="tag ${statusClass}">${statusDisplay}</span></td>
            `;
            tableBody.appendChild(tr);
        });

    } catch (err) {
        console.error("Failed to fetch orders:", err);
    }
}

// ── Logs Console ───────────────────────────────────────────

let logDisplayClearedTimestamp = 0;

async function fetchLogs() {
    try {
        const res = await apiFetch("/api/logs");
        if (!res.ok) return;
        const data = await res.json();

        const logConsole = document.getElementById("logConsole");
        const logCountEl = document.getElementById("logCount");
        if (!logConsole) return;

        let logLines = data.logs || [];

        // If user chose "Display Only", filter out logs generated prior to that action
        if (logDisplayClearedTimestamp > 0) {
            logLines = logLines.filter(rawLine => {
                const match = rawLine.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
                if (match) {
                    const lineTime = new Date(match[1]).getTime();
                    return !isNaN(lineTime) && lineTime >= logDisplayClearedTimestamp;
                }
                return false;
            });
        }

        if (logCountEl) logCountEl.textContent = `${logLines.length} / 1000`;

        const wasScrolledToBottom = logConsole.scrollHeight - logConsole.clientHeight <= logConsole.scrollTop + 20;

        logConsole.innerHTML = "";
        
        if (!logLines || logLines.length === 0) {
            if (logDisplayClearedTimestamp > 0) {
                logConsole.innerHTML = `<div class="log-line text-muted">Logs console display cleared by user — waiting for new logs... ✓</div>`;
            } else {
                logConsole.innerHTML = `<div class="log-line text-muted">No logs recorded yet</div>`;
            }
            return;
        }

        logLines.forEach(rawLine => {
            if (rawLine.includes("Loaded state:") || rawLine.includes("Portfolio snapshot:") || rawLine.includes("No state file found at") || rawLine.includes("No new closed candles")) {
                return;
            }

            // Determine custom highlight style based on line contents
            let customClass = "";
            if (rawLine.includes("STRATEGY DECISION") || rawLine.includes("Target allocation") || rawLine.includes("Signal:") || rawLine.includes("Regime:")) {
                customClass = "log-row-strategy";
            } else if (rawLine.includes("Order REJECTED") || rawLine.includes("Risk manager") || rawLine.includes("rejected:") || rawLine.includes("Kill switch")) {
                customClass = "log-row-risk";
            } else if (rawLine.includes("Order executed:") || rawLine.includes("APPROVED by risk manager") || rawLine.includes("Order executed") || rawLine.includes("Cycle completed")) {
                customClass = "log-row-executed";
            } else if (rawLine.includes("ERROR") || rawLine.includes("CRITICAL") || rawLine.includes("failed")) {
                customClass = "log-row-error";
            }

            const row = document.createElement("div");
            row.className = `log-row ${customClass}`.trim();

            // Parse pattern: "2026-09-04T12:16:54+0300 | INFO     | bot.services.state | Loaded state..."
            const match = rawLine.match(/^(\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\S*)\s*\|\s*(\w+)\s*\|\s*([\w\.]+)\s*\|\s*(.*)$/);

            if (match) {
                const timeStr = match[2]; // "12:16:54"
                const level = match[3].trim().toUpperCase(); // "INFO"
                const fullModule = match[4].trim(); // "bot.services.state"
                const msg = match[5].trim();

                // Shorten module name: "bot.services.state" -> "state"
                const moduleShort = fullModule.replace(/^bot\.(services\.|data\.|exchanges\.)?/, "");

                let levelClass = "log-level-info";
                if (level === "ERROR" || level === "CRITICAL") levelClass = "log-level-error";
                else if (level === "WARNING" || level === "WARN") levelClass = "log-level-warn";

                row.innerHTML = `
                    <span class="log-time">${timeStr}</span>
                    <span class="log-badge ${levelClass}">${level}</span>
                    <span class="log-module">${moduleShort}</span>
                    <span class="log-msg">${escapeHtml(msg)}</span>
                `;
            } else {
                // Fallback for unparsed lines
                let lineClass = "log-msg";
                if (rawLine.includes("ERROR") || rawLine.includes("CRITICAL")) lineClass += " text-danger";
                else if (rawLine.includes("WARNING")) lineClass += " text-accent";
                else if (rawLine.includes("CYCLE COMPLETE")) lineClass += " text-success";

                row.innerHTML = `<span class="${lineClass}">${escapeHtml(rawLine)}</span>`;
            }

            logConsole.appendChild(row);
        });

        if (wasScrolledToBottom) {
            logConsole.scrollTop = logConsole.scrollHeight;
        }

    } catch (err) {
        console.error("Failed to fetch logs:", err);
    }
}

// ── Copy & Clear Actions for Logs & Orders ─────────────────────

async function copyTextToClipboard(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            console.warn("navigator.clipboard.writeText failed, attempting execCommand fallback:", err);
        }
    }

    // Universal Fallback: execCommand with hidden textarea (works over non-secure HTTP and IP addresses)
    let success = false;
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.top = "0";
    textArea.style.left = "0";
    textArea.style.width = "2em";
    textArea.style.height = "2em";
    textArea.style.padding = "0";
    textArea.style.border = "none";
    textArea.style.outline = "none";
    textArea.style.boxShadow = "none";
    textArea.style.background = "transparent";
    textArea.setAttribute("readonly", "");
    document.body.appendChild(textArea);

    textArea.focus();
    textArea.select();
    if (textArea.setSelectionRange) {
        textArea.setSelectionRange(0, 999999);
    }

    try {
        success = document.execCommand("copy");
    } catch (err) {
        console.error("document.execCommand copy failed:", err);
        success = false;
    }

    document.body.removeChild(textArea);
    if (success) return true;

    // Selection Range API fallback
    try {
        const consoleEl = document.getElementById("logConsole");
        if (consoleEl) {
            const range = document.createRange();
            range.selectNodeContents(consoleEl);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            success = document.execCommand("copy");
            if (success) return true;
        }
    } catch (err) {
        console.error("Selection range copy failed:", err);
    }

    return false;
}

async function copyLogsToClipboard() {
    const logConsole = document.getElementById("logConsole");
    if (!logConsole) {
        showToast("⚠️ Log console not found", "error");
        return;
    }

    const rows = logConsole.querySelectorAll(".log-row");
    let text = "";
    if (rows && rows.length > 0) {
        const textLines = Array.from(rows).map(r => {
            const time = r.querySelector(".log-time")?.innerText || "";
            const badge = r.querySelector(".log-badge")?.innerText || "";
            const module = r.querySelector(".log-module")?.innerText || "";
            const msg = r.querySelector(".log-msg")?.innerText || "";
            if (time || badge || module || msg) {
                return `${time} | ${badge.padEnd(5)} | ${module} | ${msg}`.trim();
            }
            return r.innerText.trim();
        });
        text = textLines.join("\n");
    } else {
        text = (logConsole.innerText || logConsole.textContent || "").trim();
    }

    if (!text) {
        showToast("⚠️ No log content available", "info");
        return;
    }

    const copied = await copyTextToClipboard(text);
    if (copied) {
        if (text === "No logs recorded yet" || text === "Initializing dashboard stream..." || text === "Logs console cleared by user") {
            showToast("📋 Log console notice copied to clipboard!", "info");
        } else {
            showToast("📋 All system logs copied to clipboard!", "success");
        }
    } else {
        // Auto-select text so user can copy manually with Ctrl+C
        try {
            const range = document.createRange();
            range.selectNodeContents(logConsole);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            showToast("⚠️ Browser security blocked auto-copy. Text selected! Press Ctrl+C to copy.", "info");
        } catch (e) {
            showToast("❌ Failed to copy system logs", "error");
        }
    }
}

async function downloadLogsAsFile() {
    const nowStr = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const fileName = `bot_logs_${nowStr}.log`;

    // Try downloading full clean log file directly from server
    try {
        const response = await fetch("/api/logs/download");
        if (response.ok) {
            const blob = await response.blob();
            if (blob && blob.size > 0) {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.style.display = "none";
                a.href = url;
                a.download = fileName;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
                showToast("📥 System logs downloaded successfully!", "success");
                return;
            }
        }
    } catch (e) {
        console.warn("Direct /api/logs/download failed, falling back to UI console logs", e);
    }

    // Fallback: extract rendered logs from console
    const logConsole = document.getElementById("logConsole");
    if (!logConsole) {
        showToast("⚠️ Log console not found", "error");
        return;
    }

    const rows = logConsole.querySelectorAll(".log-row");
    let text = "";
    if (rows && rows.length > 0) {
        const textLines = Array.from(rows).map(r => {
            const time = r.querySelector(".log-time")?.innerText || "";
            const badge = r.querySelector(".log-badge")?.innerText || "";
            const module = r.querySelector(".log-module")?.innerText || "";
            const msg = r.querySelector(".log-msg")?.innerText || "";
            if (time || badge || module || msg) {
                return `${time} | ${badge.padEnd(5)} | ${module} | ${msg}`.trim();
            }
            return r.innerText.trim();
        });
        text = textLines.join("\n");
    } else {
        text = (logConsole.innerText || logConsole.textContent || "").trim();
    }

    if (!text || text === "No logs recorded yet" || text === "Initializing dashboard stream..." || text === "Logs console cleared by user") {
        showToast("⚠️ No log content available to download", "info");
        return;
    }

    try {
        const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.style.display = "none";
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        showToast("📥 System logs downloaded as file!", "success");
    } catch (err) {
        console.error("Failed to download logs file:", err);
        showToast("❌ Failed to download logs file", "error");
    }
}

function openClearLogsModal() {
    const modal = document.getElementById("clearLogsConfirmModal");
    if (modal) {
        modal.classList.add("active");
    } else {
        confirmClearSystemLogs();
    }
}

function closeClearLogsModal() {
    const modal = document.getElementById("clearLogsConfirmModal");
    if (modal) modal.classList.remove("active");
}

function clearLogsDisplayOnly() {
    closeClearLogsModal();
    logDisplayClearedTimestamp = Date.now();
    const logConsole = document.getElementById("logConsole");
    if (logConsole) {
        logConsole.innerHTML = `<div class="log-line text-muted">Logs console display cleared by user (Display only) — waiting for new logs... ✓</div>`;
    }
    const logCountEl = document.getElementById("logCount");
    if (logCountEl) logCountEl.textContent = "0 / 1000";
    showToast("🧹 Log console display cleared!", "info");
}

async function confirmClearSystemLogs() {
    closeClearLogsModal();
    logDisplayClearedTimestamp = 0;
    const btn = document.getElementById("clearLogsBtn");
    const confirmBtn = document.getElementById("confirmClearLogsBtn");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ Clearing...";
    }
    if (confirmBtn) confirmBtn.disabled = true;

    try {
        const res = await apiFetch("/api/logs/clear", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🧹 System logs cleared successfully!", "success");
            const logConsole = document.getElementById("logConsole");
            if (logConsole) {
                logConsole.innerHTML = `<div class="log-line text-muted">System logs cleared by user ✓</div>`;
            }
            const logCountEl = document.getElementById("logCount");
            if (logCountEl) logCountEl.textContent = "0 / 1000";
            await fetchLogs();
        } else {
            showToast("❌ Error clearing system logs: " + (data.error || data.message || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Error clearing system logs: " + err, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🧹 Clear Logs";
        }
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

async function clearLogsConsole() {
    await confirmClearSystemLogs();
}

async function copyOrdersToClipboard() {
    const table = document.querySelector(".data-table");
    if (!table) {
        showToast("⚠️ Order table not found", "error");
        return;
    }

    const rows = Array.from(table.querySelectorAll("tr"));
    const textLines = rows.map(r => {
        const cells = Array.from(r.querySelectorAll("th, td")).map(c => c.innerText.trim());
        return cells.join("\t");
    });
    const text = textLines.join("\n").trim();

    if (!text) {
        showToast("⚠️ No order history content available", "info");
        return;
    }

    const copied = await copyTextToClipboard(text);
    if (copied) {
        const hasExecutedOrders = rows.length > 1 && !text.includes("No order history available") && !text.includes("No orders executed yet") && !text.includes("Orders display cleared by user");
        if (hasExecutedOrders) {
            showToast("📋 Order history copied to clipboard!", "success");
        } else {
            showToast("📋 Order table headers copied to clipboard! (No orders executed yet)", "info");
        }
    } else {
        showToast("❌ Failed to copy order history", "error");
    }
}

function openClearOrdersModal() {
    const modal = document.getElementById("clearOrdersConfirmModal");
    if (modal) {
        modal.classList.add("active");
    } else {
        confirmClearOrders();
    }
}

function closeClearOrdersModal() {
    const modal = document.getElementById("clearOrdersConfirmModal");
    if (modal) modal.classList.remove("active");
}

async function confirmClearOrders() {
    closeClearOrdersModal();
    try {
        const res = await apiFetch("/api/orders/clear", { method: "POST" });
        if (res.ok) {
            const data = await res.json().catch(() => ({}));
            const tableBody = document.getElementById("ordersTableBody");
            if (tableBody) {
                tableBody.innerHTML = `<tr><td colspan="8" class="empty-cell text-muted">No orders executed yet</td></tr>`;
            }
            const orderCountEl = document.getElementById("orderCount");
            if (orderCountEl) orderCountEl.textContent = "0";
            latestOrdersList = [];
            showToast("🧹 כל הפקודות וההיסטוריה נמחקו בהצלחה! (Orders cleared)", "success");
            // Refresh dashboard data to sync UI
            await fetchDashboardData();
        } else {
            const errData = await res.json().catch(() => ({}));
            showToast(`❌ מחיקת פקודות נכשלה: ${errData.error || "Server error"}`, "error");
        }
    } catch (e) {
        console.error("Failed to clear orders:", e);
        showToast(`❌ שגיאה במחיקת פקודות: ${e.message}`, "error");
    }
}

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Actions ────────────────────────────────────────────────

function openTriggerCycleModal() {
    const title = document.getElementById("triggerCycleModalTitle");
    const desc = document.getElementById("triggerCycleModalDesc");
    const warn = document.getElementById("triggerCycleWarningBox");
    const confirmBtn = document.getElementById("confirmTriggerCycleBtn");

    if (currentRunMode === "LIVE") {
        if (title) {
            title.textContent = "🔥 Confirm LIVE Trading Cycle Execution";
            title.style.color = "var(--accent-danger, #ef4444)";
        }
        if (desc) {
            desc.innerHTML = "Current mode: <strong style='color:#ef4444;'>🔥 LIVE TRADING MODE</strong>.<br>Are you sure you want to trigger an immediate trading cycle in live trading mode?";
        }
        if (warn) {
            warn.style.background = "rgba(244, 63, 94, 0.15)";
            warn.style.borderColor = "rgba(244, 63, 94, 0.4)";
            warn.style.color = "#fecdd3";
            warn.innerHTML = "⚠️ <strong>LIVE TRADING WARNING:</strong> System is running in LIVE mode! Executing cycle will analyze live market data and place REAL buy/sell orders on your Binance account!";
        }
        if (confirmBtn) {
            confirmBtn.className = "btn btn-danger";
            confirmBtn.textContent = "Confirm & Execute LIVE ▶";
        }
    } else {
        if (title) {
            title.textContent = "▶ Confirm DRY RUN Trading Cycle Execution";
            title.style.color = "var(--accent-color, #3b82f6)";
        }
        if (desc) {
            desc.innerHTML = "Current mode: <strong>⚙️ DRY_RUN (Simulation)</strong>.<br>Are you sure you want to trigger an immediate trading cycle now?";
        }
        if (warn) {
            warn.style.background = "rgba(59, 130, 246, 0.12)";
            warn.style.borderColor = "rgba(59, 130, 246, 0.35)";
            warn.style.color = "#93c5fd";
            warn.innerHTML = "ℹ️ <strong>Simulation Mode (DRY RUN):</strong> Executing cycle will perform simulation trading on recorded system holdings.";
        }
        if (confirmBtn) {
            confirmBtn.className = "btn btn-action";
            confirmBtn.textContent = "Confirm & Execute ▶";
        }
    }

    const modal = document.getElementById("triggerCycleConfirmModal");
    if (modal) modal.classList.add("active");
}

function closeTriggerCycleConfirmModal() {
    const modal = document.getElementById("triggerCycleConfirmModal");
    if (modal) modal.classList.remove("active");
}

async function confirmTriggerCycle() {
    closeTriggerCycleConfirmModal();
    await triggerCycle();
}

async function triggerCycle() {
    const btn = document.getElementById("triggerCycleBtn");
    btn.disabled = true;
    btn.textContent = "⏳ Running...";

    try {
        const res = await apiFetch("/api/trigger", { method: "POST" });
        const data = await res.json();
        await fetchDashboardData();
        showToast("▶ Trading cycle triggered and completed successfully!", "success");
    } catch (err) {
        showToast("❌ Error executing trading cycle: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "▶ Run Instant Cycle";
    }
}

function openKillSwitchModal() {
    const modal = document.getElementById("killSwitchModal");
    const desc = document.getElementById("killSwitchModalDesc");
    const ksBtn = document.getElementById("killSwitchBtn");

    const isActive = ksBtn.textContent.includes("ACTIVE");

    if (isActive) {
        desc.innerHTML = "Current state: <strong style='color:#ef4444;'>⚠️ KILL SWITCH ACTIVE</strong>.<br>Are you sure you want to deactivate Kill Switch and resume normal trading?";
    } else {
        desc.innerHTML = "Current state: <strong>🛡️ KILL SWITCH OFF (Normal)</strong>.<br>Are you sure you want to activate Kill Switch and immediately block new trading orders?";
    }

    if (modal) modal.classList.add("active");
}

function closeKillSwitchModal() {
    const modal = document.getElementById("killSwitchModal");
    if (modal) modal.classList.remove("active");
}

async function confirmToggleKillSwitch() {
    closeKillSwitchModal();
    const btn = document.getElementById("confirmKillSwitchBtn");
    btn.disabled = true;

    try {
        const res = await apiFetch("/api/killswitch", { method: "POST" });
        const data = await res.json();
        await fetchStatus();
        const statusMsg = data.kill_switch ? "⚠️ KILL SWITCH ACTIVATED — Trading Halts!" : "🛡️ KILL SWITCH DEACTIVATED — Trading Active";
        showToast(statusMsg, data.kill_switch ? "error" : "success");
    } catch (err) {
        showToast("❌ Failed to toggle kill switch: " + err, "error");
    } finally {
        btn.disabled = false;
    }
}

function openResetPnlModal() {
    const modal = document.getElementById("resetPnlConfirmModal");
    if (modal) modal.classList.add("active");
}

function closeResetPnlModal() {
    const modal = document.getElementById("resetPnlConfirmModal");
    if (modal) modal.classList.remove("active");
}

async function confirmResetPnlStats() {
    closeResetPnlModal();
    const confirmBtn = document.getElementById("confirmResetPnlBtn");
    const miniBtn = document.getElementById("pnlResetMiniBtn");
    const toolbarBtn = document.getElementById("resetStatsBtn");

    if (confirmBtn) confirmBtn.disabled = true;
    if (miniBtn) miniBtn.disabled = true;
    if (toolbarBtn) toolbarBtn.disabled = true;

    try {
        const res = await apiFetch("/api/reset_stats", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🧹 Session stats (PNL and fees) reset successfully!", "success");
            pnlHistoryData = [];
            latestOrdersList = [];
            latestPortfolioData = null;
            await fetchDashboardData();
        } else {
            showToast("❌ Error resetting stats: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Failed to reset stats: " + err, "error");
    } finally {
        if (confirmBtn) confirmBtn.disabled = false;
        if (miniBtn) miniBtn.disabled = false;
        if (toolbarBtn) toolbarBtn.disabled = false;
    }
}

// ── Dry Run Holdings Modal Functions ─────────────────────────

function openDryRunModal() {
    const modal = document.getElementById("dryRunModal");
    if (modal) modal.classList.add("active");
    apiFetch("/api/dry_run/balances").then(async res => {
        if (res.ok) {
            const data = await res.json();
            const bal = data.balances || {};
            const usdt = document.getElementById("dryUsdtInput");
            if (usdt) usdt.value = bal.USDT !== undefined ? bal.USDT : 1000;
            const btc = document.getElementById("dryBtcInput");
            if (btc) btc.value = bal.BTC !== undefined ? bal.BTC : 0;
            const eth = document.getElementById("dryEthInput");
            if (eth) eth.value = bal.ETH !== undefined ? bal.ETH : 0;
            const sol = document.getElementById("drySolInput");
            if (sol) sol.value = bal.SOL !== undefined ? bal.SOL : 0;
        }
    }).catch(e => console.error("Failed to load dry run balances:", e));
}

function closeDryRunModal() {
    const modal = document.getElementById("dryRunModal");
    modal.classList.remove("active");
}

function openDryRunConfirmModal() {
    const usdt = parseFloat(document.getElementById("dryUsdtInput").value) || 0;
    const btc = parseFloat(document.getElementById("dryBtcInput").value) || 0;
    const eth = parseFloat(document.getElementById("dryEthInput").value) || 0;
    const sol = parseFloat(document.getElementById("drySolInput").value) || 0;

    const desc = document.getElementById("dryRunConfirmModalDesc");
    if (desc) {
        desc.innerHTML = `Are you sure you want to update system DRY RUN holdings to the following values?<br><br>` +
            `<strong style="color: #60a5fa; font-size: 1rem;">💵 USDT: ${usdt} | ₿ BTC: ${btc} | ⟠ ETH: ${eth} | ◎ SOL: ${sol}</strong>`;
    }

    const modal = document.getElementById("dryRunConfirmModal");
    if (modal) modal.classList.add("active");
}

function closeDryRunConfirmModal() {
    const modal = document.getElementById("dryRunConfirmModal");
    if (modal) modal.classList.remove("active");
}

async function confirmSaveDryRunBalances() {
    closeDryRunConfirmModal();

    const usdt = parseFloat(document.getElementById("dryUsdtInput").value) || 0;
    const btc = parseFloat(document.getElementById("dryBtcInput").value) || 0;
    const eth = parseFloat(document.getElementById("dryEthInput").value) || 0;
    const sol = parseFloat(document.getElementById("drySolInput").value) || 0;

    const confirmBtn = document.getElementById("confirmDryRunSaveBtn");
    const saveBtn = document.getElementById("saveDryRunBalances");

    if (confirmBtn) confirmBtn.disabled = true;
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving...";
    }

    try {
        const res = await apiFetch("/api/dry_run/balances", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ balances }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("⚙️ DRY RUN holdings updated successfully!", "success");
            closeDryRunModal();
            await fetchPortfolio();
        } else {
            showToast("❌ Error updating holdings: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Error updating holdings: " + err, "error");
    } finally {
        if (confirmBtn) confirmBtn.disabled = false;
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Update Holdings 💾";
        }
    }
}

// ── System Errors Diagnostic Modal Functions ───────────────────

function openErrorsModal() {
    const modal = document.getElementById("errorsModal");
    const listEl = document.getElementById("modalErrorList");
    listEl.innerHTML = "";

    if (!activeErrorsList || activeErrorsList.length === 0) {
        listEl.innerHTML = `<li class="empty-errors">No errors recorded — System is operating normally ✓</li>`;
    } else {
        activeErrorsList.forEach((err, idx) => {
            const li = document.createElement("li");
            li.className = "error-item";
            li.textContent = `#${idx + 1}: ${err}`;
            listEl.appendChild(li);
        });
    }
    modal.classList.add("active");
}

function closeErrorsModal() {
    const modal = document.getElementById("errorsModal");
    modal.classList.remove("active");
}

async function clearSystemErrors() {
    const btn = document.getElementById("clearErrorsBtn");
    btn.disabled = true;
    btn.textContent = "Clearing...";

    try {
        const res = await apiFetch("/api/errors/clear", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🧹 System errors cleared successfully!", "success");
            activeErrorsList = [];
            closeErrorsModal();
            await fetchStatus();
        } else {
            showToast("❌ Error clearing system errors: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Error clearing system errors: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "🧹 Clear Errors";
    }
}

// ── Telegram Settings Functions ───────────────────────────────

async function openTelegramModal() {
    const modal = document.getElementById("telegramModal");
    if (!modal) return;
    await loadTelegramConfig();
    modal.classList.add("active");
}

function closeTelegramModal() {
    const modal = document.getElementById("telegramModal");
    if (modal) modal.classList.remove("active");
    const statusBox = document.getElementById("telegramStatusBox");
    if (statusBox) statusBox.style.display = "none";
}

function toggleTokenVisibility() {
    const input = document.getElementById("telegramTokenInput");
    const btn = document.getElementById("toggleTokenVisibilityBtn");
    if (!input || !btn) return;
    if (input.type === "password") {
        input.type = "text";
        btn.textContent = "🙈";
    } else {
        input.type = "password";
        btn.textContent = "👁️";
    }
}

async function loadTelegramConfig() {
    try {
        const res = await apiFetch("/api/telegram");
        if (!res.ok) return;
        const data = await res.json();

        const enabledToggle = document.getElementById("telegramEnabledToggle");
        const tokenInput = document.getElementById("telegramTokenInput");
        const chatIdInput = document.getElementById("telegramChatIdInput");
        const urlInput = document.getElementById("telegramDashboardUrlInput");

        if (enabledToggle) enabledToggle.checked = !!data.enabled;
        if (tokenInput) tokenInput.value = data.bot_token || "";
        if (chatIdInput) chatIdInput.value = data.chat_id || "";
        if (urlInput) {
            urlInput.value = data.dashboard_url || window.location.origin;
        }

        const tgBtn = document.getElementById("telegramModalBtn");
        if (tgBtn) {
            if (data.enabled && data.is_configured) {
                tgBtn.style.background = "rgba(34, 197, 94, 0.18)";
                tgBtn.style.borderColor = "rgba(34, 197, 94, 0.5)";
                tgBtn.style.color = "#86efac";
                tgBtn.title = "Telegram alerts active! (Click to modify settings)";
            } else {
                tgBtn.style.background = "";
                tgBtn.style.borderColor = "";
                tgBtn.style.color = "";
                tgBtn.title = "Telegram Alerts Configuration (Telegram API Alerts)";
            }
        }
    } catch (e) {
        console.error("Failed to load Telegram configuration:", e);
    }
}

async function testTelegramConnection() {
    const testBtn = document.getElementById("testTelegramBtn");
    const statusBox = document.getElementById("telegramStatusBox");
    const token = (document.getElementById("telegramTokenInput")?.value || "").trim();
    const chatId = (document.getElementById("telegramChatIdInput")?.value || "").trim();

    if (!token || !chatId) {
        if (statusBox) {
            statusBox.style.display = "block";
            statusBox.style.background = "rgba(239, 68, 68, 0.15)";
            statusBox.style.border = "1px solid rgba(239, 68, 68, 0.4)";
            statusBox.style.color = "#fca5a5";
            statusBox.textContent = "⚠️ Please enter Bot Token and Chat ID before testing connection.";
        }
        return;
    }

    if (testBtn) {
        testBtn.disabled = true;
        testBtn.textContent = "Sending test message...";
    }

    if (statusBox) {
        statusBox.style.display = "block";
        statusBox.style.background = "rgba(59, 130, 246, 0.15)";
        statusBox.style.border = "1px solid rgba(59, 130, 246, 0.4)";
        statusBox.style.color = "#93c5fd";
        statusBox.textContent = "⏳ Contacting Telegram API...";
    }

    try {
        const res = await apiFetch("/api/telegram/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bot_token: token, chat_id: chatId }),
        });
        const data = await res.json();

        if (res.ok && data.success) {
            showToast("🧪 " + (data.message || "Test message sent successfully to Telegram!"), "success");
            if (statusBox) {
                statusBox.style.background = "rgba(34, 197, 94, 0.15)";
                statusBox.style.border = "1px solid rgba(34, 197, 94, 0.4)";
                statusBox.style.color = "#86efac";
                statusBox.textContent = "✅ " + (data.message || "Test message sent successfully to Telegram!");
            }
        } else {
            const err = data.error || data.message || "Error testing Telegram";
            showToast("❌ " + err, "error");
            if (statusBox) {
                statusBox.style.background = "rgba(239, 68, 68, 0.15)";
                statusBox.style.border = "1px solid rgba(239, 68, 68, 0.4)";
                statusBox.style.color = "#fca5a5";
                statusBox.textContent = "❌ " + err;
            }
        }
    } catch (err) {
        showToast("❌ Server communication error: " + err, "error");
        if (statusBox) {
            statusBox.style.background = "rgba(239, 68, 68, 0.15)";
            statusBox.style.border = "1px solid rgba(239, 68, 68, 0.4)";
            statusBox.style.color = "#fca5a5";
            statusBox.textContent = "❌ Communication error: " + err;
        }
    } finally {
        if (testBtn) {
            testBtn.disabled = false;
            testBtn.textContent = "🧪 Test Connection (Send Test Message)";
        }
    }
}

async function saveTelegramConfig() {
    const saveBtn = document.getElementById("saveTelegramConfigBtn");
    const enabled = !!document.getElementById("telegramEnabledToggle")?.checked;
    const token = (document.getElementById("telegramTokenInput")?.value || "").trim();
    const chatId = (document.getElementById("telegramChatIdInput")?.value || "").trim();
    const url = (document.getElementById("telegramDashboardUrlInput")?.value || "").trim();

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving...";
    }

    try {
        const res = await apiFetch("/api/telegram", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: enabled,
                bot_token: token,
                chat_id: chatId,
                dashboard_url: url,
            }),
        });
        const data = await res.json();

        if (res.ok && data.success) {
            showToast("✈️ " + (data.message || "Telegram settings saved successfully!"), "success");
            closeTelegramModal();
            await loadTelegramConfig();
        } else {
            showToast("❌ Error saving settings: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Server communication error: " + err, "error");
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Save Settings 💾";
        }
    }
}

// ── Strategy Conditions Modal & Binary Tree Handlers ───────────

let latestConditionsData = null;
let selectedTreeCoin = "ALL";
let selectedTreeMode = "BUY"; // "BUY", "SELL", "RISK"

function initBinaryTreeControls() {
    const coinSelector = document.getElementById("treeCoinSelector");
    if (coinSelector) {
        coinSelector.addEventListener("click", (e) => {
            const btn = e.target.closest(".tree-pill-btn");
            if (!btn) return;
            const coin = btn.getAttribute("data-coin");
            if (coin) {
                selectedTreeCoin = coin;
                coinSelector.querySelectorAll(".tree-pill-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                if (latestConditionsData) renderBinaryTree(latestConditionsData);
            }
        });
    }

    const modeSelector = document.getElementById("treeModeSelector");
    if (modeSelector) {
        modeSelector.addEventListener("click", (e) => {
            const btn = e.target.closest(".tree-pill-btn");
            if (!btn) return;
            const mode = btn.getAttribute("data-mode");
            if (mode) {
                selectedTreeMode = mode;
                modeSelector.querySelectorAll(".tree-pill-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                if (latestConditionsData) renderBinaryTree(latestConditionsData);
            }
        });
    }

    // Global Floating Tooltip attached directly to document.body (prevents parent clipping & transform offset)
    let pipelineTooltipEl = null;
    let tooltipHideTimeout = null;

    function getOrCreatePipelineTooltip() {
        if (!pipelineTooltipEl) {
            pipelineTooltipEl = document.getElementById("pipelineGlobalTooltip");
            if (!pipelineTooltipEl) {
                pipelineTooltipEl = document.createElement("div");
                pipelineTooltipEl.id = "pipelineGlobalTooltip";
                pipelineTooltipEl.className = "pipeline-floating-tooltip";
                document.body.appendChild(pipelineTooltipEl);

                pipelineTooltipEl.addEventListener("mouseenter", () => {
                    if (tooltipHideTimeout) {
                        clearTimeout(tooltipHideTimeout);
                        tooltipHideTimeout = null;
                    }
                });

                pipelineTooltipEl.addEventListener("mouseleave", () => {
                    hidePipelineTooltip();
                });
            }
        }
        return pipelineTooltipEl;
    }

    function showPipelineTooltip(wrapper) {
        if (!wrapper) return;
        const dataEl = wrapper.querySelector(".pipeline-popover-content") || wrapper.querySelector(".pipeline-popover");
        if (!dataEl) return;

        if (tooltipHideTimeout) {
            clearTimeout(tooltipHideTimeout);
            tooltipHideTimeout = null;
        }

        const tooltip = getOrCreatePipelineTooltip();
        tooltip.innerHTML = dataEl.innerHTML;

        const circle = wrapper.querySelector(".pipeline-node-circle") || wrapper;
        const rect = circle.getBoundingClientRect();

        tooltip.style.display = "block";
        tooltip.style.visibility = "hidden";
        tooltip.style.opacity = "0";

        const tooltipWidth = Math.min(285, window.innerWidth - 24);
        tooltip.style.width = `${tooltipWidth}px`;

        const tooltipHeight = tooltip.offsetHeight || 150;

        const circleCenterX = rect.left + (rect.width / 2);
        const minLeft = (tooltipWidth / 2) + 12;
        const maxLeft = window.innerWidth - (tooltipWidth / 2) - 12;
        const left = Math.max(minLeft, Math.min(maxLeft, circleCenterX));

        tooltip.style.left = `${left}px`;
        tooltip.style.transform = "translateX(-50%)";

        // Dynamic arrow position pointing directly to circle center
        const arrowOffset = circleCenterX - (left - tooltipWidth / 2);
        tooltip.style.setProperty("--arrow-left", `${Math.max(16, Math.min(tooltipWidth - 16, arrowOffset))}px`);

        if (rect.top >= tooltipHeight + 16) {
            tooltip.style.top = `${rect.top - tooltipHeight - 12}px`;
            tooltip.className = "pipeline-floating-tooltip popover-top visible";
        } else {
            tooltip.style.top = `${rect.bottom + 12}px`;
            tooltip.className = "pipeline-floating-tooltip popover-bottom visible";
        }

        tooltip.style.visibility = "visible";
        tooltip.style.opacity = "1";
    }

    function hidePipelineTooltip() {
        if (pipelineTooltipEl) {
            pipelineTooltipEl.classList.remove("visible");
            pipelineTooltipEl.style.opacity = "0";
            pipelineTooltipEl.style.visibility = "hidden";
        }
    }

    document.addEventListener("mouseover", (e) => {
        const wrapper = e.target.closest(".pipeline-node-wrapper");
        if (wrapper) {
            showPipelineTooltip(wrapper);
        }
    });

    document.addEventListener("mouseout", (e) => {
        const wrapper = e.target.closest(".pipeline-node-wrapper");
        if (wrapper) {
            const related = e.relatedTarget;
            if (related && (wrapper.contains(related) || (pipelineTooltipEl && pipelineTooltipEl.contains(related)))) {
                return;
            }
            tooltipHideTimeout = setTimeout(() => {
                hidePipelineTooltip();
            }, 80);
        }
    });

    document.addEventListener("click", (e) => {
        const wrapper = e.target.closest(".pipeline-node-wrapper");
        if (wrapper) {
            showPipelineTooltip(wrapper);
        } else if (pipelineTooltipEl && !pipelineTooltipEl.contains(e.target)) {
            hidePipelineTooltip();
        }
    });

    window.addEventListener("scroll", () => {
        hidePipelineTooltip();
    }, { passive: true });
}

window.openDryRunModal = openDryRunModal;
window.closeDryRunModal = closeDryRunModal;
window.openTelegramModal = openTelegramModal;
window.closeTelegramModal = closeTelegramModal;
window.openConditionsModal = openConditionsModal;
window.closeConditionsModal = closeConditionsModal;
window.openKillSwitchModal = openKillSwitchModal;
window.closeKillSwitchModal = closeKillSwitchModal;
window.openTriggerCycleModal = openTriggerCycleModal;
window.openTriggerCycleConfirmModal = openTriggerCycleModal;
window.closeTriggerCycleConfirmModal = closeTriggerCycleConfirmModal;
window.openResetPnlModal = openResetPnlModal;
window.closeResetPnlModal = closeResetPnlModal;
window.openErrorsModal = openErrorsModal;
window.closeErrorsModal = closeErrorsModal;
window.manualRefresh = manualRefresh;
window.toggleUpdater = toggleUpdater;
window.triggerManualPull = triggerManualPull;
window.switchConditionsTab = switchConditionsTab;
window.renderDashboardPipeline = renderDashboardPipeline;
window.renderBinaryTree = renderBinaryTree;

async function openConditionsModal() {
    console.log("[StrategyConditions] Opening modal...");
    const modal = document.getElementById("conditionsModal");
    if (modal) {
        modal.classList.add("active");
        modal.style.display = "flex";
        modal.style.opacity = "1";
        modal.style.pointerEvents = "auto";
        modal.style.zIndex = "99999";
        
        // Render immediate placeholder if needed
        const treeContainer = document.getElementById("binaryTreeContainer");
        if (treeContainer) {
            if (!latestConditionsData) {
                treeContainer.innerHTML = `<div class="cond-loading">טוען עץ תנאים בינארי בלייב... Fetching strategy decision tree...</div>`;
            } else {
                renderBinaryTree(latestConditionsData);
            }
        }
        
        await fetchStrategyConditions();
    } else {
        console.error("[StrategyConditions] Modal element #conditionsModal not found!");
    }
}

function closeConditionsModal() {
    const modal = document.getElementById("conditionsModal");
    if (modal) {
        modal.classList.remove("active");
        modal.style.display = "none";
    }
}

function switchConditionsTab(tabName) {
    const treeBtn = document.getElementById("tabBinaryTreeBtn");
    const liveBtn = document.getElementById("tabLiveConditionsBtn");
    const rulesBtn = document.getElementById("tabStrategyRulesBtn");

    const treeContent = document.getElementById("tabBinaryTreeContent");
    const liveContent = document.getElementById("tabLiveConditionsContent");
    const rulesContent = document.getElementById("tabStrategyRulesContent");

    [treeBtn, liveBtn, rulesBtn].forEach(btn => { if (btn) btn.classList.remove("active"); });
    [treeContent, liveContent, rulesContent].forEach(cnt => { if (cnt) cnt.classList.remove("active"); });

    if (tabName === "tree") {
        if (treeBtn) treeBtn.classList.add("active");
        if (treeContent) treeContent.classList.add("active");
        if (latestConditionsData) renderBinaryTree(latestConditionsData);
    } else if (tabName === "live") {
        if (liveBtn) liveBtn.classList.add("active");
        if (liveContent) liveContent.classList.add("active");
    } else if (tabName === "rules") {
        if (rulesBtn) rulesBtn.classList.add("active");
        if (rulesContent) rulesContent.classList.add("active");
    }
}

async function fetchStrategyConditions() {
    const grid = document.getElementById("assetCondGrid");
    const treeContainer = document.getElementById("binaryTreeContainer");
    try {
        const res = await apiFetch("/api/strategy/conditions");
        if (!res.ok) {
            let errorMsg = `Error loading strategy conditions (Status: ${res.status})`;
            try {
                const errData = await parseJsonResponse(res);
                if (errData && errData.error) errorMsg = errData.error;
            } catch (e) {}
            const errHtml = `<div class="empty-state text-danger">⚠️ ${errorMsg}</div>`;
            if (grid) grid.innerHTML = errHtml;
            if (treeContainer) treeContainer.innerHTML = errHtml;
            return;
        }
        const data = await parseJsonResponse(res);
        latestConditionsData = data;
        renderStrategyConditions(data);
    } catch (err) {
        console.error("Failed to fetch strategy conditions:", err);
        const errHtml = `<div class="empty-state text-danger">Server communication error: ${err.message || err}</div>`;
        if (grid) grid.innerHTML = errHtml;
        if (treeContainer) treeContainer.innerHTML = errHtml;
    }
}

let selectedDashPipelineCoin = "ALL";
let selectedDashPipelineMode = "BUY";

function initDashPipelineControls() {
    const coinSelector = document.getElementById("dashPipelineCoinSelector");
    if (coinSelector) {
        coinSelector.addEventListener("click", (e) => {
            const btn = e.target.closest(".tree-pill-btn");
            if (!btn) return;
            const coin = btn.getAttribute("data-coin");
            if (coin) {
                selectedDashPipelineCoin = coin;
                coinSelector.querySelectorAll(".tree-pill-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                if (latestConditionsData) renderDashboardPipeline(latestConditionsData);
            }
        });
    }

    const modeSelector = document.getElementById("dashPipelineModeSelector");
    if (modeSelector) {
        modeSelector.addEventListener("click", (e) => {
            const btn = e.target.closest(".tree-pill-btn");
            if (!btn) return;
            const mode = btn.getAttribute("data-mode");
            if (mode) {
                selectedDashPipelineMode = mode;
                modeSelector.querySelectorAll(".tree-pill-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                if (latestConditionsData) renderDashboardPipeline(latestConditionsData);
            }
        });
    }
}

function renderBinaryTree(data) {
    const container = document.getElementById("binaryTreeContainer");
    if (!container || !data) return;

    const assets = data.assets || {};
    const macro = data.macro_regime || {};

    let isBuyMode = selectedTreeMode === "BUY";
    let isSellMode = selectedTreeMode === "SELL";
    let isRiskMode = selectedTreeMode === "RISK";

    let coinsToRender = [];
    if (isRiskMode) {
        coinsToRender = ["RISK"];
    } else if (selectedTreeCoin === "ALL") {
        coinsToRender = Object.keys(assets).length > 0 ? Object.keys(assets) : ["BTC", "ETH", "SOL"];
    } else if (assets[selectedTreeCoin]) {
        coinsToRender = [selectedTreeCoin];
    } else {
        coinsToRender = [selectedTreeCoin];
    }

    const hasData = isRiskMode || coinsToRender.some(c => !!assets[c]);
    if (!hasData) {
        container.innerHTML = `<div class="empty-state">⏳ טוען נתוני שוק חיה עבור ${selectedTreeCoin}...</div>`;
        return;
    }

    let html = `<div class="binary-tree-flow">`;

    if (isRiskMode) {
        const isBull = macro.regime === "BULL";
        const pullback = macro.pullback_pct || 0;
        const distFrom5d = macro.dist_from_5d_high_pct !== undefined ? macro.dist_from_5d_high_pct : pullback;
        const underEma = !!macro.under_ema20_daily;
        const btcAtr = macro.btc_atr_pct || 0.0;
        const btcAdx = macro.btc_adx || 20.0;
        const intradayDip = macro.btc_intraday_dip_pct || 0.0;
        const flashLimit = macro.flash_wick_limit_pct || -3.8;
        const flashTriggered = !!macro.flash_circuit_triggered;
        const barsSinceTrip = macro.bars_since_circuit_trip || 999;
        const ladderStep = macro.ladder_step || "Completed (Full 10.0x Unlocked)";
        const ladderCap = macro.ladder_cap || 10.0;
        const inMomentum = macro.in_momentum !== undefined ? !!macro.in_momentum : (!underEma && pullback > -4.0);
        const safeHavenActive = macro.safe_haven_active !== undefined ? !!macro.safe_haven_active : !inMomentum;
        const effectiveLev = macro.effective_leverage || (isBull ? (safeHavenActive ? 1.0 : 2.5) : 0.0);
        const totalExposure = macro.total_crypto_weight_pct || (isBull ? (safeHavenActive ? 40 : 205) : 0);

        const nodes = [
            {
                id: "risk_node_regime",
                title: "1. משטר שוק מקרו (Macro 150-SMA & Trend Gate)",
                subtitle: "בדיקת מחיר סגירה יומי של BTC מול ממוצע 150 ימים ומבנה ממוצעים",
                criteria: `BTC Daily Close > SMA150 ($${(macro.btc_sma150||0).toLocaleString()})`,
                actual: isBull ? `BULL REGIME (BTC $${(macro.btc_close||0).toLocaleString()} > SMA $${(macro.btc_sma150||0).toLocaleString()})` : `BEAR REGIME (BTC $${(macro.btc_close||0).toLocaleString()} < SMA $${(macro.btc_sma150||0).toLocaleString()})`,
                met: isBull,
            },
            {
                id: "risk_node_momentum_gate",
                title: "2. שער אימות מומנטום מוסדי (Momentum Validation Gate)",
                subtitle: "נסיגה משיא 5 ימים עד 2.0%- ומחיר מעל EMA9 יומית (תנאי הכרחי למינוף > 1.0x)",
                criteria: "5d Pullback >= -2.0% AND BTC Close >= EMA9 Daily",
                actual: `נסיגה מ-5d: ${distFrom5d.toFixed(2)}% (סף: -2.0%) | BTC $${(macro.btc_close||0).toLocaleString()} vs EMA9 $${(macro.btc_ema9||0).toLocaleString()} ${inMomentum ? '✓ מומנטום מאומת' : '🛡️ נסיגה -> Safe Haven!'}`,
                met: isBull && inMomentum && !safeHavenActive,
            },
            {
                id: "risk_node_conviction_tier",
                title: "3. מנוע רקטת שכנוע (Conviction Rocket & ATR Tiers)",
                subtitle: "קביעת מינוף: ATR < 2.2% & ADX >= 24 (רקטת 10x) | ATR < 2.8% (5.0x) | בסיס (2.5x)",
                criteria: "ATR < 2.2% & ADX >= 24 -> 10.0x | ATR < 2.8% -> 5.0x | ATR >= 2.8% -> 2.5x",
                actual: `ATR% = ${btcAtr.toFixed(2)}%, ADX = ${btcAdx.toFixed(1)} → ${macro.active_tier || (effectiveLev >= 8 ? '🚀 10x Conviction Rocket' : (effectiveLev >= 4 ? '5.0x Tier' : '2.5x Base Tier'))}`,
                met: isBull && inMomentum && !safeHavenActive,
            },
            {
                id: "risk_node_flash_breaker",
                title: "4. מפסק ביטחון לנרות פלאש (Intraday Flash Circuit Breaker)",
                subtitle: "צניחה תוך-יומית מנר הפתיחה מעבר ל-3.8%- חותכת מיידית ל-1.0x ספוט",
                criteria: `Intraday Dip >= ${flashLimit.toFixed(1)}% (נר בטוח)`,
                actual: `${intradayDip.toFixed(2)}% מהפתיחה ${flashTriggered ? '⚠️ הופעל מפסק ביטחון!' : '✓ תקין ומוגן'}`,
                met: !flashTriggered,
            },
            {
                id: "risk_node_reentry_ladder",
                title: "5. סולם כניסה מחדש ב-4 שלבים (Controlled Re-Entry Ladder)",
                subtitle: "חזרה מדורגת לאחר מפסק: שלב 1 (1.0x) ← שלב 2 (2.0x) ← שלב 3 (4.0x) ← שלב 4 (10.0x)",
                criteria: "שלב 1: 1.0x | שלב 2: 2.0x | שלב 3: 4.0x | שלב 4+: 10.0x מלא",
                actual: `${ladderStep} (תקרה: ${ladderCap.toFixed(1)}x, נרות מאז טריגר: ${barsSinceTrip})`,
                met: barsSinceTrip > 4,
            }
        ];

        html += `
            <div class="tree-root-card">
                <span class="root-badge">🌳 START ROOT NODE</span>
                <div class="root-title">עץ ניהול סיכונים ורקטת שכנוע (INSTITUTIONAL CRASH SHIELD & 10x ROCKET TREE)</div>
                <div class="root-subtitle">שער מומנטום מוסדי (EMA9 & 5d-High), מגן Safe Haven (60% מזומן 4% APY), רקטת 10x, סולם חזרה וגידור שורט 2.0x</div>
            </div>
            <div class="tree-branch-container">
                <div class="tree-branch-line tree-branch-pass"></div>
                <span class="tree-branch-label label-pass">START ⬇️</span>
            </div>
        `;

        let allPassed = true;
        nodes.forEach((node, index) => {
            const isMet = node.met;
            if (!isMet) allPassed = false;

            const nodeClass = isMet ? "tree-node-pass" : "tree-node-fail";
            const badgeClass = isMet ? "badge-pass" : "badge-fail";
            const badgeText = isMet ? "✓ מתקיים (MET)" : "✗ לא מתקיים (UNMET)";
            const icon = isMet ? "🟢" : "🔴";
            const stepNum = index + 1;
            const totalSteps = nodes.length;

            html += `
                <div class="tree-node ${nodeClass}">
                    <div class="tree-node-header">
                        <div style="display: flex; flex-direction: column; gap: 2px;">
                            <span class="tree-node-title">${icon} שלב ${stepNum}/${totalSteps}: ${node.title}</span>
                            ${node.subtitle ? `<span class="tree-node-subtitle" style="font-size: 0.78rem; color: #94a3b8; font-weight: 500;">${node.subtitle}</span>` : ''}
                        </div>
                        <span class="tree-node-status-badge ${badgeClass}">${badgeText}</span>
                    </div>
                    <div class="tree-node-body" style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; background: rgba(0,0,0,0.25); padding: 0.65rem 0.85rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); margin-top: 0.35rem;">
                        <div>
                            <div style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; font-weight: 700;">🎯 תנאי מבוקש (Target Rule)</div>
                            <div class="tree-node-criteria" style="font-size: 0.85rem; color: #e2e8f0; font-weight: 600; margin-top: 2px;">${node.criteria}</div>
                        </div>
                        <div>
                            <div style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; font-weight: 700;">📊 נתון בלייב (Live Data)</div>
                            <div class="tree-node-actual" style="font-size: 0.85rem; margin-top: 2px;">${node.actual}</div>
                        </div>
                    </div>
                </div>
            `;

            if (index < nodes.length - 1) {
                const branchClass = isMet ? "tree-branch-pass" : "tree-branch-fail";
                const labelClass = isMet ? "label-pass" : "label-fail";
                const labelText = isMet ? "YES 🟢 (המשך לשלב הבא)" : "GUARD ⚠️ (מגבלה / מגן פעיל)";
                html += `
                    <div class="tree-branch-container">
                        <div class="tree-branch-line ${branchClass}"></div>
                        <span class="tree-branch-label ${labelClass}">${labelText}</span>
                    </div>
                `;
            }
        });

        const branchToLeafClass = allPassed ? "tree-branch-pass" : "tree-branch-fail";
        const branchToLeafLabel = allPassed ? "YES 🟢 (מינוף רקטה מירבי)" : "CALCULATED ⚡ (הגנה מופעלת)";
        html += `
            <div class="tree-branch-container">
                <div class="tree-branch-line ${branchToLeafClass}"></div>
                <span class="tree-branch-label ${allPassed ? 'label-pass' : 'label-fail'}">${branchToLeafLabel}</span>
            </div>
        `;

        if (macro.regime === "BEAR") {
            html += `
                <div class="tree-leaf-outcome outcome-sell-triggered">
                    <div class="outcome-title">🐻 משטר דובים פעיל (BEAR REGIME — 35% SHORT @ 2.0x + 65% CASH YIELD 4%)</div>
                    <div class="outcome-desc">ביטקוין מתחת לממוצעים: סגירת כל פוזיציות הלונג + פתיחת 35% שורט במינוף 2.0x על BTC (חשיפה -70%), לצד 65% מזומן דולרי המניב ריבית 4% APY.</div>
                </div>
            `;
        } else if (safeHavenActive) {
            html += `
                <div class="tree-leaf-outcome outcome-buy-waiting">
                    <div class="outcome-title">🛡️ מגן SAFE HAVEN פעיל (CRASH SHIELD: 1.0x SPOT — 60% CASH YIELD @ 4% APY)</div>
                    <div class="outcome-desc">נסיגה מעל 2.0%- משיא 5 ימים או שבירת EMA9 יומית: המינוף קוצץ מיידית ל-1.0x ספוט ללא חוב מימוני, 60% מההון הועבר למזומן תשואה, ופירמידינג ננעל להגנה מושלמת על ההון!</div>
                </div>
            `;
        } else if (flashTriggered) {
            html += `
                <div class="tree-leaf-outcome outcome-buy-waiting">
                    <div class="outcome-title">⚠️ מפסק ביטחון לנר פלאש פעיל (FLASH CIRCUIT BREAKER: 1.0x SPOT)</div>
                    <div class="outcome-desc">צניחה תוך-יומית חדה מנר הפתיחה. המינוף נחתך מיידית ל-1.0x לספיגת הירידה בהון עצמי נקי, ונכנס לתוקף סולם חזרה מדורג ב-4 שלבים.</div>
                </div>
            `;
        } else if (effectiveLev >= 8.0) {
            html += `
                <div class="tree-leaf-outcome outcome-buy-success">
                    <div class="outcome-title">🚀 רקטת שכנוע 10x פעילה (MAX CONVICTION ROCKET: ${effectiveLev.toFixed(1)}x — ${totalExposure}% EXPOSURE)</div>
                    <div class="outcome-desc">מומנטום חי מאושר (מעל EMA9 ובטווח 2% משיא 5 ימים), תנודתיות נמוכה (ATR ${btcAtr.toFixed(2)}%) ו-ADX מובהק (${btcAdx.toFixed(1)}). המערכת מזנקת למינוף מירבי פי 10 למקסום רווח שיא!</div>
                </div>
            `;
        } else {
            html += `
                <div class="tree-leaf-outcome outcome-buy-success">
                    <div class="outcome-title">⚡ מינוף שוורי מדורג פעיל (BULL LEVERAGE: ${effectiveLev.toFixed(1)}x — ${totalExposure}% EXPOSURE)</div>
                    <div class="outcome-desc">${macro.active_tier || `שוק שוורי במומנטום. מינוף דינמי של ${effectiveLev.toFixed(1)}x המעניק חשיפת תיק של ${totalExposure}% עם הגנות דרוכות.`}</div>
                </div>
            `;
        }
    } else {
        coinsToRender.forEach((coin, coinIdx) => {
            const coinData = assets[coin];
            if (!coinData) return;

            let nodes = [];
            let treeTitle = "";
            let treeSub = "";

            if (isBuyMode) {
                treeTitle = `עץ תנאי כניסה (BUY TREE) — ${coin}`;
                treeSub = `בדיקה בינארית מדורגת של תנאי הסף לקנייה ופתיחת פוזיציה ב-${coin}`;
                nodes = coinData.buy_tree_nodes || [];
            } else if (isSellMode) {
                treeTitle = `עץ תנאי יציאה ומכירה (SELL TREE) — ${coin}`;
                treeSub = `בדיקה בינארית של טריגרים ליציאה, סטופ-לוס וקטיעת הפסד/רווח ב-${coin}`;
                nodes = coinData.sell_tree_nodes || [];
            }

            if (coinIdx > 0) {
                html += `<div style="width: 100%; height: 2px; background: rgba(255,255,255,0.08); margin: 2rem 0;"></div>`;
            }

            html += `
                <div class="tree-root-card">
                    <span class="root-badge">🌳 START ROOT NODE — ${coin}</span>
                    <div class="root-title">${treeTitle}</div>
                    <div class="root-subtitle">${treeSub}</div>
                </div>
                <div class="tree-branch-container">
                    <div class="tree-branch-line tree-branch-pass"></div>
                    <span class="tree-branch-label label-pass">START ⬇️</span>
                </div>
            `;

            let allPassed = true;
            nodes.forEach((node, index) => {
                const isMet = isBuyMode ? !!node.met : !!node.triggered;
                if (!isMet) allPassed = false;

                const nodeClass = isMet ? "tree-node-pass" : "tree-node-fail";
                const badgeClass = isMet ? "badge-pass" : "badge-fail";
                const badgeText = isMet ? "✓ מתקיים (MET)" : "✗ לא מתקיים (UNMET)";
                const icon = isMet ? "🟢" : "🔴";
                const stepNum = index + 1;
                const totalSteps = nodes.length;

                html += `
                    <div class="tree-node ${nodeClass}">
                        <div class="tree-node-header">
                            <div style="display: flex; flex-direction: column; gap: 2px;">
                                <span class="tree-node-title">${icon} שלב ${stepNum}/${totalSteps}: ${node.title}</span>
                                ${node.subtitle ? `<span class="tree-node-subtitle" style="font-size: 0.78rem; color: #94a3b8; font-weight: 500;">${node.subtitle}</span>` : ''}
                            </div>
                            <span class="tree-node-status-badge ${badgeClass}">${badgeText}</span>
                        </div>
                        <div class="tree-node-body" style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; background: rgba(0,0,0,0.25); padding: 0.65rem 0.85rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); margin-top: 0.35rem;">
                            <div>
                                <div style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; font-weight: 700;">🎯 תנאי מבוקש (Target Rule)</div>
                                <div class="tree-node-criteria" style="font-size: 0.85rem; color: #e2e8f0; font-weight: 600; margin-top: 2px;">${node.criteria}</div>
                            </div>
                            <div>
                                <div style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; font-weight: 700;">📊 נתון בלייב (Live Data)</div>
                                <div class="tree-node-actual" style="font-size: 0.85rem; margin-top: 2px;">${node.actual}</div>
                            </div>
                        </div>
                    </div>
                `;

                if (index < nodes.length - 1) {
                    const branchClass = isMet ? "tree-branch-pass" : "tree-branch-fail";
                    const labelClass = isMet ? "label-pass" : "label-fail";
                    const labelText = isMet ? "YES 🟢 (המשך לשלב הבא)" : "NO 🔴 (חסום בשלב זה)";
                    html += `
                        <div class="tree-branch-container">
                            <div class="tree-branch-line ${branchClass}"></div>
                            <span class="tree-branch-label ${labelClass}">${labelText}</span>
                        </div>
                    `;
                }
            });

            const branchToLeafClass = allPassed ? "tree-branch-pass" : "tree-branch-fail";
            const branchToLeafLabel = allPassed ? "YES 🟢 (סיום בהצלחה)" : "NO 🔴 (תוצאה סופית)";
            html += `
                <div class="tree-branch-container">
                    <div class="tree-branch-line ${branchToLeafClass}"></div>
                    <span class="tree-branch-label ${allPassed ? 'label-pass' : 'label-fail'}">${branchToLeafLabel}</span>
                </div>
            `;

            if (isBuyMode) {
                const isPosActive = coinData && coinData.position && coinData.position.active;
                if (isPosActive) {
                    html += `
                        <div class="tree-leaf-outcome outcome-buy-success">
                            <div class="outcome-title">✅ פוזיציה פתוחה ופעילה (ACTIVE POSITION)</div>
                            <div class="outcome-desc">הבוט מחזיק פוזיציה ב-${coin}. תנאי הכניסה התקיימו ומנוהלים ע"י סטופ נגרר דינמי.</div>
                        </div>
                    `;
                } else if (allPassed) {
                    html += `
                        <div class="tree-leaf-outcome outcome-buy-success">
                            <div class="outcome-title">🚀 אות קנייה פעיל! (BUY SIGNAL TRIGGERED)</div>
                            <div class="outcome-desc">כל התנאים הבינאריים מתקיימים במלואם! הבוט מורשה לפתוח פוזיציה ב-${coin}.</div>
                        </div>
                    `;
                } else {
                    html += `
                        <div class="tree-leaf-outcome outcome-buy-waiting">
                            <div class="outcome-title">⏳ ממתין להתקיימות תנאים (WAITING FOR ENTRY)</div>
                            <div class="outcome-desc">לא כל תנאי הקנייה מתקיימים. פתיחת פוזיציה ב-${coin} כרגע חסומה להגנה על ההון.</div>
                        </div>
                    `;
                }
            } else if (isSellMode) {
                const sellTriggered = nodes.some(n => n.triggered);
                if (sellTriggered) {
                    html += `
                        <div class="tree-leaf-outcome outcome-sell-triggered">
                            <div class="outcome-title">🚨 טריגר מכירה ויציאה הופעל! (EXIT TRIGGERED)</div>
                            <div class="outcome-desc">טריגר יציאה הופעל ב-${coin}! הבוט יבצע סגירה/מכירה מיידית בנכס.</div>
                        </div>
                    `;
                } else {
                    html += `
                        <div class="tree-leaf-outcome outcome-sell-safe">
                            <div class="outcome-title">🛡️ פוזיציה בטוחה / אין טריגר מכירה (POSITION SAFE)</div>
                            <div class="outcome-desc">אף תנאי מכירה לא הופעל ב-${coin}. הנכס נשאר מוחזק בבטחה.</div>
                        </div>
                    `;
                }
            }
        });
    }

    html += `</div>`;
    container.innerHTML = html;
}

function evaluateLadderCircuit(nodes) {
    let isCircuitLive = true;
    let firstBlocker = null;
    const evaluated = nodes.map(node => {
        const isMet = !!node.met;
        let circuitState = "";
        if (isCircuitLive && isMet) {
            circuitState = "energized";
        } else if (isCircuitLive && !isMet) {
            circuitState = "blocking";
            isCircuitLive = false;
            firstBlocker = node;
        } else {
            circuitState = "dormant";
        }
        return { ...node, isMet, circuitState };
    });
    const allPass = evaluated.every(n => n.isMet);
    return { evaluated, allPass, firstBlocker };
}

function renderLadderTrackHtml(evaluatedNodes, allPass, firstBlocker, isPosActive, outcomeMeta) {
    let tHtml = `<div class="pipeline-track ladder-circuit">`;
    tHtml += `
        <div class="ladder-power-rail energized" title="מתח פיקוד ראשי (Power Rail)">
            <span class="rail-tag">⚡ LADDER POWER</span>
        </div>
    `;
    const firstState = evaluatedNodes.length > 0 ? evaluatedNodes[0].circuitState : 'dormant';
    const firstConnClass = (firstState === 'energized' || firstState === 'blocking') ? 'energized' : 'dormant';
    tHtml += `<div class="pipeline-connector ${firstConnClass}"></div>`;

    evaluatedNodes.forEach((node, idx) => {
        let circleClass = "";
        let circleIcon = "";
        let statusClass = "";
        let statusBadgeText = "";

        if (node.circuitState === "energized") {
            circleClass = "pass energized";
            circleIcon = "✓";
            statusClass = "badge-pass";
            statusBadgeText = node.badge || "✓ מאושר";
        } else if (node.circuitState === "blocking") {
            circleClass = "fail blocking";
            circleIcon = "🛑";
            statusClass = "badge-blocking";
            statusBadgeText = node.badge || "🛑 חסם נוכחי";
        } else {
            circleClass = "dormant";
            circleIcon = node.isMet ? "✓" : "✗";
            statusClass = "badge-dormant";
            statusBadgeText = "(ממתין לזרימה)";
        }

        const liveValText = node.live_val || node.actual || "";

        // Progress Bar & Proximity Distance Metric
        let progPct = 100;
        let progLabel = "";
        if (node.progress_pct !== undefined && node.progress_pct !== null) {
            progPct = Math.max(0, Math.min(100, Number(node.progress_pct)));
            progLabel = node.progress_label || `${progPct.toFixed(0)}%`;
        } else if (node.circuitState === "energized" || node.met === true || node.isMet === true) {
            progPct = 100;
            progLabel = node.progress_label || "✓ 100% מושג";
        } else {
            progPct = 0;
            progLabel = node.progress_label || "0% לא מתקיים";
        }

        let progBarClass = "pass";
        let progTextClass = "prog-pass";

        if (node.circuitState === "blocking") {
            progBarClass = "blocking";
            progTextClass = "prog-blocking";
        } else if (node.circuitState === "dormant") {
            if (node.isMet) {
                progBarClass = "dormant-pass";
                progTextClass = "prog-dormant-pass";
            } else {
                progBarClass = "dormant-fail";
                progTextClass = "prog-dormant-fail";
            }
        } else {
            progBarClass = "energized";
            progTextClass = "prog-energized";
        }

        tHtml += `
            <div class="pipeline-node-wrapper" data-node-id="${node.id}">
                <div class="pipeline-node-circle ${circleClass}">${circleIcon}</div>
                <span class="pipeline-node-label">${node.shortTitle || node.title}</span>
                ${liveValText ? `<span class="pipeline-node-live-val">${liveValText}</span>` : ''}
                <span class="pipeline-node-badge ${statusClass}">${statusBadgeText}</span>
                
                <div class="pipeline-progress-box" title="${progLabel}">
                    <div class="pipeline-progress-track">
                        <div class="pipeline-progress-fill ${progBarClass}" style="width: ${progPct}%;"></div>
                    </div>
                    <span class="pipeline-progress-text ${progTextClass}">${progLabel}</span>
                </div>

                <div class="pipeline-popover-content" style="display:none;">
                    <div class="popover-header">
                        <span class="popover-title">${node.fullTitle || node.title}</span>
                        <span class="popover-badge ${node.isMet ? 'badge-pass' : 'badge-fail'}">${node.isMet ? '✓ מתקיים' : '✗ לא מתקיים'}</span>
                    </div>
                    <div class="popover-grid">
                        <div><span class="popover-item-label">🎯 תנאי מבוקש:</span> <span class="popover-item-val">${node.criteria}</span></div>
                        <div><span class="popover-item-label">📊 נתון בלייב:</span> <span class="popover-item-val">${node.actual}</span></div>
                        <div><span class="popover-item-label">📈 מרחק / התקדמות:</span> <span class="popover-item-val">${progLabel} (${progPct.toFixed(0)}%)</span></div>
                    </div>
                    <div class="popover-explanation">💡 ${node.explanation || ''}</div>
                </div>
            </div>
        `;

        if (idx < evaluatedNodes.length - 1) {
            const nextNode = evaluatedNodes[idx + 1];
            let connClass = "dormant";
            if (node.circuitState === "energized") {
                if (nextNode.circuitState === "energized") {
                    connClass = "energized";
                } else if (nextNode.circuitState === "blocking") {
                    connClass = "blocking-lead";
                }
            }
            tHtml += `<div class="pipeline-connector ${connClass}"></div>`;
        }
    });

    const finalConnClass = (allPass || isPosActive) ? "energized" : "dormant";
    tHtml += `<div class="pipeline-connector ${finalConnClass}"></div>`;

    // Output Coil & Overall Circuit Progress
    const passedCount = evaluatedNodes.filter(n => n.circuitState === 'energized').length;
    const totalCount = evaluatedNodes.length;
    const circuitProgressPct = (allPass || isPosActive) ? 100 : Math.round((passedCount / totalCount) * 100);
    const coilProgLabel = (allPass || isPosActive) ? '✓ 100% מעגל מושלם' : `${passedCount}/${totalCount} שערים (${circuitProgressPct}%)`;
    const coilProgClass = (allPass || isPosActive) ? 'energized' : 'blocking';
    const coilTextClass = (allPass || isPosActive) ? 'prog-energized' : 'prog-blocking';

    tHtml += `
        <div class="pipeline-node-wrapper ladder-output-coil">
            <div class="pipeline-node-circle ${outcomeMeta.circleClass}">${outcomeMeta.icon}</div>
            <span class="pipeline-node-label">${outcomeMeta.title}</span>
            <span class="pipeline-node-live-val">${outcomeMeta.liveVal}</span>
            <span class="pipeline-node-badge ${outcomeMeta.badgeClass}">${outcomeMeta.badgeText}</span>
            <div class="pipeline-progress-box" title="${coilProgLabel}">
                <div class="pipeline-progress-track">
                    <div class="pipeline-progress-fill ${coilProgClass}" style="width: ${circuitProgressPct}%;"></div>
                </div>
                <span class="pipeline-progress-text ${coilTextClass}">${coilProgLabel}</span>
            </div>
        </div>
    `;

    tHtml += `</div>`;
    return tHtml;
}

function renderDashboardPipeline(data) {
    const container = document.getElementById("dashPipelineContainer");
    if (!container || !data) return;

    const assets = data.assets || {};
    const macro = data.macro_regime || {};

    let coinsToRender = [];
    if (selectedDashPipelineCoin === "ALL") {
        coinsToRender = ["BTC", "ETH", "SOL"];
    } else if (assets[selectedDashPipelineCoin]) {
        coinsToRender = [selectedDashPipelineCoin];
    } else {
        coinsToRender = ["BTC", "ETH", "SOL"];
    }

    let modesToRender = [];
    if (selectedDashPipelineMode === "BUY") {
        modesToRender = ["BUY"];
    } else if (selectedDashPipelineMode === "SELL") {
        modesToRender = ["SELL"];
    } else if (selectedDashPipelineMode === "RISK") {
        modesToRender = ["RISK"];
    } else {
        modesToRender = ["BUY", "SELL"];
    }

    let html = `<div class="pipeline-stepper-container">`;

    // 1. RISK PIPELINE LADDER
    if (modesToRender.includes("RISK")) {
        const isBull = macro.regime === "BULL";
        const inMomentum = !!macro.in_momentum;
        const safeHavenActive = !!macro.safe_haven_active;
        const dist5d = macro.dist_from_5d_high_pct || 0;
        const ema9 = macro.btc_ema9_daily || 0;
        const btcClose = macro.btc_close || 0;
        const btcAtr = macro.btc_atr_pct || 0.0;
        const btcAdx = macro.btc_adx_daily || 20.0;
        const intradayDip = macro.btc_intraday_dip_pct || 0.0;
        const flashLimit = macro.flash_wick_limit_pct || -3.8;
        const flashTriggered = !!macro.flash_circuit_triggered;
        const barsSinceTrip = macro.bars_since_circuit_trip || 999;
        const ladderStep = macro.ladder_step || "Completed";
        const ladderCap = macro.ladder_cap || 10.0;
        const effectiveLev = macro.effective_leverage || (isBull ? 2.5 : 0.0);
        const totalExposure = macro.total_crypto_weight_pct || (isBull ? 205 : 0);

        const btcDistSma = macro.btc_sma150 ? ((btcClose - macro.btc_sma150) / macro.btc_sma150) * 100 : 0;
        const r1_prog = isBull ? 100 : Math.max(0, Math.min(99, Math.round(100 + btcDistSma)));
        const r1_label = isBull ? `+${btcDistSma.toFixed(1)}% מעל הסף` : `חסר ${Math.abs(btcDistSma).toFixed(1)}% ל-SMA`;

        const pbGap = dist5d - (-2.0);
        const r2_prog = inMomentum ? 100 : Math.max(15, Math.min(95, Math.round(100 + pbGap * 15)));
        const r2_label = inMomentum ? "✓ 100% מומנטום" : `חריגה ${Math.abs(pbGap).toFixed(1)}% משיא 5d`;

        const r3_prog = effectiveLev >= 10.0 ? 100 : (effectiveLev >= 5.0 ? 75 : (effectiveLev >= 2.5 ? 50 : 25));
        const r3_label = `מינוף ${effectiveLev.toFixed(1)}x פעיל`;

        const flashMargin = (Math.abs(flashLimit) - Math.abs(intradayDip));
        const r4_prog = !flashTriggered ? 100 : 0;
        const r4_label = !flashTriggered ? `מרווח: ${flashMargin.toFixed(1)}%` : `צניחה ${Math.abs(intradayDip).toFixed(1)}%`;

        const r5_prog = barsSinceTrip > 4 ? 100 : Math.round((barsSinceTrip / 4) * 100);
        const r5_label = barsSinceTrip > 4 ? "✓ 100% מושלם" : `שלב ${barsSinceTrip}/4 (${r5_prog}%)`;

        const r6_prog = 100;
        const r6_label = safeHavenActive ? "🛡️ 60% מזומן" : "🚀 100% מושקע";

        const r7_prog = isBull ? 100 : 0;
        const r7_label = isBull ? "✓ מוגן (רדום)" : "🐻 שורט פעיל";

        const riskNodes = [
            {
                id: "dash_risk_1",
                shortTitle: "1. משטר מקרו",
                fullTitle: "1. משטר שוק מקרו (Macro 150-SMA)",
                criteria: `BTC Close > SMA150 ($${(macro.btc_sma150||0).toLocaleString()})`,
                actual: isBull ? `BULL REGIME ($${(macro.btc_close||0).toLocaleString()} > $${(macro.btc_sma150||0).toLocaleString()})` : `BEAR REGIME ($${(macro.btc_close||0).toLocaleString()} < $${(macro.btc_sma150||0).toLocaleString()})`,
                live_val: `$${(macro.btc_close||0).toLocaleString()}`,
                badge: isBull ? "✓ שוורי" : "🐻 דובים",
                progress_pct: r1_prog,
                progress_label: r1_label,
                met: isBull,
                explanation: "אימות מגמת עלייה שורית ראשית בביטקוין מעל ממוצע 150 ימים. בלעדיו מושבתים כל הלונגים."
            },
            {
                id: "dash_risk_2",
                shortTitle: "2. מגן מפולת",
                fullTitle: "2. שער מומנטום ומגן מפולת (Crash Shield Gate)",
                criteria: `5d Pullback >= -2.0% & Close >= EMA9 ($${(ema9||0).toLocaleString()})`,
                actual: `5d PB: ${dist5d.toFixed(2)}% | EMA9: $${(ema9||0).toLocaleString()} (${inMomentum ? '🚀 מומנטום' : '🛡️ Safe Haven'})`,
                live_val: `5d: ${dist5d.toFixed(1)}%`,
                badge: inMomentum ? "🚀 מומנטום" : "🛡️ Safe Haven",
                progress_pct: r2_prog,
                progress_label: r2_label,
                met: inMomentum,
                explanation: "שער הכניסה למינוף מוגבר: נסיגה מעל 2%- משיא 5 ימים מפעילה מיד Safe Haven והורדה ל-1.0x ספוט."
            },
            {
                id: "dash_risk_3",
                shortTitle: "3. מנוע מינוף",
                fullTitle: "3. מנוע מינוף דינמי (Dynamic Conviction Engine)",
                criteria: "ATR < 2.2% & ADX >= 24 (10x) | ATR < 2.8% (5x) | Base (2.5x)",
                actual: `ATR% = ${btcAtr.toFixed(2)}%, ADX = ${btcAdx.toFixed(1)} → ${macro.active_tier || `${effectiveLev.toFixed(1)}x Tier`}`,
                live_val: `${effectiveLev.toFixed(1)}x Tier`,
                badge: `${effectiveLev.toFixed(1)}x`,
                progress_pct: r3_prog,
                progress_label: r3_label,
                met: isBull && inMomentum && effectiveLev >= 2.5,
                explanation: "התאמת מינוף אגרסיבי במצבי וודאות מוחלטת: עד 10x ברגיעה ומומנטום, ו-5.0x / 2.5x בתנודתיות."
            },
            {
                id: "dash_risk_4",
                shortTitle: "4. מפסק פלאש",
                fullTitle: "4. מפסק ביטחון לנרות פלאש (Flash Circuit Breaker)",
                criteria: `Intraday Dip >= ${flashLimit.toFixed(1)}%`,
                actual: `${intradayDip.toFixed(2)}% ${flashTriggered ? '⚠️ הופעל!' : '✓ תקין'}`,
                live_val: `${intradayDip.toFixed(1)}%`,
                badge: flashTriggered ? "⚡ הופעל!" : "✓ תקין",
                progress_pct: r4_prog,
                progress_label: r4_label,
                met: !flashTriggered,
                explanation: "מפסק הגנה תוך-יומי החותך מיידית את המינוף ל-1.0x אם נר יומי צונח מעל 3.8%- מפתיחה."
            },
            {
                id: "dash_risk_5",
                shortTitle: "5. סולם חזרה",
                fullTitle: "5. סולם כניסה מחדש מדורג (4-Step Re-Entry Ladder)",
                criteria: "התאוששות 4 שלבים: 1.0x → 2.0x → 4.0x → 10.0x",
                actual: `${ladderStep} (תקרה ${ladderCap.toFixed(1)}x)`,
                live_val: ladderStep,
                badge: `תקרה ${ladderCap.toFixed(0)}x`,
                progress_pct: r5_prog,
                progress_label: r5_label,
                met: barsSinceTrip > 4,
                explanation: "מניעת מלכודות שוורים: חזרה הדרגתית ומבוקרת למינוף מלא ב-4 שלבים מדודים."
            },
            {
                id: "dash_risk_6",
                shortTitle: "6. עוגן מזומן",
                fullTitle: "6. עוגן מזומן בתשואה חסרת סיכון (Safe Haven Cash Yield)",
                criteria: "60% Cash @ 4.0% APY במצב הגנה | 100% מושקע במומנטום מלא",
                actual: safeHavenActive ? "🛡️ 60% Cash Buffer (4% APY) + 30% Spot" : "🚀 100% Capital Deployed",
                live_val: safeHavenActive ? "60% Cash @ 4%" : "100% מושקע",
                badge: safeHavenActive ? "4% APY" : "🚀 מומנטום",
                progress_pct: r6_prog,
                progress_label: r6_label,
                met: true,
                explanation: "הקצאת 60% מההון למזומן בריבית 4% כשהשוק בתיקון, לשמירה מוחלטת על הקרן."
            },
            {
                id: "dash_risk_7",
                shortTitle: "7. גידור שורט",
                fullTitle: "7. גידור שורט שיטתי בשוק דובי (Systematic Bear Short Hedge)",
                criteria: "BTC < SMA150 -> 35% Margin @ 2.0x Short BTC (70% Net Short)",
                actual: isBull ? "✓ משטר שוורים (שורט רדום)" : "🐻 שורט פעיל: 35% ממונף 2.0x",
                live_val: isBull ? "לונגים מאושרים" : "-70% Net Short",
                badge: isBull ? "✓ תקין" : "🐻 שורט פעיל",
                progress_pct: r7_prog,
                progress_label: r7_label,
                met: isBull,
                explanation: "הגנה אקטיבית: הקצאת 35% מההון לשורט ממונף 2.0x על ביטקוין כאשר השוק עובר למשטר דובים."
            }
        ];

        const { evaluated, allPass, firstBlocker } = evaluateLadderCircuit(riskNodes);

        let outcomeText = "";
        let outcomeClass = "";
        let outcomeIcon = "";

        if (macro.regime === "BEAR") {
            outcomeText = "🐻 BEAR (35% SHORT HEDGE @ 2.0x)";
            outcomeClass = "sell-triggered";
            outcomeIcon = "🐻";
        } else if (safeHavenActive) {
            outcomeText = `🛡️ SAFE HAVEN SHIELD (1.0x SPOT | 60% CASH @ 4% APY)`;
            outcomeClass = "blocking";
            outcomeIcon = "🛡️";
        } else if (flashTriggered) {
            outcomeText = `⚡ FLASH BREAKER (1.0x SPOT)`;
            outcomeClass = "blocking";
            outcomeIcon = "⚡";
        } else if (effectiveLev >= 10.0) {
            outcomeText = `🚀 CONVICTION ROCKET (10.0x | ${totalExposure}%)`;
            outcomeClass = "pass";
            outcomeIcon = "🚀";
        } else {
            outcomeText = `⚡ BULL LEVERAGE (${effectiveLev.toFixed(1)}x | ${totalExposure}%)`;
            outcomeClass = "pass";
            outcomeIcon = "⚡";
        }

        const riskOutcomeMeta = {
            circleClass: allPass ? "pass energized" : "blocking",
            icon: outcomeIcon,
            title: "מינוף אפקטיבי",
            liveVal: `${effectiveLev.toFixed(1)}x (${totalExposure}%)`,
            badgeClass: allPass ? "badge-pass" : "badge-blocking",
            badgeText: outcomeText.split('(')[0].trim()
        };

        html += `
            <div class="pipeline-card">
                <div class="pipeline-card-header">
                    <div class="pipeline-coin-info">
                        <span class="pipeline-coin-badge">🛡️ INSTITUTIONAL CRASH SHIELD & 10x ENGINE</span>
                        <span class="pipeline-type-tag risk">LADDER סיכונים ומקרו</span>
                    </div>
                    <span class="pipeline-outcome-pill ${outcomeClass}">${outcomeIcon} ${outcomeText}</span>
                </div>
                ${renderLadderTrackHtml(evaluated, allPass, firstBlocker, false, riskOutcomeMeta)}
            </div>
        `;
    }

    // 2. COIN PIPELINES (BUY / SELL LADDERS)
    coinsToRender.forEach(coin => {
        const coinData = assets[coin];
        if (!coinData) return;

        const iconMap = { BTC: "₿", ETH: "⟠", SOL: "◎" };
        const coinIcon = iconMap[coin] || "🪙";
        const isPosActive = !!(coinData.position && coinData.position.active);
        const posInfo = coinData.position || {};

        // BUY PIPELINE LADDER
        if (modesToRender.includes("BUY")) {
            const rawNodes = coinData.buy_tree_nodes || [];
            const { evaluated, allPass, firstBlocker } = evaluateLadderCircuit(rawNodes);

            let outcomeTitle = "";
            let outcomePillClass = "";
            let outcomeIcon = "";

            if (isPosActive) {
                const rVal = posInfo.open_r !== undefined ? `${posInfo.open_r > 0 ? '+' : ''}${posInfo.open_r}R` : 'פעילה';
                outcomeTitle = `✅ POS ACTIVE (${rVal})`;
                outcomePillClass = "pass";
                outcomeIcon = "✅";
            } else if (allPass) {
                outcomeTitle = "🚀 BUY SIGNAL — אות קנייה מופעל (LADDER סגור)!";
                outcomePillClass = "pass";
                outcomeIcon = "🚀";
            } else {
                const blockerName = firstBlocker ? firstBlocker.shortTitle : "תנאי סיכון";
                outcomeTitle = `🛑 חסום: ${blockerName} (מעגל פתוח)`;
                outcomePillClass = "blocking";
                outcomeIcon = "🛑";
            }

            let outcomeMeta = null;
            if (isPosActive) {
                outcomeMeta = {
                    circleClass: "pass energized",
                    icon: "✅",
                    title: "סטטוס פוזיציה",
                    liveVal: posInfo.open_r !== undefined ? `${posInfo.open_r > 0 ? '+' : ''}${posInfo.open_r}R` : "Active",
                    badgeClass: "badge-pass",
                    badgeText: "🛡️ פוזיציה מוגנת ומנוהלת"
                };
            } else if (allPass) {
                outcomeMeta = {
                    circleClass: "pass energized",
                    icon: "🚀",
                    title: "תוצאת קנייה",
                    liveVal: "100% מעגל סגור",
                    badgeClass: "badge-pass",
                    badgeText: "🚀 פקודת קנייה מוכנה!"
                };
            } else {
                const blockerShort = firstBlocker ? (firstBlocker.shortTitle || '').split('.')[0] : '';
                outcomeMeta = {
                    circleClass: "fail blocking",
                    icon: "🛑",
                    title: "תוצאת קנייה",
                    liveVal: firstBlocker ? `חסום בשער ${blockerShort}` : "מעגל פתוח",
                    badgeClass: "badge-blocking",
                    badgeText: firstBlocker ? `🛑 חסם: ${firstBlocker.shortTitle}` : "🛑 חסום לקנייה"
                };
            }

            const ladderModeLabel = isPosActive ? "LADDER ניהול פוזיציה (IN-TRADE)" : "LADDER כניסה (ENTRY PIPELINE)";

            html += `
                <div class="pipeline-card">
                    <div class="pipeline-card-header">
                        <div class="pipeline-coin-info">
                            <span class="pipeline-coin-badge">${coinIcon} ${coin}/USDT</span>
                            <span class="pipeline-type-tag buy">${ladderModeLabel}</span>
                        </div>
                        <span class="pipeline-outcome-pill ${outcomePillClass}">${outcomeIcon} ${outcomeTitle}</span>
                    </div>
                    ${renderLadderTrackHtml(evaluated, allPass, firstBlocker, isPosActive, outcomeMeta)}
                </div>
            `;
        }

        // SELL PIPELINE LADDER (Safety Exits & Short Protection)
        if (modesToRender.includes("SELL")) {
            const rawSellNodes = coinData.sell_tree_nodes || [];
            
            // In SELL pipeline, each node is an emergency safety breaker
            const evaluatedSell = rawSellNodes.map(node => {
                const isTriggered = !!node.triggered;
                return {
                    ...node,
                    met: !isTriggered, // Safe when NOT triggered
                    circuitState: isTriggered ? "blocking" : "energized"
                };
            });

            const anySellTrip = rawSellNodes.some(n => n.triggered);
            const triggeredNode = rawSellNodes.find(n => n.triggered);

            let outcomeTitle = "";
            let outcomePillClass = "";
            let outcomeIcon = "";

            if (anySellTrip) {
                outcomeTitle = `🚨 EXIT TRIGGERED: ${triggeredNode ? triggeredNode.shortTitle : 'פקודת יציאה מופעלת'}`;
                outcomePillClass = "sell-triggered";
                outcomeIcon = "🚨";
            } else if (isPosActive) {
                outcomeTitle = "🛡️ POSITION SAFE — כל מנגנוני הבטיחות תקינים";
                outcomePillClass = "pass";
                outcomeIcon = "🛡️";
            } else {
                outcomeTitle = "ℹ️ אין פוזיציה פתוחה בנכס (מעקב שורט מקרו בלבד)";
                outcomePillClass = "pass";
                outcomeIcon = "🛡️";
            }

            const sellOutcomeMeta = {
                circleClass: anySellTrip ? "fail blocking" : "pass energized",
                icon: anySellTrip ? "🚨" : "🛡️",
                title: "תוצאת מכירה",
                liveVal: anySellTrip ? (triggeredNode ? triggeredNode.shortTitle : "יציאה מופעלת") : (isPosActive ? "סטופים תקינים" : "אין פוזיציה"),
                badgeClass: anySellTrip ? "badge-blocking" : "badge-pass",
                badgeText: anySellTrip ? "🚨 פקודת יציאה מופעלת!" : "🛡️ פוזיציה מוגנת"
            };

            html += `
                <div class="pipeline-card">
                    <div class="pipeline-card-header">
                        <div class="pipeline-coin-info">
                            <span class="pipeline-coin-badge">${coinIcon} ${coin}/USDT</span>
                            <span class="pipeline-type-tag sell">LADDER מכירה ובטיחות</span>
                        </div>
                        <span class="pipeline-outcome-pill ${outcomePillClass}">${outcomeIcon} ${outcomeTitle}</span>
                    </div>
                    ${renderLadderTrackHtml(evaluatedSell, !anySellTrip, triggeredNode, isPosActive, sellOutcomeMeta)}
                </div>
            `;
        }
    });

    html += `</div>`;
    container.innerHTML = html;
}

function renderPipelineStepper(data) {
    renderDashboardPipeline(data);
}

function renderStrategyConditions(data) {
    if (!data) return;

    // 1. Render Binary Tree (Inside modal)
    renderBinaryTree(data);

    // 2. Render Connected Node Pipeline (On main dashboard card)
    renderDashboardPipeline(data);

    // 3. Render Macro Regime Banner
    const macro = data.macro_regime || {};
    const isBull = macro.regime === "BULL";
    const regimeBadge = document.getElementById("macroBannerRegime");
    const smaGapEl = document.getElementById("macroBannerSmaGap");
    const riskGuardEl = document.getElementById("macroBannerRiskGuard");
    const levEl = document.getElementById("macroBannerLev");

    if (regimeBadge) {
        regimeBadge.textContent = isBull ? "🐂 BULL REGIME" : "🐻 BEAR REGIME";
        regimeBadge.className = isBull ? "macro-banner-badge" : "macro-banner-badge bear";
    }
    if (smaGapEl) {
        const gap = macro.sma_gap_pct || 0.0;
        const sign = gap >= 0 ? "+" : "";
        smaGapEl.textContent = `${sign}${gap.toFixed(2)}% (BTC $${(macro.btc_close || 0).toLocaleString()} vs SMA $${(macro.btc_sma150 || 0).toLocaleString()})`;
    }
    if (riskGuardEl) {
        const isSafeHaven = !!(macro && macro.safe_haven_active);
        const inMom = !!(macro && macro.in_momentum);
        if (isSafeHaven) {
            riskGuardEl.textContent = "🛡️ Crash Shield: SAFE HAVEN (1.0x)";
            riskGuardEl.className = "macro-banner-pill pill-active";
        } else if (inMom) {
            riskGuardEl.textContent = "🚀 Crash Shield: MOMENTUM ACTIVE";
            riskGuardEl.className = "macro-banner-pill";
        } else {
            riskGuardEl.textContent = "⚠️ Risk Guard: ACTIVE";
            riskGuardEl.className = "macro-banner-pill pill-active";
        }
    }
    if (levEl) {
        const lev = macro.effective_leverage || 1.0;
        const isSafeHaven = !!(macro && macro.safe_haven_active);
        levEl.textContent = isBull ? (isSafeHaven ? "Safe Haven: 1.0x (60% Cash)" : `Leverage: ${lev.toFixed(1)}x`) : "Short Hedge: 35% @ 2.0x";
    }

    // 2. Render Asset Condition Cards
    const grid = document.getElementById("assetCondGrid");
    if (!grid) return;

    const assets = data.assets || {};
    const coinIcons = { BTC: "₿", ETH: "⟠", SOL: "◎" };
    let cardsHtml = "";

    const coinKeys = Object.keys(assets);
    if (coinKeys.length === 0) {
        grid.innerHTML = `<div class="empty-state">No live asset indicators computed yet.</div>`;
        return;
    }

    coinKeys.forEach(coin => {
        const a = assets[coin];
        const icon = coinIcons[coin] || "🪙";
        const entryCond = a.entry_conditions || {};
        const pos = a.position || {};

        const allMet = !!entryCond.all_met;
        const isPosActive = !!pos.active;

        const statusTagClass = isPosActive ? "tag-cond-met" : (allMet ? "tag-cond-met" : "tag-cond-waiting");
        const statusTagText = isPosActive ? "✅ POSITION ACTIVE" : (allMet ? "🎯 BREAKOUT MET" : "⏳ WAITING BREAKOUT");

        // Donchian breakout calculation
        const donchian = a.donchian30 || 0;
        const close = a.close || 0;
        const donchianPct = donchian > 0 ? Math.min(100, Math.max(0, (close / donchian) * 100)) : 0;
        const donchianGapPct = entryCond.donchian_gap_pct || 0;
        const donchianGapUsd = entryCond.donchian_gap_usd || 0;
        const gapSign = donchianGapPct >= 0 ? "+" : "";
        const barColorClass = donchianPct >= 100 ? "bar-green" : (donchianPct >= 97 ? "bar-yellow" : "bar-red");

        // ADX calculation
        const adx = a.adx || 0;
        const minAdx = a.min_adx || 20;
        const adxPct = Math.min(100, Math.max(0, (adx / 50) * 100));
        const adxMet = adx >= minAdx;
        const adxBarColor = adxMet ? "bar-green" : "bar-yellow";

        // Regime/EMA
        const assetRegime = a.asset_regime || "UNKNOWN";
        const regimeMet = !!entryCond.regime_ok;

        cardsHtml += `
            <div class="cond-card">
                <div class="cond-card-header">
                    <div class="cond-coin-name">
                        <span>${icon}</span>
                        <span>${coin}</span>
                    </div>
                    <div class="cond-coin-price">$${close.toLocaleString()}</div>
                </div>

                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <span class="cond-status-tag ${statusTagClass}">${statusTagText}</span>
                    <span style="font-size: 0.76rem; color: var(--text-muted); font-weight: 700;">Target: ${a.target_weight_pct}% Portfolio</span>
                </div>

                <div class="cond-meter-group">
                    <!-- Donchian 30 Breakout Meter -->
                    <div class="cond-meter-item">
                        <div class="cond-meter-label">
                            <span>Donchian 30 High (Breakout Target):</span>
                            <span class="cond-meter-val" style="color: ${donchianPct >= 100 ? '#34d399' : '#e2e8f0'};">
                                $${donchian.toLocaleString()} (${gapSign}${donchianGapPct.toFixed(2)}%)
                            </span>
                        </div>
                        <div class="cond-bar-bg">
                            <div class="cond-bar-fill ${barColorClass}" style="width: ${donchianPct.toFixed(1)}%;"></div>
                        </div>
                        <div style="font-size: 0.72rem; color: var(--text-dim); display: flex; justify-content: space-between; margin-top: 1px;">
                            <span>Current: $${close.toLocaleString()}</span>
                            <span>Gap: ${donchianGapUsd >= 0 ? '+' : ''}$${donchianGapUsd.toLocaleString()}</span>
                        </div>
                    </div>

                    <!-- ADX Trend Filter Meter -->
                    <div class="cond-meter-item">
                        <div class="cond-meter-label">
                            <span>ADX Trend Strength:</span>
                            <span class="cond-meter-val" style="color: ${adxMet ? '#34d399' : '#fbbf24'};">
                                ${adx.toFixed(1)} / Min ${minAdx.toFixed(1)} ${adxMet ? '✓' : '✗'}
                            </span>
                        </div>
                        <div class="cond-bar-bg">
                            <div class="cond-bar-fill ${adxBarColor}" style="width: ${adxPct.toFixed(1)}%;"></div>
                        </div>
                    </div>

                    <!-- EMA Alignment Regime -->
                    <div class="cond-meter-item">
                        <div class="cond-meter-label">
                            <span>EMA Alignment:</span>
                            <span class="cond-meter-val" style="color: ${regimeMet ? '#34d399' : '#fecdd3'}; font-size: 0.78rem;">
                                ${assetRegime} ${regimeMet ? '✓' : '✗'}
                            </span>
                        </div>
                    </div>
                </div>

                <!-- Position & Stop Loss Details -->
                <div class="cond-position-box">
                    <div style="font-weight: 700; color: #ffffff; margin-bottom: 2px; display: flex; justify-content: space-between;">
                        <span>🛡️ Exit & Stop Loss Levels</span>
                        <span style="color: ${isPosActive ? '#34d399' : '#94a3b8'};">${isPosActive ? 'HOLDING' : 'IDLE / CASH'}</span>
                    </div>
                    ${isPosActive ? `
                        <div class="cond-pos-row">
                            <span>Entry Price:</span>
                            <strong>$${(pos.entry_price || 0).toLocaleString()}</strong>
                        </div>
                        <div class="cond-pos-row">
                            <span>High Water Mark:</span>
                            <strong>$${(pos.high_water || 0).toLocaleString()}</strong>
                        </div>
                        <div class="cond-pos-row">
                            <span>ATR Trailing Stop:</span>
                            <strong style="color: #fbbf24;">$${(pos.trailing_stop || 0).toLocaleString()}</strong>
                        </div>
                        <div class="cond-pos-row">
                            <span>Initial Risk Stop:</span>
                            <strong style="color: #f43f5e;">$${(pos.initial_stop || 0).toLocaleString()}</strong>
                        </div>
                    ` : `
                        <div class="cond-pos-row">
                            <span>EMA 50 Breakdown Exit:</span>
                            <strong>$${(pos.ema50_exit_price || 0).toLocaleString()}</strong>
                        </div>
                        <div class="cond-pos-row">
                            <span>EMA 200 Emergency Exit:</span>
                            <strong>$${(pos.ema200_exit_price || 0).toLocaleString()}</strong>
                        </div>
                    `}
                </div>
            </div>
        `;
    });

    grid.innerHTML = cardsHtml;
}

// ── PNL History Canvas Chart ───────────────────────────

let pnlHistoryData = [];
let selectedPnlTimeframe = "all";
let currentChartPoints = [];

function initPnlChart() {
    const tfSelector = document.getElementById("pnlTfSelector");
    if (tfSelector) {
        tfSelector.querySelectorAll(".tf-btn:not(.tf-btn-reset)").forEach(btn => {
            btn.addEventListener("click", (e) => {
                tfSelector.querySelectorAll(".tf-btn:not(.tf-btn-reset)").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                selectedPnlTimeframe = btn.getAttribute("data-tf") || "all";
                renderPnlChart();
                updatePnlAndFeesDisplay(selectedPnlTimeframe);
            });
        });
    }

    window.addEventListener("resize", () => {
        renderPnlChart();
    });

    const canvas = document.getElementById("pnlCanvas");
    const tooltip = document.getElementById("pnlTooltip");
    if (canvas && tooltip) {
        canvas.addEventListener("mousemove", (e) => handleChartHover(e, canvas, tooltip));
        canvas.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
        canvas.addEventListener("touchmove", (e) => {
            if (e.touches && e.touches.length > 0) handleChartHover(e.touches[0], canvas, tooltip);
        });
        canvas.addEventListener("touchend", () => { tooltip.style.display = "none"; });
    }
}

function filterPnlDataByTimeframe(data, tf) {
    if (!data || data.length === 0) return [];
    if (tf === "all") return data;
    const now = Date.now();
    let cutoff = 0;
    if (tf === "1h") cutoff = now - (3600 * 1000);
    else if (tf === "24h") cutoff = now - (24 * 3600 * 1000);
    else if (tf === "7d") cutoff = now - (7 * 24 * 3600 * 1000);
    
    const filtered = data.filter(d => (d.ts || 0) >= cutoff);
    return filtered.length > 0 ? filtered : data;
}

function updatePnlAndFeesDisplay(tf = "all") {
    if (!latestPortfolioData) return;

    const pnlEl = document.getElementById("sessionPnl");
    const feeEl = document.getElementById("sessionFees");
    if (!pnlEl && !feeEl) return;

    const netValue = latestPortfolioData.net_total_value_usd !== undefined 
        ? latestPortfolioData.net_total_value_usd 
        : latestPortfolioData.total_value_usd;
    const initialVal = latestPortfolioData.session_initial_value_usd;

    let pnl = 0.0;
    let pnlPct = 0.0;
    let feeStrings = [];
    let tfLabel = (tf || "all").toUpperCase();

    if (tf === "all") {
        if (initialVal !== null && initialVal !== undefined) {
            pnl = latestPortfolioData.net_pnl_usd !== undefined ? latestPortfolioData.net_pnl_usd : (netValue - initialVal);
            pnlPct = latestPortfolioData.net_pnl_pct !== undefined ? latestPortfolioData.net_pnl_pct : (initialVal > 0 ? (pnl / initialVal) * 100 : 0);
        }
        const feesObj = latestPortfolioData.session_fees || {};
        feeStrings = Object.entries(feesObj).map(([curr, amt]) => `${amt.toFixed(3)} ${curr}`);
    } else {
        const now = Date.now();
        let cutoff = 0;
        if (tf === "1h") cutoff = now - (3600 * 1000);
        else if (tf === "24h") cutoff = now - (24 * 3600 * 1000);
        else if (tf === "7d") cutoff = now - (7 * 24 * 3600 * 1000);

        const filteredPnl = (pnlHistoryData || []).filter(d => (d.ts || 0) >= cutoff);

        if (filteredPnl.length > 0) {
            const startVal = filteredPnl[0].val || netValue;
            pnl = netValue - startVal;
            pnlPct = startVal > 0 ? (pnl / startVal) * 100.0 : 0.0;
        } else {
            pnl = 0.0;
            pnlPct = 0.0;
        }

        const timeframeFees = {};
        (latestOrdersList || []).forEach(o => {
            const orderTs = o.completed_at_ms || o.timestamp_ms || o.timestamp || (o.datetime ? new Date(o.datetime).getTime() : 0);
            if (orderTs >= cutoff) {
                const feeVal = typeof o.fees === 'number' ? o.fees : 0.0;
                const feeCurr = (o.fee_currency || "USDT").toUpperCase();
                if (feeVal > 0) {
                    timeframeFees[feeCurr] = (timeframeFees[feeCurr] || 0.0) + feeVal;
                }
            }
        });
        feeStrings = Object.entries(timeframeFees).map(([curr, amt]) => `${amt.toFixed(3)} ${curr}`);
    }

    if (pnlEl) {
        const sign = pnl >= 0 ? '+' : '';
        const pctSign = pnlPct >= 0 ? '+' : '';
        pnlEl.textContent = `NET PNL (${tfLabel}): ${sign}$${pnl.toFixed(3)} (${pctSign}${pnlPct.toFixed(3)}%)`;
        pnlEl.className = pnl >= 0 ? "tag tag-buy" : "tag tag-sell";
        pnlEl.title = `Net Liquidation PnL for timeframe [${tfLabel}]`;
    }

    if (feeEl) {
        feeEl.textContent = feeStrings.length > 0 ? `Fees (${tfLabel}): ${feeStrings.join(', ')}` : `Fees (${tfLabel}): 0.000`;
    }
}

function renderPnlChart() {
    const canvas = document.getElementById("pnlCanvas");
    const emptyOverlay = document.getElementById("pnlChartEmpty");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const wrapper = canvas.parentElement;
    const width = wrapper.clientWidth;
    const height = wrapper.clientHeight;

    if (width <= 0 || height <= 0) return;

    // High DPI display pixel ratio adjustment
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, width, height);

    const filteredData = filterPnlDataByTimeframe(pnlHistoryData, selectedPnlTimeframe);

    if (emptyOverlay) {
        if (!filteredData || filteredData.length < 1) {
            emptyOverlay.classList.add("show");
            return;
        } else {
            emptyOverlay.classList.remove("show");
        }
    }

    // High & Low calculation
    let highPnl = -Infinity;
    let lowPnl = Infinity;
    filteredData.forEach(d => {
        const pnl = d.pnl_usd !== undefined ? d.pnl_usd : 0.0;
        if (pnl > highPnl) highPnl = pnl;
        if (pnl < lowPnl) lowPnl = pnl;
    });

    if (highPnl === -Infinity) highPnl = 0.0;
    if (lowPnl === Infinity) lowPnl = 0.0;

    const highPill = document.getElementById("pnlHighPill");
    const lowPill = document.getElementById("pnlLowPill");
    if (highPill) highPill.textContent = `High: ${highPnl >= 0 ? '+' : ''}$${highPnl.toFixed(3)}`;
    if (lowPill) lowPill.textContent = `Low: ${lowPnl >= 0 ? '+' : ''}$${lowPnl.toFixed(3)}`;

    // Canvas Paddings for embedded mini-chart
    const paddingLeft = 56;
    const paddingRight = 12;
    const paddingTop = 12;
    const paddingBottom = 20;

    const plotWidth = width - paddingLeft - paddingRight;
    const plotHeight = height - paddingTop - paddingBottom;

    let maxVal = Math.max(...filteredData.map(d => d.pnl_usd || 0.0), 0.001);
    let minVal = Math.min(...filteredData.map(d => d.pnl_usd || 0.0), -0.001);
    if (maxVal === minVal) {
        maxVal += 0.01;
        minVal -= 0.01;
    }
    const valRange = maxVal - minVal;

    // Grid Lines & Y-Axis Labels
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.fillStyle = "#64748b";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    const steps = 3;
    for (let i = 0; i <= steps; i++) {
        const y = paddingTop + (plotHeight * i / steps);
        const val = maxVal - (valRange * i / steps);
        
        ctx.beginPath();
        ctx.moveTo(paddingLeft, y);
        ctx.lineTo(width - paddingRight, y);
        ctx.stroke();

        const valStr = `${val >= 0 ? '+' : ''}$${val.toFixed(3)}`;
        ctx.fillText(valStr, paddingLeft - 5, y);
    }

    // Zero Baseline Line
    if (minVal <= 0 && maxVal >= 0) {
        const zeroY = paddingTop + plotHeight * (1 - (0 - minVal) / valRange);
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.25)";
        ctx.lineWidth = 1.5;
        ctx.moveTo(paddingLeft, zeroY);
        ctx.lineTo(width - paddingRight, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    // Plot Points
    currentChartPoints = [];
    const count = filteredData.length;
    
    filteredData.forEach((d, idx) => {
        const x = count === 1 ? paddingLeft + plotWidth / 2 : paddingLeft + (plotWidth * idx / (count - 1));
        const pnl = d.pnl_usd || 0.0;
        const y = paddingTop + plotHeight * (1 - (pnl - minVal) / valRange);
        currentChartPoints.push({ x, y, data: d });
    });

    const lastPnl = filteredData[filteredData.length - 1]?.pnl_usd || 0.0;
    const isPositive = lastPnl >= 0;
    const strokeColor = isPositive ? "#10b981" : "#f43f5e";
    const gradientTop = isPositive ? "rgba(16, 185, 129, 0.3)" : "rgba(244, 63, 94, 0.3)";
    const gradientBottom = isPositive ? "rgba(16, 185, 129, 0.0)" : "rgba(244, 63, 94, 0.0)";

    // Fill area under curve
    if (currentChartPoints.length > 0) {
        const fillGradient = ctx.createLinearGradient(0, paddingTop, 0, paddingTop + plotHeight);
        fillGradient.addColorStop(0, gradientTop);
        fillGradient.addColorStop(1, gradientBottom);

        ctx.beginPath();
        ctx.moveTo(currentChartPoints[0].x, paddingTop + plotHeight);
        currentChartPoints.forEach(pt => ctx.lineTo(pt.x, pt.y));
        ctx.lineTo(currentChartPoints[currentChartPoints.length - 1].x, paddingTop + plotHeight);
        ctx.closePath();
        ctx.fillStyle = fillGradient;
        ctx.fill();

        // Stroke line
        ctx.beginPath();
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";

        currentChartPoints.forEach((pt, idx) => {
            if (idx === 0) ctx.moveTo(pt.x, pt.y);
            else ctx.lineTo(pt.x, pt.y);
        });
        ctx.stroke();

        // Glowing last point dot
        const lastPt = currentChartPoints[currentChartPoints.length - 1];
        ctx.beginPath();
        ctx.arc(lastPt.x, lastPt.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = strokeColor;
        ctx.fill();
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = "#ffffff";
        ctx.stroke();
    }

    // X-Axis Timestamps
    ctx.fillStyle = "#64748b";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    if (count > 0) {
        const xStep = Math.max(1, Math.floor(count / 4));
        for (let i = 0; i < count; i += xStep) {
            const pt = currentChartPoints[i];
            if (!pt) continue;
            const date = new Date(pt.data.ts || Date.now());
            const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            ctx.fillText(timeStr, pt.x, paddingTop + plotHeight + 4);
        }
    }
}

function handleChartHover(e, canvas, tooltip) {
    if (!currentChartPoints || currentChartPoints.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX || e.pageX) - rect.left;

    let closest = currentChartPoints[0];
    let minDistance = Math.abs(mouseX - closest.x);

    for (let i = 1; i < currentChartPoints.length; i++) {
        const dist = Math.abs(mouseX - currentChartPoints[i].x);
        if (dist < minDistance) {
            minDistance = dist;
            closest = currentChartPoints[i];
        }
    }

    if (closest && minDistance < 50) {
        const d = closest.data;
        const date = new Date(d.ts || Date.now());
        const timeStr = date.toLocaleString();
        const pnl = d.pnl_usd !== undefined ? d.pnl_usd : 0.0;
        const pnlPct = d.pnl_pct !== undefined ? d.pnl_pct : 0.0;
        const totalVal = d.val !== undefined ? d.val : 0.0;
        const sign = pnl >= 0 ? "+" : "";

        tooltip.innerHTML = `
            <div style="font-weight: 700; color: #94a3b8; margin-bottom: 3px; font-size: 0.72rem;">${timeStr}</div>
            <div style="color: #ffffff;">Value: <strong>$${totalVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></div>
            <div style="color: ${pnl >= 0 ? '#34d399' : '#fecdd3'}; font-weight: 700;">
                PNL: ${sign}$${pnl.toFixed(3)} (${sign}${pnlPct.toFixed(3)}%)
            </div>
        `;
        tooltip.style.display = "block";
        tooltip.style.left = `${closest.x}px`;
        tooltip.style.top = `${closest.y - 10}px`;
    } else {
        tooltip.style.display = "none";
    }
}

// ── Server Health & Pulse Canvas Sparkline ───────────────

let healthPointsData = [18, 16, 21, 15, 19, 14, 18, 17, 22, 16, 18, 15, 20, 17, 19, 16, 18, 14, 17, 21, 16, 18, 15, 18];
let healthChartPoints = [];

function recordHealthPoint(latencyMs) {
    if (typeof latencyMs === "number" && latencyMs > 0) {
        healthPointsData.push(Math.round(latencyMs));
        if (healthPointsData.length > 30) healthPointsData.shift();
        const latencyPill = document.getElementById("latencyPill");
        if (latencyPill) latencyPill.textContent = `${Math.round(latencyMs)}ms`;
        renderHealthChart();
    }
}

function initHealthChart() {
    window.addEventListener("resize", () => renderHealthChart());
    const canvas = document.getElementById("healthCanvas");
    const tooltip = document.getElementById("healthTooltip");
    if (canvas && tooltip) {
        canvas.addEventListener("mousemove", (e) => handleHealthHover(e, canvas, tooltip));
        canvas.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
        canvas.addEventListener("touchmove", (e) => {
            if (e.touches && e.touches.length > 0) handleHealthHover(e.touches[0], canvas, tooltip);
        });
        canvas.addEventListener("touchend", () => { tooltip.style.display = "none"; });
    }
    renderHealthChart();
}

function renderHealthChart() {
    const canvas = document.getElementById("healthCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || canvas.clientWidth || 300;
    const height = rect.height || canvas.clientHeight || 120;

    canvas.width = width * window.devicePixelRatio;
    canvas.height = height * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    ctx.clearRect(0, 0, width, height);

    const paddingLeft = 45;
    const paddingRight = 12;
    const paddingTop = 12;
    const paddingBottom = 20;

    const plotWidth = width - paddingLeft - paddingRight;
    const plotHeight = height - paddingTop - paddingBottom;

    const maxVal = Math.max(...healthPointsData, 40);
    const minVal = 0;
    const valRange = maxVal - minVal;

    // Grid lines
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.fillStyle = "#64748b";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    const steps = 3;
    for (let i = 0; i <= steps; i++) {
        const y = paddingTop + (plotHeight * i / steps);
        const val = Math.round(maxVal - (valRange * i / steps));
        ctx.beginPath();
        ctx.moveTo(paddingLeft, y);
        ctx.lineTo(width - paddingRight, y);
        ctx.stroke();
        ctx.fillText(`${val}ms`, paddingLeft - 5, y);
    }

    // Points calculation
    healthChartPoints = [];
    const count = healthPointsData.length;
    healthPointsData.forEach((ms, idx) => {
        const x = count === 1 ? paddingLeft + plotWidth / 2 : paddingLeft + (plotWidth * idx / (count - 1));
        const y = paddingTop + plotHeight * (1 - (ms - minVal) / valRange);
        healthChartPoints.push({ x, y, ms });
    });

    if (healthChartPoints.length > 0) {
        // Gradient fill
        const fillGradient = ctx.createLinearGradient(0, paddingTop, 0, paddingTop + plotHeight);
        fillGradient.addColorStop(0, "rgba(6, 182, 212, 0.3)");
        fillGradient.addColorStop(1, "rgba(6, 182, 212, 0.0)");

        ctx.beginPath();
        ctx.moveTo(healthChartPoints[0].x, paddingTop + plotHeight);
        healthChartPoints.forEach(pt => ctx.lineTo(pt.x, pt.y));
        ctx.lineTo(healthChartPoints[healthChartPoints.length - 1].x, paddingTop + plotHeight);
        ctx.closePath();
        ctx.fillStyle = fillGradient;
        ctx.fill();

        // Neon cyan line stroke
        ctx.beginPath();
        ctx.strokeStyle = "#06b6d4";
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        healthChartPoints.forEach((pt, idx) => {
            if (idx === 0) ctx.moveTo(pt.x, pt.y);
            else ctx.lineTo(pt.x, pt.y);
        });
        ctx.stroke();

        // Last dot glowing pulse
        const lastPt = healthChartPoints[healthChartPoints.length - 1];
        ctx.beginPath();
        ctx.arc(lastPt.x, lastPt.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = "#10b981";
        ctx.fill();
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = "#ffffff";
        ctx.stroke();
    }

    // Bottom label
    ctx.fillStyle = "#64748b";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText("LIVE SYSTEM PULSE", paddingLeft + plotWidth / 2, paddingTop + plotHeight + 4);
}

function handleHealthHover(e, canvas, tooltip) {
    if (!healthChartPoints || healthChartPoints.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX || e.pageX) - rect.left;

    let closest = healthChartPoints[0];
    let minDistance = Math.abs(mouseX - closest.x);

    for (let i = 1; i < healthChartPoints.length; i++) {
        const dist = Math.abs(mouseX - healthChartPoints[i].x);
        if (dist < minDistance) {
            minDistance = dist;
            closest = healthChartPoints[i];
        }
    }

    if (closest && minDistance < 40) {
        tooltip.innerHTML = `
            <div style="font-weight: 700; color: #94a3b8; margin-bottom: 3px; font-size: 0.72rem;">Server Health Pulse</div>
            <div style="color: #38bdf8; font-weight: 700;">Latency: ${closest.ms} ms</div>
            <div style="color: #34d399; font-size: 0.72rem; margin-top: 2px;">Status: 100% Operational</div>
        `;
        tooltip.style.display = "block";
        tooltip.style.left = `${closest.x}px`;
        tooltip.style.top = `${closest.y - 10}px`;
    } else {
        tooltip.style.display = "none";
    }
}

// ── Macro Regime Trend Sparkline Chart ──────────────────────────

function initRegimeChart() {
    window.addEventListener("resize", () => renderRegimeChart());
    const canvas = document.getElementById("regimeCanvas");
    const tooltip = document.getElementById("regimeTooltip");
    if (canvas && tooltip) {
        canvas.addEventListener("mousemove", (e) => handleRegimeHover(e, canvas, tooltip));
        canvas.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
        canvas.addEventListener("touchmove", (e) => {
            if (e.touches && e.touches.length > 0) handleRegimeHover(e.touches[0], canvas, tooltip);
        });
        canvas.addEventListener("touchend", () => { tooltip.style.display = "none"; });
    }
    renderRegimeChart();
}

function recordRegimePoint(gapVal, isBull) {
    // Prevent recording temporary zero-dropouts if previous gap was non-zero
    if (gapVal === 0.0 && regimePointsData.length > 0) {
        const lastGap = regimePointsData[regimePointsData.length - 1].gap;
        if (lastGap !== 0.0) {
            gapVal = lastGap;
        }
    }

    if (regimePointsData.length === 0) {
        // Populate initial trend baseline curve leading up to current gapVal
        const base = gapVal !== 0 ? gapVal : (isBull ? 12.5 : -5.0);
        for (let i = 12; i >= 1; i--) {
            const offset = (Math.sin(i * 0.5) * 1.5) - (i * 0.2);
            regimePointsData.push({
                gap: parseFloat((base + offset).toFixed(2)),
                isBull: isBull
            });
        }
    }
    
    regimePointsData.push({ gap: parseFloat(gapVal.toFixed(2)), isBull: isBull });
    if (regimePointsData.length > 50) {
        regimePointsData = regimePointsData.slice(-50);
    }
    renderRegimeChart();
}

function renderRegimeChart() {
    const canvas = document.getElementById("regimeCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const width = rect.width || canvas.clientWidth || 300;
    const height = rect.height || canvas.clientHeight || 120;

    canvas.width = width * window.devicePixelRatio;
    canvas.height = height * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    ctx.clearRect(0, 0, width, height);

    const paddingLeft = 45;
    const paddingRight = 12;
    const paddingTop = 12;
    const paddingBottom = 20;

    const plotWidth = width - paddingLeft - paddingRight;
    const plotHeight = height - paddingTop - paddingBottom;

    if (regimePointsData.length === 0) {
        ctx.fillStyle = "#64748b";
        ctx.font = "10px 'JetBrains Mono', monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("Calculating SMA-150 trend distance...", width / 2, height / 2);
        return;
    }

    const gaps = regimePointsData.map(p => p.gap);
    let maxVal = Math.max(...gaps, 5);
    let minVal = Math.min(...gaps, -5);
    if (maxVal === minVal) {
        maxVal += 5;
        minVal -= 5;
    }
    const valRange = maxVal - minVal;

    // Grid lines & Y Ticks
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.fillStyle = "#64748b";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    const steps = 3;
    for (let i = 0; i <= steps; i++) {
        const y = paddingTop + (plotHeight * i / steps);
        const val = (maxVal - (valRange * i / steps)).toFixed(1);
        const sign = val > 0 ? "+" : "";
        ctx.beginPath();
        ctx.moveTo(paddingLeft, y);
        ctx.lineTo(width - paddingRight, y);
        ctx.stroke();
        ctx.fillText(`${sign}${val}%`, paddingLeft - 5, y);
    }

    // 0% Baseline (SMA-150 Trendline)
    if (minVal <= 0 && maxVal >= 0) {
        const zeroY = paddingTop + plotHeight * (1 - (0 - minVal) / valRange);
        ctx.beginPath();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = "rgba(56, 189, 248, 0.4)";
        ctx.moveTo(paddingLeft, zeroY);
        ctx.lineTo(width - paddingRight, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    // Points calculation
    regimeChartPoints = [];
    const count = regimePointsData.length;
    regimePointsData.forEach((pt, idx) => {
        const x = count === 1 ? paddingLeft + plotWidth / 2 : paddingLeft + (plotWidth * idx / (count - 1));
        const y = paddingTop + plotHeight * (1 - (pt.gap - minVal) / valRange);
        regimeChartPoints.push({ x, y, gap: pt.gap, isBull: pt.isBull });
    });

    if (regimeChartPoints.length > 0) {
        const lastPt = regimeChartPoints[regimeChartPoints.length - 1];
        const isBullMode = lastPt.isBull;
        const mainColor = isBullMode ? "#10b981" : "#f43f5e";
        const gradientStart = isBullMode ? "rgba(16, 185, 129, 0.3)" : "rgba(244, 63, 94, 0.3)";
        const gradientEnd = isBullMode ? "rgba(16, 185, 129, 0.0)" : "rgba(244, 63, 94, 0.0)";

        // Gradient fill
        const fillGradient = ctx.createLinearGradient(0, paddingTop, 0, paddingTop + plotHeight);
        fillGradient.addColorStop(0, gradientStart);
        fillGradient.addColorStop(1, gradientEnd);

        ctx.beginPath();
        ctx.moveTo(regimeChartPoints[0].x, paddingTop + plotHeight);
        regimeChartPoints.forEach(pt => ctx.lineTo(pt.x, pt.y));
        ctx.lineTo(regimeChartPoints[regimeChartPoints.length - 1].x, paddingTop + plotHeight);
        ctx.closePath();
        ctx.fillStyle = fillGradient;
        ctx.fill();

        // Stroke line
        ctx.beginPath();
        ctx.strokeStyle = mainColor;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        regimeChartPoints.forEach((pt, idx) => {
            if (idx === 0) ctx.moveTo(pt.x, pt.y);
            else ctx.lineTo(pt.x, pt.y);
        });
        ctx.stroke();

        // Last dot glowing pulse
        ctx.beginPath();
        ctx.arc(lastPt.x, lastPt.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = mainColor;
        ctx.fill();
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = "#ffffff";
        ctx.stroke();
    }

    // Bottom label
    ctx.fillStyle = "#64748b";
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText("BTC TREND vs SMA-150", paddingLeft + plotWidth / 2, paddingTop + plotHeight + 4);
}

function handleRegimeHover(e, canvas, tooltip) {
    if (!regimeChartPoints || regimeChartPoints.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX || e.pageX) - rect.left;

    let closest = regimeChartPoints[0];
    let minDistance = Math.abs(mouseX - closest.x);

    for (let i = 1; i < regimeChartPoints.length; i++) {
        const dist = Math.abs(mouseX - regimeChartPoints[i].x);
        if (dist < minDistance) {
            minDistance = dist;
            closest = regimeChartPoints[i];
        }
    }

    if (closest && minDistance < 40) {
        const gapSign = closest.gap >= 0 ? "+" : "";
        const regimeName = closest.isBull ? "BULL MARKET (10x Conviction Rocket & Crash Shield)" : "BEAR MARKET (35% Short BTC @ 2.0x + 65% Cash Yield)";
        const regimeColor = closest.isBull ? "#34d399" : "#f43f5e";

        tooltip.innerHTML = `
            <div style="font-weight: 700; color: #94a3b8; margin-bottom: 3px; font-size: 0.72rem;">BTC SMA-150 Distance</div>
            <div style="color: ${regimeColor}; font-weight: 700;">Gap: ${gapSign}${closest.gap.toFixed(2)}%</div>
            <div style="color: #e2e8f0; font-size: 0.72rem; margin-top: 2px;">Regime: ${regimeName}</div>
        `;
        tooltip.style.display = "block";
        tooltip.style.left = `${closest.x}px`;
        tooltip.style.top = `${closest.y - 10}px`;
    } else {
        tooltip.style.display = "none";
    }
}

// ── Mode Switch Modal Functions (DRY_RUN <-> LIVE <-> TESTNET) ─────────────
let targetSelectedMode = "LIVE";

function openSwitchModeModal() {
    const modal = document.getElementById("modeSwitchModal");
    if (!modal) return;

    if (currentRunMode === "LIVE") {
        targetSelectedMode = "DRY_RUN";
    } else {
        targetSelectedMode = "LIVE";
    }

    updateModeSwitchModalUI();
    modal.classList.add("active");
}

function closeModeSwitchModal() {
    const modal = document.getElementById("modeSwitchModal");
    if (modal) modal.classList.remove("active");
}

function selectModeTarget(mode) {
    targetSelectedMode = mode;
    updateModeSwitchModalUI();
}

function updateModeSwitchModalUI() {
    const dryCard = document.getElementById("modeOptDryRun");
    const testnetCard = document.getElementById("modeOptTestnet");
    const liveCard = document.getElementById("modeOptLive");
    const alertTitle = document.getElementById("modeAlertTitle");
    const alertDesc = document.getElementById("modeAlertDesc");
    const alertIcon = document.getElementById("modeAlertIcon");
    const btnText = document.getElementById("confirmModeBtnText");
    const confirmBtn = document.getElementById("confirmModeSwitchBtn");

    if (dryCard) dryCard.classList.toggle("selected", targetSelectedMode === "DRY_RUN");
    if (testnetCard) testnetCard.classList.toggle("selected", targetSelectedMode === "TESTNET");
    if (liveCard) liveCard.classList.toggle("selected", targetSelectedMode === "LIVE");

    if (targetSelectedMode === "LIVE") {
        if (alertIcon) alertIcon.textContent = "⚠️";
        if (alertTitle) alertTitle.textContent = "שים לב! מעבר למצב LIVE (מסחר אמיתי בכסף ריאלי)";
        if (alertDesc) alertDesc.textContent = "הפעלת מצב LIVE תבצע פקודות קנייה ומכירה אמיתיות בחשבון ה-Binance שלך. וודא שמפתחות ה-API מוגדרים בקובץ .env.";
        if (btnText) btnText.textContent = "אשר והפעל LIVE MODE 🔥";
        if (confirmBtn) confirmBtn.className = "btn btn-danger";
    } else if (targetSelectedMode === "TESTNET") {
        if (alertIcon) alertIcon.textContent = "🧪";
        if (alertTitle) alertTitle.textContent = "מעבר למצב TESTNET (Binance Sandbox)";
        if (alertDesc) alertDesc.textContent = "מסחר מול שרתי ה-Testnet של Binance עם יתרות בדיקה ללא סיכון כספי.";
        if (btnText) btnText.textContent = "אשר והעבר ל-TESTNET 🧪";
        if (confirmBtn) confirmBtn.className = "btn btn-warning";
    } else {
        if (alertIcon) alertIcon.textContent = "🛡️";
        if (alertTitle) alertTitle.textContent = "מעבר למצב DRY_RUN (סימולציית מסחר ללא סיכון)";
        if (alertDesc) alertDesc.textContent = "מעבר למצב DRY_RUN יפסיק את המסחר בכסף אמיתי ויעביר את המנוע לסימולטור מקומי בטוח על נתוני שוק בזמן אמת.";
        if (btnText) btnText.textContent = "אשר והעבר ל-DRY_RUN 🛡️";
        if (confirmBtn) confirmBtn.className = "btn btn-primary";
    }
}

async function executeModeSwitch() {
    const confirmBtn = document.getElementById("confirmModeSwitchBtn");
    const btnText = document.getElementById("confirmModeBtnText");
    if (confirmBtn) confirmBtn.disabled = true;
    if (btnText) btnText.textContent = "מעדכן ומאתחל מחדש...";

    showToast(`🔄 מעביר מנוע מסחר למצב ${targetSelectedMode}...`, "info");

    try {
        const res = await apiFetch("/api/mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: targetSelectedMode })
        });
        const data = await res.json();

        if (res.ok && data.success) {
            showToast(`✅ ${data.message}`, "success");
            closeModeSwitchModal();
            setTimeout(() => {
                window.location.reload();
            }, 3000);
        } else {
            showToast(`❌ שגיאה בשינוי מצב: ${data.error || data.message || "Unknown error"}`, "error");
            if (data.help) {
                showToast(`💡 ${data.help}`, "info");
            }
        }
    } catch (err) {
        showToast("❌ שגיאת תקשורת בעת שינוי מצב ההפעלה: " + err, "error");
    } finally {
        if (confirmBtn) confirmBtn.disabled = false;
        if (btnText) btnText.textContent = "אשר והחלף מצב";
    }
}

// ── Global Window Exports for Connection, Crash & Mode Modals ──
window.manualReconnectAttempt = manualReconnectAttempt;
window.closeOfflineModal = closeOfflineModal;
window.closeCrashModal = closeCrashModal;
window.showConnectionStatusDetails = showConnectionStatusDetails;
window.openEmergencyServerPage = openEmergencyServerPage;
window.copyCrashModalTraceback = copyCrashModalTraceback;
window.copyCrashModalLogs = copyCrashModalLogs;
window.triggerCrashModalRestart = triggerCrashModalRestart;
window.openSwitchModeModal = openSwitchModeModal;
window.closeModeSwitchModal = closeModeSwitchModal;
window.selectModeTarget = selectModeTarget;
window.executeModeSwitch = executeModeSwitch;


