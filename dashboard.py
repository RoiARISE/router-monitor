"""
router-monitor Web ダッシュボード

ブラウザから http://localhost:8088 でアクセスできます。
stdlib のみで動作します（追加パッケージ不要）。

使い方:
  python3 dashboard.py                  # ポート 8088 で起動
  python3 dashboard.py --port 9000      # ポート変更
  python3 dashboard.py --config path/to/config.yaml
"""

import argparse
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router_monitor.config import load_config
from router_monitor.logging_utils import load_jsonl_reverse
from router_monitor.stats import get_reboot_stats, get_daily_ping_stats

# ---------------------------------------------------------------------------
# HTML テンプレート
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Router Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

    :root {
      --bg:       #080c14;
      --surface:  #0f1520;
      --border:   rgba(255,255,255,.07);
      --text:     #e2e8f0;
      --muted:    #64748b;
      --accent:   #3b82f6;
      --green:    #10b981;
      --orange:   #f59e0b;
      --red:      #ef4444;
      --purple:   #8b5cf6;
    }

    body {
      font-family:'Inter',sans-serif;
      background:var(--bg);
      color:var(--text);
      min-height:100vh;
    }

    /* ── header ─────────────────────────────────────────── */
    header {
      background:rgba(15,21,32,.8);
      backdrop-filter:blur(12px);
      border-bottom:1px solid var(--border);
      position:sticky; top:0; z-index:100;
      padding:0 24px;
    }
    .header-inner {
      max-width:1200px; margin:0 auto;
      display:flex; align-items:center; justify-content:space-between;
      height:60px;
    }
    .logo { display:flex; align-items:center; gap:10px; }
    .logo-icon { font-size:24px; }
    .logo h1 { font-size:18px; font-weight:700; letter-spacing:-.3px; }
    .header-right { display:flex; align-items:center; gap:12px; font-size:13px; color:var(--muted); }
    .dot {
      width:8px; height:8px; border-radius:50%;
      background:var(--green);
      box-shadow:0 0 6px var(--green);
      animation:pulse 2s infinite;
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
    #refresh-countdown { font-variant-numeric:tabular-nums; }

    /* ── main ────────────────────────────────────────────── */
    main { max-width:1200px; margin:0 auto; padding:28px 24px 60px; }

    /* ── stat cards ──────────────────────────────────────── */
    .cards {
      display:grid;
      grid-template-columns:repeat(auto-fill, minmax(230px, 1fr));
      gap:16px; margin-bottom:32px;
    }
    .card {
      background:var(--surface);
      border:1px solid var(--border);
      border-radius:16px;
      padding:20px 22px;
      display:flex; flex-direction:column; gap:6px;
      transition:transform .15s, box-shadow .15s;
    }
    .card:hover { transform:translateY(-2px); box-shadow:0 8px 32px rgba(0,0,0,.4); }
    .card-label { font-size:12px; font-weight:500; color:var(--muted); text-transform:uppercase; letter-spacing:.6px; }
    .card-value { font-size:36px; font-weight:800; line-height:1; }
    .card-sub   { font-size:12px; color:var(--muted); }
    .accent  { color:var(--accent); }
    .green   { color:var(--green);  }
    .orange  { color:var(--orange); }
    .red     { color:var(--red);    }
    .purple  { color:var(--purple); }

    /* ── section ─────────────────────────────────────────── */
    .section { margin-bottom:32px; }
    .section-header {
      display:flex; align-items:center; justify-content:space-between;
      margin-bottom:14px;
    }
    .section-header h2 { font-size:15px; font-weight:600; }
    .toggle-btn {
      font-size:12px; padding:4px 12px; border-radius:20px;
      border:1px solid var(--border); background:transparent;
      color:var(--muted); cursor:pointer; transition:all .15s;
    }
    .toggle-btn:hover { border-color:var(--accent); color:var(--accent); }

    /* ── table ───────────────────────────────────────────── */
    .tbl-wrap { overflow-x:auto; border-radius:12px; border:1px solid var(--border); }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th {
      text-align:left; padding:10px 14px;
      background:rgba(255,255,255,.03);
      color:var(--muted); font-weight:500; font-size:11px;
      text-transform:uppercase; letter-spacing:.5px;
      border-bottom:1px solid var(--border);
    }
    td { padding:10px 14px; border-bottom:1px solid rgba(255,255,255,.04); vertical-align:top; }
    tr:last-child td { border-bottom:none; }
    tr:hover td { background:rgba(255,255,255,.02); }

    .badge {
      display:inline-block; padding:2px 8px; border-radius:20px;
      font-size:11px; font-weight:600; white-space:nowrap;
    }
    .badge-gray   { background:rgba(100,116,139,.15); color:#94a3b8; }
    .badge-green  { background:rgba(16,185,129,.15);  color:#34d399; }
    .badge-orange { background:rgba(245,158,11,.15);  color:#fbbf24; }
    .badge-red    { background:rgba(239,68,68,.15);   color:#f87171; }
    .badge-blue   { background:rgba(59,130,246,.15);  color:#60a5fa; }
    .badge-purple { background:rgba(139,92,246,.15);  color:#a78bfa; }

    .detail-kv { display:flex; flex-wrap:wrap; gap:4px; }
    .kv { font-size:11px; color:var(--muted); }
    .kv span { color:var(--text); }

    .empty { padding:32px; text-align:center; color:var(--muted); font-size:13px; }

    /* ── ping row color ──────────────────────────────────── */
    .ping-ok td { opacity:.4; }

    @media(max-width:600px) {
      main { padding:16px 12px 40px; }
      .card-value { font-size:28px; }
    }
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="logo">
      <span class="logo-icon">🔁</span>
      <h1>Router Monitor</h1>
    </div>
    <div class="header-right">
      <div class="dot"></div>
      <span id="last-updated">--</span>
      <span>次回更新 <span id="refresh-countdown">30</span>s</span>
    </div>
  </div>
</header>

<main>
  <div class="cards" id="cards">
    <!-- JS で生成 -->
  </div>

  <div class="section">
    <div class="section-header">
      <h2>📋 直近のイベント</h2>
      <button class="toggle-btn" id="toggle-ping" onclick="togglePing()">
        ping_cycle を表示
      </button>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr><th>日時</th><th>イベント</th><th>詳細</th></tr>
        </thead>
        <tbody id="events-body">
          <tr><td colspan="3" class="empty">読み込み中...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-header">
      <h2>⚡ 再起動履歴</h2>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr><th>日時</th><th>試行</th><th>OFF時間</th></tr>
        </thead>
        <tbody id="reboots-body">
          <tr><td colspan="3" class="empty">読み込み中...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<script>
let showPing = false;
let countdown = 30;
let countdownTimer;

// ── イベント種別 → badge class ────────────────────────────
const BADGE = {
  monitor_started:                   'badge-gray',
  ping_cycle:                        'badge-gray',
  router_down_confirmed:             'badge-red',
  power_cycle_request_started:       'badge-orange',
  power_cycle_request_succeeded:     'badge-blue',
  power_cycle_request_failed:        'badge-red',
  power_cycle_request_timed_out:     'badge-red',
  failure_session_closed:            'badge-green',
  entered_failed:                    'badge-red',
  entered_verifying:                 'badge-blue',
  resumed_monitoring_from_failed:    'badge-orange',
  upstream_down:                     'badge-orange',
  upstream_recovered:                'badge-green',
  router_recovered_from_down_without_power_cycle: 'badge-green',
  daily_stability_report:            'badge-purple',
  reboot_skipped_due_to_lock:        'badge-gray',
  retry_limit_reached_skipping_power_cycle: 'badge-red',
  arp_diagnostic:                    'badge-gray',
  cycle_error:                       'badge-red',
  relay_set_state_succeeded:         'badge-blue',
  relay_set_state_attempt_failed:    'badge-orange',
};

function badgeClass(event) { return BADGE[event] || 'badge-gray'; }

function fmtTs(ts) {
  if (!ts) return '--';
  return ts.replace('T', ' ').slice(0, 19);
}

function escapeHtml(unsafe) {
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function fmtKV(entry) {
  const skip = new Set(['timestamp','event']);
  const pairs = Object.entries(entry)
    .filter(([k]) => !skip.has(k))
    .map(([k,v]) => `<span class="kv">${escapeHtml(k)}=<span>${escapeHtml(v)}</span></span>`)
    .join('');
  return pairs ? `<div class="detail-kv">${pairs}</div>` : '';
}

// ── サマリーカード ────────────────────────────────────────
async function loadSummary() {
  const r = await fetch('/api/summary').then(r => r.json()).catch(() => null);
  if (!r) return;

  document.getElementById('last-updated').textContent = fmtTs(r.last_updated);

  const reboot = r.reboot;
  const ping   = r.ping_today;
  const total  = ping.total_cycles || 1;
  const rf     = ping.router_false;
  const ef     = ping.external_false;
  const stable = rf === 0 && ef === 0 && reboot.today === 0;

  const cards = [
    {
      label: '累計再起動回数',
      value: reboot.total,
      sub:   `今月 ${reboot.month}回`,
      cls:   reboot.total === 0 ? 'green' : 'orange',
    },
    {
      label: '今日の再起動回数',
      value: reboot.today,
      sub:   '本日',
      cls:   reboot.today === 0 ? 'green' : 'red',
    },
    {
      label: '今日の通信安定性',
      value: stable ? '✅' : '⚠️',
      sub:   `ルーター失敗 ${rf}回 / 外部 ${ef}回`,
      cls:   stable ? 'green' : 'orange',
    },
    {
      label: '今日の監視回数',
      value: ping.total_cycles,
      sub:   `${(ping.total_cycles * 30 / 3600).toFixed(1)} 時間分`,
      cls:   'accent',
    },
  ];

  document.getElementById('cards').innerHTML = cards.map(c => `
    <div class="card">
      <div class="card-label">${c.label}</div>
      <div class="card-value ${c.cls}">${c.value}</div>
      <div class="card-sub">${c.sub}</div>
    </div>
  `).join('');
}

// ── イベントテーブル ──────────────────────────────────────
async function loadEvents() {
  const events = await fetch('/api/events?limit=200').then(r => r.json()).catch(() => []);
  const body = document.getElementById('events-body');

  const filtered = showPing ? events : events.filter(e => e.event !== 'ping_cycle' ||
    e.router_ok === false || e.external_ok === false);

  if (!filtered.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty">イベントなし</td></tr>';
    return;
  }

  body.innerHTML = filtered.map(e => {
    const isPingOk = e.event === 'ping_cycle' && e.router_ok !== false && e.external_ok !== false;
    return `
    <tr class="${isPingOk ? 'ping-ok' : ''}">
      <td style="white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--muted)">${fmtTs(e.timestamp)}</td>
      <td><span class="badge ${badgeClass(e.event)}">${e.event}</span></td>
      <td>${fmtKV(e)}</td>
    </tr>`;
  }).join('');
}

// ── 再起動テーブル ────────────────────────────────────────
async function loadReboots() {
  const reboots = await fetch('/api/reboots').then(r => r.json()).catch(() => []);
  const body = document.getElementById('reboots-body');

  if (!reboots.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty">再起動記録なし</td></tr>';
    return;
  }

  body.innerHTML = reboots.map(r => `
    <tr>
      <td style="white-space:nowrap;color:var(--muted)">${fmtTs(r.timestamp)}</td>
      <td>${r.attempt || '-'} 回目</td>
      <td>${r.off_duration_seconds || '-'} 秒</td>
    </tr>
  `).join('');
}

function togglePing() {
  showPing = !showPing;
  document.getElementById('toggle-ping').textContent =
    showPing ? 'ping_cycle を隠す' : 'ping_cycle を表示';
  loadEvents();
}

async function refresh() {
  await Promise.all([loadSummary(), loadEvents(), loadReboots()]);
  resetCountdown();
}

function resetCountdown() {
  countdown = 30;
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown--;
    document.getElementById('refresh-countdown').textContent = countdown;
    if (countdown <= 0) { refresh(); }
  }, 1000);
}

refresh();
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# HTTP ハンドラ
# ---------------------------------------------------------------------------
_config = None





class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/":
            self._html()
        elif parsed.path == "/api/summary":
            self._json(self._summary())
        elif parsed.path == "/api/events":
            limit = int(qs.get("limit", ["200"])[0])
            self._json(load_jsonl_reverse(_config.logging.events_path, limit))
        elif parsed.path == "/api/reboots":
            self._json(load_jsonl_reverse(_config.logging.reboots_path))
        else:
            self.send_error(404)

    def _summary(self) -> dict:
        now = datetime.now().astimezone()
        reboot = get_reboot_stats(_config.logging.reboots_path, now)
        ping   = get_daily_ping_stats(_config.logging.events_path, now)
        return {
            "reboot":       reboot,
            "ping_today":   ping,
            "last_updated": now.isoformat(),
        }

    def _html(self):
        data = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # アクセスログを抑制


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
def main():
    global _config

    parser = argparse.ArgumentParser(description="router-monitor Web ダッシュボード")
    parser.add_argument("--host",   default="127.0.0.1", help="バインドするホスト名/IP (デフォルト: 127.0.0.1、LAN公開時は 0.0.0.0)")
    parser.add_argument("--port",   type=int, default=8088)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env",    default=".env")
    args = parser.parse_args()

    try:
        _config = load_config(args.config, args.env)
    except ValueError as e:
        print(f"設定読み込みエラー: {e}")
        sys.exit(1)

    server = HTTPServer((args.host, args.port), DashboardHandler)
    host_display = "localhost" if args.host == "127.0.0.1" else args.host
    print(f"Dashboard: http://{host_display}:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()
