#!/usr/bin/env python3
"""
Sarkar Dashboard Generator
------------------------------
Reads the local DuckDB database and generates dashboard.html — a
self-contained, interactive dashboard you open in any browser.

Run any time after fetching fresh data:
    python3 generate_dashboard.py

This does NOT hit the Meta API — it only reads what's already in DuckDB.
Run fetch_meta_ads.py first if you want fresh numbers.
"""

import os
import json
from datetime import datetime, date

import duckdb
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get(
    "SARKAR_DB_PATH", os.path.expanduser("~/sarkar reports/sarkar.duckdb")
)
OUTPUT_PATH = os.environ.get("DASHBOARD_OUTPUT_PATH", "dashboard.html")

# Optional friendly names for your two ad accounts, e.g. "act_111=Sarkar,act_222=Sarkar 2"
# If not set, the raw account ID is shown instead.
ACCOUNT_LABELS_RAW = os.environ.get("META_ACCOUNT_LABELS", "")


def parse_account_labels():
    labels = {}
    if ACCOUNT_LABELS_RAW:
        for pair in ACCOUNT_LABELS_RAW.split(","):
            if "=" in pair:
                acct_id, label = pair.split("=", 1)
                labels[acct_id.strip()] = label.strip()
    return labels


def compute_rates(t):
    """Given a totals dict, compute the derived funnel rates the sheet tracks."""
    def safe_div(a, b):
        return round(a / b, 6) if b else 0

    t["ctr"] = safe_div(t["clicks"], t["impressions"])
    t["lpv_per_click"] = safe_div(t["landing_page_views"], t["clicks"])
    t["atc_per_content_view"] = safe_div(t["adds_to_cart"], t["content_views"])
    t["checkout_per_atc"] = safe_div(t["checkouts_initiated"], t["adds_to_cart"])
    t["payment_per_checkout"] = safe_div(t["payment_info_added"], t["checkouts_initiated"])
    t["purchase_per_payment"] = safe_div(t["purchases"], t["payment_info_added"])
    t["cac"] = safe_div(t["spend"], t["purchases"])
    t["roas"] = safe_div(t["revenue"], t["spend"])
    t["aov"] = safe_div(t["revenue"], t["purchases"])
    return t


def zero_totals():
    return {
        "spend": 0.0, "impressions": 0, "reach": 0, "clicks": 0,
        "landing_page_views": 0, "content_views": 0, "adds_to_cart": 0,
        "checkouts_initiated": 0, "payment_info_added": 0, "purchases": 0,
        "revenue": 0.0,
    }


def sum_into(totals, row):
    for k in ["spend", "impressions", "reach", "clicks", "landing_page_views",
              "content_views", "adds_to_cart", "checkouts_initiated",
              "payment_info_added", "purchases", "revenue"]:
        totals[k] += row[k]


GST_RATE = 0.18  # revenue is reported inclusive of 18% GST; ROAS/AOV should be based on ex-GST revenue


