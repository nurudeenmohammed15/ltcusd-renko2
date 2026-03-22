import threading
import json
import time
import os
import requests
from flask import Flask, jsonify, render_template_string
import websocket

app = Flask(__name__)

renko_lock = threading.Lock()
current_price = None

# Cascade timeframes matching your MT4 template chain
# M1->M2->M3->M4->M6->M7->M8->M9->M11->M12->M13->M14->M16->M17->M18->M19->M21->M23->M24->M25->M26->M27->M28->M29->M31
CASCADE = [
    {'name': 'M1',  'box': 0.50},
    {'name': 'M2',  'box': 0.32},
    {'name': 'M3',  'box': 0.33},
    {'name': 'M4',  'box': 0.34},
    {'name': 'M6',  'box': 0.35},
    {'name': 'M7',  'box': 0.36},
    {'name': 'M8',  'box': 0.37},
    {'name': 'M9',  'box': 0.38},
    {'name': 'M11', 'box': 0.39},
    {'name': 'M12', 'box': 0.40},
    {'name': 'M13', 'box': 0.41},
    {'name': 'M14', 'box': 0.42},
    {'name': 'M16', 'box': 0.43},
    {'name': 'M17', 'box': 0.44},
    {'name': 'M18', 'box': 0.45},
    {'name': 'M19', 'box': 0.46},
    {'name': 'M21', 'box': 0.47},
    {'name': 'M23', 'box': 0.48},
    {'name': 'M24', 'box': 0.49},
    {'name': 'M25', 'box': 0.50},
    {'name': 'M26', 'box': 0.51},
    {'name': 'M27', 'box': 0.52},
    {'name': 'M28', 'box': 0.53},
    {'name': 'M29', 'box': 0.54},
    {'name': 'M31', 'box': 0.55},
]

# State for each cascade level
cascade_state = {}
for level in CASCADE:
    cascade_state[level['name']] = {
        'bars': [],
        'last_close': None,
        'box': level['box']
    }

def process_price_for_level(name, price, ts=None):
    state = cascade_state[name]
    box = state['box']

    if state['last_close'] is None:
        state['last_close'] = round(price / box) * box
        return

    diff = price - state['last_close']
    if abs(diff) >= box:
        num_boxes = int(abs(diff) / box)
        direction = 1 if diff > 0 else -1
        for _ in range(num_boxes):
            new_close = round(state['last_close'] + direction * box, 4)
            bar = {
                'open': state['last_close'],
                'close': new_close,
                'direction': 'up' if direction > 0 else 'down',
                'time': ts if ts else time.time()
            }
            state['bars'].append(bar)
            state['last_close'] = new_close
            if len(state['bars']) > 500:
                state['bars'].pop(0)

def process_price_all(price, ts=None):
    global current_price
    current_price = price
    for level in CASCADE:
        process_price_for_level(level['name'], price, ts)

def load_history():
    print("Loading LTCUSDT historical data from Binance...")
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": "LTCUSDT", "interval": "1m", "limit": 1000}
        resp = requests.get(url, params=params, timeout=10)
        candles = resp.json()
        print(f"Fetched {len(candles)} candles")

        with renko_lock:
            for level in CASCADE:
                cascade_state[level['name']]['bars'] = []
                cascade_state[level['name']]['last_close'] = None

            for candle in candles:
                ts = candle[0] / 1000
                for p in [float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])]:
                    process_price_all(p, ts)

        print(f"History loaded. M1 bars: {len(cascade_state['M1']['bars'])}, M31 bars: {len(cascade_state['M31']['bars'])}")
    except Exception as e:
        print(f"History error: {e}")

def on_message(ws, message):
    try:
        data = json.loads(message)
        price = float(data['p'])
        with renko_lock:
            process_price_all(price)
    except Exception as e:
        print(f"Message error: {e}")

def on_error(ws, error):
    print(f"WS error: {error}")

def on_close(ws, *args):
    print("WS closed, reconnecting...")

def on_open(ws):
    print("Binance WS connected - LTCUSDT live")

