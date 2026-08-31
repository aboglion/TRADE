import os
import json
import numpy as np
import pandas as pd
from engine import (load_real_data, add_indicators, run_backtest, calculate_metrics, BARS_PER_YEAR)
from main import BEST_CFGS, run_best

def build_dashboard_data():
    files = {
        'BTC': 'BTC_USD_4h.csv',
        'ETH': 'ETH_USD_4h.csv',
        'SOL': 'SOL_USD_4h.csv'
    }
    weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    capital = 1000.0

    assets_payload = {}
    eqs = {}
    bhs = {}

    for name, f in files.items():
        tr, eq, bh = run_best(f, capital * weights[name])
        eqs[name] = eq.rename(name)
        bhs[name] = bh.rename(name)

        # Load raw candles and downsample price to daily for clean chart rendering
        df_raw = load_real_data(f)
        price_daily = df_raw['Close'].resample('D').last().dropna()

        # Format trades into JSON objects
        trades_list = []
        if not tr.empty:
            for _, row in tr.iterrows():
                if pd.notna(row.get('exit_date')):
                    trades_list.append({
                        'entry_date': pd.to_datetime(row['entry_date']).strftime('%Y-%m-%d'),
                        'entry_px': round(float(row['entry']), 2),
                        'exit_date': pd.to_datetime(row['exit_date']).strftime('%Y-%m-%d'),
                        'exit_px': round(float(row['exit']), 2),
                        'return_pct': round(float(row['return_pct']), 2),
                        'pnl_usd': round(float(row['pnl_usd']), 2),
                        'mode': str(row.get('mode', 'STRATEGY')),
                        'reason': str(row.get('reason', 'exit')),
                        'bars_held': int(row.get('bars_held', 0))
                    })

        metrics = calculate_metrics(eq, tr, bh)

        # Equity daily downsampled
        eq_daily = eq.resample('D').last().dropna()
        bh_daily = bh.resample('D').last().dropna()

        assets_payload[name] = {
            'metrics': metrics,
            'price_dates': [d.strftime('%Y-%m-%d') for d in price_daily.index],
            'price_vals': [round(float(v), 2) for v in price_daily.values],
            'eq_vals': [round(float(v), 2) for v in eq_daily.values],
            'bh_vals': [round(float(v), 2) for v in bh_daily.values],
            'trades': trades_list
        }

    # Combined portfolio equity
    comb_eq = pd.concat(eqs.values(), axis=1).ffill()
    comb_bh = pd.concat(bhs.values(), axis=1).ffill()

    for n in weights:
        comb_eq[n] = comb_eq[n].fillna(capital * weights[n])
        comb_bh[n] = comb_bh[n].fillna(capital * weights[n])

    port_eq = comb_eq.sum(axis=1)
    port_bh = comb_bh.sum(axis=1)

    port_eq_daily = port_eq.resample('D').last().dropna()
    port_bh_daily = port_bh.resample('D').last().dropna()

    total_ret = (port_eq.iloc[-1] / capital - 1) * 100
    bh_ret = (port_bh.iloc[-1] / capital - 1) * 100

    years = len(port_eq) / BARS_PER_YEAR
    cagr = ((port_eq.iloc[-1] / capital) ** (1 / years) - 1) * 100
    bh_cagr = ((port_bh.iloc[-1] / capital) ** (1 / years) - 1) * 100

    port_dd = ((port_eq_daily - port_eq_daily.cummax()) / port_eq_daily.cummax()) * 100
    bh_dd = ((port_bh_daily - port_bh_daily.cummax()) / port_bh_daily.cummax()) * 100

    portfolio_payload = {
        'summary': {
            'strat_final': round(float(port_eq.iloc[-1]), 0),
            'strat_return': round(float(total_ret), 1),
            'strat_cagr': round(float(cagr), 1),
            'strat_max_dd': round(float(port_dd.min()), 1),

            'bh_final': round(float(port_bh.iloc[-1]), 0),
            'bh_return': round(float(bh_ret), 1),
            'bh_cagr': round(float(bh_cagr), 1),
            'bh_max_dd': round(float(bh_dd.min()), 1),
        },
        'dates': [d.strftime('%Y-%m-%d') for d in port_eq_daily.index],
        'strat_vals': [round(float(v), 2) for v in port_eq_daily.values],
        'bh_vals': [round(float(v), 2) for v in port_bh_daily.values],
    }

    return {
        'portfolio': portfolio_payload,
        'assets': assets_payload
    }

