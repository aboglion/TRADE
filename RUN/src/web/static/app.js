/**
 * Dashboard Client App.
 *
 * Polls REST API endpoints every 5 seconds and updates the UI dynamically.
 */

let activeErrorsList = [];
let authToken = sessionStorage.getItem("dash_password") || "";

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
    }
    setInterval(fetchDashboardData, 5000);

    // Event listeners with instant visual feedback
    document.getElementById("refreshBtn").addEventListener("click", manualRefresh);
    document.getElementById("triggerCycleBtn").addEventListener("click", triggerCycle);
    document.getElementById("killSwitchBtn").addEventListener("click", toggleKillSwitch);
    document.getElementById("toggleUpdaterBtn").addEventListener("click", toggleUpdater);

    // Dry Run modal event listeners
    document.getElementById("dryRunModalBtn").addEventListener("click", openDryRunModal);
    document.getElementById("closeDryRunModal").addEventListener("click", closeDryRunModal);
    document.getElementById("cancelDryRunSave").addEventListener("click", closeDryRunModal);
    document.getElementById("saveDryRunBalances").addEventListener("click", saveDryRunBalances);

    // Reset Stats listener
    document.getElementById("resetStatsBtn").addEventListener("click", resetSessionStats);

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
                        errorMsg.textContent = `חשבון ננעל זמנית! נסה שוב בעוד ${data.retry_after_seconds} שניות.`;
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
        errorMsg.textContent = "אנא הכנס סיסמה";
        errorMsg.style.display = "block";
        return;
    }

    btn.disabled = true;
    btn.textContent = "מאמת...";
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
            showToast("🔑 התחברת בהצלחה ללוח הבקרה!", "success");
            fetchDashboardData();
        } else {
            authToken = "";
            sessionStorage.removeItem("dash_password");
            errorMsg.textContent = data.error || "סיסמה שגויה (Invalid password)";
            errorMsg.style.display = "block";
        }
    } catch (err) {
        errorMsg.textContent = "שגיאת תקשורת: " + err;
        errorMsg.style.display = "block";
    } finally {
        btn.disabled = false;
        btn.textContent = "התחבר למערכת 🔑";
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
    showToast("✨ נתוני הדשבורד רועננו בהצלחה!", "success");
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
            showToast("🔄 Git Auto-Updater הופעל בהצלחה! (Active)", "success");
        } else {
            showToast("⏸️ Git Auto-Updater הוקפא (Paused)", "info");
        }
    } catch (err) {
        showToast("❌ שגיאה בשינוי סטטוס auto-updater: " + err, "error");
    } finally {
        btn.disabled = false;
    }
}

// ── Status & Regime ────────────────────────────────────────