def feeder_thread():
    load_history()
    while True:
        try:
            ws = websocket.WebSocketApp(
                "wss://stream.binance.com:9443/ws/ltcusdt@aggTrade",
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            ws.run_forever()
        except Exception as e:
            print(f"Feeder error: {e}")
        time.sleep(5)

@app.route('/')
def index():
    return render_template_string(HTML_CHART)

@app.route('/api/cascade')
def get_cascade():
    with renko_lock:
        result = {}
        for level in CASCADE:
            name = level['name']
            bars = cascade_state[name]['bars']
            last_dir = bars[-1]['direction'] if bars else None
            result[name] = {
                'bars': len(bars),
                'direction': last_dir,
                'last_close': cascade_state[name]['last_close'],
                'box': level['box']
            }
        return jsonify({
            'cascade': result,
            'price': current_price,
            'levels': [l['name'] for l in CASCADE]
        })

@app.route('/api/bars/<level>')
def get_bars(level):
    with renko_lock:
        if level in cascade_state:
            return jsonify(cascade_state[level]['bars'][-100:])
        return jsonify([])

HTML_CHART = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LTCUSDT Renko Cascade</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; height:100vh; display:flex; flex-direction:column; }

#header {
  display:flex; align-items:center; justify-content:space-between;
  padding:8px 16px; background:#161b22; border-bottom:1px solid #30363d;
  flex-shrink:0;
}
#header h1 { font-size:16px; color:#58a6ff; }
#price-display { font-size:24px; font-weight:bold; }
#live-status { font-size:12px; }
.up { color:#3fb950; }
.down { color:#f85149; }
.neutral { color:#8b949e; }

#main { display:flex; flex:1; overflow:hidden; }

/* LEFT: Cascade Panel */
#cascade-panel {
  width:180px; flex-shrink:0; background:#161b22;
  border-right:1px solid #30363d; overflow-y:auto; padding:8px;
}
#cascade-panel h3 { font-size:11px; color:#8b949e; margin-bottom:8px; text-transform:uppercase; letter-spacing:1px; }

.cascade-row {
  display:flex; align-items:center; justify-content:space-between;
  padding:4px 6px; margin-bottom:2px; border-radius:4px;
  cursor:pointer; transition:background 0.2s;
}
.cascade-row:hover { background:#21262d; }
.cascade-row.active { background:#21262d; border:1px solid #30363d; }
.cascade-row.highlight { background:#1f3a2a; border:1px solid #3fb950; }

.cascade-name { font-size:12px; font-weight:bold; }
.cascade-dir { font-size:16px; }
.cascade-bars { font-size:10px; color:#8b949e; }

#signal-box {
  margin:8px 0; padding:8px; border-radius:6px; text-align:center;
  background:#21262d; font-size:12px;
}
#signal-box .signal-label { font-size:10px; color:#8b949e; margin-bottom:4px; }
#signal-text { font-size:16px; font-weight:bold; }

/* RIGHT: Chart area */
#chart-area { flex:1; display:flex; flex-direction:column; overflow:hidden; }

#chart-tabs {
  display:flex; background:#161b22; border-bottom:1px solid #30363d;
  padding:4px 8px; gap:4px; flex-shrink:0;
}
.tab-btn {
  padding:4px 12px; font-size:12px; border:1px solid #30363d;
  background:#21262d; color:#8b949e; border-radius:4px; cursor:pointer;
}
.tab-btn.active { background:#388bfd22; color:#58a6ff; border-color:#388bfd; }

#chart-container { flex:1; overflow:hidden; position:relative; }
#loading { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); text-align:center; color:#8b949e; }
#loading h2 { color:#58a6ff; margin-bottom:8px; font-size:16px; }
canvas { display:block; }

#info-bar {
  background:#161b22; border-top:1px solid #30363d;
  padding:5px 16px; display:flex; gap:16px;
  font-size:11px; color:#8b949e; flex-shrink:0;
}
#info-bar span { color:#e6edf3; }
</style>
</head>
<body>

<div id="header">
  <h1>📊 LTCUSDT Renko Cascade</h1>
  <div id="price-display" class="neutral">--</div>
  <div id="live-status" class="neutral">Loading history...</div>
</div>

<div id="main">

  <!-- CASCADE PANEL -->
  <div id="cascade-panel">
    <div id="signal-box">
      <div class="signal-label">CASCADE SIGNAL</div>
      <div id="signal-text" class="neutral">--</div>
    </div>
    <h3>Levels</h3>
    <div id="cascade-rows"></div>
  </div>

  <!-- CHART AREA -->
  <div id="chart-area">
    <div id="chart-tabs">
      <button class="tab-btn active" onclick="switchTab('M25')">M25</button>
      <button class="tab-btn" onclick="switchTab('M31')">M31</button>
      <button class="tab-btn" onclick="switchTab('M1')">M1</button>
    </div>
    <div id="chart-container">
      <div id="loading">
        <h2>⏳ Loading historical data...</h2>
        <p>Fetching 1000 candles from Binance</p>
      </div>
      <canvas id="renkoCanvas" style="display:none; width:100%; height:100%;"></canvas>
    </div>
    <div id="info-bar">
      Chart: <span id="info-level">M25</span>
      &nbsp;|&nbsp; Bars: <span id="bar-count">0</span>
      &nbsp;|&nbsp; Box: $<span id="box-size">--</span>
      &nbsp;|&nbsp; Last: $<span id="last-renko">--</span>
      &nbsp;|&nbsp; Dir: <span id="last-dir">--</span>
    </div>
  </div>

</div>

<script>
const canvas = document.getElementById('renkoCanvas');
const ctx = canvas.getContext('2d');
const loadingEl = document.getElementById('loading');

let currentTab = 'M25';
let cascadeData = {};
let chartBars = [];
let allLevels = [];

const BOX_SIZES = {
  M1:0.50,M2:0.32,M3:0.33,M4:0.34,M6:0.35,M7:0.36,M8:0.37,M9:0.38,
  M11:0.39,M12:0.40,M13:0.41,M14:0.42,M16:0.43,M17:0.44,M18:0.45,M19:0.46,
  M21:0.47,M23:0.48,M24:0.49,M25:0.50,M26:0.51,M27:0.52,M28:0.53,M29:0.54,M31:0.55
};

function switchTab(level) {
  currentTab = level;
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.textContent === level);
  });
  document.getElementById('info-level').textContent = level;
  fetchChartBars();
  // Highlight active cascade row
  document.querySelectorAll('.cascade-row').forEach(r => {
    r.classList.toggle('active', r.dataset.level === level);
  });
}

function drawChart(bars) {
  if (!bars || bars.length === 0) return;

  const container = document.getElementById('chart-container');
  const H = container.clientHeight;
  const W = container.clientWidth;

  const BAR_W = 14;
  const GAP = 2;
  const TOTAL_W = Math.max(bars.length * (BAR_W + GAP) + 60, W);

  canvas.width = TOTAL_W;
  canvas.height = H;
  canvas.style.width = TOTAL_W + 'px';
  canvas.style.height = H + 'px';

  const prices = bars.flatMap(b => [b.open, b.close]);
  const minP = Math.min(...prices) - 0.5;
  const maxP = Math.max(...prices) + 0.5;
  const range = maxP - minP;

  function toY(p) { return H - ((p - minP) / range) * (H - 40) - 20; }

  // Background
  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, TOTAL_W, H);

  // Grid
  ctx.strokeStyle = '#21262d';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 8; i++) {
    const p = minP + (range / 8) * i;
    const y = toY(p);
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(TOTAL_W, y); ctx.stroke();
    ctx.fillStyle = '#484f58';
    ctx.font = '10px monospace';
    ctx.fillText('$' + p.toFixed(2), 2, y - 2);
  }

  // Bars
  bars.forEach((bar, i) => {
    const x = 40 + i * (BAR_W + GAP);
    const y1 = toY(Math.max(bar.open, bar.close));
    const y2 = toY(Math.min(bar.open, bar.close));
    const bH = Math.max(y2 - y1, 2);

    ctx.fillStyle = bar.direction === 'up' ? '#3fb950' : '#f85149';
    ctx.fillRect(x, y1, BAR_W, bH);
    ctx.strokeStyle = bar.direction === 'up' ? '#2ea043' : '#da3633';
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y1, BAR_W, bH);
  });

  // Scroll to latest
  container.scrollLeft = TOTAL_W;
}