def generate_html_dashboard(data, output_filepath='dashboard.html'):
    json_str = json.dumps(data)

    html_content = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>השוואת אסטרטגיה מול Buy & Hold</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;700;900&family=Outfit:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/apexcharts"></script>
    <style>
        :root {{
            --bg-dark: #0b0f19;
            --card-bg: #141c2e;
            --card-border: rgba(255, 255, 255, 0.08);
            --green: #10b981;
            --red: #ef4444;
            --gold: #f59e0b;
            --blue: #3b82f6;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Heebo', sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px;
            direction: rtl;
        }}

        .container {{ max-width: 1400px; margin: 0 auto; }}

        /* Header & Tabs */
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 24px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            margin-bottom: 20px;
        }}

        .header h1 {{
            font-size: 1.6rem;
            font-weight: 800;
            color: #ffffff;
        }}

        .tabs {{
            display: flex;
            gap: 10px;
            background: rgba(0, 0, 0, 0.3);
            padding: 6px;
            border-radius: 12px;
            border: 1px solid var(--card-border);
        }}

        .tab-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 0.95rem;
            font-weight: 700;
            font-family: 'Heebo', sans-serif;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .tab-btn.active {{
            background: var(--blue);
            color: #ffffff;
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
        }}

        /* Date Filter Controls Bar */
        .filter-bar {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 16px 24px;
            border-radius: 16px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }}

        .filter-group {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}

        .filter-label {{
            font-size: 0.9rem;
            font-weight: 700;
            color: var(--text-muted);
        }}

        .preset-btn {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 7px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            font-family: 'Heebo', sans-serif;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .preset-btn:hover, .preset-btn.active {{
            background: rgba(59, 130, 246, 0.2);
            border-color: var(--blue);
            color: #ffffff;
        }}

        .date-input {{
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid var(--card-border);
            color: #ffffff;
            padding: 6px 12px;
            border-radius: 8px;
            font-family: 'Outfit', sans-serif;
            font-size: 0.88rem;
        }}

        .apply-btn {{
            background: var(--green);
            color: #ffffff;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            font-weight: 700;
            font-family: 'Heebo', sans-serif;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
            transition: all 0.2s ease;
        }}

        .apply-btn:hover {{
            opacity: 0.9;
        }}

        /* Metrics Row */
        .metrics-row {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }}

        @media (max-width: 900px) {{
            .metrics-row {{ grid-template-columns: repeat(2, 1fr); }}
            .filter-bar {{ flex-direction: column; align-items: stretch; }}
        }}

        .metric-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 20px;
            border-radius: 14px;
        }}

        .metric-title {{
            font-size: 0.88rem;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}

        .metric-val-main {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.9rem;
            font-weight: 800;
            color: var(--green);
        }}

        .metric-val-compare {{
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-top: 6px;
        }}

        .metric-val-compare strong {{
            color: var(--text-main);
        }}

        /* Chart Card */
        .chart-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 24px;
            border-radius: 16px;
            margin-bottom: 24px;
        }}

        .chart-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }}

        .chart-title {{
            font-size: 1.15rem;
            font-weight: 700;
        }}

        /* Trades Table */
        .table-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 24px;
            border-radius: 16px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
        }}

        th, td {{
            padding: 12px 16px;
            text-align: right;
            border-bottom: 1px solid var(--card-border);
            font-size: 0.92rem;
        }}

        th {{
            color: var(--text-muted);
            font-weight: 600;
        }}

        td {{
            font-family: 'Outfit', sans-serif;
            font-weight: 600;
        }}

        .pnl-badge {{
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 700;
            display: inline-block;
        }}

        .pnl-badge.win {{
            background: rgba(16, 185, 129, 0.15);
            color: var(--green);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}

        .pnl-badge.loss {{
            background: rgba(239, 68, 68, 0.15);
            color: var(--red);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}

        .badge-mode {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-muted);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.78rem;
        }}
    </style>