async function fetchStatus() {
    try {
        const res = await apiFetch("/api/status");
        if (!res.ok) return;
        const data = await res.json();

        // Mode badge
        document.getElementById("modeText").textContent = data.run_mode || "DRY_RUN";

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

        // Total Portfolio Value
        document.getElementById("portfolioValue").textContent = `$${data.total_value_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        // PNL and Fees
        const initialVal = data.session_initial_value_usd;
        if (initialVal !== null && initialVal !== undefined) {
            const pnl = data.total_value_usd - initialVal;
            const pnlPct = initialVal > 0 ? (pnl / initialVal) * 100 : 0;
            const pnlEl = document.getElementById("sessionPnl");
            if (pnlEl) {
                pnlEl.textContent = `PNL: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`;
                pnlEl.className = pnl >= 0 ? "tag tag-buy" : "tag tag-sell";
            }
        }
        
        const feesObj = data.session_fees || {};
        const feeStrings = Object.entries(feesObj).map(([curr, amt]) => `${amt.toFixed(4)} ${curr}`);
        const feeEl = document.getElementById("sessionFees");
        if (feeEl) {
            feeEl.textContent = feeStrings.length > 0 ? `Fees: ${feeStrings.join(', ')}` : "Fees: 0.00";
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
            if (symbol === "BTC") bgClass = "bg-btc";
            else if (symbol === "ETH") bgClass = "bg-eth";
            else if (symbol === "SOL") bgClass = "bg-sol";

            const isZero = (h.total === 0 || h.weight_pct === 0);
            const totalStr = isZero ? "0.00" : (h.total < 1 ? h.total.toFixed(4) : h.total.toFixed(2));
            const valueStr = h.value_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            const fillWidth = isZero ? 0 : Math.min(100, Math.max(1, h.weight_pct));

            const row = document.createElement("div");
            row.className = `alloc-row ${isZero ? "alloc-row-zero" : ""}`;
            row.innerHTML = `
                <div class="alloc-info">
                    <span class="alloc-symbol">${symbol}</span>
                    <span class="alloc-center">${totalStr} ($${valueStr})</span>
                    <span class="alloc-weight"><strong>${h.weight_pct}%</strong></span>
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
        document.getElementById("orderCount").textContent = allOrders.length;

        if (allOrders.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="7" class="empty-cell text-muted">No orders executed yet</td></tr>`;
            return;
        }

        allOrders.slice(0, 15).forEach(o => {
            const tr = document.createElement("tr");
            const sideClass = (o.side || "").toLowerCase() === "buy" ? "tag-buy" : "tag-sell";
            const priceStr = o.average_price ? `$${o.average_price.toFixed(2)}` : (o.price ? `$${o.price.toFixed(2)}` : "MARKET");

            const feeVal = typeof o.fees === 'number' ? o.fees : 0.0;
            const feeStr = feeVal > 0 ? `${feeVal.toFixed(4)} ${o.fee_currency || ''}`.trim() : "0.0";

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
                <td><span class="tag ${sideClass}">${(o.side || "BUY").toUpperCase()}</span></td>
                <td><strong>${o.symbol}</strong></td>
                <td>${o.amount || o.filled_amount || 0}</td>
                <td>${priceStr}</td>
                <td>${feeStr}</td>
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
        const wasScrolledToBottom = logConsole.scrollHeight - logConsole.clientHeight <= logConsole.scrollTop + 20;

        logConsole.innerHTML = "";
        
        if (!data.logs || data.logs.length === 0) {
            logConsole.innerHTML = `<div class="log-line text-muted">No logs recorded yet</div>`;
            return;
        }

        data.logs.forEach(rawLine => {
            if (rawLine.includes("Loaded state:") || rawLine.includes("Portfolio snapshot:") || rawLine.includes("No state file found at")) {
                return;
            }
            const row = document.createElement("div");
            row.className = "log-row";

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

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Actions ────────────────────────────────────────────────

async function triggerCycle() {
    const btn = document.getElementById("triggerCycleBtn");
    btn.disabled = true;
    btn.textContent = "⏳ Running...";

    try {
        const res = await apiFetch("/api/trigger", { method: "POST" });
        const data = await res.json();
        await fetchDashboardData();
        showToast("▶ מחזור מסחר הופעל והושלם בהצלחה!", "success");
    } catch (err) {
        showToast("❌ שגיאה בהפעלת מחזור: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "▶ Run Instant Cycle";
    }
}

async function toggleKillSwitch() {
    try {
        const res = await apiFetch("/api/killswitch", { method: "POST" });
        const data = await res.json();
        await fetchStatus();
        const statusMsg = data.kill_switch ? "⚠️ KILL SWITCH ACTIVATED — Trading Halts!" : "🛡️ KILL SWITCH DEACTIVATED — Trading Active";
        showToast(statusMsg, data.kill_switch ? "error" : "success");
    } catch (err) {
        showToast("❌ Failed to toggle kill switch: " + err, "error");
    }
}

async function resetSessionStats() {
    if (!confirm("Are you sure you want to reset PNL, fees, and order history? This will start a new session.")) {
        return;
    }
    
    const btn = document.getElementById("resetStatsBtn");
    btn.disabled = true;
    btn.textContent = "⏳ Resetting...";

    try {
        const res = await apiFetch("/api/reset_stats", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🧹 נתוני הסשן (רווחים ועמלות) אופסו בהצלחה!", "success");
            await fetchDashboardData();
        } else {
            showToast("❌ Error resetting stats: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ Failed to reset stats: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "🧹 Reset PNL Stats";
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

async function saveDryRunBalances() {
    const usdt = parseFloat(document.getElementById("dryUsdtInput").value) || 0;
    const btc = parseFloat(document.getElementById("dryBtcInput").value) || 0;
    const eth = parseFloat(document.getElementById("dryEthInput").value) || 0;
    const sol = parseFloat(document.getElementById("drySolInput").value) || 0;

    const balances = { USDT: usdt, BTC: btc, ETH: eth, SOL: sol };

    const saveBtn = document.getElementById("saveDryRunBalances");
    saveBtn.disabled = true;
    saveBtn.textContent = "שומר...";

    try {
        const res = await apiFetch("/api/dry_run/balances", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ balances }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("⚙️ אחזקות DRY RUN עודכנו בהצלחה!", "success");
            closeDryRunModal();
            await fetchPortfolio();
        } else {
            showToast("❌ שגיאה בעדכון אחזקות: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ שגיאה בעדכון אחזקות: " + err, "error");
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = "עדכן אחזקות 💾";
    }
}

// ── System Errors Diagnostic Modal Functions ───────────────────

function openErrorsModal() {
    const modal = document.getElementById("errorsModal");
    const listEl = document.getElementById("modalErrorList");
    listEl.innerHTML = "";

    if (!activeErrorsList || activeErrorsList.length === 0) {
        listEl.innerHTML = `<li class="empty-errors">אין שגיאות רשומות — המערכת פועלת באופן תקין לחלוטין ✓</li>`;
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
    btn.textContent = "מנקה...";

    try {
        const res = await apiFetch("/api/errors/clear", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🧹 שגיאות המערכת נוקו בהצלחה!", "success");
            activeErrorsList = [];
            closeErrorsModal();
            await fetchStatus();
        } else {
            showToast("❌ שגיאה בניקוי שגיאות: " + (data.error || "Unknown error"), "error");
        }
    } catch (err) {
        showToast("❌ שגיאה בניקוי שגיאות: " + err, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "🧹 נקה שגיאות (Clear Errors)";
    }
}
