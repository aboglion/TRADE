/**
 * Dashboard Client App.
 *
 * Polls REST API endpoints every 5 seconds and updates the UI dynamically.
 */

document.addEventListener("DOMContentLoaded", () => {
    initClock();
    fetchDashboardData();
    setInterval(fetchDashboardData, 5000);

    // Event listeners with instant visual feedback
    document.getElementById("refreshBtn").addEventListener("click", manualRefresh);
    document.getElementById("triggerCycleBtn").addEventListener("click", triggerCycle);
    document.getElementById("killSwitchBtn").addEventListener("click", toggleKillSwitch);
});

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
    ]);
}

// ── Status & Regime ────────────────────────────────────────

async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
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
        const healthVal = document.getElementById("systemHealthVal");
        if (data.critical_errors_count > 0) {
            healthVal.textContent = `${data.critical_errors_count} ERRORS`;
            healthVal.className = "metric-value text-danger";
        } else {
            healthVal.textContent = "HEALTHY";
            healthVal.className = "metric-value text-success";
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
        const res = await fetch("/api/portfolio");
        if (!res.ok) return;
        const data = await res.json();

        // Total Portfolio Value
        document.getElementById("portfolioValue").textContent = `$${data.total_value_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

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

            const row = document.createElement("div");
            row.className = "alloc-row";
            row.innerHTML = `
                <div class="alloc-info">
                    <span>${symbol}</span>
                    <span>${h.total.toFixed(4)} ($${h.value_usd.toFixed(2)}) — <strong>${h.weight_pct}%</strong></span>
                </div>
                <div class="progress-bg">
                    <div class="progress-fill ${bgClass}" style="width: ${Math.min(100, Math.max(2, h.weight_pct))}%"></div>
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
        const res = await fetch("/api/orders");
        if (!res.ok) return;
        const data = await res.json();

        const tableBody = document.getElementById("ordersTableBody");
        tableBody.innerHTML = "";

        const allOrders = [...(data.pending || []), ...(data.completed || [])].reverse();
        document.getElementById("orderCount").textContent = allOrders.length;

        if (allOrders.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="6" class="empty-cell text-muted">No orders executed yet</td></tr>`;
            return;
        }

        allOrders.slice(0, 15).forEach(o => {
            const tr = document.createElement("tr");
            const sideClass = (o.side || "").toLowerCase() === "buy" ? "tag-buy" : "tag-sell";
            const priceStr = o.average_price ? `$${o.average_price.toFixed(2)}` : (o.price ? `$${o.price.toFixed(2)}` : "MARKET");

            tr.innerHTML = `
                <td><code>${(o.client_order_id || o.exchange_order_id || "N/A").substring(0, 18)}</code></td>
                <td><span class="tag ${sideClass}">${(o.side || "BUY").toUpperCase()}</span></td>
                <td><strong>${o.symbol}</strong></td>
                <td>${o.amount || o.filled_amount || 0}</td>
                <td>${priceStr}</td>
                <td><span class="tag tag-filled">${(o.status || "FILLED").toUpperCase()}</span></td>
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
        const res = await fetch("/api/logs");
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
        const res = await fetch("/api/trigger", { method: "POST" });
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
    if (!confirm("האם אתה בטוח שברצונך לשנות את מצב עצירת החירום (Kill Switch)?")) return;
    try {
        const res = await fetch("/api/killswitch", { method: "POST" });
        const data = await res.json();
        await fetchStatus();
        const statusMsg = data.kill_switch ? "⚠️ Kill Switch הופעל! פקודות חסומות." : "🛡️ Kill Switch כובה. מסחר פעיל.";
        showToast(statusMsg, data.kill_switch ? "error" : "info");
    } catch (err) {
        showToast("❌ שגיאה בשינוי Kill Switch: " + err, "error");
    }
}
