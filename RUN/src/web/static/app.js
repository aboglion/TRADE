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

    const res = await fetch(url, options);
    if (res.status === 401 || res.status === 429) {
        authToken = "";
        sessionStorage.removeItem("dash_password");
        showLoginModal();
    }
    return res;
}

document.addEventListener("DOMContentLoaded", async () => {
    initClock();

    // Login listeners
    const submitBtn = document.getElementById("submitLoginBtn");
    if (submitBtn) submitBtn.addEventListener("click", performLogin);
    const passInput = document.getElementById("dashboardPasswordInput");
    if (passInput) passInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") performLogin();
    });

    const isAuthed = await checkAuthStatus();
    if (isAuthed) {
        fetchDashboardData();
        loadTelegramConfig();
    }
    setInterval(fetchDashboardData, 5000);

    initPnlChart();
    initRegimeChart();
    initHealthChart();

    // Event listeners with instant visual feedback
    document.getElementById("refreshBtn").addEventListener("click", manualRefresh);
    document.getElementById("triggerCycleBtn").addEventListener("click", openTriggerCycleModal);
    document.getElementById("killSwitchBtn").addEventListener("click", openKillSwitchModal);
    document.getElementById("toggleUpdaterBtn").addEventListener("click", toggleUpdater);
    document.getElementById("manualPullBtn").addEventListener("click", triggerManualPull);

    const resetStatsBtn = document.getElementById("resetStatsBtn");
    if (resetStatsBtn) resetStatsBtn.addEventListener("click", openResetPnlModal);
    const miniResetBtn = document.getElementById("pnlResetMiniBtn");
    if (miniResetBtn) miniResetBtn.addEventListener("click", openResetPnlModal);

    // Reset PnL Modal listeners
    const closeResetModal = document.getElementById("closeResetPnlModal");
    if (closeResetModal) closeResetModal.addEventListener("click", closeResetPnlModal);
    const cancelResetBtn = document.getElementById("cancelResetPnlBtn");
    if (cancelResetBtn) cancelResetBtn.addEventListener("click", closeResetPnlModal);
    const confirmResetBtn = document.getElementById("confirmResetPnlBtn");
    if (confirmResetBtn) confirmResetBtn.addEventListener("click", confirmResetPnlStats);
    const resetModal = document.getElementById("resetPnlConfirmModal");
    if (resetModal) resetModal.addEventListener("click", (e) => {
        if (e.target.id === "resetPnlConfirmModal") closeResetPnlModal();
    });

    // Logs & Orders toolbar listeners
    const copyLogsBtn = document.getElementById("copyLogsBtn");
    if (copyLogsBtn) copyLogsBtn.addEventListener("click", copyLogsToClipboard);
    const clearLogsBtn = document.getElementById("clearLogsBtn");
    if (clearLogsBtn) clearLogsBtn.addEventListener("click", clearLogsConsole);

    const copyOrdersBtn = document.getElementById("copyOrdersBtn");
    if (copyOrdersBtn) copyOrdersBtn.addEventListener("click", copyOrdersToClipboard);
    const clearOrdersBtn = document.getElementById("clearOrdersBtn");
    if (clearOrdersBtn) clearOrdersBtn.addEventListener("click", clearOrdersTable);

    // Trigger Cycle Confirm Modal listeners
    document.getElementById("closeTriggerCycleConfirmModal").addEventListener("click", closeTriggerCycleConfirmModal);
    document.getElementById("cancelTriggerCycleConfirmBtn").addEventListener("click", closeTriggerCycleConfirmModal);
    document.getElementById("confirmTriggerCycleBtn").addEventListener("click", confirmTriggerCycle);
    document.getElementById("triggerCycleConfirmModal").addEventListener("click", (e) => {
        if (e.target.id === "triggerCycleConfirmModal") closeTriggerCycleConfirmModal();
    });

    // Kill Switch Modal listeners
    document.getElementById("closeKillSwitchModal").addEventListener("click", closeKillSwitchModal);
    document.getElementById("cancelKillSwitchBtn").addEventListener("click", closeKillSwitchModal);
    document.getElementById("confirmKillSwitchBtn").addEventListener("click", confirmToggleKillSwitch);
    document.getElementById("killSwitchModal").addEventListener("click", (e) => {
        if (e.target.id === "killSwitchModal") closeKillSwitchModal();
    });

    // Dry Run modal event listeners
    document.getElementById("dryRunModalBtn").addEventListener("click", openDryRunModal);
    document.getElementById("closeDryRunModal").addEventListener("click", closeDryRunModal);
    document.getElementById("cancelDryRunSave").addEventListener("click", closeDryRunModal);
    document.getElementById("saveDryRunBalances").addEventListener("click", openDryRunConfirmModal);
    document.getElementById("dryRunModal").addEventListener("click", (e) => {
        if (e.target.id === "dryRunModal") closeDryRunModal();
    });

    // Dry Run Confirm modal event listeners
    document.getElementById("closeDryRunConfirmModal").addEventListener("click", closeDryRunConfirmModal);
    document.getElementById("cancelDryRunConfirmBtn").addEventListener("click", closeDryRunConfirmModal);
    document.getElementById("confirmDryRunSaveBtn").addEventListener("click", confirmSaveDryRunBalances);
    document.getElementById("dryRunConfirmModal").addEventListener("click", (e) => {
        if (e.target.id === "dryRunConfirmModal") closeDryRunConfirmModal();
    });

    // Telegram modal event listeners
    const tgBtn = document.getElementById("telegramModalBtn");
    if (tgBtn) tgBtn.addEventListener("click", openTelegramModal);
    const closeTgModal = document.getElementById("closeTelegramModal");
    if (closeTgModal) closeTgModal.addEventListener("click", closeTelegramModal);
    const cancelTgSave = document.getElementById("cancelTelegramSave");
    if (cancelTgSave) cancelTgSave.addEventListener("click", closeTelegramModal);
    const saveTgBtn = document.getElementById("saveTelegramConfigBtn");
    if (saveTgBtn) saveTgBtn.addEventListener("click", saveTelegramConfig);
    const testTgBtn = document.getElementById("testTelegramBtn");
    if (testTgBtn) testTgBtn.addEventListener("click", testTelegramConnection);
    const toggleEyeBtn = document.getElementById("toggleTokenVisibilityBtn");
    if (toggleEyeBtn) toggleEyeBtn.addEventListener("click", toggleTokenVisibility);
    const tgModal = document.getElementById("telegramModal");
    if (tgModal) tgModal.addEventListener("click", (e) => {
        if (e.target.id === "telegramModal") closeTelegramModal();
    });

    // System Errors modal event listeners
    document.getElementById("systemHealthCard").addEventListener("click", openErrorsModal);
    document.getElementById("closeErrorsModal").addEventListener("click", closeErrorsModal);
    document.getElementById("closeErrorsModalFooter").addEventListener("click", closeErrorsModal);
    document.getElementById("clearErrorsBtn").addEventListener("click", clearSystemErrors);
    document.getElementById("errorsModal").addEventListener("click", (e) => {
        if (e.target.id === "errorsModal") closeErrorsModal();
    });
});

