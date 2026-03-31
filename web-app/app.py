import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, request, jsonify, render_template_string, Response
from shared.scraper import check_rooms
import json, threading, queue, uuid, time

app = Flask(__name__)
_sessions = {}  # session_id -> (Queue, created_time)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>SIT RBS Room Checker</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 40px 16px; }
    .card { background: white; border-radius: 12px; box-shadow: 0 2px 16px rgba(0,0,0,0.1); padding: 32px; width: 100%; max-width: 520px; }
    h1 { font-size: 1.4rem; color: #c0392b; margin-bottom: 4px; }
    .subtitle { color: #666; font-size: 0.85rem; margin-bottom: 24px; }
    label { display: block; font-size: 0.85rem; font-weight: 600; color: #333; margin-bottom: 4px; margin-top: 14px; }
    input, select { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 0.95rem; color: #333; background: #fafafa; }
    input:focus, select:focus { outline: none; border-color: #c0392b; background: white; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .hint { font-size: 0.75rem; color: #999; margin-top: 3px; }
    button { width: 100%; margin-top: 22px; padding: 12px; background: #c0392b; color: white; border: none; border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
    button:hover { background: #a93226; }
    button:disabled { background: #ccc; cursor: not-allowed; }
    #status { margin-top: 16px; text-align: center; font-size: 0.9rem; color: #555; min-height: 20px; }
    #results { margin-top: 24px; width: 100%; max-width: 720px; }
    .section { background: white; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); margin-bottom: 16px; overflow: hidden; }
    .section-header { padding: 14px 20px; font-weight: 700; font-size: 0.95rem; display: flex; align-items: center; gap: 8px; }
    .section-header.full { background: #eafaf1; color: #1e8449; }
    .section-header.partial { background: #fef9e7; color: #b7950b; }
    .section-header.none { background: #fdedec; color: #922b21; }
    .room-row { padding: 10px 20px; border-top: 1px solid #f0f0f0; font-size: 0.88rem; }
    .room-name { font-weight: 600; color: #222; }
    .slots { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }
    .slot { padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 500; }
    .slot.avail { background: #d5f5e3; color: #1e8449; }
    .slot.taken { background: #fadbd8; color: #922b21; }
    .spinner { display: inline-block; width: 18px; height: 18px; border: 3px solid #ddd; border-top-color: #c0392b; border-radius: 50%; animation: spin 0.7s linear infinite; vertical-align: middle; margin-right: 8px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .empty { padding: 12px 20px; color: #999; font-size: 0.85rem; font-style: italic; }
    .progress-wrap { margin-top: 18px; display: none; }
    .progress-bar-bg { background: #eee; border-radius: 8px; height: 10px; overflow: hidden; }
    .progress-bar-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #c0392b, #e74c3c); border-radius: 8px; transition: width 0.4s ease; }
    .progress-label { font-size: 0.78rem; color: #888; margin-top: 4px; text-align: right; }
    .log-box { margin-top: 14px; background: #1a1a2e; border-radius: 8px; padding: 12px 14px; max-height: 180px; overflow-y: auto; display: none; }
    .log-line { font-size: 0.78rem; font-family: monospace; color: #a8d8a8; line-height: 1.7; }
    .log-line.info { color: #a8d8a8; }
    .log-line.step { color: #7ec8e3; }
    .log-line.warn { color: #f9ca24; }
    .log-line.done { color: #6ab04c; font-weight: bold; }
  </style>
</head>
<body>
  <div class="card">
    <h1>SIT Room Checker</h1>
    <p class="subtitle">Check available Discussion Rooms on the RBS system</p>

    <form id="form">
      <label>SIT Username</label>
      <input type="text" id="username" placeholder="e.g. 1234567@sit.singaporetech.edu.sg" required />
      <div class="hint">Students: ID@sit.singaporetech.edu.sg &nbsp;|&nbsp; Staff: username@singaporetech.edu.sg</div>

      <label>Password</label>
      <input type="password" id="password" required />

      <label>Date</label>
      <input type="date" id="date" required />

      <div class="row">
        <div>
          <label>Start Time</label>
          <select id="start"></select>
        </div>
        <div>
          <label>End Time</label>
          <select id="end"></select>
        </div>
      </div>

      <button type="submit" id="btn">Check Availability</button>
    </form>

    <div id="status"></div>

    <div class="progress-wrap" id="progressWrap">
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progressFill"></div>
      </div>
      <div class="progress-label" id="progressLabel">0%</div>
    </div>

    <div class="log-box" id="logBox"></div>
  </div>

  <div id="results"></div>
  <div style="text-align:center;margin-top:16px;max-width:720px;width:100%">
    <button id="recheckBtn" style="display:none;width:auto;padding:10px 28px;font-size:0.9rem" onclick="recheckSame()">🔄 Check Again</button>
  </div>

  <script>
    // Populate time dropdowns
    const times = [];
    for (let h = 7; h <= 22; h++) {
      times.push(`${String(h).padStart(2,'0')}:00`);
      if (h < 22) times.push(`${String(h).padStart(2,'0')}:30`);
    }
    const startSel = document.getElementById('start');
    times.forEach(t => { const o = document.createElement('option'); o.value = t; o.text = t; startSel.appendChild(o); });
    startSel.value = '07:00';

    function populateEnd() {
      const endSel   = document.getElementById('end');
      const prev     = endSel.value;
      const afterVal = startSel.value;
      endSel.innerHTML = '';
      times.filter(t => t > afterVal).forEach(t => {
        const o = document.createElement('option'); o.value = t; o.text = t; endSel.appendChild(o);
      });
      endSel.value = (prev > afterVal) ? prev : endSel.options[endSel.options.length - 1].value;
    }
    startSel.addEventListener('change', populateEnd);
    populateEnd();

    // Default date to today
    const today = new Date();
    document.getElementById('date').value = today.toISOString().slice(0,10);

    // ── SSE-powered submit ──────────────────────────────────────────────────
    document.getElementById('form').addEventListener('submit', async e => {
      e.preventDefault();
      const btn          = document.getElementById('btn');
      const status       = document.getElementById('status');
      const results      = document.getElementById('results');
      const progressWrap = document.getElementById('progressWrap');
      const progressFill = document.getElementById('progressFill');
      const progressLabel= document.getElementById('progressLabel');
      const logBox       = document.getElementById('logBox');

      const startVal = document.getElementById('start').value;
      const endVal   = document.getElementById('end').value;
      if (startVal >= endVal) {
        status.textContent = '❌ End time must be after start time.';
        return;
      }

      btn.disabled = true;
      document.getElementById('recheckBtn').style.display = 'none';
      results.innerHTML = '';
      logBox.innerHTML  = '';
      logBox.style.display       = 'block';
      progressWrap.style.display = 'block';
      progressFill.style.width   = '0%';
      progressLabel.textContent  = '0%';
      status.innerHTML = '<span class="spinner"></span> Starting...';

      const dateVal  = document.getElementById('date').value;
      const dateObj  = new Date(dateVal + 'T00:00:00');
      const months   = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const fmtDate  = `${dateObj.getDate()} ${months[dateObj.getMonth()]} ${dateObj.getFullYear()}`;

      let es;
      try {
        const resp = await fetch('/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: document.getElementById('username').value,
            password: document.getElementById('password').value,
            date:     fmtDate,
            start:    document.getElementById('start').value,
            end:      document.getElementById('end').value
          })
        });
        const { session_id } = await resp.json();

        es = new EventSource(`/stream/${session_id}`);
        let completed = false;

        es.onmessage = ev => {
          const msg = JSON.parse(ev.data);

          if (msg.type === 'log') {
            appendLog(msg.level, msg.msg);

          } else if (msg.type === 'progress') {
            const pct = Math.round((msg.done / msg.total) * 100);
            progressFill.style.width  = pct + '%';
            progressLabel.textContent = pct + '%';
            status.innerHTML = `<span class="spinner"></span> Checking rooms\u2026 ${pct}%`;

          } else if (msg.type === 'result') {
            completed = true;
            progressFill.style.width  = '100%';
            progressLabel.textContent = '100%';
            const d = msg.data;
            status.textContent = `Done! Checked ${d.fully.length + d.partial.length + d.none.length} rooms.`;
            renderResults(d);
            document.getElementById('recheckBtn').style.display = 'inline-block';
            playChime();
            btn.disabled = false;
            es.close();

          } else if (msg.type === 'error') {
            completed = true;
            es.close();
            status.textContent = '\u274C ' + msg.msg;
            btn.disabled = false;
          }
        };

        es.onerror = () => {
          es.close();
          if (!completed) {           // only if we haven't finished cleanly
            status.textContent = '\u274C Connection lost.';
            btn.disabled = false;
          }
        };

      } catch (err) {
        status.textContent = '\u274C Network error: ' + err.message;
        btn.disabled = false;
      }
    });

    // ── Helpers ─────────────────────────────────────────────────────────────
    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function recheckSame() {
      document.getElementById('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }

    function appendLog(level, msg) {
      const logBox = document.getElementById('logBox');
      const line   = document.createElement('div');
      line.className   = `log-line ${level}`;
      line.textContent = msg;
      logBox.appendChild(line);
      logBox.scrollTop = logBox.scrollHeight;
    }

    function playChime() {
      try {
        const ctx   = new (window.AudioContext || window.webkitAudioContext)();
        const notes = [523.25, 659.25, 783.99, 1046.5];   // C5 E5 G5 C6
        notes.forEach((freq, i) => {
          const osc  = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.type          = 'sine';
          osc.frequency.value = freq;
          const t0 = ctx.currentTime + i * 0.18;
          gain.gain.setValueAtTime(0.28, t0);
          gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.65);
          osc.start(t0);
          osc.stop(t0 + 0.65);
        });
      } catch (_) {}
    }

    function renderResults({ fully, partial, none }) {
      const results = document.getElementById('results');
      results.innerHTML = '';

      results.innerHTML += section('full', `\u2705 Fully Available \u2014 ${fully.length} room(s)`,
        fully.length ? fully.map(r => `
          <div class="room-row">
            <div class="room-name">${esc(r.name)}</div>
            <div class="slots">${r.slots.map(s => `<span class="slot avail">${esc(s)}</span>`).join('')}</div>
          </div>`).join('') : '<div class="empty">No rooms fully available for this time range.</div>');

      results.innerHTML += section('partial', `&#x1F7E1; Partially Available \u2014 ${partial.length} room(s)`,
        partial.length ? partial.map(r => `
          <div class="room-row">
            <div class="room-name">${esc(r.name)} (${r.avail}/${r.total} slots free)</div>
            <div class="slots">${r.slots.map(s => `<span class="slot ${s.avail ? 'avail' : 'taken'}">${esc(s.time)}</span>`).join('')}</div>
          </div>`).join('') : '<div class="empty">No partially available rooms.</div>');

      results.innerHTML += section('none', `\u274C Fully Booked \u2014 ${none.length} room(s)`,
        none.length ? none.map(n => `<div class="room-row"><div class="room-name">${esc(n)}</div></div>`).join('') : '<div class="empty">None.</div>');
    }

    function section(cls, title, body) {
      return `<div class="section"><div class="section-header ${cls}">${title}</div>${body}</div>`;
    }
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/start", methods=["POST"])
def start():
    # Clean up sessions older than 5 minutes
    cutoff = time.time() - 300
    stale = [s for s, (_, ts) in _sessions.items() if ts < cutoff]
    for s in stale:
        _sessions.pop(s, None)

    data = request.get_json()
    sid = str(uuid.uuid4())
    q = queue.Queue()
    _sessions[sid] = (q, time.time())

    def log_fn(level, msg=None, **kwargs):
        if level == 'progress':
            q.put(json.dumps({'type': 'progress', **kwargs}))
        else:
            q.put(json.dumps({'type': 'log', 'level': level, 'msg': msg}))

    def run():
        try:
            result = check_rooms(
                username=data['username'],
                password=data['password'],
                date=data['date'],
                start_time=data['start'],
                end_time=data['end'],
                log_fn=log_fn,
            )
            q.put(json.dumps({'type': 'result', 'data': result}))
        except Exception as exc:
            q.put(json.dumps({'type': 'error', 'msg': str(exc)}))
        finally:
            q.put(None)  # end-of-stream sentinel

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'session_id': sid})


@app.route("/stream/<sid>")
def stream(sid):
    entry = _sessions.get(sid)
    q = entry[0] if entry else None
    if not q:
        def err():
            yield 'data: {"type":"error","msg":"session not found"}\n\n'
        return Response(err(), mimetype='text/event-stream')

    def generate():
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                yield f"data: {item}\n\n"
        finally:
            _sessions.pop(sid, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def ensure_browser():
    """Install Playwright's Chromium on first run if missing."""
    import os, subprocess, sys

    browsers_path = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'ms-playwright')
    # Pin the browsers path for both install and runtime
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browsers_path

    already_installed = (
        os.path.isdir(browsers_path) and
        any(d.startswith('chromium') for d in os.listdir(browsers_path))
    )
    if already_installed:
        return

    print("First run: downloading Chromium browser (~130 MB), please wait...")
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller bundle — invoke node.exe + cli.js directly
        node = os.path.join(sys._MEIPASS, 'playwright', 'driver', 'node.exe')
        cli  = os.path.join(sys._MEIPASS, 'playwright', 'driver', 'package', 'cli.js')
        subprocess.run([node, cli, 'install', 'chromium'], check=True,
                       env={**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': browsers_path})
    else:
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True)
    print("Chromium installed. Starting app...")


if __name__ == "__main__":
    import webbrowser
    try:
        ensure_browser()
        def open_browser():
            time.sleep(1.5)
            webbrowser.open("http://localhost:5000")
        threading.Thread(target=open_browser, daemon=True).start()
        print("Starting SIT RBS Checker at http://localhost:5000")
        app.run(port=5000, threaded=True)
    except Exception as e:
        print(f"\nERROR: {e}")
        input("\nPress Enter to exit...")
