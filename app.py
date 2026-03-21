"""
=============================================================================
  LTCUSDT Renko Chart — Combined Server + Feeder
  Runs on Render.com FREE Web Service plan
  Single script — web server + Binance feeder in one process
=============================================================================
"""

import asyncio
import csv
import json
import logging
import math
import os
import struct
import sys
import threading
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
#  PATHS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path("/opt/render/project/src")
TICKS_FILE = BASE_DIR / "ticks.csv"
HST_FILE   = BASE_DIR / "LTCUSDT1.hst"
HTML_FILE  = BASE_DIR / "chart.html"
LOG_FILE   = BASE_DIR / "feeder.log"

BASE_DIR.mkdir(parents=True, exist_ok=True)

PORT          = int(os.environ.get("PORT", 10000))
REBUILD_EVERY = 500
BINANCE_WS    = "wss://stream.binance.com:9443/ws/ltcusdt@trade"
BINANCE_REST  = "https://api.binance.com/api/v3/aggTrades?symbol=LTCUSDT&limit=1000"

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  TICK FILE
# ─────────────────────────────────────────────────────────────────────────────

def get_last_tick_ts():
    if not TICKS_FILE.exists():
        return 0
    try:
        with open(TICKS_FILE, 'r') as f:
            lines = f.readlines()
        for line in reversed(lines):
            line = line.strip()
            if line:
                parts = line.split(',')
                if len(parts) >= 2:
                    return int(parts[0])
    except Exception:
        pass
    return 0

def count_ticks():
    if not TICKS_FILE.exists():
        return 0
    try:
        with open(TICKS_FILE, 'r') as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0