def build_data():
    con = duckdb.connect(DB_PATH, read_only=True)

    rows = con.execute("""
        SELECT ad_account_id, date, spend, impressions, reach, clicks,
               landing_page_views, content_views, adds_to_cart,
               checkouts_initiated, payment_info_added, purchases, revenue
        FROM meta_daily_funnel
        ORDER BY date
    """).fetchall()
    cols = [d[0] for d in con.description]
    con.close()

    records = [dict(zip(cols, r)) for r in rows]
    for r in records:
        r["date"] = r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"])
        r["revenue"] = r["revenue"] * (1 - GST_RATE)  # store ex-GST from here on, everything downstream uses this

    labels = parse_account_labels()
    account_ids = sorted(set(r["ad_account_id"] for r in records))

    # Per-account daily series + totals
    accounts = {}
    for acct_id in account_ids:
        acct_rows = [r for r in records if r["ad_account_id"] == acct_id]
        totals = zero_totals()
        for r in acct_rows:
            sum_into(totals, r)
        accounts[acct_id] = {
            "label": labels.get(acct_id, acct_id),
            "daily": sorted(acct_rows, key=lambda x: x["date"]),
            "totals": compute_rates(totals),
        }

    # Blended (all accounts combined) daily series + totals
    blended_by_date = {}
    for r in records:
        d = r["date"]
        if d not in blended_by_date:
            blended_by_date[d] = zero_totals()
            blended_by_date[d]["date"] = d
        sum_into(blended_by_date[d], r)

    blended_daily = sorted(blended_by_date.values(), key=lambda x: x["date"])
    blended_totals = zero_totals()
    for r in records:
        sum_into(blended_totals, r)
    blended_totals = compute_rates(blended_totals)

    date_range = [records[0]["date"], records[-1]["date"]] if records else [None, None]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date_range": date_range,
        "accounts": accounts,
        "blended": {"daily": blended_daily, "totals": blended_totals},
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Sarkar — Funnel Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #F6F5F1;
    --bg-panel: #FFFFFF;
    --bg-tile: #FFFFFF;
    --border: #E1DED4;
    --navy: #1F3864;
    --navy-soft: #E9EDF4;
    --accent: #B5651D;
    --text: #26241E;
    --text-dim: #6B6656;
    --text-faint: #9A9686;
    --good: #3F7A44;
    --bad: #B5453A;
    --row-alt: #F5F6F8;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    margin: 0;
    padding: 36px 32px 80px;
  }
  .wrap { max-width: 1220px; margin: 0 auto; }
  .masthead { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 4px; flex-wrap: wrap; gap: 16px; }
  h1 {
    font-family: 'Fraunces', serif;
    font-weight: 500;
    font-size: 30px;
    letter-spacing: -0.01em;
    margin: 0;
    color: var(--navy);
  }
  .subtitle { color: var(--text-dim); font-size: 13.5px; margin-top: 6px; }
  .meta { color: var(--text-faint); font-size: 11.5px; text-align: right; }

  .filterbar {
    display: flex;
    align-items: center;
    gap: 22px;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 20px;
    margin: 24px 0 24px;
    flex-wrap: wrap;
  }
  .filter-group { display: flex; align-items: center; gap: 8px; }
  .filter-group label { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); font-weight: 600; }
  input[type="date"], select {
    background: #FBFAF7;
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    font-size: 13px;
    padding: 7px 10px;
    border-radius: 6px;
  }
  select { cursor: pointer; min-width: 170px; }

  .grid-main { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; align-items: stretch; }
  .panel {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 22px;
  }
  .panel h2 {
    font-family: 'Fraunces', serif;
    font-weight: 500;
    font-size: 16px;
    margin: 0 0 4px;
    color: var(--navy);
  }
  .panel .panel-sub { font-size: 11.5px; color: var(--text-faint); margin-bottom: 14px; }

  .panel-chart { display: flex; flex-direction: column; height: 100%; }
  .panel-chart .chart-wrap { flex: 1; position: relative; min-height: 240px; }
  .panel-chart canvas { max-height: none !important; }

  .funnel-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; margin-bottom: 16px; }
  .funnel-chart-card { background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .funnel-chart-card h3 { font-size: 12.5px; font-weight: 600; margin: 0 0 10px; color: var(--navy); }
  .funnel-chart-card canvas { max-height: 150px; }

  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }

  .toggle-btn {
    background: var(--navy-soft);
    border: 1px solid var(--border);
    color: var(--navy);
    font-family: 'Inter', sans-serif;
    font-size: 12px;
    font-weight: 600;
    padding: 7px 14px;
    border-radius: 6px;
    cursor: pointer;
    margin-bottom: 14px;
  }
  .toggle-btn:hover { background: #DCE3EF; }

  .counts-col { display: none; }
  body.show-counts .counts-col { display: table-cell; }
  thead th {
    background: var(--navy);
    color: #fff;
    text-align: right;
    font-weight: 600;
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 9px 10px;
  }
  th:first-child, td:first-child { text-align: left; }
  td { text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
  tbody tr:nth-child(even) { background: var(--row-alt); }
  tbody tr:last-child td { border-bottom: none; }
  .roas-good { color: var(--good); font-weight: 600; }
  .roas-bad { color: var(--bad); font-weight: 600; }

  canvas { max-height: 260px; }

  footer { margin-top: 32px; color: var(--text-faint); font-size: 11px; line-height: 1.6; }

  @media (max-width: 980px) {
    .grid-main { grid-template-columns: 1fr; }
    .funnel-grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="wrap">

  <div class="masthead">
    <div>
      <h1>Sarkar — Funnel Dashboard</h1>
      <div class="subtitle" id="subtitle"></div>
    </div>
    <div class="meta" id="generated-meta"></div>
  </div>

  <div class="filterbar">
    <div class="filter-group">
      <label>Account</label>
      <select id="accountSelect"></select>
    </div>
    <div class="filter-group">
      <label>From</label>
      <input type="date" id="startDate">
    </div>
    <div class="filter-group">
      <label>To</label>
      <input type="date" id="endDate">
    </div>
  </div>

  <div class="grid-main">
    <div class="panel panel-chart">
      <h2>Spend &amp; ROAS — Daily</h2>
      <div class="panel-sub">Bars: spend (₹) &middot; Line: ROAS</div>
      <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
    </div>
    <div class="panel panel-chart">
      <h2>Revenue Trend</h2>
      <div class="panel-sub">Revenue = Spend &times; ROAS</div>
      <div class="chart-wrap"><canvas id="revenueChart"></canvas></div>
    </div>
  </div>

  <div class="panel" style="margin-bottom:16px;">
    <h2>Daily Breakdown</h2>
    <button id="toggleCounts" class="toggle-btn">Show raw counts (Clicks, Content Views, ATC, Checkouts, Payment Info)</button>
    <div style="overflow-x:auto;">
      <table id="dailyTable">
        <thead>
          <tr>
            <th>Date</th><th>Spend</th><th>Impr.</th><th>CPM</th>
            <th class="counts-col">Clicks</th>
            <th>CTR</th>
            <th>LPV / Click %</th>
            <th class="counts-col">Content Views</th>
            <th class="counts-col">ATC</th>
            <th>ATC / CV %</th>
            <th class="counts-col">Checkouts</th>
            <th>Checkout / ATC %</th>
            <th class="counts-col">Payment Info</th>
            <th>Payment / Checkout %</th>
            <th>Purchases</th>
            <th>Purchase / Payment %</th>
            <th>Revenue</th><th>ROAS</th><th>CAC</th><th>AOV</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Funnel Conversion Rates — Daily Trend</h2>
    <div class="panel-sub">Same seven ratios as your original sheet, tracked day by day</div>
    <div class="funnel-grid">
      <div class="funnel-chart-card"><h3>CTR % (Clicks / Impressions)</h3><canvas id="chartCTR"></canvas></div>
      <div class="funnel-chart-card"><h3>LPV / Click %</h3><canvas id="chartLPV"></canvas></div>
      <div class="funnel-chart-card"><h3>ATC / Content View %</h3><canvas id="chartATC"></canvas></div>
      <div class="funnel-chart-card"><h3>Checkout / ATC %</h3><canvas id="chartCheckout"></canvas></div>
      <div class="funnel-chart-card"><h3>Payment Info / Checkout %</h3><canvas id="chartPayment"></canvas></div>
      <div class="funnel-chart-card"><h3>Purchase / Payment Info %</h3><canvas id="chartPurchase"></canvas></div>
      <div class="funnel-chart-card"><h3>CAC (INR) — Cost per Purchase</h3><canvas id="chartCAC"></canvas></div>
    </div>
  </div>

  <footer id="footer-note"></footer>

</div>

<script>
const DATA = __DATA_JSON__;

const fmtINR = n => '₹' + Math.round(n).toLocaleString('en-IN');
const fmtNum = n => Math.round(n).toLocaleString('en-IN');
const fmtPct = n => (n * 100).toFixed(1) + '%';
const fmtX = n => n.toFixed(2) + 'x';

let trendChart, revenueChart;
const miniCharts = {};

function baseDataset() {
  const sel = document.getElementById('accountSelect').value;
  return sel === 'blended' ? DATA.blended : DATA.accounts[sel];
}

function filteredDaily() {
  const start = document.getElementById('startDate').value;
  const end = document.getElementById('endDate').value;
  const daily = baseDataset().daily;
  return daily.filter(d => (!start || d.date >= start) && (!end || d.date <= end));
}

function computeTotals(rows) {
  const t = {
    spend: 0, impressions: 0, reach: 0, clicks: 0, landing_page_views: 0,
    content_views: 0, adds_to_cart: 0, checkouts_initiated: 0,
    payment_info_added: 0, purchases: 0, revenue: 0,
  };
  rows.forEach(r => {
    Object.keys(t).forEach(k => t[k] += r[k]);
  });
  const safeDiv = (a, b) => b ? a / b : 0;
  t.ctr = safeDiv(t.clicks, t.impressions);
  t.atc_per_content_view = safeDiv(t.adds_to_cart, t.content_views);
  t.checkout_per_atc = safeDiv(t.checkouts_initiated, t.adds_to_cart);
  t.payment_per_checkout = safeDiv(t.payment_info_added, t.checkouts_initiated);
  t.purchase_per_payment = safeDiv(t.purchases, t.payment_info_added);
  t.cac = safeDiv(t.spend, t.purchases);
  t.roas = safeDiv(t.revenue, t.spend);
  t.aov = safeDiv(t.revenue, t.purchases);
  return t;
}

function renderMeta() {
  document.getElementById('generated-meta').textContent = 'Updated ' + DATA.generated_at.replace('T', ' ');
  document.getElementById('footer-note').innerHTML =
    'Generated ' + DATA.generated_at.replace('T', ' ') + ' from local pipeline data. ' +
    'Formulas: CTR = Clicks / Impressions &middot; ATC / Content View = Adds to Cart / Content Views &middot; ' +
    'Checkout / ATC = Checkouts Initiated / Adds to Cart &middot; Payment Info / Checkout = Payment Info Added / Checkouts Initiated &middot; ' +
    'Purchase / Payment Info = Purchases / Payment Info Added &middot; CAC = Spend / Purchases &middot; ROAS = Revenue / Spend &middot; AOV = Revenue / Purchases. ' +
    'Revenue shown throughout excludes 18% GST (Revenue &times; 82%), so ROAS and AOV are based on ex-GST revenue. ' +
    'Totals are computed over the selected range (total/total), not an average of daily percentages.' +
    (DATA.preview_note ? ' <strong>' + DATA.preview_note + '</strong>' : '');
}

function renderAccountSelect() {
  const sel = document.getElementById('accountSelect');
  sel.innerHTML = '';
  const blendedOpt = document.createElement('option');
  blendedOpt.value = 'blended';
  blendedOpt.textContent = 'All accounts (Blended)';
  sel.appendChild(blendedOpt);
  Object.entries(DATA.accounts).forEach(([id, acc]) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = acc.label;
    sel.appendChild(opt);
  });
}

function initDateRange() {
  const all = DATA.blended.daily;
  document.getElementById('startDate').value = all[0].date;
  document.getElementById('endDate').value = all[all.length - 1].date;
  document.getElementById('startDate').min = all[0].date;
  document.getElementById('endDate').max = all[all.length - 1].date;
}


const NAVY = '#1F3864';
const ACCENT = '#B5651D';
const GOOD = '#3F7A44';

function destroyIfExists(chart) { if (chart) chart.destroy(); }

function renderTrendChart(rows) {
  const labels = rows.map(d => d.date.slice(5));
  const spend = rows.map(d => d.spend);
  const roas = rows.map(d => d.spend ? d.revenue / d.spend : 0);

  destroyIfExists(trendChart);
  trendChart = new Chart(document.getElementById('trendChart').getContext('2d'), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Spend (₹)', data: spend, backgroundColor: 'rgba(31, 56, 100, 0.55)', borderRadius: 4, yAxisID: 'y' },
        { type: 'line', label: 'ROAS', data: roas, borderColor: ACCENT, backgroundColor: ACCENT, tension: 0.3, yAxisID: 'y1', pointRadius: 3 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#6B6656', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#9A9686', font: { size: 10 } }, grid: { display: false } },
        y: { position: 'left', ticks: { color: '#9A9686', font: { size: 10 } }, grid: { color: '#EDEBE3' } },
        y1: { position: 'right', ticks: { color: '#9A9686', font: { size: 10 } }, grid: { display: false } },
      },
    },
  });
}

