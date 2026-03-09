"""Civ stats web UI — serves a sortable table of civ_elo_stats.csv."""

import csv
import json
import os
from aiohttp import web

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

HTML_PAGE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Namma PUB — Civ Stats</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300&display=swap');

:root {
  --parchment: #f4e8c1;
  --parchment-dark: #e8d5a3;
  --ink: #2c1810;
  --ink-light: #5c4033;
  --accent: #8b1a1a;
  --accent-glow: #c0392b;
  --gold: #b8860b;
  --gold-light: #daa520;
  --border: #c4a86b;
  --header-bg: #3b2314;
  --row-hover: rgba(184, 134, 11, 0.12);
  --row-alt: rgba(139, 69, 19, 0.04);
  --shadow: rgba(44, 24, 16, 0.15);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: 'Crimson Pro', Georgia, serif;
  background: var(--parchment);
  color: var(--ink);
  min-height: 100vh;
  background-image:
    radial-gradient(ellipse at 20% 50%, rgba(184, 134, 11, 0.06) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(139, 26, 26, 0.04) 0%, transparent 50%),
    url("data:image/svg+xml,%3Csvg width='60' height='60' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
}

.container {
  max-width: 1400px;
  margin: 0 auto;
  padding: 2rem 1.5rem 3rem;
}

header {
  text-align: center;
  margin-bottom: 2.5rem;
  padding-bottom: 1.5rem;
  border-bottom: 2px solid var(--border);
  position: relative;
}

header::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 50%;
  transform: translateX(-50%);
  width: 60px;
  height: 2px;
  background: var(--gold);
}

h1 {
  font-family: 'Cinzel', serif;
  font-weight: 700;
  font-size: 2.2rem;
  color: var(--ink);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 0.3rem;
}

.subtitle {
  font-family: 'Crimson Pro', serif;
  font-style: italic;
  font-weight: 300;
  font-size: 1.1rem;
  color: var(--ink-light);
  letter-spacing: 0.02em;
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 3px;
  box-shadow: 0 4px 20px var(--shadow), 0 1px 3px var(--shadow);
  background: rgba(255, 255, 255, 0.35);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
  min-width: 900px;
}

thead {
  position: sticky;
  top: 0;
  z-index: 10;
}

th {
  background: var(--header-bg);
  color: var(--parchment);
  font-family: 'Cinzel', serif;
  font-weight: 600;
  font-size: 0.72rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 0.85rem 0.7rem;
  text-align: left;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  border-bottom: 2px solid var(--gold);
  transition: background 0.2s;
  position: relative;
}

th:hover {
  background: #4d2e1a;
}

th .sort-arrow {
  display: inline-block;
  margin-left: 4px;
  font-size: 0.65rem;
  opacity: 0.4;
  transition: opacity 0.2s, transform 0.2s;
}

th.sort-asc .sort-arrow,
th.sort-desc .sort-arrow {
  opacity: 1;
  color: var(--gold-light);
}

th.sort-desc .sort-arrow {
  transform: rotate(180deg);
}

/* Column group borders */
th:nth-child(1) { padding-left: 1rem; }
th:nth-child(4), th:nth-child(6), th:nth-child(8), th:nth-child(10) {
  border-left: 1px solid rgba(196, 168, 107, 0.3);
}

td {
  padding: 0.6rem 0.7rem;
  border-bottom: 1px solid rgba(196, 168, 107, 0.25);
  transition: background 0.15s;
}

td:nth-child(1) {
  padding-left: 1rem;
  font-family: 'Cinzel', serif;
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--ink);
  letter-spacing: 0.03em;
}

td:nth-child(4), td:nth-child(6), td:nth-child(8), td:nth-child(10) {
  border-left: 1px solid rgba(196, 168, 107, 0.15);
}

tr:nth-child(even) td {
  background: var(--row-alt);
}

tr:hover td {
  background: var(--row-hover);
}