def append_tick(ts_sec, price):
    with open(TICKS_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([ts_sec, f"{price:.5f}"])

# ─────────────────────────────────────────────────────────────────────────────
#  HST BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_hst():
    log.info("Rebuilding HST...")
    if not TICKS_FILE.exists():
        return
    ticks = []
    with open(TICKS_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) < 2:
                continue
            try:
                ticks.append((int(parts[0]), float(parts[1])))
            except ValueError:
                continue
    if not ticks:
        return
    with open(HST_FILE, 'wb') as fh:
        fh.write(struct.pack('<i', 401))
        fh.write(b'\x00' * 64)
        fh.write(b'LTCUSDT\x00\x00\x00\x00\x00')
        fh.write(struct.pack('<i', 1))
        fh.write(struct.pack('<i', 5))
        fh.write(struct.pack('<i', 0))
        fh.write(struct.pack('<i', 0))
        fh.write(b'\x00' * 52)
        prev_ts = -1
        for ts_sec, price in ticks:
            if ts_sec <= prev_ts:
                ts_sec = prev_ts + 1
            prev_ts = ts_sec
            fh.write(struct.pack('<qddddqiq',
                ts_sec, price, price, price, price, 1, 0, 1))
    log.info(f"  HST: {len(ticks):,} bars")

# ─────────────────────────────────────────────────────────────────────────────
#  CASCADE
# ─────────────────────────────────────────────────────────────────────────────

STAGES = [
    (s, tf, (orig/3)*0.0001)
    for s, tf, orig in [
        ( 1,  2, 600), ( 2,  3, 316), ( 3,  4, 317), ( 4,  6, 318),
        ( 5,  7, 319), ( 6,  8, 320), ( 7,  9, 321), ( 8, 11, 322),
        ( 9, 12, 323), (10, 13, 324), (11, 14, 325), (12, 16, 326),
        (13, 17, 327), (14, 18, 328), (15, 19, 329), (16, 21, 330),
        (17, 23, 331), (18, 24, 332), (19, 25, 333), (20, 26, 334),
        (21, 27, 335), (22, 28, 336), (23, 29, 337), (24, 31, 338),
    ]
]

def compute_renko(bars, box_pts):
    if not bars:
        return []
    cmp = lambda a, b: abs(a-b) < 1e-9
    prev_low  = math.floor(bars[0]['c'] / box_pts) * box_pts
    prev_high = prev_low + box_pts
    prev_time = bars[0]['t']
    cur_vol   = 1
    bricks    = []
    for i in range(1, len(bars)-1):
        b = bars[i]; bp = bars[i-1]
        cur_vol += b['v']
        up = (b['h']+b['l']) > (bp['h']+bp['l'])
        while up and (b['l'] < prev_low-box_pts or cmp(b['l'], prev_low-box_pts)):
            prev_high -= box_pts; prev_low -= box_pts
            bricks.append({'t':prev_time,'o':prev_high,'h':prev_high,'l':prev_high,'c':prev_low,'v':cur_vol,'bull':False})
            cur_vol = 0; prev_time = b['t'] if b['t']>prev_time else prev_time+1
        while b['h'] > prev_high+box_pts or cmp(b['h'], prev_high+box_pts):
            prev_high += box_pts; prev_low += box_pts
            bricks.append({'t':prev_time,'o':prev_low,'h':prev_low,'l':prev_low,'c':prev_high,'v':cur_vol,'bull':True})
            cur_vol = 0; prev_time = b['t'] if b['t']>prev_time else prev_time+1
        while not up and (b['l'] < prev_low-box_pts or cmp(b['l'], prev_low-box_pts)):
            prev_high -= box_pts; prev_low -= box_pts
            bricks.append({'t':prev_time,'o':prev_high,'h':prev_high,'l':prev_high,'c':prev_low,'v':cur_vol,'bull':False})
            cur_vol = 0; prev_time = b['t'] if b['t']>prev_time else prev_time+1
    return bricks

def bricks_to_bars(bricks):
    return [{'t':b['t'],'o':b['o'],'h':b['h'],'l':b['l'],'c':b['c'],'v':b['v']} for b in bricks]

def run_cascade():
    if not HST_FILE.exists():
        return [], []
    HDR = 148; REC = 60
    bars = []
    with open(HST_FILE, 'rb') as f:
        f.seek(HDR)
        while True:
            data = f.read(REC)
            if len(data) < REC: break
            t,o,h,l,c,tv,sp,rv = struct.unpack('<qddddqiq', data)
            bars.append({'t':int(t),'o':c,'h':c,'l':c,'c':c,'v':1})
    if len(bars) < 10:
        return [], []
    input_bars = bars
    m25_bricks = []
    m31_bricks = []
    for stage, tf, box_pts in STAGES:
        bricks = compute_renko(input_bars, box_pts)
        if stage == 19: m25_bricks = bricks
        if stage == 24: m31_bricks = bricks
        if bricks: input_bars = bricks_to_bars(bricks)
    return m25_bricks, m31_bricks

# ─────────────────────────────────────────────────────────────────────────────
#  HTML BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_chart():
    log.info("Building chart HTML...")
    t0 = datetime.datetime.utcnow()
    m25, m31 = run_cascade()
    if not m25 or not m31:
        log.warning("No bricks produced")
        return
    elapsed = (datetime.datetime.utcnow() - t0).total_seconds()
    log.info(f"  Cascade done: M25={len(m25):,} M31={len(m31):,} ({elapsed:.1f}s)")

    m25_js = json.dumps([[int(b['t']),round(b['o'],5),round(b['c'],5),int(b['v']),1 if b['bull'] else 0] for b in m25], separators=(',',':'))
    m31_js = json.dumps([[int(b['t']),round(b['o'],5),round(b['c'],5),int(b['v']),1 if b['bull'] else 0] for b in m31], separators=(',',':'))

    from_dt = datetime.datetime.utcfromtimestamp(m25[0]['t']).strftime('%Y-%m-%d')
    to_dt   = datetime.datetime.utcfromtimestamp(m25[-1]['t']).strftime('%Y-%m-%d')
    gen_at  = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    n_ticks = count_ticks()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>#LTCUSD M25+M31 Renko</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Bebas+Neue&display=swap" rel="stylesheet">
<style>
:root{{--bg:#050810;--panel:#090d18;--sub:#0b1019;--b1:#131f30;--b2:#1a2f48;
--bull:#26d97f;--bear:#e8365d;--acc:#38bdf8;--gold:#f5c842;--muted:#3d5a72;--dim:#243648;--text:#8eafc4;--white:#ddeeff;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
html,body{{height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;}}
body{{display:flex;flex-direction:column;}}
body::before{{content:'';position:fixed;inset:0;pointer-events:none;z-index:900;background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,.07) 3px,rgba(0,0,0,.07) 4px);}}
.topbar{{height:46px;background:rgba(5,8,16,.98);border-bottom:1px solid var(--b2);display:flex;align-items:center;justify-content:space-between;padding:0 12px;flex-shrink:0;z-index:100;}}
.brand{{display:flex;align-items:center;gap:8px;}}
.bicon{{width:20px;height:20px;display:grid;grid-template-columns:1fr 1fr;gap:2px;}}
.bicon span{{border-radius:1px;animation:pulse 2.4s ease-in-out infinite;}}
.bicon span:nth-child(1){{background:var(--bull)}}
.bicon span:nth-child(2){{background:var(--bear);animation-delay:.5s}}
.bicon span:nth-child(3){{background:var(--acc);animation-delay:1s}}
.bicon span:nth-child(4){{background:var(--gold);animation-delay:1.5s}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.15}}}}
.bname{{font-family:'Bebas Neue',sans-serif;font-size:.95rem;letter-spacing:3px;color:var(--white);}}
.bname em{{color:var(--gold);font-style:normal;}}
.bsub{{font-size:.4rem;color:var(--muted);letter-spacing:1.5px;}}
.tright{{display:flex;align-items:center;gap:8px;font-size:.48rem;color:var(--muted);flex-wrap:wrap;}}
.pe{{font-size:.85rem;font-weight:700;color:var(--white);}}
.pe.up{{color:var(--bull)}}.pe.dn{{color:var(--bear)}}
.dot{{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px;animation:blink 1.2s ease-in-out infinite;}}
.dot.live{{background:var(--bull)}}.dot.dead{{background:var(--bear);animation:none}}.dot.wait{{background:var(--gold)}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.1}}}}
.gentag{{font-size:.36rem;color:var(--dim);padding:2px 5px;border:1px solid var(--b1);border-radius:2px;white-space:nowrap;}}
.rbar{{height:3px;background:var(--b1);flex-shrink:0;}}
.rprog{{height:100%;background:linear-gradient(90deg,var(--gold),var(--acc));width:0%;}}
.infobar{{background:var(--sub);border-bottom:1px solid var(--b1);padding:3px 12px;display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap;}}
.ib{{font-size:.42rem;color:var(--muted);}}.ib b{{color:var(--bull);}}
.il{{display:flex;align-items:center;gap:4px;font-size:.42rem;color:var(--muted);}}.sw{{width:20px;height:2px;border-radius:1px;}}
.charts-row{{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--b1);overflow:hidden;min-height:0;}}
.chart-panel{{background:var(--panel);display:flex;flex-direction:column;overflow:hidden;}}
.ph{{height:36px;background:rgba(5,8,16,.95);border-bottom:1px solid var(--b2);display:flex;align-items:center;justify-content:space-between;padding:0 10px;flex-shrink:0;}}
.ph-title{{font-family:'Bebas Neue',sans-serif;font-size:.85rem;letter-spacing:2px;}}
.ph-m25{{color:var(--acc)}}.ph-m31{{color:var(--gold)}}
.ph-sub{{font-size:.36rem;color:var(--muted);margin-top:1px;}}
.ph-price{{font-size:.68rem;font-weight:700;}}.ph-price.bull{{color:var(--bull)}}.ph-price.bear{{color:var(--bear)}}
.ph-dir{{font-size:.36rem;}}.ph-dir.bull{{color:var(--bull)}}.ph-dir.bear{{color:var(--bear)}}
.ps{{display:flex;border-bottom:1px solid var(--b1);flex-shrink:0;}}
.psi{{flex:1;text-align:center;padding:2px 2px;border-right:1px solid var(--b1);}}.psi:last-child{{border-right:none;}}
.psv{{display:block;font-size:.54rem;font-weight:700;color:var(--white);}}.psv.bull{{color:var(--bull)}}.psv.bear{{color:var(--bear)}}.psv.gold{{color:var(--gold)}}.psv.acc{{color:var(--acc)}}
.psl{{display:block;font-size:.33rem;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-top:1px;}}
.pc{{flex:1;position:relative;overflow:hidden;}}
canvas{{display:block;cursor:crosshair;}}
.pz{{position:absolute;top:5px;right:5px;display:flex;gap:3px;z-index:10;}}
.pzb{{width:24px;height:24px;background:rgba(9,13,24,.92);border:1px solid var(--b2);color:var(--text);font-size:.75rem;cursor:pointer;display:flex;align-items:center;justify-content:center;border-radius:2px;}}
.pzb.w{{width:32px;font-size:.42rem;}}
.pif{{padding:2px 8px;border-top:1px solid var(--b1);background:var(--sub);display:flex;justify-content:space-between;font-size:.36rem;color:var(--dim);flex-shrink:0;}}
.psb{{height:13px;background:var(--sub);border-top:1px solid var(--b1);position:relative;flex-shrink:0;}}
.psbt{{position:absolute;top:2px;bottom:2px;left:0;right:0;}}
.psbh{{position:absolute;top:0;bottom:0;border-radius:2px;cursor:grab;min-width:14px;}}
#tt{{position:fixed;background:rgba(8,11,20,.98);border:1px solid var(--b2);padding:7px 11px;font-size:.52rem;line-height:2;pointer-events:none;z-index:950;display:none;min-width:185px;box-shadow:0 6px 24px rgba(0,0,0,.8);}}
.tth{{color:var(--white);font-weight:700;font-size:.56rem;border-bottom:1px solid var(--b1);padding-bottom:3px;margin-bottom:3px;}}
.ttbull{{color:var(--bull)}}.ttbear{{color:var(--bear)}}.ttgold{{color:var(--gold)}}.ttacc{{color:var(--acc)}}
</style>
</head>
<body>
<div id="tt"><div class="tth" id="tth"></div><div id="ttb"></div></div>
<div class="topbar">
  <div class="brand">
    <div class="bicon"><span></span><span></span><span></span><span></span></div>
    <div>
      <div class="bname">#LTC<em>USD</em> — M25+M31 Repaint-Free</div>
      <div class="bsub">24-stage ÷3 · {from_dt} → {to_dt} · {n_ticks:,} ticks · Keltner(200) · PPP2(200)</div>
    </div>
  </div>
  <div class="tright">
    <span class="pe" id="lp">—</span>
    <span id="tc" style="color:var(--dim)">—</span>
    <span><span class="dot wait" id="cd"></span><span id="ct">—</span></span>
    <span class="gentag">Built: {gen_at}</span>
  </div>