function renderRevenueChart(rows) {
  const labels = rows.map(d => d.date.slice(5));
  const revenue = rows.map(d => d.revenue);

  destroyIfExists(revenueChart);
  revenueChart = new Chart(document.getElementById('revenueChart').getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Revenue (₹)', data: revenue, borderColor: GOOD,
        backgroundColor: 'rgba(63, 122, 68, 0.12)', fill: true, tension: 0.3, pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#9A9686', font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { color: '#9A9686', font: { size: 10 } }, grid: { color: '#EDEBE3' } },
      },
    },
  });
}

function miniLineConfig(labels, data, color, isPercent) {
  return {
    type: 'line',
    data: { labels, datasets: [{ data, borderColor: color, backgroundColor: color, tension: 0.3, pointRadius: 2, borderWidth: 2 }] },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#9A9686', font: { size: 9 } }, grid: { display: false } },
        y: {
          ticks: {
            color: '#9A9686', font: { size: 9 },
            callback: v => isPercent ? (v * 100).toFixed(0) + '%' : v,
          },
          grid: { color: '#EDEBE3' },
        },
      },
    },
  };
}

function renderFunnelTrendCharts(rows) {
  const labels = rows.map(d => d.date.slice(5));
  const safeDiv = (a, b) => b ? a / b : 0;

  const series = {
    chartCTR: rows.map(d => safeDiv(d.clicks, d.impressions)),
    chartLPV: rows.map(d => safeDiv(d.landing_page_views, d.clicks)),
    chartATC: rows.map(d => safeDiv(d.adds_to_cart, d.content_views)),
    chartCheckout: rows.map(d => safeDiv(d.checkouts_initiated, d.adds_to_cart)),
    chartPayment: rows.map(d => safeDiv(d.payment_info_added, d.checkouts_initiated)),
    chartPurchase: rows.map(d => safeDiv(d.purchases, d.payment_info_added)),
    chartCAC: rows.map(d => safeDiv(d.spend, d.purchases)),
  };

  Object.entries(series).forEach(([id, data]) => {
    destroyIfExists(miniCharts[id]);
    const isPercent = id !== 'chartCAC';
    miniCharts[id] = new Chart(
      document.getElementById(id).getContext('2d'),
      miniLineConfig(labels, data, NAVY, isPercent)
    );
  });
}