function updateCascadePanel(data) {
  const rows = document.getElementById('cascade-rows');
  rows.innerHTML = '';
  allLevels = data.levels || [];

  let upCount = 0, downCount = 0;

  allLevels.forEach(name => {
    const info = data.cascade[name];
    if (!info) return;
    const dir = info.direction;
    if (dir === 'up') upCount++;
    else if (dir === 'down') downCount++;

    const row = document.createElement('div');
    row.className = 'cascade-row' + (name === currentTab ? ' active' : '') +
                    (name === 'M25' || name === 'M31' ? ' highlight' : '');
    row.dataset.level = name;
    row.onclick = () => switchTab(name);

    const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : '–';
    const color = dir === 'up' ? '#3fb950' : dir === 'down' ? '#f85149' : '#8b949e';

    row.innerHTML = `
      <span class="cascade-name">${name}</span>
      <span class="cascade-dir" style="color:${color}">${arrow}</span>
      <span class="cascade-bars">${info.bars}b</span>
    `;
    rows.appendChild(row);
  });

  // Signal
  const total = upCount + downCount;
  const signalEl = document.getElementById('signal-text');
  if (total === 0) {
    signalEl.textContent = '--';
    signalEl.className = 'neutral';
  } else if (upCount > downCount * 1.5) {
    signalEl.textContent = '▲ BUY ' + upCount + '/' + total;
    signalEl.className = 'up';
  } else if (downCount > upCount * 1.5) {
    signalEl.textContent = '▼ SELL ' + downCount + '/' + total;
    signalEl.className = 'down';
  } else {
    signalEl.textContent = '↔ MIXED ' + upCount + 'U ' + downCount + 'D';
    signalEl.className = 'neutral';
  }
}