</div>
<div class="rbar"><div class="rprog" id="rp"></div></div>
<div class="infobar">
  <div class="ib">ALL BRICKS FROM SAVED TICKS · <b>REPAINT-FREE · CLOSE+REOPEN = IDENTICAL</b></div>
  <div class="il"><div class="sw" style="background:#fff;"></div><span>Keltner</span></div>
  <div class="il"><div class="sw" style="background:#00ff00;height:3px;"></div><span>PPP2 Up</span></div>
  <div class="il"><div class="sw" style="background:#ff0000;height:3px;"></div><span>PPP2 Down</span></div>
  <div class="il"><div class="sw" style="background:#00ffff;height:2px;"></div><span>PPP2 Main</span></div>
  <span style="font-size:.42rem;color:var(--gold);">M25={len(m25):,} · M31={len(m31):,} · Rebuilds every 500 ticks</span>
</div>
<div class="charts-row">
  <div class="chart-panel">
    <div class="ph">
      <div><div class="ph-title ph-m25">TF25 — M25 · STAGE 19</div><div class="ph-sub">BoxPts=0.01110 · Keltner(200) · PPP2(200)</div></div>
      <div style="text-align:right;"><div class="ph-price" id="p25-pr">—</div><div class="ph-dir" id="p25-dr">—</div></div>
    </div>
    <div class="ps">
      <div class="psi"><span class="psv acc" id="25-n">{len(m25):,}</span><span class="psl">Bricks</span></div>
      <div class="psi"><span class="psv bull" id="25-bu">—</span><span class="psl">Bull</span></div>
      <div class="psi"><span class="psv bear" id="25-be">—</span><span class="psl">Bear</span></div>
      <div class="psi"><span class="psv gold" id="25-pc">—</span><span class="psl">Bull%</span></div>
      <div class="psi"><span class="psv acc" id="25-km">—</span><span class="psl">KeltMid</span></div>
      <div class="psi"><span class="psv" id="25-pp">—</span><span class="psl">PPP2</span></div>
    </div>
    <div class="pc" id="w25"><canvas id="cv25"></canvas>
      <div class="pz">
        <button class="pzb" onclick="z25(bW25*1.4)">+</button>
        <button class="pzb" onclick="z25(bW25/1.4)">−</button>
        <button class="pzb w" onclick="f25()">ALL</button>
        <button class="pzb w" onclick="e25()">END</button>
      </div>
    </div>
    <div class="pif"><span>Stage 19 · v600 ÷3 · repaint-free</span><span style="color:var(--bull)">✓ LOCKED</span></div>
    <div class="psb"><div class="psbt" id="sbt25"><div class="psbh" id="sbh25" style="background:rgba(56,189,248,.2);border:1px solid rgba(56,189,248,.4);"></div></div></div>
  </div>
  <div class="chart-panel">
    <div class="ph">
      <div><div class="ph-title ph-m31">TF31 — M31 · STAGE 24</div><div class="ph-sub">BoxPts=0.01127 · Keltner(200) · PPP2(200)</div></div>
      <div style="text-align:right;"><div class="ph-price" id="p31-pr">—</div><div class="ph-dir" id="p31-dr">—</div></div>
    </div>
    <div class="ps">
      <div class="psi"><span class="psv acc" id="31-n">{len(m31):,}</span><span class="psl">Bricks</span></div>
      <div class="psi"><span class="psv bull" id="31-bu">—</span><span class="psl">Bull</span></div>
      <div class="psi"><span class="psv bear" id="31-be">—</span><span class="psl">Bear</span></div>
      <div class="psi"><span class="psv gold" id="31-pc">—</span><span class="psl">Bull%</span></div>
      <div class="psi"><span class="psv acc" id="31-km">—</span><span class="psl">KeltMid</span></div>
      <div class="psi"><span class="psv" id="31-pp">—</span><span class="psl">PPP2</span></div>
    </div>
    <div class="pc" id="w31"><canvas id="cv31"></canvas>
      <div class="pz">
        <button class="pzb" onclick="z31(bW31*1.4)">+</button>
        <button class="pzb" onclick="z31(bW31/1.4)">−</button>
        <button class="pzb w" onclick="f31()">ALL</button>
        <button class="pzb w" onclick="e31()">END</button>
      </div>
    </div>
    <div class="pif"><span>Stage 24 · fed by M25 · repaint-free</span><span style="color:var(--bull)">✓ LOCKED</span></div>
    <div class="psb"><div class="psbt" id="sbt31"><div class="psbh" id="sbh31" style="background:rgba(245,200,66,.2);border:1px solid rgba(245,200,66,.4);"></div></div></div>
  </div>