function renderTable(rows) {
  const tbody = document.querySelector('#dailyTable tbody');
  const safeDiv = (a, b) => b ? a / b : 0;
  tbody.innerHTML = rows.map(d => {
    const roas = safeDiv(d.revenue, d.spend);
    const cac = safeDiv(d.spend, d.purchases);
    const aov = safeDiv(d.revenue, d.purchases);
    const ctr = safeDiv(d.clicks, d.impressions);
    const cpm = safeDiv(d.spend, d.impressions) * 1000;
    const lpvPerClick = safeDiv(d.landing_page_views, d.clicks);
    const atcPerCv = safeDiv(d.adds_to_cart, d.content_views);
    const checkoutPerAtc = safeDiv(d.checkouts_initiated, d.adds_to_cart);
    const paymentPerCheckout = safeDiv(d.payment_info_added, d.checkouts_initiated);
    const purchasePerPayment = safeDiv(d.purchases, d.payment_info_added);
    const roasClass = roas >= 4 ? 'roas-good' : (roas < 2 ? 'roas-bad' : '');
    return `<tr>
      <td>${d.date}</td>
      <td>${fmtINR(d.spend)}</td>
      <td>${fmtNum(d.impressions)}</td>
      <td>${fmtINR(cpm)}</td>
      <td class="counts-col">${fmtNum(d.clicks)}</td>
      <td>${fmtPct(ctr)}</td>
      <td>${fmtPct(lpvPerClick)}</td>
      <td class="counts-col">${fmtNum(d.content_views)}</td>
      <td class="counts-col">${fmtNum(d.adds_to_cart)}</td>
      <td>${fmtPct(atcPerCv)}</td>
      <td class="counts-col">${fmtNum(d.checkouts_initiated)}</td>
      <td>${fmtPct(checkoutPerAtc)}</td>
      <td class="counts-col">${fmtNum(d.payment_info_added)}</td>
      <td>${fmtPct(paymentPerCheckout)}</td>
      <td>${fmtNum(d.purchases)}</td>
      <td>${fmtPct(purchasePerPayment)}</td>
      <td>${fmtINR(d.revenue)}</td>
      <td class="${roasClass}">${fmtX(roas)}</td>
      <td>${fmtINR(cac)}</td>
      <td>${fmtINR(aov)}</td>
    </tr>`;
  }).join('');
}