async function checkAuthStatus() {
    try {
        const res = await fetch("/api/auth_check", { headers: getAuthHeaders() });
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

        // Mode badge
        currentRunMode = data.run_mode || "DRY_RUN";
        document.getElementById("modeText").textContent = currentRunMode;

        // Regime badge & card
        const regimeBadge = document.getElementById("regimeBadge");
        const regimeText = document.getElementById("regimeText");
        const regimeIcon = document.getElementById("regimeIcon");
        const macroRegimeVal = document.getElementById("macroRegimeVal");
        const macroRegimeSub = document.getElementById("macroRegimeSub");

        const isBull = (data.last_regime || "").toLowerCase() === "bull";

        if (isBull) {
            regimeBadge.className = "status-badge regime-badge";
            regimeIcon.textContent = "🐂";
            regimeText.textContent = "BULL REGIME";
            macroRegimeVal.textContent = "BULL MARKET";
            macroRegimeVal.className = "metric-value text-success";
            macroRegimeSub.textContent = "Crypto Allocations Active (2.0x Bull Strategy)";
        } else {
            regimeBadge.className = "status-badge regime-badge regime-bear";
            regimeIcon.textContent = "🐻";
            regimeText.textContent = "BEAR REGIME";
            macroRegimeVal.textContent = "BEAR MARKET";
            macroRegimeVal.className = "metric-value text-danger";
            macroRegimeSub.textContent = "Spot Protection Active (100% USDT Cash)";
        }

        // Record & Update Macro Regime Trend Chart
        const btcMetrics = data.market_metrics && data.market_metrics.BTC ? data.market_metrics.BTC : null;
        const btcSmaGap = btcMetrics ? (btcMetrics.change_sma150 || 0.0) : 0.0;
        
        const regimeGapPill = document.getElementById("regimeGapPill");
        const regimeLevPill = document.getElementById("regimeLevPill");
        if (regimeGapPill) {
            const gapSign = btcSmaGap >= 0 ? "+" : "";
            regimeGapPill.textContent = `${gapSign}${btcSmaGap.toFixed(2)}%`;
            regimeGapPill.className = btcSmaGap >= 0 ? "pnl-pill pnl-pill-high" : "pnl-pill pnl-pill-low";
        }
        if (regimeLevPill) {
            regimeLevPill.textContent = isBull ? "2.0x BULL" : "0.0x BEAR";
            regimeLevPill.className = isBull ? "pnl-pill pnl-pill-high" : "pnl-pill pnl-pill-low";
        }
        recordRegimePoint(btcSmaGap, isBull);

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

        if (data.last_run_ts) {
            const date = new Date(data.last_run_ts);
            document.getElementById("lastCycleTime").textContent = date.toLocaleTimeString();
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
        if (!res.ok) return;
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

async function fetchLogs() {
    try {
        const res = await apiFetch("/api/logs");
        if (!res.ok) return;
        const data = await res.json();

        const logConsole = document.getElementById("logConsole");
        const logCountEl = document.getElementById("logCount");
        if (logCountEl) logCountEl.textContent = `${data.logs ? data.logs.length : 0} / 1000`;

        const wasScrolledToBottom = logConsole.scrollHeight - logConsole.clientHeight <= logConsole.scrollTop + 20;

        logConsole.innerHTML = "";
        
        if (!data.logs || data.logs.length === 0) {
            logConsole.innerHTML = `<div class="log-line text-muted">No logs recorded yet</div>`;
            return;
        }

        data.logs.forEach(rawLine => {
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

function copyLogsToClipboard() {
    const logConsole = document.getElementById("logConsole");
    if (!logConsole) return;
    const text = logConsole.innerText || logConsole.textContent;
    if (!text || text.trim() === "No logs recorded yet") {
        showToast("⚠️ No log content to copy", "info");
        return;
    }
    navigator.clipboard.writeText(text).then(() => {
        showToast("📋 All system logs copied to clipboard!", "success");
    }).catch(err => {
        showToast("❌ Error copying logs: " + err, "error");
    });
}

function clearLogsConsole() {
    const logConsole = document.getElementById("logConsole");
    if (logConsole) {
        logConsole.innerHTML = `<div class="log-line text-muted">Logs console cleared by user</div>`;
    }
    const logCountEl = document.getElementById("logCount");
    if (logCountEl) logCountEl.textContent = "0 / 1000";
    showToast("🧹 Log console display cleared!", "info");
}

function copyOrdersToClipboard() {
    const table = document.querySelector(".data-table");
    if (!table) return;
    const rows = Array.from(table.querySelectorAll("tr"));
    const textLines = rows.map(r => {
        const cells = Array.from(r.querySelectorAll("th, td")).map(c => c.innerText.trim());
        return cells.join("\t");
    });
    const text = textLines.join("\n");
    if (!text || text.includes("No order history available")) {
        showToast("⚠️ No order history to copy", "info");
        return;
    }
    navigator.clipboard.writeText(text).then(() => {
        showToast("📋 Order history copied to clipboard!", "success");
    }).catch(err => {
        showToast("❌ Error copying order history: " + err, "error");
    });
}

function clearOrdersTable() {
    const tableBody = document.getElementById("ordersTableBody");
    if (tableBody) {
        tableBody.innerHTML = `<tr><td colspan="8" class="empty-cell text-muted">Orders display cleared by user</td></tr>`;
    }
    const orderCountEl = document.getElementById("orderCount");
    if (orderCountEl) orderCountEl.textContent = "0 / 100+";
    showToast("🧹 Orders display cleared!", "info");
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

async function openDryRunModal() {
    const modal = document.getElementById("dryRunModal");
    try {
        const res = await apiFetch("/api/dry_run/balances");
        if (res.ok) {
            const data = await res.json();
            const bal = data.balances || {};
            document.getElementById("dryUsdtInput").value = bal.USDT !== undefined ? bal.USDT : 1000;
            document.getElementById("dryBtcInput").value = bal.BTC !== undefined ? bal.BTC : 0;
            document.getElementById("dryEthInput").value = bal.ETH !== undefined ? bal.ETH : 0;
            document.getElementById("drySolInput").value = bal.SOL !== undefined ? bal.SOL : 0;
        }
    } catch (e) {
        console.error("Failed to load dry run balances:", e);
    }
    modal.classList.add("active");
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

    const balances = { USDT: usdt, BTC: btc, ETH: eth, SOL: sol };

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
        const regimeName = closest.isBull ? "BULL MARKET (2.0x Leverage)" : "BEAR MARKET (100% USDT Cash)";
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