</div>
<script>
const B25={m25_js};
const B31={m31_js};
const DIGITS=5,fmt=v=>(+v).toFixed(DIGITS);
const KELT_P=200,PPP_P=200;
let k25,p25,k31,p31;
let vs25=0,bW25=2,vs31=0,bW31=2;
let lastP=0,prevP=0,tickN=0,DPR=window.devicePixelRatio||1,progStart=Date.now();
const cv25=document.getElementById('cv25'),ctx25=cv25.getContext('2d');
const cv31=document.getElementById('cv31'),ctx31=cv31.getContext('2d');
const w25=document.getElementById('w25'),w31=document.getElementById('w31');
function keltner(B,p){{const n=B.length,U=[],M=[],L=[];for(let i=0;i<n;i++){{if(i<p-1){{U.push(null);M.push(null);L.push(null);continue;}}let sc=0,sr=0;for(let j=i;j>i-p;j--){{sc+=B[j][2];sr+=Math.abs(B[j][2]-B[j][1]);}}const mid=sc/p,avg=sr/p;U.push(mid+avg);M.push(mid);L.push(mid-avg);}}return{{U,M,L}};}}
function psar(B,step,mx){{const n=B.length;if(n<2)return new Array(n).fill(null);const out=new Array(n).fill(null);let bull=true,ep=B[0][1],af=step,val=B[0][2];out[0]=val;for(let i=1;i<n;i++){{const ph=B[i-1][1],pl=B[i-1][2],ch=B[i][1],cl=B[i][2];let ns=val+af*(ep-val);if(bull){{ns=Math.min(ns,pl,i>=2?B[i-2][2]:pl);if(cl<ns){{bull=false;ns=ep;ep=cl;af=step;}}else if(ch>ep){{ep=ch;af=Math.min(af+step,mx);}}}}else{{ns=Math.max(ns,ph,i>=2?B[i-2][1]:ph);if(ch>ns){{bull=true;ns=ep;ep=ch;af=step;}}else if(cl<ep){{ep=cl;af=Math.min(af+step,mx);}}}}val=ns;out[i]=val;}}return out;}}
function ppp2(B,mP){{const n=B.length,ps=psar(B,0.02,0.02);const cb=new Array(n).fill(null),up=new Array(n).fill(null),dn=new Array(n).fill(null);for(let i=mP-1;i<n;i++){{let s=0;for(let j=i;j>i-mP;j--)s+=B[j][2];if(ps[i]!==null)cb[i]=(ps[i]+s/mP)/2;}}for(let i=1;i<n;i++){{if(cb[i]===null||cb[i-1]===null)continue;if(cb[i]>cb[i-1])up[i]=cb[i];else dn[i]=cb[i];}}return{{cb,up,dn}};}}
const DBG=['rgba(56,189,248,.055)','rgba(167,139,250,.055)','rgba(52,211,153,.04)','rgba(245,200,66,.04)','rgba(232,54,93,.04)','rgba(56,189,248,.04)','rgba(167,139,250,.04)','rgba(52,211,153,.035)','rgba(245,200,66,.04)'];
function dayBands(B){{const map={{}},order=[];B.forEach((b,i)=>{{const k=new Date(b[0]*1000).toISOString().slice(0,10);if(!map[k]){{map[k]={{s:i,e:i}};order.push(k);}}else map[k].e=i;}});return order.map(k=>(({{...map[k]}})));}}
function resize(){{DPR=window.devicePixelRatio||1;for(const[cv,wrap]of[[cv25,w25],[cv31,w31]]){{const W=wrap.clientWidth,H=wrap.clientHeight;cv.width=Math.round(W*DPR);cv.height=Math.round(H*DPR);cv.style.width=W+'px';cv.style.height=H+'px';}}}}
function f25(){{bW25=Math.max(1,Math.floor((w25.clientWidth-58)/B25.length));vs25=0;drawBoth();}}
function f31(){{bW31=Math.max(1,Math.floor((w31.clientWidth-58)/B31.length));vs31=0;drawBoth();}}
function e25(){{const v=Math.max(1,Math.floor((w25.clientWidth-58)/bW25));vs25=Math.max(0,B25.length-v);drawBoth();}}
function e31(){{const v=Math.max(1,Math.floor((w31.clientWidth-58)/bW31));vs31=Math.max(0,B31.length-v);drawBoth();}}
function z25(bw){{bW25=Math.max(1,Math.min(60,bw));drawBoth();}}
function z31(bw){{bW31=Math.max(1,Math.min(60,bw));drawBoth();}}
function drawPanel(cv,ctx,wrap,B,k,p,vs,bW,acCol,sbhId,sbtId){{
  if(!B.length)return;
  const W=wrap.clientWidth,H=wrap.clientHeight,PL=2,PR=60,PT=9,PB=16,CW=W-PL-PR,CH=H-PT-PB;
  const visible=Math.max(1,Math.floor(CW/bW));
  vs=Math.max(0,Math.min(vs,B.length-visible));
  const show=B.slice(vs,vs+visible),N=show.length,off=vs;
  ctx.setTransform(DPR,0,0,DPR,0,0);ctx.clearRect(0,0,W,H);ctx.fillStyle='#090d18';ctx.fillRect(0,0,W,H);
  if(!N)return;
  let lo=Infinity,hi=-Infinity;
  show.forEach(b=>{{lo=Math.min(lo,b[1],b[2]);hi=Math.max(hi,b[1],b[2]);}});
  if(k)show.forEach((_,i)=>{{const ki=off+i;if(k.U[ki]!==null){{lo=Math.min(lo,k.L[ki]);hi=Math.max(hi,k.U[ki]);}}}});
  const pad=(hi-lo)*0.04;lo-=pad;hi+=pad;const RNG=hi-lo||1;
  const Y=v=>PT+CH*(1-(v-lo)/RNG),XL=i=>PL+i*(CW/N),XC=i=>PL+(i+.5)*(CW/N),BW=Math.max(0.4,CW/N-0.3);
  const bands=dayBands(show);
  bands.forEach((band,di)=>{{ctx.fillStyle=DBG[di%DBG.length];ctx.fillRect(XL(band.s),PT,XL(Math.min(N,band.e+1))-XL(band.s),CH);}});
  bands.forEach((band,di)=>{{if(!di)return;const x=XL(band.s);ctx.strokeStyle='rgba(26,47,72,.75)';ctx.lineWidth=1;ctx.setLineDash([2,5]);ctx.beginPath();ctx.moveTo(x,PT);ctx.lineTo(x,PT+CH);ctx.stroke();ctx.setLineDash([]);if(BW>=2){{const d=new Date(show[band.s][0]*1000);ctx.fillStyle='rgba(62,90,114,.8)';ctx.font='8px JetBrains Mono,monospace';ctx.textAlign='center';ctx.fillText('Mar '+d.getUTCDate(),x+14,PT+8);}}}});
  for(let g=0;g<=4;g++){{const v=lo+RNG*(g/4),y=Y(v);ctx.strokeStyle='rgba(19,31,48,.8)';ctx.lineWidth=1;ctx.setLineDash([2,6]);ctx.beginPath();ctx.moveTo(PL,y);ctx.lineTo(W-PR,y);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='rgba(62,90,114,.85)';ctx.font='8px JetBrains Mono,monospace';ctx.textAlign='left';ctx.fillText(fmt(v),W-PR+3,y+3);}}
  if(k){{let go=false;ctx.beginPath();for(let i=0;i<N;i++){{const ki=off+i;if(k.U[ki]===null)continue;go?ctx.lineTo(XC(i),Y(k.U[ki])):(ctx.moveTo(XC(i),Y(k.U[ki])),go=true);}}for(let i=N-1;i>=0;i--){{const ki=off+i;if(k.L[ki]===null)continue;ctx.lineTo(XC(i),Y(k.L[ki]));}}ctx.closePath();ctx.fillStyle='rgba(255,255,255,.03)';ctx.fill();
  ctx.beginPath();go=false;ctx.strokeStyle='rgba(255,255,255,.48)';ctx.lineWidth=1;ctx.setLineDash([]);for(let i=0;i<N;i++){{const ki=off+i;if(k.U[ki]===null)continue;go?ctx.lineTo(XC(i),Y(k.U[ki])):(ctx.moveTo(XC(i),Y(k.U[ki])),go=true);}}ctx.stroke();
  ctx.beginPath();go=false;ctx.strokeStyle='rgba(255,255,255,.48)';ctx.lineWidth=1;for(let i=0;i<N;i++){{const ki=off+i;if(k.L[ki]===null)continue;go?ctx.lineTo(XC(i),Y(k.L[ki])):(ctx.moveTo(XC(i),Y(k.L[ki])),go=true);}}ctx.stroke();
  ctx.beginPath();go=false;ctx.strokeStyle='rgba(255,255,255,.26)';ctx.lineWidth=1;ctx.setLineDash([3,4]);for(let i=0;i<N;i++){{const ki=off+i;if(k.M[ki]===null)continue;go?ctx.lineTo(XC(i),Y(k.M[ki])):(ctx.moveTo(XC(i),Y(k.M[ki])),go=true);}}ctx.stroke();ctx.setLineDash([]);}}
  if(p){{let go=false;ctx.beginPath();go=false;ctx.strokeStyle='#00ff00';ctx.lineWidth=BW>=3?2:1;for(let i=0;i<N;i++){{const ki=off+i;if(p.up[ki]===null)continue;go?ctx.lineTo(XC(i),Y(p.up[ki])):(ctx.moveTo(XC(i),Y(p.up[ki])),go=true);}}ctx.stroke();
  ctx.beginPath();go=false;ctx.strokeStyle='#ff0000';ctx.lineWidth=BW>=3?2:1;for(let i=0;i<N;i++){{const ki=off+i;if(p.dn[ki]===null)continue;go?ctx.lineTo(XC(i),Y(p.dn[ki])):(ctx.moveTo(XC(i),Y(p.dn[ki])),go=true);}}ctx.stroke();
  ctx.beginPath();go=false;ctx.strokeStyle='rgba(0,255,255,.4)';ctx.lineWidth=1;for(let i=0;i<N;i++){{const ki=off+i;if(p.cb[ki]===null)continue;go?ctx.lineTo(XC(i),Y(p.cb[ki])):(ctx.moveTo(XC(i),Y(p.cb[ki])),go=true);}}ctx.stroke();}}
  show.forEach((b,i)=>{{const x=XL(i),y1=Y(b[1]),y2=Y(b[2]);const top=Math.min(y1,y2),bh=Math.max(1,Math.abs(y1-y2));
  if(b[4]===1){{const g=ctx.createLinearGradient(x,top,x,top+bh);g.addColorStop(0,'#26d97f');g.addColorStop(1,'#1aaa62bb');ctx.fillStyle=g;ctx.strokeStyle=BW>=2?'#26d97faa':'#26d97f33';}}
  else{{const g=ctx.createLinearGradient(x,top,x,top+bh);g.addColorStop(0,'#c02248aa');g.addColorStop(1,'#e8365d');ctx.fillStyle=g;ctx.strokeStyle=BW>=2?'#e8365daa':'#e8365d33';}}
  ctx.lineWidth=BW>=2?0.3:0.1;ctx.fillRect(x,top,BW,bh);if(BW>=2)ctx.strokeRect(x,top,BW,bh);}});
  const tg=ctx.createLinearGradient(0,0,W,0);tg.addColorStop(0,'transparent');tg.addColorStop(.2,acCol);tg.addColorStop(.8,acCol);tg.addColorStop(1,'transparent');
  ctx.strokeStyle=tg;ctx.lineWidth=1.5;ctx.setLineDash([]);ctx.beginPath();ctx.moveTo(PL,PT);ctx.lineTo(W-PR,PT);ctx.stroke();
  const lc=show[N-1][2],ly=Y(lc),lb=show[N-1][4]===1;ctx.strokeStyle=lb?'#26d97f55':'#e8365d55';ctx.lineWidth=1;ctx.setLineDash([2,4]);
  ctx.beginPath();ctx.moveTo(PL,ly);ctx.lineTo(W-PR,ly);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=lb?'#26d97f':'#e8365d';
  ctx.font='8px JetBrains Mono,monospace';ctx.textAlign='left';ctx.fillText(fmt(lc),W-PR+3,ly+3);
  cv._show=show;cv._vS=vs;cv._PL=PL;cv._CW=CW;cv._N=N;cv._k=k;cv._p=p;
  const tw=document.getElementById(sbtId).clientWidth,ratio=Math.min(1,visible/B.length),pos=tw*(vs/B.length);
  const thumb=document.getElementById(sbhId);thumb.style.width=Math.max(14,tw*ratio)+'px';thumb.style.left=pos+'px';
}}
function drawBoth(){{drawPanel(cv25,ctx25,w25,B25,k25,p25,vs25,bW25,'#38bdf8','sbh25','sbt25');drawPanel(cv31,ctx31,w31,B31,k31,p31,vs31,bW31,'#f5c842','sbh31','sbt31');}}
function updateStats(id,B,k,p){{
  const bull=B.filter(b=>b[4]===1).length,bear=B.length-bull,last=B[B.length-1],isUp=last[4]===1,n=B.length;
  const lKM=k?k.M[n-1]:null,lPC=p?p.cb[n-1]:null,pUp=p&&p.up[n-1]!==null;
  const set=(eid,v,c)=>{{const e=document.getElementById(eid);if(e){{e.textContent=v;if(c)e.className='psv '+c;}}}};
  set(id+'-n',n.toLocaleString(),'acc');set(id+'-bu',bull.toLocaleString(),'bull');set(id+'-be',bear.toLocaleString(),'bear');
  set(id+'-pc',((bull/n)*100).toFixed(1)+'%','gold');if(lKM!==null)set(id+'-km',fmt(lKM),'acc');
  if(lPC!==null)set(id+'-pp',fmt(lPC)+(pUp?' ▲':' ▼'),pUp?'bull':'bear');
  const pe=document.getElementById('p'+id+'-pr'),de=document.getElementById('p'+id+'-dr');
  if(pe){{pe.textContent=fmt(last[2]);pe.className='ph-price '+(isUp?'bull':'bear');}}
  if(de){{de.textContent=isUp?'▲ BULL':'▼ BEAR';de.className='ph-dir '+(isUp?'bull':'bear');}}
}}
function connect(){{
  const dot=document.getElementById('cd'),txt=document.getElementById('ct');dot.className='dot wait';
  const ws=new WebSocket('wss://stream.binance.com:9443/ws/ltcusdt@trade');
  ws.onopen=()=>{{dot.className='dot live';txt.textContent='Price live';}};
  ws.onmessage=e=>{{try{{const msg=JSON.parse(e.data);if(msg.e!=='trade')return;const price=parseFloat(msg.p);tickN++;prevP=lastP;lastP=price;const pe=document.getElementById('lp');pe.textContent=price.toFixed(DIGITS);pe.className='pe '+(price>=prevP?'up':'dn');document.getElementById('tc').textContent=tickN.toLocaleString()+' ticks';}}catch(e){{}}}};
  ws.onerror=()=>{{dot.className='dot dead';}};ws.onclose=()=>{{dot.className='dot wait';setTimeout(connect,3000);}};
}}
function startRefresh(){{clearInterval(window._rl);progStart=Date.now();window._rl=setInterval(()=>{{const rem=Math.max(0,1-(Date.now()-progStart)/1000);const bar=document.getElementById('rp');if(bar)bar.style.width=(((1-rem)/1)*100)+'%';if(rem<=0){{drawBoth();progStart=Date.now();}}}},100);}}
function setupSB(sbhId,sbtId,which){{let drag=false,x0=0,vs0=0;document.getElementById(sbhId).addEventListener('mousedown',e=>{{drag=true;x0=e.clientX;vs0=which===25?vs25:vs31;e.preventDefault();}});window.addEventListener('mousemove',e=>{{if(!drag)return;const tw=document.getElementById(sbtId).clientWidth;const B=which===25?B25:B31,bW=which===25?bW25:bW31,wrap=which===25?w25:w31;const vis=Math.max(1,Math.floor((wrap.clientWidth-58)/bW));const vs=Math.max(0,Math.min(B.length-vis,vs0+Math.round((e.clientX-x0)/tw*B.length)));if(which===25)vs25=vs;else vs31=vs;drawBoth();}});window.addEventListener('mouseup',()=>{{drag=false;}});}}
setupSB('sbh25','sbt25',25);setupSB('sbh31','sbt31',31);
function setupTouch(cvEl,which){{let tx0=0,tvs0=0,lastDist=0;cvEl.addEventListener('touchstart',e=>{{tx0=e.touches[0].clientX;tvs0=which===25?vs25:vs31;if(e.touches.length===2)lastDist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);e.preventDefault();}},{{passive:false}});cvEl.addEventListener('touchmove',e=>{{if(e.touches.length===2){{const dist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);if(lastDist>0){{if(which===25)z25(bW25*(dist/lastDist));else z31(bW31*(dist/lastDist));}}lastDist=dist;}}else{{const bW=which===25?bW25:bW31,B=which===25?B25:B31,wrap=which===25?w25:w31;const vis=Math.max(1,Math.floor((wrap.clientWidth-58)/bW));const vs=Math.max(0,Math.min(B.length-vis,tvs0+Math.round((tx0-e.touches[0].clientX)/bW)));if(which===25)vs25=vs;else vs31=vs;drawBoth();}}e.preventDefault();}},{{passive:false}});}}
setupTouch(cv25,25);setupTouch(cv31,31);
w25.addEventListener('wheel',e=>{{e.preventDefault();z25(bW25*(e.deltaY<0?1.15:0.87));}},{{passive:false}});
w31.addEventListener('wheel',e=>{{e.preventDefault();z31(bW31*(e.deltaY<0?1.15:0.87));}},{{passive:false}});
window.addEventListener('keydown',e=>{{[25,31].forEach(which=>{{const bW=which===25?bW25:bW31,B=which===25?B25:B31,wrap=which===25?w25:w31;const vis=Math.max(1,Math.floor((wrap.clientWidth-58)/bW));const step=Math.max(1,Math.floor(vis*.2));const cur=which===25?vs25:vs31;let nv=cur;if(e.key==='ArrowRight')nv=Math.min(B.length-vis,cur+step);if(e.key==='ArrowLeft')nv=Math.max(0,cur-step);if(e.key==='Home')nv=0;if(e.key==='End')nv=Math.max(0,B.length-vis);if(which===25)vs25=nv;else vs31=nv;}});drawBoth();}});
const tt=document.getElementById('tt');
function setupHover(cvEl){{cvEl.addEventListener('mousemove',e=>{{if(!cvEl._show)return;const rect=cvEl.getBoundingClientRect();const DPR2=window.devicePixelRatio||1;const mx=(e.clientX-rect.left)*(cvEl.width/rect.width/DPR2);const idx=Math.floor((mx-cvEl._PL)/(cvEl._CW/cvEl._N));if(idx<0||idx>=cvEl._N){{tt.style.display='none';return;}}const b=cvEl._show[idx],ki=cvEl._vS+idx,k=cvEl._k,p=cvEl._p;const dt=new Date(b[0]*1000).toISOString().replace('T',' ').slice(0,19)+' UTC';const bull=b[4]===1;document.getElementById('tth').innerHTML=(bull?'<span class="ttbull">▲ BULL</span>':'<span class="ttbear">▼ BEAR</span>')+' Brick #'+(ki+1)+' · '+dt;let ind='';if(k&&k.U[ki]!==null)ind+=`<span class="ttacc">Kelt: ${{fmt(k.U[ki])}} / ${{fmt(k.M[ki])}} / ${{fmt(k.L[ki])}}</span><br>`;if(p&&p.cb[ki]!==null){{const up=p.up[ki]!==null;ind+=`<span style="color:${{up?'#00ff00':'#ff0000'}}">PPP2: ${{fmt(p.cb[ki])}} ${{up?'▲':'▼'}}</span><br>`;}}document.getElementById('ttb').innerHTML=`Open: ${{fmt(b[1])}}<br>Close: ${{fmt(b[2])}}<br>Vol: ${{b[3].toLocaleString()}}<br>`+(ind?'<hr style="border-color:var(--dim);margin:3px 0">'+ind:'');tt.style.display='block';tt.style.left=(e.clientX+14)+'px';tt.style.top=Math.max(8,e.clientY-28)+'px';}});cvEl.addEventListener('mouseleave',()=>{{tt.style.display='none';}});}}
setupHover(cv25);setupHover(cv31);
window.addEventListener('load',()=>{{
  resize();k25=keltner(B25,KELT_P);p25=ppp2(B25,PPP_P);k31=keltner(B31,KELT_P);p31=ppp2(B31,PPP_P);
  updateStats('25',B25,k25,p25);updateStats('31',B31,k31,p31);f25();f31();connect();startRefresh();
  window.addEventListener('resize',()=>{{resize();f25();f31();drawBoth();}});
}});
</script>
</body>
</html>"""

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    log.info(f"  HTML saved: {HTML_FILE.stat().st_size/1024:.0f} KB")

# ─────────────────────────────────────────────────────────────────────────────
#  WEB SERVER (runs in background thread)
# ─────────────────────────────────────────────────────────────────────────────

BUILDING_PAGE = b"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15">
<title>LTCUSD Chart Loading</title></head>
<body style="background:#050810;color:#38bdf8;font-family:monospace;padding:40px;text-align:center;">
<h2 style="letter-spacing:3px;">#LTCUSD RENKO CHART</h2><br>
<p style="color:#f5c842;">Building chart... collecting first 500 ticks (~3-5 minutes)</p>
<p style="color:#3d5a72;font-size:.8rem;">This page refreshes automatically every 15 seconds.</p>
<div style="width:200px;height:4px;background:#131f30;margin:20px auto;border-radius:2px;overflow:hidden;">
<div style="width:60%;height:100%;background:linear-gradient(90deg,#f5c842,#38bdf8);animation:s 1.5s ease-in-out infinite alternate;"></div></div>
<style>@keyframes s{{from{{margin-left:0}}to{{margin-left:40%}}}}</style>
</body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if HTML_FILE.exists():
            try:
                with open(HTML_FILE, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type',   'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control',  'no-cache, no-store, must-revalidate')
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(200)
            self.send_header('Content-Type',   'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(BUILDING_PAGE)))
            self.end_headers()
            self.wfile.write(BUILDING_PAGE)

    def log_message(self, format, *args):
        pass  # Suppress noisy access logs

def run_web_server():
    from socketserver import TCPServer
    TCPServer.allow_reuse_address = True
    with TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        log.info(f"[WEB] Server running on port {PORT}")
        httpd.serve_forever()

# ─────────────────────────────────────────────────────────────────────────────
#  GAP FILL
# ─────────────────────────────────────────────────────────────────────────────

async def gap_fill(session, last_ts):
    import aiohttp
    if last_ts <= 0:
        return 0
    url = f"{BINANCE_REST}&startTime={last_ts*1000}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                trades = await r.json()
                filled = 0
                for t in trades:
                    ts = t['T'] // 1000
                    if ts <= last_ts:
                        continue
                    append_tick(ts, float(t['p']))
                    filled += 1
                if filled:
                    log.info(f"Gap-fill: {filled} ticks")
                return filled
    except Exception as e:
        log.warning(f"Gap-fill failed: {e}")
    return 0

# ─────────────────────────────────────────────────────────────────────────────
#  BINANCE FEEDER (async)
# ─────────────────────────────────────────────────────────────────────────────

async def run_feeder():
    import aiohttp
    import websockets

    ticks_since_rebuild = 0
    total_ticks = count_ticks()

    log.info("=" * 56)
    log.info("  LTCUSDT Feeder + Chart Server — Render Free Plan")
    log.info(f"  Ticks saved : {total_ticks:,}")
    log.info(f"  Rebuild every {REBUILD_EVERY} new ticks")
    log.info(f"  Web server  : port {PORT}")
    log.info("=" * 56)

    # Build initial chart if HST exists
    if HST_FILE.exists() and not HTML_FILE.exists():
        rebuild_chart()

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                last_ts = get_last_tick_ts()
                if last_ts > 0:
                    filled = await gap_fill(session, last_ts)
                    if filled > 0:
                        ticks_since_rebuild += filled
                        total_ticks += filled

                log.info("Connecting to Binance WebSocket...")

                async with websockets.connect(
                    BINANCE_WS,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    log.info("Connected — receiving ticks")

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if msg.get('e') != 'trade':
                                continue

                            price  = float(msg['p'])
                            ts_sec = msg['T'] // 1000

                            append_tick(ts_sec, price)
                            ticks_since_rebuild += 1
                            total_ticks         += 1

                            if total_ticks % 1000 == 0:
                                dt = datetime.datetime.utcfromtimestamp(ts_sec)
                                log.info(f"Tick #{total_ticks:,}  {dt.strftime('%H:%M:%S')}  {price:.5f}")

                            if ticks_since_rebuild >= REBUILD_EVERY:
                                ticks_since_rebuild = 0
                                rebuild_hst()
                                rebuild_chart()

                        except Exception as e:
                            log.warning(f"Tick error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"WebSocket error: {e} — retry in 10s")
                await asyncio.sleep(10)

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN — start web server + feeder together
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Install packages if needed
    missing = []
    try:
        import websockets
    except ImportError:
        missing.append("websockets")
    try:
        import aiohttp
    except ImportError:
        missing.append("aiohttp")
    if missing:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install"] + missing, check=True)

    # Start web server in background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    log.info(f"Web server started on port {PORT}")

    # Run feeder in main thread (asyncio event loop)
    try:
        asyncio.run(run_feeder())
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