document.getElementById('toggleCounts').addEventListener('click', () => {
  document.body.classList.toggle('show-counts');
  const btn = document.getElementById('toggleCounts');
  btn.textContent = document.body.classList.contains('show-counts')
    ? 'Hide raw counts'
    : 'Show raw counts (Clicks, Content Views, ATC, Checkouts, Payment Info)';
});

function renderAll() {
  const rows = filteredDaily();
  const start = rows.length ? rows[0].date : DATA.date_range[0];
  const end = rows.length ? rows[rows.length - 1].date : DATA.date_range[1];
  document.getElementById('subtitle').textContent =
    `Daily paid social funnel performance — ${start} to ${end}`;

  renderTrendChart(rows);
  renderRevenueChart(rows);
  renderFunnelTrendCharts(rows);
  renderTable(rows);
}

document.getElementById('accountSelect').addEventListener('change', renderAll);
document.getElementById('startDate').addEventListener('change', renderAll);
document.getElementById('endDate').addEventListener('change', renderAll);

renderMeta();
renderAccountSelect();
initDateRange();
renderAll();
</script>
</body>
</html>
"""

def main():
    print(f"Reading data from {DB_PATH} ...")
    data = build_data()

    if not data["accounts"]:
        print("No data found in meta_daily_funnel. Run fetch_meta_ads.py first.")
        return

    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(data))

    with open(OUTPUT_PATH, "w") as f:
        f.write(html)

    print(f"Dashboard written to {os.path.abspath(OUTPUT_PATH)}")
    print("Open it by double-clicking the file, or run: open dashboard.html")


if __name__ == "__main__":
    main()