</head>
<body>

<div class="container">
    <!-- Header -->
    <div class="header">
        <div>
            <h1>📊 האסטרטגיה שלי מול Buy & Hold</h1>
            <div style="font-size: 0.88rem; color: var(--text-muted); margin-top: 4px;">סינון תקופה דינמי ותרשימי עסקאות אינטראקטיביים</div>
        </div>
        <div class="tabs">
            <button class="tab-btn active" onclick="selectTab('PORTFOLIO')">תיק השקעות כולל</button>
            <button class="tab-btn" onclick="selectTab('BTC')">Bitcoin (BTC)</button>
            <button class="tab-btn" onclick="selectTab('ETH')">Ethereum (ETH)</button>
            <button class="tab-btn" onclick="selectTab('SOL')">Solana (SOL)</button>
        </div>
    </div>

    <!-- Date Range Filter Bar -->
    <div class="filter-bar">
        <div class="filter-group">
            <span class="filter-label">📅 בחירת תקופה:</span>
            <button class="preset-btn active" id="btnAll" onclick="selectPreset('ALL')">כל התקופה</button>
            <button class="preset-btn" id="btn1Y" onclick="selectPreset('1Y')">שנה אחרונה (1Y)</button>
            <button class="preset-btn" id="btn2Y" onclick="selectPreset('2Y')">שנתיים (2Y)</button>
            <button class="preset-btn" id="btnBear" onclick="selectPreset('BEAR')">2021-2022 (Bear Market)</button>
            <button class="preset-btn" id="btnBull" onclick="selectPreset('BULL')">2023-2024 (Bull Run)</button>
        </div>
        <div class="filter-group">
            <span class="filter-label">מתאריך:</span>
            <input type="date" id="startDate" class="date-input">
            <span class="filter-label">עד תאריך:</span>
            <input type="date" id="endDate" class="date-input">
            <button class="apply-btn" onclick="applyCustomDates()">עדכן תקופה</button>
        </div>
    </div>

    <!-- Metrics Summary -->
    <div class="metrics-row">
        <div class="metric-card">
            <div class="metric-title">תשואה מצטברת לתקופה (Total Return)</div>
            <div class="metric-val-main" id="mTotalRet">+2,242%</div>
            <div class="metric-val-compare" id="mTotalRetBH">מול הולד רגיל: <strong>+1,503%</strong></div>
        </div>
        <div class="metric-card">
            <div class="metric-title">תשואה שנתית ממוצעת (CAGR)</div>
            <div class="metric-val-main" style="color: var(--gold);" id="mCagr">60.1%</div>
            <div class="metric-val-compare" id="mCagrBH">מול הולד רגיל: <strong>44.2%</strong></div>
        </div>
        <div class="metric-card">
            <div class="metric-title">נפילה מקסימלית לתקופה (Max DD)</div>
            <div class="metric-val-main" style="color: var(--green);" id="mMaxDD">-25.8%</div>
            <div class="metric-val-compare" id="mMaxDDBH">מול הולד רגיל: <strong style="color: var(--red);">-89.2%</strong></div>
        </div>
        <div class="metric-card">
            <div class="metric-title" id="mExtraTitle">אחוז עסקאות מרוויחות</div>
            <div class="metric-val-main" style="color: var(--blue);" id="mExtraVal">46.1%</div>
            <div class="metric-val-compare" id="mExtraSub">סה"כ 271 עסקאות מקטע</div>
        </div>
    </div>

    <!-- Main Chart Section -->
    <div class="chart-card">
        <div class="chart-header">
            <div class="chart-title" id="chartTitle">📈 גרף תשואה: האסטרטגיה שלי מול Buy & Hold</div>
            <div style="font-size: 0.85rem; color: var(--text-muted);" id="chartSubtitle">השוואת צמיחת הון לאורך התקופה הנבחרת</div>
        </div>
        <div id="mainChart" style="min-height: 420px;"></div>
    </div>

    <!-- Trades Section (Shown for BTC / ETH / SOL) -->
    <div class="table-card" id="tradesTableSection" style="display: none;">
        <div class="chart-header">
            <div class="chart-title">🏷️ יומן עסקאות מקטעים לתקופה הנבחרת</div>
            <div style="font-size: 0.85rem; color: var(--text-muted);" id="tradesCountLabel"></div>
        </div>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>תאריך קנייה</th>
                    <th>מחיר קנייה</th>
                    <th>תאריך מכירה</th>
                    <th>מחיר מכירה</th>
                    <th>סוג כניסה</th>
                    <th>סיבת יציאה</th>
                    <th>רווח / הפסד מקטע (%)</th>
                </tr>
            </thead>
            <tbody id="tradesTableBody">
                <!-- Trade rows -->
            </tbody>
        </table>
    </div>