/* Winrate coloring */
td.wr { font-weight: 600; }
td.wr-high { color: #1a6b1a; }
td.wr-mid { color: var(--ink-light); }
td.wr-low { color: var(--accent); }

td.games {
  color: var(--ink-light);
  font-size: 0.9rem;
}

.col-group-label {
  text-align: center;
  background: #2c1810;
  color: var(--gold-light);
  font-family: 'Cinzel', serif;
  font-size: 0.68rem;
  letter-spacing: 0.12em;
  padding: 0.5rem 0.5rem;
  border-bottom: 1px solid rgba(196, 168, 107, 0.2);
}

.loading {
  text-align: center;
  padding: 4rem;
  font-style: italic;
  color: var(--ink-light);
  font-size: 1.1rem;
}

.error {
  text-align: center;
  padding: 2rem;
  color: var(--accent);
  font-weight: 600;
}

footer {
  text-align: center;
  margin-top: 2rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  font-size: 0.85rem;
  color: var(--ink-light);
  font-style: italic;
}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Civilization Statistics</h1>
    <div class="subtitle">Namma PUB — Pickup Game Performance Analysis</div>
  </header>

  <div class="table-wrap">
    <table id="stats-table">
      <thead>
        <tr class="col-group-row">
          <th class="col-group-label" colspan="3">Overall</th>
          <th class="col-group-label" colspan="2">Player Elo ≥ 1000</th>
          <th class="col-group-label" colspan="2">Player Elo &lt; 1000</th>
          <th class="col-group-label" colspan="2">Team Avg ≥ 1000</th>
          <th class="col-group-label" colspan="2">Team Avg &lt; 1000</th>
        </tr>
        <tr id="header-row"></tr>
      </thead>
      <tbody id="table-body">
        <tr><td colspan="11" class="loading">Loading statistics...</td></tr>
      </tbody>
    </table>
  </div>

  <footer>Data sourced from PUB bot matches &amp; AoE2 Companion API</footer>
</div>

<script>
const COLUMNS = [
  { key: 'civ', label: 'Civilization', type: 'string' },
  { key: 'games', label: 'Games', type: 'number' },
  { key: 'winrate', label: 'Win %', type: 'number' },
  { key: 'games_player_above', label: 'Games', type: 'number' },
  { key: 'winrate_player_above', label: 'Win %', type: 'number' },
  { key: 'games_player_below', label: 'Games', type: 'number' },
  { key: 'winrate_player_below', label: 'Win %', type: 'number' },
  { key: 'games_team_above', label: 'Games', type: 'number' },
  { key: 'winrate_team_above', label: 'Win %', type: 'number' },
  { key: 'games_team_below', label: 'Games', type: 'number' },
  { key: 'winrate_team_below', label: 'Win %', type: 'number' },
];

let data = [];
let sortCol = 'civ';
let sortAsc = true;

function wrClass(v) {
  if (v >= 0.55) return 'wr wr-high';
  if (v >= 0.45) return 'wr wr-mid';
  return 'wr wr-low';
}

function fmtWr(v) {
  return (v * 100).toFixed(1) + '%';
}

function buildHeaders() {
  const row = document.getElementById('header-row');
  row.innerHTML = '';
  COLUMNS.forEach(col => {
    const th = document.createElement('th');
    th.textContent = col.label;
    const arrow = document.createElement('span');
    arrow.className = 'sort-arrow';
    arrow.textContent = '\\u25B2';
    th.appendChild(arrow);
    th.addEventListener('click', () => sortBy(col.key));
    th.dataset.col = col.key;
    row.appendChild(th);
  });
  updateSortIndicators();
}

function updateSortIndicators() {
  document.querySelectorAll('#header-row th').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.col === sortCol) {
      th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
    }
  });
}

function sortBy(col) {
  if (sortCol === col) {
    sortAsc = !sortAsc;
  } else {
    sortCol = col;
    sortAsc = col === 'civ';
  }
  updateSortIndicators();
  renderTable();
}

function renderTable() {
  const tbody = document.getElementById('table-body');
  const colDef = COLUMNS.find(c => c.key === sortCol);
  const sorted = [...data].sort((a, b) => {
    let va = a[sortCol], vb = b[sortCol];
    if (colDef.type === 'string') {
      va = (va || '').toLowerCase();
      vb = (vb || '').toLowerCase();
      return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    return sortAsc ? va - vb : vb - va;
  });

  tbody.innerHTML = sorted.map(row => {
    const cells = COLUMNS.map(col => {
      const v = row[col.key];
      if (col.key === 'civ') return '<td>' + v + '</td>';
      if (col.key.startsWith('winrate')) return '<td class="' + wrClass(v) + '">' + fmtWr(v) + '</td>';
      return '<td class="games">' + v + '</td>';
    }).join('');
    return '<tr>' + cells + '</tr>';
  }).join('');
}

async function init() {
  try {
    const resp = await fetch('/api/civ-stats');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();
    buildHeaders();
    renderTable();
  } catch (e) {
    document.getElementById('table-body').innerHTML =
      '<tr><td colspan="11" class="error">Failed to load data: ' + e.message + '</td></tr>';
  }
}

init();
</script>
</body>
</html>'''


async def handle_index(request):
	return web.Response(text=HTML_PAGE, content_type='text/html')


async def handle_civ_stats(request):
	csv_path = os.path.join(DATA_DIR, 'civ_elo_stats.csv')
	if not os.path.exists(csv_path):
		return web.json_response({'error': 'civ_elo_stats.csv not found'}, status=404)

	rows = []
	with open(csv_path, 'r') as f:
		reader = csv.DictReader(f)
		# Detect threshold from column names
		threshold = 1000
		for name in (reader.fieldnames or []):
			if name.startswith('games_player_elo_above_'):
				threshold = int(name.split('_')[-1])
				break

		for row in reader:
			rows.append({
				'civ': row['civ'],
				'games': int(row['games']),
				'winrate': float(row['winrate']),
				'games_player_above': int(row.get(f'games_player_elo_above_{threshold}', 0)),
				'winrate_player_above': float(row.get(f'winrate_player_elo_above_{threshold}', 0)),
				'games_player_below': int(row.get(f'games_player_elo_below_{threshold}', 0)),
				'winrate_player_below': float(row.get(f'winrate_player_elo_below_{threshold}', 0)),
				'games_team_above': int(row.get(f'games_team_elo_above_{threshold}', 0)),
				'winrate_team_above': float(row.get(f'winrate_team_elo_above_{threshold}', 0)),
				'games_team_below': int(row.get(f'games_team_elo_below_{threshold}', 0)),
				'winrate_team_below': float(row.get(f'winrate_team_elo_below_{threshold}', 0)),
			})

	return web.json_response(rows)


def create_app():
	app = web.Application()
	app.router.add_get('/', handle_index)
	app.router.add_get('/api/civ-stats', handle_civ_stats)
	return app


async def start_web_server(port=None):
	"""Start the web server. Returns the runner for cleanup."""
	if port is None:
		port = int(os.environ.get('PORT', 8080))
	app = create_app()
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, '0.0.0.0', port)
	await site.start()
	print(f"Web server started on port {port}")
	return runner