async function fetchChartBars() {
  try {
    const res = await fetch('/api/bars/' + currentTab);
    const bars = await res.json();
    chartBars = bars;

    if (bars.length > 0) {
      loadingEl.style.display = 'none';
      canvas.style.display = 'block';
      drawChart(bars);

      const last = bars[bars.length - 1];
      document.getElementById('bar-count').textContent = bars.length;
      document.getElementById('box-size').textContent = (BOX_SIZES[currentTab] || '--').toFixed(2);
      document.getElementById('last-renko').textContent = last.close.toFixed(2);
      const dirEl = document.getElementById('last-dir');
      dirEl.textContent = last.direction === 'up' ? '▲ Up' : '▼ Down';
      dirEl.className = last.direction === 'up' ? 'up' : 'down';
    }
  } catch(e) {
    console.error(e);
  }
}

async function fetchAll() {
  try {
    const res = await fetch('/api/cascade');
    const data = await res.json();
    cascadeData = data;

    // Update price
    const priceEl = document.getElementById('price-display');
    if (data.price) {
      priceEl.textContent = '$' + data.price.toFixed(2);
    }

    // Check if loaded
    const m1 = data.cascade['M1'];
    if (m1 && m1.bars > 0) {
      document.getElementById('live-status').textContent = '🟢 Live';
      document.getElementById('live-status').className = 'up';
      priceEl.className = m1.direction === 'up' ? 'up' : 'down';
    } else {
      document.getElementById('live-status').textContent = '⏳ Loading...';
    }

    updateCascadePanel(data);
    fetchChartBars();
  } catch(e) {
    document.getElementById('live-status').textContent = '🔴 Error';
  }
}

fetchAll();
setInterval(fetchAll, 2000);
window.addEventListener('resize', () => drawChart(chartBars));
</script>
</body>
</html>
"""

if __name__ == '__main__':
    t = threading.Thread(target=feeder_thread, daemon=True)
    t.start()
    print("Cascade feeder started")
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