</div>

<script>
    const dbData = {json_str};
    let currentTab = 'PORTFOLIO';
    let currentStartDate = null;
    let currentEndDate = null;
    let chartInstance = null;

    // Initialize default min and max dates from dataset
    const allDates = dbData.portfolio.dates;
    const minDate = allDates[0];
    const maxDate = allDates[allDates.length - 1];

    document.getElementById('startDate').value = minDate;
    document.getElementById('endDate').value = maxDate;
    currentStartDate = minDate;
    currentEndDate = maxDate;

    function selectTab(tab) {{
        currentTab = tab;
        document.querySelectorAll('.tab-btn').forEach(btn => {{
            btn.classList.toggle('active', btn.innerText.includes(tab) || (tab === 'PORTFOLIO' && btn.innerText.includes('כולל')));
        }});
        renderDashboard();
    }}

    function selectPreset(preset) {{
        document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));

        const maxD = new Date(maxDate);

        if (preset === 'ALL') {{
            document.getElementById('btnAll').classList.add('active');
            currentStartDate = minDate;
            currentEndDate = maxDate;
        }} else if (preset === '1Y') {{
            document.getElementById('btn1Y').classList.add('active');
            const d = new Date(maxD);
            d.setFullYear(d.getFullYear() - 1);
            currentStartDate = d.toISOString().split('T')[0];
            currentEndDate = maxDate;
        }} else if (preset === '2Y') {{
            document.getElementById('btn2Y').classList.add('active');
            const d = new Date(maxD);
            d.setFullYear(d.getFullYear() - 2);
            currentStartDate = d.toISOString().split('T')[0];
            currentEndDate = maxDate;
        }} else if (preset === 'BEAR') {{
            document.getElementById('btnBear').classList.add('active');
            currentStartDate = '2021-11-01';
            currentEndDate = '2022-12-31';
        }} else if (preset === 'BULL') {{
            document.getElementById('btnBull').classList.add('active');
            currentStartDate = '2023-01-01';
            currentEndDate = '2024-12-31';
        }}

        document.getElementById('startDate').value = currentStartDate;
        document.getElementById('endDate').value = currentEndDate;

        renderDashboard();
    }}

    function applyCustomDates() {{
        document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
        currentStartDate = document.getElementById('startDate').value;
        currentEndDate = document.getElementById('endDate').value;
        renderDashboard();
    }}

    function calculateMaxDD(series) {{
        if (!series || series.length === 0) return 0;
        let maxPeak = series[0];
        let maxDD = 0;
        for (let i = 0; i < series.length; i++) {{
            if (series[i] > maxPeak) maxPeak = series[i];
            const dd = (series[i] - maxPeak) / maxPeak;
            if (dd < maxDD) maxDD = dd;
        }}
        return Math.round(maxDD * 1000) / 10;
    }}

    function renderDashboard() {{
        if (chartInstance) {{
            chartInstance.destroy();
        }}

        const tradesTableSection = document.getElementById('tradesTableSection');

        if (currentTab === 'PORTFOLIO') {{
            const p = dbData.portfolio;

            // Find slice indices
            let startIdx = p.dates.findIndex(d => d >= currentStartDate);
            let endIdx = p.dates.findLastIndex(d => d <= currentEndDate);

            if (startIdx === -1) startIdx = 0;
            if (endIdx === -1 || endIdx <= startIdx) endIdx = p.dates.length - 1;

            const slicedDates = p.dates.slice(startIdx, endIdx + 1);
            const rawStratVals = p.strat_vals.slice(startIdx, endIdx + 1);
            const rawBhVals = p.bh_vals.slice(startIdx, endIdx + 1);

            // Normalize series to $1,000 start for selected period
            const baseStrat = rawStratVals[0] || 1;
            const baseBh = rawBhVals[0] || 1;

            const stratVals = rawStratVals.map(v => Math.round((v / baseStrat) * 1000));
            const bhVals = rawBhVals.map(v => Math.round((v / baseBh) * 1000));

            const totalRet = Math.round(((rawStratVals[rawStratVals.length - 1] / baseStrat) - 1) * 1000) / 10;
            const bhRet = Math.round(((rawBhVals[rawBhVals.length - 1] / baseBh) - 1) * 1000) / 10;

            const days = Math.max(1, (new Date(slicedDates[slicedDates.length - 1]) - new Date(slicedDates[0])) / (1000 * 60 * 60 * 24));
            const years = days / 365.25;

            const cagr = years > 0.2 ? (Math.pow(rawStratVals[rawStratVals.length - 1] / baseStrat, 1 / years) - 1) * 100 : totalRet;
            const bhCagr = years > 0.2 ? (Math.pow(rawBhVals[rawBhVals.length - 1] / baseBh, 1 / years) - 1) * 100 : bhRet;

            const maxDD = calculateMaxDD(rawStratVals);
            const bhMaxDD = calculateMaxDD(rawBhVals);

            document.getElementById('mTotalRet').innerText = `${{totalRet >= 0 ? '+' : ''}}${{totalRet.toLocaleString()}}%`;
            document.getElementById('mTotalRetBH').innerHTML = `מול הולד רגיל: <strong>${{bhRet >= 0 ? '+' : ''}}${{bhRet.toLocaleString()}}%</strong>`;

            document.getElementById('mCagr').innerText = `${{cagr.toFixed(1)}}%`;
            document.getElementById('mCagrBH').innerHTML = `מול הולד רגיל: <strong>${{bhCagr.toFixed(1)}}%</strong>`;

            document.getElementById('mMaxDD').innerText = `${{maxDD}}%`;
            document.getElementById('mMaxDDBH').innerHTML = `מול הולד רגיל: <strong style="color: var(--red);">${{bhMaxDD}}%</strong>`;

            document.getElementById('mExtraTitle').innerText = 'משקלי תיק מומנטום חכם';
            document.getElementById('mExtraVal').innerText = '40/30/30';
            document.getElementById('mExtraSub').innerText = `תקופה: ${{slicedDates[0]}} עד ${{slicedDates[slicedDates.length - 1]}}`;

            document.getElementById('chartTitle').innerText = `📈 גרף תשואת התיק ($1,000 Start) לתקופה (${{slicedDates[0]}} - ${{slicedDates[slicedDates.length - 1]}})`;
            document.getElementById('chartSubtitle').innerText = 'השוואת צמיחת התיק הכולל בלייב';

            tradesTableSection.style.display = 'none';

            const options = {{
                series: [
                    {{ name: 'האסטרטגיה שלי (Portfolio)', data: stratVals }},
                    {{ name: 'Buy & Hold רגיל', data: bhVals }}
                ],
                chart: {{
                    type: 'line',
                    height: 420,
                    toolbar: {{ show: true }},
                    background: 'transparent',
                    foreColor: '#94a3b8',
                    fontFamily: 'Outfit, sans-serif'
                }},
                colors: ['#10b981', '#64748b'],
                stroke: {{ curve: 'smooth', width: [3, 2] }},
                xaxis: {{ categories: slicedDates, type: 'datetime' }},
                yaxis: {{ labels: {{ formatter: v => '$' + Math.round(v).toLocaleString() }} }},
                tooltip: {{ theme: 'dark', x: {{ format: 'dd MMM yyyy' }}, y: {{ formatter: v => '$' + v.toLocaleString() }} }},
                grid: {{ borderColor: 'rgba(255, 255, 255, 0.06)' }},
                legend: {{ position: 'top', horizontalAlign: 'right' }}
            }};
            chartInstance = new ApexCharts(document.querySelector("#mainChart"), options);
            chartInstance.render();

        }} else {{
            const assetData = dbData.assets[currentTab];

            // Slice price data
            let startIdx = assetData.price_dates.findIndex(d => d >= currentStartDate);
            let endIdx = assetData.price_dates.findLastIndex(d => d <= currentEndDate);

            if (startIdx === -1) startIdx = 0;
            if (endIdx === -1 || endIdx <= startIdx) endIdx = assetData.price_dates.length - 1;

            const slicedDates = assetData.price_dates.slice(startIdx, endIdx + 1);
            const slicedPrice = assetData.price_vals.slice(startIdx, endIdx + 1);
            const slicedEq = assetData.eq_vals.slice(startIdx, endIdx + 1);
            const slicedBh = assetData.bh_vals.slice(startIdx, endIdx + 1);

            // Filter trades for period
            const filteredTrades = assetData.trades.filter(t => t.entry_date >= currentStartDate && t.exit_date <= currentEndDate);

            const baseEq = slicedEq[0] || 1;
            const baseBh = slicedBh[0] || 1;

            const totalRet = Math.round(((slicedEq[slicedEq.length - 1] / baseEq) - 1) * 1000) / 10;
            const bhRet = Math.round(((slicedBh[slicedBh.length - 1] / baseBh) - 1) * 1000) / 10;

            const days = Math.max(1, (new Date(slicedDates[slicedDates.length - 1]) - new Date(slicedDates[0])) / (1000 * 60 * 60 * 24));
            const years = days / 365.25;
            const cagr = years > 0.2 ? (Math.pow(slicedEq[slicedEq.length - 1] / baseEq, 1 / years) - 1) * 100 : totalRet;

            const maxDD = calculateMaxDD(slicedEq);
            const bhMaxDD = calculateMaxDD(slicedBh);

            const wins = filteredTrades.filter(t => t.return_pct > 0);
            const winRate = filteredTrades.length > 0 ? Math.round((wins.length / filteredTrades.length) * 1000) / 10 : 0;

            document.getElementById('mTotalRet').innerText = `${{totalRet >= 0 ? '+' : ''}}${{totalRet.toLocaleString()}}%`;
            document.getElementById('mTotalRetBH').innerHTML = `מול הולד רגיל: <strong>${{bhRet >= 0 ? '+' : ''}}${{bhRet.toLocaleString()}}%</strong>`;

            document.getElementById('mCagr').innerText = `${{cagr.toFixed(1)}}%`;
            document.getElementById('mCagrBH').innerHTML = `מול הולד רגיל: <strong>${{bhRet >= 0 ? '+' : ''}}${{bhRet.toLocaleString()}}%</strong>`;

            document.getElementById('mMaxDD').innerText = `${{maxDD}}%`;
            document.getElementById('mMaxDDBH').innerHTML = `מול הולד רגיל: <strong style="color: var(--red);">${{bhMaxDD}}%</strong>`;

            document.getElementById('mExtraTitle').innerText = 'עסקאות מרוויחות לתקופה';
            document.getElementById('mExtraVal').innerText = `${{winRate}}%`;
            document.getElementById('mExtraSub').innerText = `סה"כ ${{filteredTrades.length}} עסקאות מקטע בתקופה`;

            document.getElementById('chartTitle').innerText = `📊 גרף מחיר ${{currentTab}} עם סימוני עסקאות (${{slicedDates[0]}} - ${{slicedDates[slicedDates.length - 1]}})`;
            document.getElementById('chartSubtitle').innerText = '▲ קנייה | ▼ מכירה עם אחוז רווח/הפסד';

            // Point Annotations for filtered trades
            const pointAnnotations = [];
            filteredTrades.forEach(t => {{
                pointAnnotations.push({{
                    x: new Date(t.entry_date).getTime(),
                    y: t.entry_px,
                    marker: {{ size: 6, fillColor: '#10b981', strokeColor: '#ffffff', strokeWidth: 2 }},
                    label: {{
                        borderColor: '#10b981',
                        style: {{ color: '#fff', background: '#10b981', fontSize: '11px', fontWeight: 'bold' }},
                        text: `קנייה: $${{t.entry_px.toLocaleString()}}`
                    }}
                }});

                const isWin = t.return_pct >= 0;
                const badgeColor = isWin ? '#10b981' : '#ef4444';
                pointAnnotations.push({{
                    x: new Date(t.exit_date).getTime(),
                    y: t.exit_px,
                    marker: {{ size: 6, fillColor: badgeColor, strokeColor: '#ffffff', strokeWidth: 2 }},
                    label: {{
                        borderColor: badgeColor,
                        style: {{ color: '#fff', background: badgeColor, fontSize: '11px', fontWeight: 'bold' }},
                        text: `מכירה: $${{t.exit_px.toLocaleString()}} (${{isWin ? '+' : ''}}${{t.return_pct}}%)`
                    }}
                }});
            }});

            const options = {{
                series: [
                    {{ name: `מחיר ${{currentTab}} ($)`, data: slicedPrice }}
                ],
                chart: {{
                    type: 'line',
                    height: 450,
                    toolbar: {{ show: true }},
                    background: 'transparent',
                    foreColor: '#94a3b8',
                    fontFamily: 'Outfit, sans-serif'
                }},
                colors: ['#3b82f6'],
                stroke: {{ curve: 'smooth', width: 2 }},
                xaxis: {{ categories: slicedDates, type: 'datetime' }},
                yaxis: {{ labels: {{ formatter: v => '$' + Math.round(v).toLocaleString() }} }},
                annotations: {{
                    points: pointAnnotations
                }},
                tooltip: {{ theme: 'dark', x: {{ format: 'dd MMM yyyy' }}, y: {{ formatter: v => '$' + v.toLocaleString() }} }},
                grid: {{ borderColor: 'rgba(255, 255, 255, 0.06)' }}
            }};

            chartInstance = new ApexCharts(document.querySelector("#mainChart"), options);
            chartInstance.render();

            // Populate Trades Table for filtered period
            tradesTableSection.style.display = 'block';
            document.getElementById('tradesCountLabel').innerText = `סה"כ ${{filteredTrades.length}} עסקאות מקטע בתקופה הנבחרת`;

            const tbody = document.getElementById('tradesTableBody');
            tbody.innerHTML = '';
            filteredTrades.forEach((t, idx) => {{
                const isWin = t.return_pct >= 0;
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${{idx + 1}}</td>
                    <td>${{t.entry_date}}</td>
                    <td>$${{t.entry_px.toLocaleString()}}</td>
                    <td>${{t.exit_date}}</td>
                    <td>$${{t.exit_px.toLocaleString()}}</td>
                    <td><span class="badge-mode">${{t.mode}}</span></td>
                    <td><span class="badge-mode">${{t.reason}}</span></td>
                    <td>
                        <span class="pnl-badge ${{isWin ? 'win' : 'loss'}}">
                            ${{isWin ? '+' : ''}}${{t.return_pct}}% (${{t.pnl_usd >= 0 ? '+' : ''}}$${{t.pnl_usd.toLocaleString()}})
                        </span>
                    </td>
                `;
                tbody.appendChild(tr);
            }});
        }}
    }}

    // Initial render
    renderDashboard();
</script>

</body>
</html>
"""

    with open(output_filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"[INTERACTIVE DATE DASHBOARD GENERATED] {output_filepath}")

if __name__ == '__main__':
    print("⏳ Building interactive date dashboard data...")
    payload = build_dashboard_data()
    generate_html_dashboard(payload, 'dashboard.html')
