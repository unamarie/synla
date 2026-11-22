"""Synla's pages. They run on your own computer only (127.0.0.1) and open in your browser.

Protection against other websites: a web page you have open could try to send requests to these pages.
So every request must name this computer as 127.0.0.1 or localhost (stops DNS rebinding), and every change must carry
a secret token that only Synla's own pages know, sent as JSON (other sites cannot send that without asking first).

The idea of the pages: Synla asks, you tap. It sorts what it is sure about on its own and asks about the few windows
it is unsure of, one at a time, with its best guess already picked. Everything else is one click away.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from . import record, recorder, report, roles, workload
from .classify import ASK_BELOW
from .context import BROWSERS, d365, glue, outlook_item, strip_app
from .app import OFF, Problem
from .model import INVISIBLE, KINDS, MANUAL_APP, NOT_WORK, UNKNOWN, reserved
from .store import write_whole

KEYS = "qwertyui"                         # keys for the kinds of work you see, in order
COUNT = {1: "One quick question", 2: "Two quick questions", 3: "Three quick questions", 4: "Four quick questions",
         5: "Five quick questions"}
APPS = {"excel.exe": "Excel", "winword.exe": "Word", "powerpnt.exe": "PowerPoint", "outlook.exe": "Outlook",
        "olk.exe": "Outlook", "ms-teams.exe": "Teams", "teams.exe": "Teams", "msedge.exe": "Edge",
        "chrome.exe": "Chrome", "firefox.exe": "Firefox", "code.exe": "VS Code", "pbidesktop.exe": "Power BI Desktop",
        "explorer.exe": "File Explorer", "notepad.exe": "Notepad", "onenote.exe": "OneNote", "acrobat.exe": "Acrobat",
        "acrord32.exe": "Acrobat", "slack.exe": "Slack", "zoom.exe": "Zoom", "ssms.exe": "SQL Server Management Studio",
        "devenv.exe": "Visual Studio"}
PALETTE = 8      # a Norwegian beach: sea glass, driftwood, heather, marram grass, sea pinks, lichen, fjord, rose hips
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
NOT_SORTED = "Not sorted"
WINDOWS = sys.platform == "win32"        # Synla's own recorder, autostart and the microphone check are for Windows

CSS = """
:root{--paper:#f3f4ef;--surface:#fff;--ink:#1f2b26;--muted:#5d665f;--line:#e1e4dc;--track:#e8eae3;--sea:#0b746c;
 --sun:#dd9314;--sunink:#8a5601;--hatch:#f7deb5;--amber:#f0b43c;--ochre:#c28e24;--ochreink:#7c5500;--green:#3f7a3c;--red:#b8352a;
 --shadow:#1f2b260f;--toastlink:#f0b43c;--pillbg:#f9e9ce;--pillink:#7c5500;--yellowbg:#faf6ed;--yellowline:#ead7b2;--greenbg:#f0f4ef;
 --greenline:#c5d7c5;--redbg:#faf1f0;--redline:#ebc6c3;
 --p0:#078d82;--p1:#8f5214;--p2:#764aa2;--p3:#538c2d;--p4:#b04c7d;--p5:#bd8703;--p6:#006cb4;--p7:#ab4238;
 color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#171a18;--surface:#202421;--ink:#ece8de;--muted:#a3a89f;--line:#353a35;
 --track:#2a2e2a;--sea:#4fbfb0;--sun:#eba941;--sunink:#eba941;--ochre:#d9a850;--ochreink:#d9a850;--green:#8cc68a;--red:#ee7b6b;
 --shadow:#00000059;--toastlink:#7c5500;--pillbg:#3c2a0e;--pillink:#edbf71;--hatch:#5e4216;--yellowbg:#31240e;--yellowline:#5a431a;
 --greenbg:#1c2b18;--greenline:#365030;--redbg:#371b18;--redline:#6a3730;
 --p0:#0aa99b;--p1:#b06f35;--p2:#9a6ec9;--p3:#6ca649;--p4:#d16b9b;--p5:#bf8909;--p6:#328bd6;--p7:#d4675b;
 color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 "Segoe UI",-apple-system,BlinkMacSystemFont,Inter,system-ui,sans-serif;
 font-variant-numeric:tabular-nums}
a{color:var(--sea);text-decoration:none}a:hover{text-decoration:underline}
button{font:inherit;color:inherit}
.top{background:var(--surface);border-bottom:1px solid var(--line)}
.top .in{max-width:900px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;gap:24px}
.brand{font-weight:700;font-size:1.15rem;color:var(--ink)}.brand:hover{text-decoration:none}
nav{display:flex;gap:4px}nav a{padding:6px 12px;border-radius:8px;color:var(--muted);white-space:nowrap}
nav a:hover{text-decoration:none;color:var(--ink)}nav a.on{background:var(--paper);color:var(--ink);font-weight:700}
.rec{margin-left:auto;font-size:.85rem;color:var(--muted);display:flex;align-items:center;gap:7px;white-space:nowrap}
.rec i{width:8px;height:8px;border-radius:50%;background:var(--muted)}.rec.on i{background:var(--green);box-shadow:0 0 0 4px color-mix(in srgb,var(--green) 15%,transparent)}
.rec.none i{background:var(--red)}.rec .link{margin-left:4px}
.menu{position:relative}.menu summary{list-style:none;cursor:pointer;color:var(--muted);font-size:1.3rem;line-height:1;padding:4px 8px;border-radius:8px}
.menu summary::-webkit-details-marker{display:none}.menu[open] summary,.menu summary:hover{background:var(--paper);color:var(--ink)}
.pop{position:absolute;right:0;top:38px;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:6px;
 min-width:240px;box-shadow:0 12px 32px var(--shadow);z-index:20}
.pop button,.pop a{display:block;width:100%;text-align:left;padding:8px 12px;border-radius:8px;border:0;background:none;color:var(--ink);cursor:pointer}
.pop button:hover,.pop a:hover{background:var(--paper);text-decoration:none}.pop hr{border:0;border-top:1px solid var(--line);margin:6px 4px}
main{max-width:900px;margin:0 auto;padding:28px 20px 80px}
.dayhead{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:14px}
.date{color:var(--muted);display:flex;align-items:center;gap:8px}
.step{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.step:hover{text-decoration:none;border-color:var(--muted)}.step.off{visibility:hidden}
.big{font-size:2.6rem;font-weight:700;line-height:1.1;letter-spacing:-1px}
.wl{font-size:.92rem;color:var(--muted);display:flex;align-items:center;gap:7px}.wl b{color:var(--ink)}.wl:hover{text-decoration:none}
.dot{width:11px;height:11px;border-radius:50%;display:inline-block;flex:none;background:var(--muted)}
.dot.green{background:var(--green)}.dot.yellow{background:var(--ochre)}.dot.red{background:var(--red)}.dot.big{width:16px;height:16px;margin-top:4px}
.timeline{margin:6px 0 4px}.track{position:relative;height:30px;background:var(--track);border-radius:8px;overflow:hidden}
.seg{position:absolute;top:0;bottom:0;box-shadow:inset 2px 0 0 var(--track)}
.seg.asked{background:color-mix(in srgb,var(--sun) 12%,transparent);outline:2px dashed var(--ink);outline-offset:-2px;border-radius:4px}
.uns{background:repeating-linear-gradient(45deg,var(--sun) 0 4px,var(--hatch) 4px 8px)!important}
.ticks{position:relative;height:18px;font-size:.72rem;color:var(--muted)}.tick{position:absolute;transform:translateX(-50%);top:3px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.9rem;margin:6px 0 26px;color:var(--muted)}
.lg{display:flex;align-items:center;gap:7px}.lg b{color:var(--ink)}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block;flex:none}
.ask,.done,.panel{background:var(--surface);border:1px solid var(--line);border-radius:16px}
.ask{padding:22px 26px 20px;box-shadow:0 8px 24px var(--shadow)}
.askhead{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:.9rem;font-weight:700}
.prog{display:flex;gap:5px}.prog i{width:22px;height:5px;border-radius:3px;background:var(--line)}.prog i.on{background:var(--sun)}
.ask h2{margin:14px 0 2px;font-size:1rem;color:var(--muted);font-weight:400}
.what{font-size:1.45rem;font-weight:700;line-height:1.25;overflow-wrap:anywhere}
.meta{color:var(--muted);font-size:.92rem;margin:4px 0 14px}
.row{display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:start;margin-top:10px}
.lab{font-size:.85rem;color:var(--muted);padding-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font-size:.95rem;border:1.5px solid var(--line);background:var(--surface);color:var(--ink);border-radius:999px;
 padding:6px 14px 6px 8px;display:flex;align-items:center;gap:8px;cursor:pointer;--c:var(--ink)}
.chip:hover{border-color:var(--muted)}
.chip.on{border-color:var(--c);background:color-mix(in srgb,var(--c) 13%,var(--surface));box-shadow:inset 0 0 0 1px var(--c)}
.chip.ghost{color:var(--muted);border-style:dashed;padding-left:14px}
.isnotwork .kinds{opacity:.35;pointer-events:none}
td.code{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}tr.off td{color:var(--muted)}
.quiet .links{display:flex;gap:18px;flex-wrap:wrap;justify-content:flex-end}
.stack.free{background:repeating-linear-gradient(135deg,var(--track) 0 6px,var(--paper) 6px 12px)}
.ismeeting{display:inline-flex;align-items:center;gap:9px;font-size:.95rem;cursor:pointer;padding-top:2px}
.ismeeting input{width:18px;height:18px;margin:0;accent-color:var(--ink)}
.push{margin:2px 0 12px;padding:10px 14px;border-radius:10px;background:var(--paper);border-left:3px solid var(--sun);font-size:.95rem}
.chip[hidden]{display:none}.more:not([hidden]){display:contents}
.find{font:inherit;font-size:.95rem;border:1.5px solid var(--line);border-radius:999px;padding:6px 14px;background:var(--surface);color:var(--ink);width:220px}
.roles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
.rolecard{display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-start;text-align:left;background:var(--surface);
 border:1.5px solid var(--line);border-radius:12px;padding:12px 14px;cursor:pointer;color:var(--ink)}
.rolecard:hover{border-color:var(--muted)}.rolecard.on{border-color:var(--sea);box-shadow:inset 0 0 0 1px var(--sea)}
.rolecard small{display:block;color:var(--muted);margin-top:4px;line-height:1.35}.rolecard small.planned{color:var(--ink);margin-top:6px}
.knames{display:grid;gap:8px;max-width:620px;margin-top:8px}
.kname{display:grid;grid-template-columns:210px minmax(0,1fr);gap:12px;align-items:center;font-size:.92rem;color:var(--muted)}
.kname input{font:inherit;width:100%;min-width:0;padding:6px 10px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.rolepick{margin:0 0 18px}.rolepick .buttons{margin-top:10px}
.chip .sw{width:10px;height:10px;border-radius:50%}
.newp input{font:inherit;font-size:.95rem;border:1.5px solid var(--sea);border-radius:999px;padding:6px 14px;background:var(--surface);color:var(--ink);width:200px}
.key{font-size:.72rem;color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:0 5px;background:var(--paper);min-width:18px;text-align:center}
.key.light{background:transparent;border-color:color-mix(in srgb,var(--paper) 45%,transparent);color:color-mix(in srgb,var(--paper) 85%,transparent)}
.hint{font-size:.85rem;color:var(--muted);margin:8px 0 0 132px}
.act{display:flex;justify-content:flex-end;align-items:center;gap:10px;margin-top:20px}
.skip{background:none;border:0;color:var(--muted);cursor:pointer;padding:8px 14px;display:flex;gap:8px;align-items:center}
.save{font-weight:700;background:var(--ink);color:var(--paper);border:0;border-radius:10px;padding:10px 18px;display:inline-flex;
 gap:10px;align-items:center;cursor:pointer;white-space:nowrap}.save:hover{text-decoration:none}
.btn{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 14px;cursor:pointer;color:var(--ink);display:inline-block}
.btn:hover{border-color:var(--muted);text-decoration:none}
.link{background:none;border:0;color:var(--sea);cursor:pointer;padding:0}
.link.inline{font:inherit;text-decoration:underline;text-underline-offset:2px}
.inside{list-style:none;margin:-2px 0 10px 128px;padding:2px 0 4px 12px;border-left:2px solid var(--line);font-size:.86rem}
.inside li{display:flex;justify-content:space-between;gap:14px;padding:2px 0}.inside li span{overflow-wrap:anywhere}
.inside small{color:var(--muted);white-space:nowrap}.ask .inside{margin:6px 0 4px}
.done{display:flex;gap:16px;align-items:flex-start;padding:22px 26px}
.ok{width:32px;height:32px;border-radius:50%;background:var(--greenbg);color:var(--green);display:grid;place-items:center;font-weight:700;flex:none}
.ok.open{background:var(--paper);color:var(--muted);border:1.5px dashed var(--line)}.ok.warn{background:var(--yellowbg);color:var(--ochreink)}
.done p{margin:4px 0 0;color:var(--muted)}.done .act{justify-content:flex-start;margin-top:12px}
.quiet{display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-top:22px;font-size:.95rem}
.panel{padding:18px 22px;margin-top:16px}.panel h3{margin:0 0 12px;font-size:1rem;display:flex;justify-content:space-between;align-items:center;gap:8px}
.note{color:var(--muted);font-size:.88rem}
.crow{display:grid;grid-template-columns:118px minmax(0,1fr) 190px 64px 64px;gap:10px;align-items:center;padding:9px 0;border-top:1px solid var(--line);font-size:.93rem}
.crow:first-of-type{border-top:0}.crow small{display:block;color:var(--muted);font-size:.8rem}
.crow .t{color:var(--muted)}.crow .w b{font-weight:600;overflow-wrap:anywhere}.crow .pj{display:flex;gap:8px;align-items:flex-start}
.crow .pj .sw{margin-top:5px;border-radius:50%}.crow .by{font-size:.78rem;color:var(--muted)}.crow .by.you{color:var(--green)}
.crow .by.unsure{color:var(--sunink)}.crow button{justify-self:end}
.foot{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:12px;margin-top:4px}
.ask.edit{margin:4px 0 12px;box-shadow:none;border-color:var(--sea)}
.scope{display:flex;flex-direction:column;gap:4px;font-size:.9rem;margin-bottom:6px}.scope:empty{display:none}
.times{display:flex;align-items:center;gap:8px;color:var(--muted)}
.times input,.field input{font:inherit;padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.verdict{display:flex;gap:12px;align-items:flex-start;border-radius:14px;padding:14px 18px;margin:8px 0 22px;font-size:1.02rem;border:1px solid}
.verdict.yellow{background:var(--yellowbg);border-color:var(--yellowline)}.verdict.green{background:var(--greenbg);border-color:var(--greenline)}
.verdict.red{background:var(--redbg);border-color:var(--redline)}.verdict a{margin-left:6px;font-size:.92rem}
.weekgrid{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:16px;align-items:end;margin-bottom:8px;padding:0 6px}
.col{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;color:var(--ink)}
.col:hover{text-decoration:none}.col:hover .stack{outline:2px solid var(--line);outline-offset:3px}
.col .h{font-weight:700;font-size:.95rem;margin-bottom:6px;display:flex;flex-direction:column;align-items:center;min-height:38px;justify-content:flex-end}
.late{font-size:.72rem;font-weight:400;color:var(--sunink)}
.stack{width:64%;max-width:96px;display:flex;flex-direction:column-reverse;height:200px;border-radius:8px;overflow:hidden;background:var(--track)}
.stack span{display:block;width:100%;flex:none}.stack span+span{border-bottom:2px solid var(--paper)}
.col .d{margin-top:8px;color:var(--muted);font-size:.9rem}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}.two .panel{margin-top:0}
.pill{font-size:.8rem;background:var(--pillbg);color:var(--pillink);border-radius:999px;padding:2px 10px;font-weight:700;white-space:nowrap}
.pr{display:grid;grid-template-columns:minmax(0,130px) 1fr 58px;gap:10px;align-items:center;margin:8px 0;font-size:.93rem}
.pr span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pb{background:var(--track);height:10px;border-radius:5px;overflow:hidden}.pb span{display:block;height:100%;border-radius:5px}
.pv{text-align:right;font-weight:700}
.inv{list-style:none;margin:0;padding:0;font-size:.92rem}.inv li{display:grid;grid-template-columns:118px minmax(0,1fr) auto;gap:8px;padding:7px 0;border-top:1px solid var(--line)}
.inv li:first-child{border-top:0}.inv i{font-style:normal;color:var(--muted)}.inv span{overflow-wrap:anywhere}.inv b{white-space:nowrap}
.inv small{display:block;color:var(--muted);font-size:.8rem;margin-top:2px}
.sealed{display:flex;align-items:center;gap:14px}.sealed > div{flex:1;min-width:0}
.fp{font-family:ui-monospace,Consolas,monospace;font-size:.8rem;color:var(--muted);overflow-wrap:anywhere}
.mini{font-size:.85rem;margin-top:2px}
table{border-collapse:collapse;width:100%}
td,th{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.88rem}tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;white-space:nowrap}td.lvl,td.nw{white-space:nowrap}td.lim{white-space:nowrap}tr.total td{font-weight:700;border-top:2px solid var(--line)}
.panel table{margin:-4px 0}.panel:has(table){overflow-x:auto}
h1{font-size:1.6rem;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 18px}
h2{font-size:1.05rem;margin:26px 0 8px}
.tiles{display:flex;gap:8px;flex-wrap:wrap}.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 10px;
 text-align:center;font-size:.8rem;min-width:84px;color:var(--ink)}.tile b{display:block;font-size:1rem;margin:2px 0}
.tile:hover{text-decoration:none;border-color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:8px 0 6px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.card .n{font-size:1.6rem;font-weight:700}.card .l{color:var(--muted);font-size:.85rem}
.buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.field{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}.field input{min-width:260px}
ul.plain{margin:6px 0;padding-left:20px}ul.plain li{margin:4px 0}
.good{color:var(--green);font-weight:700}.bad{color:var(--red);font-weight:700}
.steps{list-style:none;margin:0;padding:0;counter-reset:step}.steps>li{counter-increment:step}
.steps h3{justify-content:flex-start}.steps h3::before{content:counter(step);display:inline-grid;place-items:center;width:26px;
 height:26px;border-radius:50%;border:1.5px solid var(--line);color:var(--muted);font-size:.85rem;font-weight:700;flex:none}
.prows{display:grid;gap:8px;margin-top:12px}
.prow{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr) 72px;gap:8px;align-items:center}
.prow.withcode{grid-template-columns:minmax(0,1fr) minmax(0,1.5fr) 110px 88px}
.prow input{font:inherit;width:100%;min-width:0;padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.prow.head{font-size:.85rem;color:var(--muted)}.prow button{justify-self:start}
.prow[data-archived] input{color:var(--muted);background:var(--paper)}.prow[data-archived] .pname{text-decoration:line-through}
.field input[type=time]{min-width:0;width:8.5em}.field input[type=number]{min-width:0;width:6em}.chips.calls .chip{padding:6px 18px}
.choice{display:flex;align-items:center;gap:9px;margin-top:12px;cursor:pointer}
.rolecard small .lab{display:block;font-size:.72rem;letter-spacing:.02em;color:var(--muted);margin-bottom:2px}
.import{margin-top:14px}.import summary{cursor:pointer;color:var(--accent);font-weight:600}
.import textarea{font:inherit;width:100%;margin-top:6px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);
 background:var(--surface);color:var(--ink);resize:vertical}
.btn.file{position:relative;overflow:hidden;cursor:pointer}.btn.file input{position:absolute;inset:0;opacity:0;cursor:pointer}.choice input{width:18px;height:18px;margin:0;accent-color:var(--ink)}
.offscreen{position:absolute;left:-9999px;width:1px;height:1px}
#toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);opacity:0;pointer-events:none;background:var(--ink);color:var(--paper);
 border-radius:12px;padding:11px 16px;display:flex;gap:16px;align-items:center;box-shadow:0 12px 32px #00000040;transition:.2s;z-index:30;max-width:92vw}
#toast.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}#toast button{background:none;border:0;color:var(--toastlink);font-weight:700;cursor:pointer}
@media (max-width:720px){.prow,.prow.withcode{grid-template-columns:1fr}.prow.head{display:none}
 .prow{padding-bottom:10px;border-bottom:1px solid var(--line)}
 .top .in{gap:8px;padding:10px 12px}nav a{padding:6px 7px;font-size:.92rem}.brand{font-size:1rem}.rec span{display:none}main{padding:20px 14px 80px}
 .row{grid-template-columns:1fr;gap:6px}.lab{padding-top:0}.hint{margin-left:0}.ask{padding:18px 16px}.two{grid-template-columns:1fr}
 .crow{grid-template-columns:1fr auto}.crow .t,.crow .by{display:none}.crow .pj{grid-column:1}.weekgrid{gap:6px}.stack{width:80%;height:160px}
 .kname{grid-template-columns:1fr;gap:4px}.sealed{flex-wrap:wrap}.sealed > div{flex-basis:calc(100% - 50px)}.sealed .save{width:100%;justify-content:center}td.lim{white-space:normal}
 .big{font-size:2.1rem}.key{display:none}}
@media print{.top,.act,.buttons,.quiet,button,#toast,.noprint{display:none!important}body{background:#fff;color:#000}
 .panel,.card{border-color:#999}main{padding:0}}
"""

JS = r"""
const TOKEN=document.querySelector('meta[name=synla-token]').content,WORD=document.querySelector('meta[name=synla-word]').content;
const $=(q,el=document)=>el.querySelector(q), $$=(q,el=document)=>[...el.querySelectorAll(q)];
async function post(url,body){
 let r;
 try{r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Synla-Token':TOKEN},body:JSON.stringify(body)})}
 catch(e){toast('Synla is not running. Start it again with: python -m synla');return null}
 const text=await r.text();
 if(!r.ok){toast(text||'That did not work.');return null}
 try{return JSON.parse(text)}catch(e){return {}}
}
async function refresh(){
 const open=$$('[data-panel]').filter(x=>!x.hidden).map(x=>x.id);
 const doc=new DOMParser().parseFromString(await (await fetch(location.href)).text(),'text/html');
 $('main').replaceWith(doc.querySelector('main'));
 $('header.top').replaceWith(doc.querySelector('header.top'));
 open.forEach(id=>{const x=document.getElementById(id);if(x)x.hidden=false});
 if(document.activeElement)document.activeElement.blur();
}
let timer;
function toast(msg,undo){
 const t=$('#toast');$('.msg',t).textContent=msg;const b=$('button',t);b.hidden=!undo;b.dataset.undo=undo||'';
 t.classList.add('show');clearTimeout(timer);timer=setTimeout(()=>t.classList.remove('show'),undo?10000:5000);
}
function reveal(c){const m=c.closest('.more');if(m&&m.hidden){m.hidden=false;const b=m.nextElementSibling;if(b&&b.matches('[data-showmore]'))b.hidden=true}}
function direct(card,sel){const g=$(sel,card);return g?$$(':scope > .chip[data-v]:not([data-notwork])',g):[]}
function pick(c){if(!c)return;reveal(c);$$('.chip[data-v]',c.closest('.chips')).forEach(x=>{const on=x===c;x.classList.toggle('on',on);x.setAttribute('aria-pressed',on)});
 const g=c.closest('.ask');if(g&&c.closest('.chips.p'))g.classList.toggle('isnotwork',c.hasAttribute('data-notwork'));
 const t=c.closest('.chips.k')&&$('.meeting',c.closest('.ask,.away'));if(t&&c.dataset.v==='travel')t.checked=false}
function choose(box,sel,v){const c=$$(sel+' .chip[data-v]',box).find(x=>x.dataset.v===v);if(c)pick(c);else $$(sel+' .chip',box).forEach(x=>x.classList.remove('on'))}
const active=()=>{const e=$('#editor');return e&&!e.hidden?e:$('.ask.q')};
async function saveGap(card){
 const p=$('.chips.p .chip.on',card),k=$('.chips.k .chip.on',card);
 if(!p){toast('Pick a '+WORD+', or Not work.');return}
 const body={date:card.dataset.date,code:card.dataset.code};
 if(p.hasAttribute('data-notwork'))body.notWork=true;
 else{if(!k){toast('Pick a kind of work.');return}
  Object.assign(body,{project:p.dataset.v,kind:k.dataset.v,start:$('.from',card).value,end:$('.to',card).value,
   meeting:$('.meeting',card).checked})}
 const res=await post('/api/gap',body);if(!res)return;
 await refresh();
 toast(body.notWork?'Saved: not work.':'Saved: '+body.project+', '+k.dataset.label.toLowerCase()+(body.meeting?', meeting':'')+', '+body.start+' to '+body.end+'.',res.undo);
}
async function saveOff(card,kind){
 const res=await post('/api/dayoff',{date:card.dataset.date,kind:kind});if(!res)return;
 await refresh();toast(kind?'Saved: '+$('.chip[data-v="'+kind+'"]',card).dataset.label.toLowerCase()+'.':'Saved: not a day off.',res.undo);
}
async function save(card){
 if(card.dataset.type==='gap')return saveGap(card);
 if(card.dataset.type==='offday'){const o=$('.chips.p .chip.on',card);if(!o){toast('Pick the kind of day, or I worked.');return}return saveOff(card,o.dataset.v)}
 const p=$('.chips.p .chip.on',card),nw=p&&p.hasAttribute('data-notwork'),k=nw?null:$('.chips.k .chip.on',card);
 if(!p){toast('Pick a '+WORD+', or Not work.');return}
 const res=await post('/api/answer',{date:card.dataset.date,ids:JSON.parse(card.dataset.ids),how:card.dataset.how,
  asked:card.dataset.asked==='1',project:p.dataset.v,kind:k?k.dataset.v:''});
 if(!res)return;
 await refresh();toast('Saved: '+(nw?'not work':p.dataset.v+(k?', '+k.dataset.label.toLowerCase():''))+'.',res.undo);
}
async function skip(card){
 const res=await post('/api/skip',{date:card.dataset.date,code:card.dataset.code});
 if(res){await refresh();toast(card.dataset.type==='offday'?'OK, not a day off.':"Skipped. It keeps Synla's best guess.",res.undo)}
}
function newButton(){const b=document.createElement('button');b.type='button';b.className='chip ghost';b.dataset.newproject='';
 b.textContent='+ New '+WORD;return b}
function newProject(btn){
 const wrap=btn.closest('.newp');
 const input=document.createElement('input');input.type='text';input.maxLength=60;input.placeholder='Name, then Enter';
 input.setAttribute('aria-label','New '+WORD+' name');wrap.replaceChildren(input);input.focus();
 input.addEventListener('keydown',async e=>{
  if(e.key==='Escape'){wrap.replaceChildren(newButton());return}
  if(e.key!=='Enter')return;
  e.preventDefault();const name=input.value.trim();if(!name)return;
  const res=await post('/api/project',{name});if(!res)return;
  $$('.chips.p').forEach(g=>{
   if($$('.chip[data-v]',g).some(x=>x.dataset.v===res.name))return;
   const c=document.createElement('button');c.type='button';c.className='chip';c.dataset.v=res.name;c.style.setProperty('--c',res.color);
   const sw=document.createElement('i');sw.className='sw';sw.style.background=res.color;c.append(sw,document.createTextNode(res.name));
   g.insertBefore(c,g.querySelector(':scope > .more, :scope > .newp, :scope > [data-showmore]'));
  });
  const box=wrap.closest('.ask,.away');wrap.replaceChildren(newButton());choose(box,'.chips.p',res.name);
 });
}
function change(row){
 const ed=$('#editor');row.after(ed);ed.hidden=false;ed.dataset.ids=row.dataset.ids;ed.dataset.how='block';
 choose(ed,'.chips.p',row.dataset.project);choose(ed,'.chips.k',row.dataset.kind);
 const scope=$('.scope',ed);scope.replaceChildren();
 const wins=JSON.parse(row.dataset.windows||'[]');
 const add=(text,ids,how,on)=>{const l=document.createElement('label'),r=document.createElement('input');r.type='radio';r.name='scope';r.checked=on;
  r.addEventListener('change',()=>{ed.dataset.ids=ids;ed.dataset.how=how});l.append(r,document.createTextNode(' '+text));scope.append(l)};
 if(wins.length>1){add('All of it',row.dataset.ids,'block',true);wins.forEach(w=>add('Only '+w.title+' ('+w.time+')',JSON.stringify(w.ids),'split',false))}
}
async function addTime(box){
 const p=$('.chips.p .chip.on',box),k=$('.chips.k .chip.on',box),from=$('.from',box).value,to=$('.to',box).value;
 if(!from||!to){toast('Fill in from and to.');return}
 if(!p){toast('Pick a '+WORD+'.');return}
 const res=await post('/api/time',{date:box.dataset.date,start:from,end:to,project:p.dataset.v,kind:k?k.dataset.v:'',
  meeting:$('.meeting',box).checked});
 if(res){await refresh();toast('Added '+from+' to '+to+'.','time:'+res.id)}
}
function rows(){return $$('.prow[data-row]').map(r=>({old:r.hasAttribute('data-old')?r.dataset.old:null,
 name:$('.pname',r).value,words:$('.pwords',r).value,code:r.querySelector('.prcode')?r.querySelector('.prcode').value:(r.dataset.code||''),
 archived:r.hasAttribute('data-archived')}))}
function parseList(text){
 const lines=text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean).slice(0,500);if(!lines.length)return [];
 const sep=['\t',';',','].find(s=>lines.filter(l=>l.includes(s)).length>=Math.ceil(lines.length/2));
 const code=v=>/^[A-Za-z0-9._\-\/]{1,20}$/.test(v)&&/\d/.test(v);
 let all=lines.map(l=>(sep?l.split(sep):[l]).map(c=>c.trim().replace(/^"(.*)"$/,'$1').trim()));
 if(all.length>1&&!/\d/.test(lines[0])&&/name|navn|project|prosjekt|code|kode|number|nummer|\bnr\b/i.test(lines[0]))all=all.slice(1);
 return all.map(cols=>{const at=cols.length>1?cols.findIndex(code):-1,rest=cols.filter((c,i)=>i!==at&&c);
  return {name:(rest[0]||'').slice(0,60),code:at>=0?cols[at]:'',words:rest.slice(1).join(', ').slice(0,600)}}).filter(r=>r.name);
}
function addList(list){
 const have=new Map($$('.prow[data-row]').map(r=>[$('.pname',r).value.trim().toLowerCase(),r]).filter(x=>x[0]));
 let added=0;
 for(const it of list){
  let r=have.get(it.name.toLowerCase());
  if(!r){r=$$('.prow[data-row]').find(x=>!$('.pname',x).value.trim()&&!x.hasAttribute('data-old'));
   if(!r){r=$('#newrow').content.firstElementChild.cloneNode(true);$('#prows').append(r)}
   $('.pname',r).value=it.name;have.set(it.name.toLowerCase(),r);added++}
  if(it.words&&!$('.pwords',r).value)$('.pwords',r).value=it.words;
  const c=r.querySelector('.prcode');if(c&&it.code&&!c.value)c.value=it.code;
  if(it.code)r.dataset.code=it.code;
 }
 toast(added?'Added '+added+' to the list. Check it, then save.':'They were in the list already.');
}
function readFile(file){
 const go=enc=>new Promise(res=>{const f=new FileReader();f.onload=()=>res(String(f.result));f.readAsText(file,enc)});
 return go('utf-8').then(t=>t.includes('\ufffd')?go('windows-1252'):t);   // Excel in Norway often saves CSV that way
}
document.addEventListener('change',async e=>{
 if(!e.target.matches('[data-importfile]')||!e.target.files.length)return;
 if(e.target.files[0].size>2000000){toast('That file is too large for a list.');return}
 addList(parseList(await readFile(e.target.files[0])));e.target.value='';
});
let starting=false;
async function startRecording(){
 if(starting)return;starting=true;toast('Starting the recorder.');
 const r=await post('/api/record',{});starting=false;if(r){await refresh();toast('Recording.')}
}
document.addEventListener('click',async e=>{
 const on=q=>e.target.closest(q);let b;
 const m=$('#menu');if(m&&m.open&&!on('#menu'))m.open=false;
 if((b=on('.chips.k .chip[data-v]'))&&b.classList.contains('on')){b.classList.remove('on');b.setAttribute('aria-pressed','false');return}
 if(b=on('.chip[data-v]')){pick(b);return}
 if(b=on('[data-newproject]')){newProject(b);return}
 if(b=on('[data-showmore]')){const m=b.previousElementSibling;m.hidden=false;b.hidden=true;const f=$('.find',m);if(f)f.focus();return}
 if(b=on('[data-role]')){if(await post('/api/settings',{role:b.dataset.role}))location.reload();return}
 if(b=on('[data-savenames]')){const names={};$$('.kindname').forEach(x=>names[x.dataset.kind]=x.value);
  if(await post('/api/settings',{kindNames:names}))location.reload();return}
 if(b=on('[data-save]')){save(b.closest('.ask'));return}
 if(b=on('[data-setoff]')){const box=b.closest('section')||b;let kind='';
  if(b.dataset.setoff==='pick'){const o=$('.chips.o .chip.on',box);if(!o){toast('Pick the kind of day.');return}kind=o.dataset.v}
  const res=await post('/api/dayoff',{date:b.dataset.date,kind:kind});if(res){await refresh();toast(kind?'Saved: day off.':'Saved: not a day off.',res.undo)}return}
 if(b=on('[data-skip]')){skip(b.closest('.ask'));return}
 if(b=on('[data-cancel]')){$('#editor').hidden=true;return}
 if(b=on('[data-inside]')){const l=document.getElementById(b.dataset.inside);if(l){l.hidden=!l.hidden;b.setAttribute('aria-expanded',String(!l.hidden))}return}
 if(b=on('[data-change]')){change(b.closest('.crow'));return}
 if(b=on('[data-toggle]')){const x=document.getElementById(b.dataset.toggle);x.hidden=!x.hidden;if(!x.hidden)x.scrollIntoView({behavior:'smooth',block:'nearest'});return}
 if(b=on('[data-more]')){if(await post('/api/more',{date:b.dataset.more}))refresh();return}
 if(b=on('[data-approve]')){const r=await post('/api/approve',{date:b.dataset.approve});if(r){await refresh();toast('Marked '+r.count+' as checked by you.',r.undo)}return}
 if(b=on('[data-addtime]')){addTime(b.closest('.away'));return}
 if(b=on('[data-removetime]')){if(await post('/api/time/remove',{start:b.dataset.removetime})){await refresh();toast('Removed.')}return}
 if(b=on('[data-undo]')){const u=b.dataset.undo;$('#toast').classList.remove('show');
  const r=u.startsWith('time:')?await post('/api/time/remove',{start:u.slice(5)}):await post('/api/undo',{token:u});
  if(r){await refresh();toast('Undone.')}return}
 if(b=on('[data-pause]')){if(await post('/api/pause',{minutes:b.dataset.pause})){await refresh();toast(b.dataset.pause==='0'?'Recording again.':'Recording paused.')}return}
 if(b=on('[data-seal]')){if(await post('/api/seal',{week:b.dataset.seal})){await refresh();toast('Sealed in your record book.')}return}
 if(b=on('[data-copy]')){let t=document.getElementById(b.dataset.copy).value;
  if(b.dataset.copy==='tsv'){const sep=(1.5).toLocaleString().charAt(1);t=t.replace(/(\d)\.(\d)/g,'$1'+sep+'$2')}
  await navigator.clipboard.writeText(t);toast('Copied.');return}
 if(b=on('[data-saveemail]')){if(await post('/api/settings',{recordEmail:$('#email').value})){await refresh();toast('Saved.')}return}
 if(b=on('[data-savecodes]')){const projects={},absence={};$$('.pcode').forEach(x=>projects[x.dataset.project]=x.value);
  $$('.acode').forEach(x=>absence[x.dataset.kind]=x.value);if(await post('/api/codes',{projects,absence})){await refresh();toast('Codes saved.')}return}
 if(b=on('[data-setting]')){const v=b.dataset.value==='1';if(await post('/api/settings',{[b.dataset.setting]:v})){await refresh();toast(v?'Turned on.':'Turned off.')}return}
 if(b=on('[data-gaps]')){if(await post('/api/settings',{askAboutGaps:b.dataset.gaps==='1'})){await refresh();toast(b.dataset.gaps==='1'?'Synla asks about time away again.':'No more questions about time away.')}return}
 if(b=on('[data-delete]')){if(confirm(b.dataset.confirm)&&await post('/api/delete',{what:b.dataset.delete})){await refresh();toast('Deleted.')}return}
 if(b=on('[data-print]')){window.print();return}
 if(b=on('[data-pickrole]')){$$('[data-pickrole]').forEach(x=>x.classList.toggle('on',x===b));return}
 if(b=on('[data-import]')){const t=$('#importlist');addList(parseList(t.value));t.value='';return}
 if(b=on('[data-addrow]')){const row=$('#newrow').content.firstElementChild.cloneNode(true);$('#prows').append(row);$('.pname',row).focus();return}
 if(b=on('[data-removerow]')){b.closest('.prow').remove();return}
 if(b=on('[data-archive]')){const r=b.closest('.prow'),a=!r.hasAttribute('data-archived');r.toggleAttribute('data-archived',a);
  b.textContent=a?'Bring back':'Archive';return}
 if(b=on('[data-saveprojects]')){if(await post('/api/projects',{projects:rows()})){await refresh();toast('Saved.')}return}
 if(b=on('[data-startrec]')){startRecording();return}
 if(b=on('[data-autostart]')){const v=b.dataset.autostart==='1';if(await post('/api/autostart',{on:v})){await refresh();
  toast(v?'Synla starts when you sign in.':'Synla no longer starts when you sign in.')}return}
 if(b=on('[data-setup]')){const role=$('[data-pickrole].on'),calls=$('.chips.calls .chip.on'),auto=$('#autostart');
  b.disabled=true;toast(auto?'Saving and starting the recorder.':'Saving.');
  const r=await post('/api/setup',{role:role?role.dataset.pickrole:'',projects:rows(),start:$('#daystart').value,
   end:$('#dayend').value,weekly:$('#weekly').value,calls:calls?calls.dataset.v==='1':false,
   record:!!auto,autostart:auto?auto.checked:false});
  b.disabled=false;if(r)location.href='/';return}
});
document.addEventListener('input',e=>{
 if(!e.target.matches('.find'))return;const q=e.target.value.trim().toLowerCase();
 $$('.chip[data-v]:not([data-notwork])',e.target.closest('.chips')).forEach(c=>c.hidden=!!q&&!c.dataset.v.toLowerCase().includes(q));
});
document.addEventListener('keydown',e=>{
 if(e.metaKey||e.ctrlKey||e.altKey)return;
 const m=$('#menu');if(e.key==='Escape'&&m&&m.open){m.open=false;return}
 if(e.key==='Enter'&&e.target.matches('.ask.q .times input')){e.preventDefault();save(e.target.closest('.ask'));return}
 if(e.target.matches('.find')){
  if(e.key==='Enter'){e.preventDefault();const c=$$('.chip[data-v]:not([data-notwork])',e.target.closest('.chips')).find(x=>!x.hidden);if(c){pick(c);e.target.blur()}}
  if(e.key==='Escape')e.target.blur();
  return}
 if(e.target.closest('input,textarea,select'))return;
 const card=active();if(!card)return;const k=e.key.toLowerCase();
 if(/^[1-9]$/.test(k)){pick(direct(card,'.chips.p')[+k-1]);e.preventDefault();return}
 if(k==='0'){pick($('.chips.p [data-notwork]',card));e.preventDefault();return}
 if(k==='m'&&$('.meeting',card)){const t=$('.meeting',card);t.checked=!t.checked;e.preventDefault();return}
 const i=k.length===1?'qwertyui'.indexOf(k):-1;
 if(i>=0){pick(direct(card,'.chips.k')[i]);e.preventDefault();return}
 if(e.key==='Enter'&&(e.target===document.body||e.target.closest('.chip'))){e.preventDefault();save(card);return}
 if(e.key==='Escape'){if(card.id==='editor')card.hidden=true;else skip(card)}
});
"""

# Only Synla's own script runs: the browser checks its fingerprint, so a window name that looks like code stays text.
CSP = (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-"
       f"{base64.b64encode(hashlib.sha256(JS.encode('utf-8')).digest()).decode()}'; connect-src 'self'; img-src 'self' data:; "
       "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---- small helpers ---------------------------------------------------------------------------------------------------
def inside_list(rid: str, items: list, limit: int = 20) -> str:
    """What a block or a question holds: every window or page, longest first, with its time."""
    li = "".join(f"<li><span>{escape(w['title'])}</span><small>{escape(w['where'])} · "
                 f"{'under a minute' if w['minutes'] < 1 else hm(w['minutes'])}</small></li>" for w in items[:limit])
    if len(items) > limit:
        li += f"<li><small>and {len(items) - limit} more</small></li>"
    return f"<ul class='inside' id='{rid}' hidden>{li}</ul>"


def hm(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def app_name(app: str) -> str:
    return app if app == MANUAL_APP else APPS.get((app or "").lower(), re.sub(r"\.exe$", "", app or "", flags=re.I))


OUTLOOK_SAYS = {"email": "Email in Outlook", "invite": "Meeting invite in Outlook", "calendar": "Calendar in Outlook",
                "inbox": "Inbox in Outlook"}


def where(app: str, title: str) -> str:
    """The app, what an Outlook window shows (an email, an invite, the calendar), or the record in Dynamics 365."""
    item = outlook_item(app, title)
    if item:
        return OUTLOOK_SAYS[item]
    crm = d365(title)
    if crm:
        system = "Finance and Operations" if crm[0] == "fo" else "Dynamics 365"
        return f"{crm[1]} in {system}" if crm[1] else system
    return app_name(app)


def is_meeting(b) -> bool:
    """A meeting: mostly a call, or time away you said was a meeting. Synla notes the time, never what was said."""
    calls = sum(s.minutes for s in b.segments if s.call)
    return calls > 0 and calls >= b.minutes / 2


def day_list(days) -> str:
    if [d.weekday() for d in days] == [0, 1, 2, 3, 4]:
        return "every workday"
    names = [f"{d:%A}" for d in days]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def stretches(spans: list) -> list:
    """Stretches of the same window in a row, joined when less than 2 minutes apart."""
    out = []
    for a, b in sorted(spans):
        if out and (a - out[-1][1]).total_seconds() < 120:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def when_text(spans: list) -> str:
    """'33 min at 13:34 and 23 min at 21:00', or 'in 7 stretches between 08:12 and 16:40'."""
    st = stretches(spans)
    if len(st) > 3:
        return f"{hm(sum((b - a).total_seconds() for a, b in st) / 60)} in {len(st)} stretches between {st[0][0]:%H:%M} and {st[-1][1]:%H:%M}"
    parts = [f"{hm((b - a).total_seconds() / 60)} at {a:%H:%M}" for a, b in st]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def shown(title: str, app: str) -> str:
    return strip_app(title or "") or app_name(app)


def day_title(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B}" + (f" {d.year}" if d.year != date.today().year else "")


def week_title(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    span = (f"{monday.day} to {sunday.day} {sunday:%B}" if monday.month == sunday.month
            else f"{monday.day} {monday:%B} to {sunday.day} {sunday:%B}")
    return f"Week {monday.isocalendar()[1]} · {span}"


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def color(app, project: str) -> str:
    if project in (UNKNOWN, NOT_SORTED, "Unsorted", NOT_WORK):
        return "var(--muted)"
    own = app.config.get("projects", {}).get(project)
    if isinstance(own, dict) and HEX.match(str(own.get("color", ""))):
        return own["color"]
    i = app.projects.index(project) if project in app.projects else sum(project.encode())
    return f"var(--p{i % PALETTE})"


def unsure(b) -> bool:
    return not b.confirmed and (b.guess.confidence < ASK_BELOW or b.guess.project == UNKNOWN)


def project_of(b) -> str:
    return NOT_SORTED if b.guess.project == UNKNOWN else b.guess.project


def ids_attr(ids) -> str:
    return escape(json.dumps(list(ids)), quote=True)


def project_chips(app, picked: str = "", keys: bool = True, notwork: bool = False) -> str:
    """The projects you use most as buttons, in your list's order, Synla's guess if it is another one, and the rest
    one search away under More. notwork: also Not work, for private time at the computer."""
    shown, more = app.project_choices(picked if picked != NOT_WORK else "")
    w = escape(roles.word(app.config).lower())

    def chip(p, i=None):
        c = color(app, p)
        key = f"<span class='key'>{i + 1}</span>" if keys and i is not None and i < 9 else ""
        on = p == picked
        return (f"<button type='button' class='chip{' on' if on else ''}' aria-pressed='{str(on).lower()}' "
                f"data-v='{escape(p, quote=True)}' style='--c:{c}'>{key}<i class='sw' style='background:{c}'></i>"
                f"{escape(p)}</button>")
    new = f"<span class='newp'><button type='button' class='chip ghost' data-newproject>+ New {w}</button></span>"
    html = "".join(chip(p, i) for i, p in enumerate(shown))
    if notwork:
        on = picked == NOT_WORK
        html += (f"<button type='button' class='chip{' on' if on else ''}' aria-pressed='{str(on).lower()}' "
                 f"data-v='{NOT_WORK}' data-notwork title='Private time. Not counted, and Synla never asks why.'>"
                 f"{'<span class=key>0</span>' if keys else ''}<i class='sw' style='background:var(--muted)'></i>"
                 f"Not work</button>")
    if not more:
        return html + new
    return (html + f"<span class='more' hidden><input type='search' class='find' placeholder='Find a {w}' "
            f"aria-label='Find a {w}'>{''.join(chip(p) for p in more)}{new}</span>"
            f"<button type='button' class='chip ghost' data-showmore>More ({len(more)})</button>")


def kind_chips(app, picked: str = "project", keys: bool = True) -> str:
    """Your role's kinds of work, in its order, with your own names; the others under More."""
    shown, more = app.kind_choices(picked)

    def chip(k, i=None):
        label = roles.say(app.config, k)
        key = f"<span class='key'>{KEYS[i]}</span>" if keys and i is not None and i < len(KEYS) else ""
        on = k == picked
        return (f"<button type='button' class='chip{' on' if on else ''}' aria-pressed='{str(on).lower()}' data-v='{k}' "
                f"data-label='{escape(label, quote=True)}' title='{escape(roles.TIP.get(k, ''), quote=True)}'>"
                f"{key}{escape(label)}</button>")
    html = "".join(chip(k, i) for i, k in enumerate(shown))
    if more:
        html += (f"<span class='more' hidden>{''.join(chip(k) for k in more)}</span>"
                 f"<button type='button' class='chip ghost' data-showmore>More</button>")
    return html


def why(g) -> str:
    """Synla's reason for its project guess, in plain words."""
    for r in g.reasons:
        for start, words in (("keyword in", "from the words in the title"), ("word ", "from the words in the title"),
                             ("file ", "from the file name"), ("same email thread", "from the email thread"),
                             ("folder ", "from the folder"), ("same window as before", "from your earlier answers"),
                             ("number ", "from the number in the title"),
                             ("app ", "from the app")):
            if r.startswith(start):
                return ", " + words
    return ""


# ---- the page around every page ---------------------------------------------------------------------------------------
def recording(app) -> bool:
    """A recorder works for this folder (also while you are away from the computer), or ActivityWatch is read."""
    if recorder.alive(app.store):
        return True
    last = recorder.last_seen(app.store)
    return bool(last and datetime.now(timezone.utc) - last < timedelta(minutes=2)) or app.source_name == "ActivityWatch"


def status(app) -> str:
    paused = app.store.paused_until()
    if paused:
        return (f"<span class='rec'><i></i><span>Paused until {paused.astimezone():%H:%M}</span>"
                f"<button class='link' data-pause='0'>Resume</button></span>")
    if app.config.get("source", "auto") not in ("auto", "synla", "activitywatch"):
        return "<span class='rec'><i></i><span>Made-up week</span></span>"
    last = recorder.last_seen(app.store)
    if recorder.alive(app.store) or last and datetime.now(timezone.utc) - last < timedelta(minutes=2):
        return "<span class='rec on'><i></i><span>Recording</span></span>"
    if app.source_name == "ActivityWatch":
        return "<span class='rec on'><i></i><span>Reading ActivityWatch</span></span>"
    if WINDOWS:
        return ("<span class='rec none'><i></i><span>Not recording</span>"
                "<button class='link' data-startrec>Start</button></span>")
    return ("<span class='rec none' title='On Mac and Linux, Synla reads ActivityWatch while it runs'><i></i>"
            "<span>Not recording</span></span>")


def menu(app) -> str:
    if app.store.paused_until():
        pause = "<button data-pause='0'>Resume recording</button>"
    else:
        pause = "<button data-pause='60'>Pause for 1 hour</button><button data-pause='rest'>Pause until tomorrow</button>"
    return (f"<details class='menu' id='menu'><summary aria-label='More'>⋯</summary><div class='pop'>{pause}<hr>"
            f"<a href='/projects'>Your {escape(roles.word(app.config, plural=True).lower())}</a>"
            f"<a href='/week/timesheet'>Timesheet</a><a href='/year'>Year report</a><a href='/role'>Your role and words</a>"
            f"<a href='/data'>Your data</a></div></details>")


def page(app, title: str, body: str, token: str, here: str = "") -> str:
    nav = "".join(f"<a href='{u}' class='{'on' if k == here else ''}'>{t}</a>"
                  for u, t, k in (("/", "Today", "today"), ("/week", "Week", "week"), ("/record", "Record book", "record")))
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><meta name='synla-token' content='{escape(token)}'><meta name='synla-word' content='{escape(roles.word(app.config).lower())}'>"
            f"<title>{escape(title)} · Synla</title><style>{CSS}</style></head><body>"
            f"<header class='top'><div class='in'><a class='brand' href='/'>Synla</a><nav>{nav}</nav>{status(app)}{menu(app)}</div></header>"
            f"<main>{damaged(app)}{body}</main><div id='toast' role='status' aria-live='polite'><span class='msg'></span>"
            f"<button type='button' hidden>Undo</button></div><script>{JS}</script></body></html>")


def damaged(app) -> str:
    """A file Synla could not read, said plainly at the top of every page until the file is good again."""
    msgs = list(app.store.damaged.values())
    if not msgs:
        return ""
    return ("<section class='panel sealed'><span class='ok warn'>!</span><div><b>A file needs a look</b>"
            + "".join(f"<div class='note'>{escape(m)}</div>" for m in msgs) + "</div></section>")


# ---- today -----------------------------------------------------------------------------------------------------------
def timeline(app, d: date, bl: list, asked: list | None = None) -> str:
    ws, we = app.workday
    start_h, end_h = int(ws[:2]), int(we[:2]) + (1 if we[3:5] != "00" else 0)
    segs = [s for b in bl for s in b.segments]
    h0, h1 = start_h, end_h
    if segs:
        first, last = min(s.start for s in segs), max(s.end for s in segs)
        h0 = min(start_h, first.hour)
        h1 = max(end_h, 24 if last.date() > d else last.hour + (1 if last.minute or last.second else 0))
    t0 = datetime.combine(d, time(h0), segs[0].start.tzinfo) if segs else datetime.combine(d, time(h0)).astimezone()
    span = (h1 - h0) * 3600
    bars = []
    for b in bl:
        cls, c = ("seg uns", "") if unsure(b) else ("seg", color(app, project_of(b)))
        for s in b.segments:
            x = max(0.0, (s.start - t0).total_seconds() / span * 100)
            w = min(100 - x, max(0.3, s.minutes * 60 / span * 100))
            tip = f"{s.start:%H:%M} to {s.end:%H:%M} · {shown(s.title, s.app)} · {'not sorted yet' if cls != 'seg' else project_of(b)}"
            bars.append(f"<span class='{cls}' style='left:{x:.2f}%;width:{w:.2f}%;background:{c}' title='{escape(tip, quote=True)}'></span>")
    for a, b in asked or []:                   # what Synla is asking about right now: time away, or a window's stretches
        x = max(0.0, (a - t0).total_seconds() / span * 100)
        w = min(100 - x, max(0.6, (b - a).total_seconds() / span * 100))
        bars.append(f"<span class='seg asked' style='left:{x:.2f}%;width:{w:.2f}%' title='Asked about below'></span>")
    every = 1 if h1 - h0 <= 14 else 2
    ticks = "".join(f"<span class='tick' style='left:{(h - h0) / (h1 - h0) * 100:.2f}%'>{h:02d}</span>"
                    for h in range(h0, h1 + 1, every))
    hours = {}
    for b in bl:
        k = "uns" if unsure(b) else project_of(b)
        hours[k] = hours.get(k, 0.0) + b.minutes / 60
    legend = "".join(
        f"<span class='lg'><i class='sw {'uns' if p == 'uns' else ''}' style='background:{'' if p == 'uns' else color(app, p)}'></i>"
        f"{'Not sorted yet' if p == 'uns' else escape(p)} <b>{h:.1f} h</b></span>"
        for p, h in sorted(hours.items(), key=lambda kv: (kv[0] == "uns", -kv[1])))
    return (f"<div class='timeline' role='img' aria-label='The day from {h0:02d} to {h1:02d}'><div class='track'>{''.join(bars)}</div>"
            f"<div class='ticks'>{ticks}</div></div><div class='legend'>{legend}</div>")


def ask_card(app, d: date, q: dict) -> str:
    it = q["items"][0]
    g = it["guess"]
    n = len(q["items"])
    known = g.project if g.project in app.projects or g.project == NOT_WORK else ""
    meta = [where(it["app"], it["title"]), when_text(it["spans"])]
    hint = (f"Synla thinks {escape(known)}{why(g)}." if known
            else "Synla has no guess yet. Your answer teaches it for next time.")
    spot = it.get("spot")
    if spot:
        hint = ("One check a day on time Synla sorted on its own, picked at random. It shows how often Synla is right "
                "when it doesn't ask.")
    question = ("Synla sorted this on its own. Is it right?" if spot else
                "What was this meeting for?" if it["call"] else "What was this?")
    head = "Spot check" if spot and n == 1 else COUNT.get(n, f"{n} quick questions")
    rid = "in-" + hashlib.sha256(it["key"].encode()).hexdigest()[:10]
    more = (f"<small> <button type='button' class='link inline' data-inside='{rid}' aria-expanded='false'>and {it['windows'] - 1}"
            f" more {'pages' if it['app'].lower().removesuffix('.exe') in BROWSERS else 'windows'}</button></small>"
            ) if it.get("windows", 1) > 1 else ""
    pages = [{"title": shown(p["title"], p["app"]), "where": where(p["app"], p["title"]), "minutes": p["minutes"]}
             for p in it.get("pages", [])]
    inside = inside_list(rid, pages) if it.get("windows", 1) > 1 else ""
    dots = "".join(f"<i class='{'on' if i == 0 else ''}'></i>" for i in range(min(n, 8)))
    return f"""<section class='ask q' data-date='{d}' data-ids='{ids_attr(it["ids"])}' data-how='{"spot" if spot else "window"}'
 data-asked='1' data-code='{escape(it["code"])}'>
<div class='askhead'><span>{head}</span><span class='prog'>{dots}</span></div>
<h2>{question}</h2><div class='what'>{escape(shown(it["title"], it["app"]))}{more}</div>{inside}
<div class='meta'>{escape(" · ".join(meta))}</div>
<div class='row'><span class='lab'>{roles.word(app.config)}</span><div class='chips p'>{project_chips(app, known, notwork=True)}</div></div>
<div class='hint'>{hint}</div>
<div class='row kinds'><span class='lab'>Kind of work</span><div class='chips k'>{kind_chips(app, g.kind if g.kind in KINDS else ("" if known == NOT_WORK else "project"))}</div></div>
<div class='row kinds'><span class='lab'></span><span class='note'>Optional. Click a kind again to leave it out.</span></div>
<div class='act'><button type='button' class='skip' data-skip>Skip <span class='key'>Esc</span></button>
<button type='button' class='save' data-save>Save <span class='key light'>Enter</span></button></div></section>"""


def off_chips(current: str | None = None, keys: bool = True) -> str:
    return "".join(f"<button type='button' class='chip{' on' if k == current else ''}' data-v='{k}' data-label='{v}' "
                   f"aria-pressed='{'true' if k == current else 'false'}'>{f'<span class=key>{i}</span>' if keys else ''}{v}</button>"
                   for i, (k, v) in enumerate(OFF.items(), 1))


def offday_card(app, d: date, q: dict) -> str:
    it = q["items"][0]
    return f"""<section class='ask q' data-type='offday' data-date='{d}' data-code='{escape(it["code"])}'>
<div class='askhead'><span>One quick question</span></div>
<h2>Nothing was recorded on {d:%A} {d.day} {d:%B}.</h2><div class='what'>Were you off?</div>
<div class='row'><span class='lab'>Day off</span><div class='chips p'>{off_chips()}</div></div>
<div class='hint'>Only the kind of day is kept, never a reason. It goes into your week and the timesheet.</div>
<div class='act'><button type='button' class='skip' data-skip>I worked <span class='key'>Esc</span></button>
<button type='button' class='save' data-save>Save <span class='key light'>Enter</span></button></div></section>"""


def free_card(app, d: date) -> str:
    """A red day or a day off: nothing to ask."""
    off = app.day_off(d)
    if off:
        return (f"<section class='done'><span class='ok'>✓</span><div><b>{OFF[off]}.</b><p>Synla asks nothing about this "
                f"day. <button class='link' data-setoff='' data-date='{d}'>Not a day off</button></p></div></section>")
    return (f"<section class='done'><span class='ok'>✓</span><div><b>{escape(app.red_day(d))}.</b><p>A red day, so "
            f"Synla asks nothing about it.</p></div></section>")


def dayoff_section(app, d: date) -> str:
    return f"""<section class='panel away' id='dayoff' data-panel hidden data-date='{d}'><h3>Day off</h3>
<p class='note'>A whole day of vacation, sick leave, sick child or time off. Only the kind of day is kept.</p>
<div class='row'><span class='lab'>{d:%A}</span><div class='chips o'>{off_chips(app.day_off(d), keys=False)}</div></div>
<div class='act'><button type='button' class='save' data-setoff='pick' data-date='{d}'>Save</button></div></section>"""


MEETING_TICK = ("<div class='row kinds'><span class='lab'></span><label class='ismeeting'><input type='checkbox' class='meeting' "
                "checked> It was a meeting <span class='key'>M</span></label></div>")


def gap_card(app, d: date, q: dict) -> str:
    it = q["items"][0]
    n = len(q["items"])
    a, b = it["start"], it["end"]
    dots = "".join(f"<i class='{'on' if i == 0 else ''}'></i>" for i in range(min(n, 8)))
    notwork = ("<button type='button' class='chip' aria-pressed='false' data-v='' data-notwork><span class='key'>0</span>"
               "Not work</button>")
    return f"""<section class='ask q gap' data-type='gap' data-date='{d}' data-asked='1' data-code='{escape(it["code"])}'>
<div class='askhead'><span>{COUNT.get(n, f"{n} quick questions")}</span><span class='prog'>{dots}</span></div>
<h2>What was this?</h2><div class='what'>Away from the computer</div>
<div class='meta'>{hm(it["minutes"])} · {a:%H:%M} to {b:%H:%M}</div>
<div class='row'><span class='lab'>{roles.word(app.config)}</span><div class='chips p'>{notwork}{project_chips(app)}</div></div>
<div class='hint'>Not work is enough. Synla never asks why, and keeps only the time.</div>
<div class='row kinds'><span class='lab'>Kind of work</span><div class='chips k'>{kind_chips(app, "project")}</div></div>
{MEETING_TICK}
<div class='row kinds'><span class='lab'>When</span><div class='times'><input type='time' class='from' value='{a:%H:%M}'
aria-label='From'> to <input type='time' class='to' value='{b:%H:%M}' aria-label='To'></div></div>
<div class='act'><button type='button' class='skip' data-skip>Skip <span class='key'>Esc</span></button>
<button type='button' class='save' data-save>Save <span class='key light'>Enter</span></button></div></section>"""


def done_card(app, d: date, q: dict, nums: dict, bl: list) -> str:
    if q["waiting"]:
        return (f"<section class='done'><span class='ok'>✓</span><div><b>That's enough questions for today.</b>"
                f"<p>The rest keeps Synla's best guess. Every answer teaches it, so it asks less as time goes by.</p>"
                f"<div class='act'><button class='btn' data-more='{d}'>Answer {q['waiting']} more</button></div></div></section>")
    if not bl:
        monday = monday_of(d)
        seal = app.record.latest(monday)
        if seal and any(x["date"] == d.isoformat() and x["hours"] for x in seal["data"]["days"]):
            text = (f"The details for this day have been deleted. <a href='/week?date={monday}'>The record book has this "
                    f"week.</a>")
        elif d == date.today():
            text = "Synla shows your day here as it goes."
            if not recording(app):
                text += (" <div class='act'><button class='save' data-startrec>Start recording</button></div>" if WINDOWS
                         else " On this computer Synla reads ActivityWatch while it runs.")
        else:
            text = "Nothing was recorded this day."
        return f"<section class='done'><span class='ok open'></span><div><b>Nothing recorded{' yet' if d == date.today() else ''}.</b><p>{text}</p></div></section>"
    left = nums["unsure"]
    extra = (f"{hm(left)} of small things keep Synla's best guess." if left >= 1
             else "Synla learns from every answer, so it asks less as time goes by.")
    return f"<section class='done'><span class='ok'>✓</span><div><b>All sorted.</b><p>{extra}</p></div></section>"


def check_section(app, d: date, bl: list) -> str:
    rows = []
    for b in bl:
        manual = b.main.app == MANUAL_APP
        who = "added" if manual else "you" if b.confirmed else "unsure" if unsure(b) else "guess"
        tag = {"added": "Added", "you": "You", "guess": "Synla", "unsure": "Not sure"}[who]
        wins = {}
        for s in b.segments:
            w = wins.setdefault(s.key, {"title": shown(s.title, s.app), "where": where(s.app, s.title), "minutes": 0.0,
                                        "ids": [], "glue": glue(s.app, s.title)})
            w["minutes"] += s.minutes
            w["ids"].append(s.id)
        real = sorted((w for w in wins.values() if not w["glue"]), key=lambda w: -w["minutes"])
        rid = f"in{len(rows)}"
        more = (f" <button type='button' class='link inline' data-inside='{rid}' aria-expanded='false'>and {len(real) - 1}"
                f" more</button>") if len(real) > 1 else ""
        inside = inside_list(rid, real) if len(real) > 1 else ""
        wl = [w for w in real if w["minutes"] >= 1][:6]      # dialogs and glances go with All of it
        p = project_of(b)
        act = (f"<button class='link' data-removetime='{escape(b.segments[0].id)}'>Remove</button>" if manual
               else "<button class='link' data-change>Change</button>")
        data = escape(json.dumps([{"title": w["title"][:60], "time": hm(w["minutes"]), "ids": w["ids"]} for w in wl]), quote=True)
        rows.append(
            f"<div class='crow' data-ids='{ids_attr(s.id for s in b.segments)}' data-project='{escape(p, quote=True)}' "
            f"data-kind='{escape(b.guess.kind)}' data-windows='{data}'>"
            f"<span class='t'>{b.start:%H:%M} to {b.end:%H:%M}</span>"
            f"<span class='w'><b>{escape(shown(b.main.title, b.main.app))}</b><small>{escape(where(b.main.app, b.main.title))}{more} · "
            f"{hm(b.minutes)}</small></span>"
            f"<span class='pj'><i class='sw {'uns' if who == 'unsure' else ''}' style='background:{color(app, p)}'></i>"
            f"<span>{escape(p)}<small>{escape(roles.say(app.config, b.guess.kind)) if b.guess.kind in KINDS else ''}"
            f"{' · meeting' if is_meeting(b) and b.guess.kind != 'meeting' else ''}</small></span></span>"
            f"<span class='by {who}'>{tag}</span>{act}</div>{inside}")
    guesses = sum(1 for b in bl if not b.confirmed and not unsure(b))
    foot = (f"<div class='foot'><span class='note'>Looks right? This marks Synla's {guesses} guesses as checked by you.</span>"
            f"<button class='btn' data-approve='{d}'>All correct</button></div>") if guesses else ""
    editor = (f"<div id='editor' class='ask edit' hidden data-date='{d}' data-how='block' data-ids='[]'><div class='scope'></div>"
              f"<div class='row'><span class='lab'>{roles.word(app.config)}</span><div class='chips p'>{project_chips(app, notwork=True)}</div></div>"
              f"<div class='row kinds'><span class='lab'>Kind of work</span><div class='chips k'>{kind_chips(app, '')}</div></div>"
              f"<div class='row kinds'><span class='lab'></span><span class='note'>Optional. Click a kind again to leave it out.</span></div>"
              f"<div class='act'><button type='button' class='skip' data-cancel>Cancel</button>"
              f"<button type='button' class='save' data-save>Save</button></div></div>")
    return (f"<section class='panel' id='check' data-panel hidden><h3>The whole day</h3>{''.join(rows)}{foot}{editor}</section>")


def away_section(app, d: date) -> str:
    return f"""<section class='panel away' id='away' data-panel hidden data-date='{d}'><h3>Time away from the computer</h3>
<p class='note'>A meeting in a room, a site visit, a phone call. Only the time, project, kind and if it was a meeting are
kept, no notes.</p>
<div class='row'><span class='lab'>When</span><div class='times'><input type='time' class='from' aria-label='From'> to
<input type='time' class='to' aria-label='To'></div></div>
<div class='row'><span class='lab'>{roles.word(app.config)}</span><div class='chips p'>{project_chips(app, keys=False)}</div></div>
<div class='row'><span class='lab'>Kind of work</span><div class='chips k'>{kind_chips(app, "project", keys=False)}</div></div>
{MEETING_TICK.replace("<span class='key'>M</span>", "")}
<div class='act'><button type='button' class='save' data-addtime>Add</button></div></section>"""


def mic_card(app) -> str:
    """Asked once, on a computer where Synla's own recorder runs: count calls from the microphone or not."""
    if "detectCalls" in app.config or app.config.get("source", "auto") not in ("auto", "synla") or \
            (not WINDOWS and app.config.get("source") != "synla"):
        return ""
    return ("<section class='panel rolepick'><b>Count calls from the microphone?</b> <span class='note'>When Teams, Zoom, "
            "Webex, Slack or Skype is using the microphone, Synla counts the time as a meeting, also when another window "
            "is in front. It only asks Windows which app holds the microphone, the same as the microphone icon in the "
            "taskbar. It never hears anything.</span><div class='buttons'><button class='btn' data-setting='detectCalls' "
            "data-value='1'>Yes, count calls</button><button class='btn' data-setting='detectCalls' data-value='0'>No"
            "</button></div></section>")


def role_card(app) -> str:
    """Shown once, until you pick a role."""
    if app.config.get("role"):
        return ""
    buttons = "".join(f"<button class='btn' data-role='{k}'>{escape(r['name'])}</button>" for k, r in roles.ROLES.items())
    return (f"<section class='panel rolepick'><b>Which fits your work best?</b> <span class='note'>It picks the kinds of "
            f"work you choose from. Change it any time under ⋯.</span><div class='buttons'>{buttons}</div></section>")


def role_page(app, token: str) -> str:
    current = app.config.get("role") or roles.DEFAULT
    plain = {**app.config, "kindNames": {}}
    cards = "".join(
        f"<button class='rolecard{' on' if k == current else ''}' data-role='{k}'><b>{escape(r['name'])}</b>"
        f"<small><span class='lab'>Kinds of work</span>"
        + escape(", ".join(roles.say({**plain, "role": k}, x) for x in r["kinds"])) + "</small>"
        + (f"<small class='planned'>Planned in this job: "
           f"{escape(', '.join(roles.say({**plain, 'role': k}, x) for x in r['planned']))}</small>" if r.get("planned") else "")
        + "</button>"
        for k, r in roles.ROLES.items())
    own = app.config.get("kindNames") or {}
    names = "".join(
        f"<label class='kname'><span>{escape(roles.say(plain, k))}</span><input class='kindname' data-kind='{k}' maxlength='40' "
        f"value='{escape(own.get(k, '') if isinstance(own.get(k), str) else '', quote=True)}' "
        f"placeholder='{escape(roles.say(plain, k), quote=True)}'></label>" for k in roles.role(app.config)["kinds"])
    body = f"""<h1>Your role</h1><p class='sub'>It picks the kinds of work you choose from, what they are called, and which
count as planned in your job. Underneath, the kinds of work are the same for every role, so the record book and earlier weeks
keep their meaning.</p>
<section class='panel'><div class='roles'>{cards}</div></section>
<h2>Your own words</h2><section class='panel'><p class='note'>Call a kind of work what you call it, for example Error
handling instead of Urgent problems. Leave a box empty to use your role's word.</p><div class='knames'>{names}</div>
<div class='buttons'><button class='btn' data-savenames>Save</button></div></section>
<p class='note'>A meeting is not a kind of work. Synla notes that a stretch was a meeting, never what was said, and asks
what it was for. A meeting about an urgent issue counts as urgent work.</p>
<p class='note'>Buttons you have not used for four weeks move under More, except planned work and the kinds of unplanned
work, which are what Synla measures. Projects show the five you use most, and the rest are one search away.</p>"""
    return page(app, "Your role", body, token)


def today_page(app, d: date, token: str) -> str:
    bl = app.day(d)
    nums = app.day_numbers(d)
    q = app.questions(d)
    prev, nxt = d - timedelta(days=1), d + timedelta(days=1)
    wl = ""
    if bl:
        level = app.workload(d)["level"]
        wl = (f"<a class='wl' href='/week?date={d}'>Week {d.isocalendar()[1]} <span class='dot {level}'></span>"
              f"<b>{level.capitalize()}</b></a>")
    head = (f"<div class='dayhead'><div><div class='date'><a class='step' href='/day?date={prev}' aria-label='Previous day'>‹</a>"
            f"{day_title(d)}<a class='step{' off' if nxt > app.last_day() else ''}' href='/day?date={nxt}' aria-label='Next day'>›</a>"
            f"</div><div class='big'>{hm(nums['total']) if bl else '0 h'}</div>"
            + (f"<div class='note'>And {hm(nums['notwork'])} not work, which is not counted</div>" if nums["notwork"] >= 1 else "")
            + f"</div>{wl}</div>")
    if q["items"]:
        card = {"gap": gap_card, "offday": offday_card}.get(q["items"][0]["type"], ask_card)(app, d, q)
    elif q.get("spot") and not q["waiting"]:
        card = ask_card(app, d, {"items": [q["spot"]]})
    elif app.free(d):
        card = free_card(app, d)
    else:
        card = done_card(app, d, q, nums, bl)
    check = ""
    if bl:
        label = (f"Synla sorted {hm(nums['sure'])} on its own. Check ›" if nums["sure"] >= 1 else "See the whole day ›")
        check = f"<button class='link' data-toggle='check'>{label}</button>"
    off = "" if app.free(d) or d.weekday() >= 5 else "<button class='link' data-toggle='dayoff'>+ Day off</button>"
    quiet = (f"<div class='quiet'>{check or '<span></span>'}<span class='links'>{off}"
             f"<button class='link' data-toggle='away'>+ Time away from the computer</button></span></div>")
    first = q["items"][0] if q["items"] else (q["spot"] if q.get("spot") and not q["waiting"] else None)
    asked = None
    if first and first["type"] != "offday":
        asked = [(first["start"], first["end"])] if first["type"] == "gap" else [tuple(x) for x in stretches(first["spans"])]
    body = (role_card(app) + mic_card(app) + head + (timeline(app, d, bl, asked) if bl else "") + card + quiet
            + (check_section(app, d, bl) if bl else "") + away_section(app, d) + dayoff_section(app, d))
    return page(app, "Today" if d == app.today() else day_title(d), body, token, "today" if d == app.today() else "")


# ---- the first time ---------------------------------------------------------------------------------------------------
def project_rows(rows: list, codes: bool = False) -> str:
    """Rows of name, words and (on the Projects page) code. Existing projects carry their name as it was, so a new
    name is a rename."""
    out = []
    for name, v in rows:
        v = v if isinstance(v, dict) else {}
        words = ", ".join(str(k) for k in v.get("keywords", []))
        old = f" data-old='{escape(name, quote=True)}'" if name is not None else ""
        arch = " data-archived" if v.get("archived") else ""
        code = (f"<input class='prcode' aria-label='Code' maxlength='20' placeholder='Code' "
                f"value='{escape(str(v.get('code', '')), quote=True)}'>") if codes else ""
        button = (f"<button type='button' class='link' data-archive>{'Bring back' if arch else 'Archive'}</button>"
                  if codes and name is not None else "<button type='button' class='link' data-removerow>Remove</button>")
        out.append(f"<div class='prow{' withcode' if codes else ''}' data-row{old}{arch}>"
                   f"<input class='pname' aria-label='Name' maxlength='60' placeholder='Name' value='{escape(name or '', quote=True)}'>"
                   f"<input class='pwords' aria-label='Words in window titles' maxlength='600' "
                   f"placeholder='Words, for example budget, forecast' value='{escape(words, quote=True)}'>{code}{button}</div>")
    return "".join(out)


IMPORT = """<details class='import'><summary>Add from a list you have</summary>
<p class='note'>Copy the cells in Excel and paste them here, or paste a list with one on each line. Name and code in
any order, words after them if you have any. A CSV file works too. The list is read here, on this computer.</p>
<textarea id='importlist' rows='5' aria-label='Your list' placeholder='Data platform; 4500; lakehouse, pipeline'></textarea>
<div class='buttons'><button type='button' class='btn' data-import>Add to the list</button>
<label class='btn file'>Choose a CSV file<input type='file' accept='.csv,.txt,.tsv,text/csv,text/plain' data-importfile></label>
</div></details>"""


def setup_page(app, token: str) -> str:
    current = app.config.get("role") or roles.DEFAULT
    cards = "".join(
        f"<button type='button' class='rolecard{' on' if k == current else ''}' data-pickrole='{k}'><b>{escape(r['name'])}</b>"
        f"<small><span class='lab'>Kinds of work</span>" + escape(", ".join(roles.say({'role': k}, x) for x in r["kinds"][:4]))
        + ", and more</small></button>"
        for k, r in roles.ROLES.items())
    rows = project_rows([(p, v) for p, v in app.config.get("projects", {}).items()] + [(None, {})] * 2)
    start, end = app.workday
    weekly = app.config.get("weeklyHours", 37.5)
    calls = ("<li class='panel'><h3>Calls</h3><p>Count a call as a meeting, also when another window is in front?</p>"
             "<p class='note'>Synla then asks Windows whether Teams, Zoom, Webex, Slack or Skype is using the microphone, the "
             "same as the microphone icon in the taskbar. It never listens.</p><div class='chips calls'>"
             "<button type='button' class='chip' data-v='1' aria-pressed='false'>Yes</button>"
             "<button type='button' class='chip on' data-v='0' aria-pressed='true'>No</button></div></li>") if WINDOWS else ""
    go = ("<label class='choice'><input type='checkbox' id='autostart' checked> Start recording when I sign in to Windows</label>"
          "<div class='act'><button class='save' data-setup>Save and start recording</button></div>") if WINDOWS else \
        ("<p class='note'>On this computer Synla reads ActivityWatch while it runs.</p>"
         "<div class='act'><button class='save' data-setup>Save</button></div>")
    body = f"""<h1>Welcome to Synla</h1><p class='sub'>Synla notes which window is in front, so your timesheet almost fills
itself in. Everything stays on this computer, and only you can see it.</p>
<section class='panel'><h3>What Synla keeps</h3><ul class='plain'>
<li>The program and the title of the window in front, and when. Never keystrokes, screenshots, sound or web addresses.</li>
<li>Names of people in chats, and email addresses, are hidden before anything is stored.</li>
<li>Window titles are deleted after 14 days. Your hours stay.</li>
<li>You can pause, or delete everything, at any time.</li></ul></section>
<ol class='steps'>
<li class='panel'><h3>Your work</h3><p class='note'>Pick what fits best. It decides the kinds of work you choose
from, and what they are called. You can rename them later.</p>
<div class='roles'>{cards}</div></li>
<li class='panel'><h3>What you report hours on</h3><p class='note'>One line for each thing you report hours on: a
project, a customer, a supplier, an area or internal work. Synla calls them all projects. Add a few words you see in
window titles when you work on it, such as a customer, a system or a file name. Synla learns the rest from your
answers.</p><div class='prows' id='prows'><div class='prow head'><span>Name</span><span>Words in window titles,
with commas between</span><span></span></div>{rows}</div><div class='buttons'><button type='button' class='btn' data-addrow>Add a line</button></div>
<template id='newrow'>{project_rows([(None, {})])}</template>{IMPORT}</li>
<li class='panel'><h3>Your day</h3><div class='field'>Work usually starts <input type='time' id='daystart' value='{escape(start, quote=True)}'>
and ends <input type='time' id='dayend' value='{escape(end, quote=True)}'></div>
<div class='field'>A normal week is <input type='number' id='weekly' min='1' max='80' step='0.5' value='{weekly:g}'> hours</div>
<p class='note'>Synla only asks about time away from the computer inside your workday.</p></li>
{calls}</ol>
<section class='panel'>{go}<p class='note'>You can change all of this later, under ⋯.</p></section>"""
    return page(app, "Welcome", body, token)


def projects_page(app, token: str) -> str:
    many, one = roles.word(app.config, plural=True), roles.word(app.config).lower()
    items = list(app.config.get("projects", {}).items())
    items.sort(key=lambda kv: bool(isinstance(kv[1], dict) and kv[1].get("archived")))     # archived last
    body = f"""<h1>Your {escape(many.lower())}</h1><p class='sub'>What you report hours on, the words in window titles that
point Synla to each {escape(one)}, and your code in the reporting system.</p>
<section class='panel'><div class='prows' id='prows'><div class='prow withcode head'><span>Name</span><span>Words in window
titles, with commas between</span><span>Code</span><span></span></div>{project_rows(items, codes=True)}</div>
<template id='newrow'>{project_rows([(None, {})], codes=True)}</template>{IMPORT}
<div class='foot'><button type='button' class='btn' data-addrow>Add a {escape(one)}</button>
<button type='button' class='save' data-saveprojects>Save</button></div></section>
<p class='note'>A new name changes the name in your answers and timesheets too. Sealed weeks in the record book keep the
name they had. An archived {escape(one)} keeps its hours, but Synla no longer offers or guesses it.</p>"""
    return page(app, f"Your {many.lower()}", body, token)


# ---- a week ----------------------------------------------------------------------------------------------------------
def _outside(app, segs, day) -> tuple[float, float]:
    ws, we = (time.fromisoformat(x) for x in app.workday)
    before = after = 0.0
    for s in segs:
        tz = s.start.tzinfo
        w0, w1 = datetime.combine(day, ws, tz), datetime.combine(day, we, tz)
        before += max(0.0, (min(s.end, w0) - s.start).total_seconds() / 60)
        after += max(0.0, (s.end - max(s.start, w1)).total_seconds() / 60)
    return before, after


def week_view(app, monday: date) -> dict | None:
    """Everything the week page shows, from the recording while it is there, else from the record book."""
    days = app.week(monday)
    status = app.week_status(monday)
    v = {"monday": monday, "status": status, "days": {}, "projects": {}, "top": [], "live": True}
    if days:
        s = report.summary(days, app.workday, roles.unplanned(app.config))
        assessed = workload.assess(workload.measure(days, s), app.config)
        for d, bl in sorted(days.items()):
            per = {}
            for b in bl:
                per[project_of(b)] = per.get(project_of(b), 0.0) + b.minutes / 60
            before, after = _outside(app, [x for b in bl for x in b.segments], d)
            v["days"][d] = {"hours": sum(per.values()), "projects": per, "early": before >= 15, "late": after >= 15,
                            "urgent": s["per_day"][d]["urgent"], "pushed": s["per_day"][d]["pushed"]}
            for p, h in per.items():
                v["projects"][p] = v["projects"].get(p, 0.0) + h
        sunday = monday + timedelta(days=6)
        v.update(hours=s["hours"], share=s["invisible_share"], unplanned=s["invisible_hours"], assessed=assessed,
                 top=[(r["kind"], r["what"], r["hours"], app.comes_back(r["what"], sunday)) for r in s["top_invisible"]])
        return v
    seal = status["seal"]
    if not seal:
        return None
    st = seal["data"]
    ws, we = app.workday
    for x in st["days"]:
        d = date.fromisoformat(x["date"])
        per = {}
        early = late = False
        urgent = pushed = 0.0
        for r in x["stretches"]:
            p = NOT_SORTED if r["project"] in ("Unsorted", UNKNOWN) else r["project"]
            per[p] = per.get(p, 0.0) + r["minutes"] / 60
            early = early or r["start"][11:16] < ws
            late = late or r["end"][11:16] > we
            if r.get("kind") == "firefighting":
                urgent += r["minutes"] / 60
                pushed += r["minutes"] / 60 * _inside_share(r["start"], r["end"], ws, we)
        v["days"][d] = {"hours": x["hours"], "projects": per, "early": early, "late": late, "urgent": urgent,
                        "pushed": pushed}
    v["projects"] = {(NOT_SORTED if p in ("Unsorted", UNKNOWN) else p): h for p, h in st["projects"].items()}
    inv = {k: h for k, h in st.get("kinds", {}).items() if k in roles.unplanned(app.config)}
    total = st["totalHours"]
    v.update(hours=total, unplanned=sum(inv.values()), share=sum(inv.values()) / total if total else 0.0, live=False,
             assessed=workload.assess(st.get("workload", {}), app.config),
             top=[(k, roles.say(app.config, k), h, None) for k, h in sorted(inv.items(), key=lambda kv: -kv[1])])
    return v


def _inside_share(start: str, end: str, ws: str, we: str) -> float:
    """How much of a sealed stretch lies inside working hours, by the clock."""
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    w0, w1 = (datetime.combine(a.date(), time.fromisoformat(x), a.tzinfo) for x in (ws, we))
    span = (b - a).total_seconds()
    return max(0.0, (min(b, w1) - max(a, w0)).total_seconds()) / span if span > 0 else 0.0


def push_note(app, v: dict) -> str:
    """Urgent work always pushes planned work: how much, on which days, and whether those days ran into the evening."""
    days = sorted(d for d, x in v["days"].items() if x.get("urgent", 0) >= 0.25)
    if not days:
        return ""
    label = escape(roles.say(app.config, "firefighting"))
    when = day_list(days)
    when = when if when.startswith("every") else "on " + when
    pushed = sum(v["days"][d].get("pushed", 0) for d in days)
    if pushed < 0.25:
        hours = sum(v["days"][d]["urgent"] for d in days)
        return f"<p class='push'><b>{label}: {hours:.1f} h</b> {when}, all outside working hours.</p>"
    text = f"<b>{label} pushed {pushed:.1f} h of planned work</b> {when}."
    late = [d for d in days if v["days"][d]["late"]]
    if late:
        who = ("The day" if len(days) == 1 else "Both days" if len(late) == len(days) == 2
               else "All of them" if len(late) == len(days) else day_list(late))
        text += f" {who[0].upper() + who[1:]} ran into the evening."
    return f"<p class='push'>{text}</p>"


def unplanned_row(app, kind: str, what: str, hours: float, back: dict | None) -> str:
    again = (f"<small>Came back on {back['days']} days since {back['since'].day} {back['since']:%b}, "
             f"{back['hours']:.1f} h in all</small>" if back else "")
    return f"<li><i>{escape(roles.say(app.config, kind))}</i><span>{escape(what)}{again}</span><b>{hours:.1f} h</b></li>"


def record_panel(app, monday: date, status: dict) -> str:
    seal = status["seal"]
    ended = monday + timedelta(days=6) < date.today()
    if status["state"] == "open":
        by_synla = app.statement(monday)["unconfirmedHours"]
        note = (f"Synla sorted {by_synla:.1f} h on its own, and the record says so. " if by_synla else "") + \
               spot_line(app) + "Weeks you don't seal are sealed automatically a week after they end."
        return (f"<section class='panel sealed'><span class='ok open'></span><div><b>Not sealed yet.</b> "
                f"{'Seal it to keep this week as it is.' if ended else 'Seal it when the week is done, or now.'}"
                f"<div class='note'>{note}</div></div><button class='save' data-seal='{monday}'>Seal this week</button></section>")
    when = datetime.fromisoformat(seal["sealedAt"]).astimezone()
    on = f"{when.day} {when:%B}"
    if status["state"] == "changed":
        return (f"<section class='panel sealed'><span class='ok warn'>!</span><div><b>Changed since it was sealed</b> on {on}."
                f"<div class='note'>The sealed version stays in the record book. Sealing the change adds version "
                f"{seal['version'] + 1}.</div></div><button class='save' data-seal='{monday}'>Seal the change</button></section>")
    _, text = record.email_text(seal)
    to = app.config.get("recordEmail", "")
    by = " automatically" if seal["data"].get("sealedBy") == "auto" else ""
    version = f", version {seal['version']}" if seal["version"] > 1 else ""
    return (f"<section class='panel sealed'><span class='ok'>✓</span><div><b>Sealed in your record book</b>{by} {on}{version}"
            f"<div class='fp'>Fingerprint {seal['fingerprint'][:16]}…</div>"
            f"<div class='mini'><button class='link' data-copy='mailtext'>Copy text</button>"
            f"{f' · <a href={chr(39)}{escape(record.mailto(to, seal), quote=True)}{chr(39)}>Email it</a>' if to else ''}</div></div>"
            f"<a class='save' href='/record/statement.json?seq={seal['seq']}'>Save a copy</a>"
            f"<textarea id='mailtext' class='offscreen' aria-hidden='true' tabindex='-1'>{escape(text)}</textarea></section>")


def week_page(app, d: date, token: str) -> str:
    monday = monday_of(d)
    nav = (f"<div class='date'><a class='step' href='/week?date={monday - timedelta(days=7)}' aria-label='Previous week'>‹</a>"
           f"{week_title(monday)}<a class='step{' off' if monday + timedelta(days=7) > app.last_day() else ''}' "
           f"href='/week?date={monday + timedelta(days=7)}' aria-label='Next week'>›</a></div>")
    v = week_view(app, monday)
    if not v:
        body = (f"<div class='dayhead'><div>{nav}<div class='big'>0 h</div></div></div>"
                f"<section class='done'><span class='ok open'></span><div><b>Nothing recorded this week.</b></div></section>")
        return page(app, f"Week {monday.isocalendar()[1]}", body, token, "week")
    a = v["assessed"]
    level = a["level"]
    so_far = " so far" if monday <= date.today() <= monday + timedelta(days=6) else ""
    verdict = (f"<div class='verdict {level}'><span class='dot {level} big'></span><div><b>{level.capitalize()} week{so_far}.</b> "
               f"{escape(workload.sentence(a))}<a href='/week/indicators?date={monday}'>What counts</a></div></div>")
    shown_days = [monday + timedelta(days=i) for i in range(7) if i < 5 or monday + timedelta(days=i) in v["days"]]
    scale = max([10.0] + [x["hours"] for x in v["days"].values()])
    order = [p for p in app.projects if p in v["projects"]] + sorted(p for p in v["projects"] if p not in app.projects and p != NOT_SORTED)
    order += [NOT_SORTED] if NOT_SORTED in v["projects"] else []
    cols = []
    for day in shown_days:
        x = v["days"].get(day)
        if not x:
            why = app.free(day)
            cols.append(f"<a class='col' href='/day?date={day}'><div class='h'><span>{escape(why) if why else '-'}</span></div>"
                        f"<div class='stack{' free' if why else ''}'></div><div class='d'>{day:%a}</div></a>")
            continue
        parts = "".join(
            f"<span class='{'uns' if p == NOT_SORTED else ''}' style='height:{x['projects'][p] / scale * 100:.1f}%;"
            f"background:{color(app, p)}' title='{escape(p, quote=True)} {x['projects'][p]:.1f} h'></span>"
            for p in order if x["projects"].get(p, 0) > 0)
        tag = "<span class='late'>evening</span>" if x["late"] else "<span class='late'>early start</span>" if x["early"] else ""
        if day.weekday() >= 5:
            tag = "<span class='late'>weekend</span>"
        cols.append(f"<a class='col' href='/day?date={day}'><div class='h'>{x['hours']:.1f} h{tag}</div>"
                    f"<div class='stack'>{parts}</div><div class='d'>{day:%a}</div></a>")
    pmax = max(v["projects"].values() or [1])
    prow = "".join(f"<div class='pr'><span>{escape(p)}</span><span class='pb'><span class='{'uns' if p == NOT_SORTED else ''}' "
                   f"style='width:{h / pmax * 100:.0f}%;background:{color(app, p)}'></span></span><span class='pv'>{h:.1f} h</span></div>"
                   for p, h in sorted(v["projects"].items(), key=lambda kv: (kv[0] == NOT_SORTED, -kv[1])))
    inv = "".join(unplanned_row(app, k, w, h, r) for k, w, h, r in v["top"][:3])
    more = (f"<p class='note'><a href='/week/indicators?date={monday}#unplanned'>All of it ›</a></p>" if len(v["top"]) > 3 else "")
    deleted = ("" if v["live"] else "<p class='note'>The details for this week have been deleted. This is the week as sealed "
               "in your record book.</p>")
    body = f"""<div class='dayhead'><div>{nav}<div class='big'>{v['hours']:.1f} h</div></div></div>{deleted}{verdict}
<div class='weekgrid'>{''.join(cols)}</div>
<div class='two'><section class='panel'><h3>{roles.word(app.config, True)}</h3>{prow}</section>
<section class='panel'><h3>Unplanned work <span class='pill'>{v['share']:.0%} · {v['unplanned']:.1f} h</span></h3>
{push_note(app, v)}{f"<ul class='inv'>{inv}</ul>" if inv else "<p class='note'>None this week.</p>"}{more}</section></div>
{record_panel(app, monday, v['status'])}
<div class='quiet'><a href='/week/timesheet?date={monday}'>Timesheet ›</a><a href='/week/indicators?date={monday}'>All indicators ›</a>
<a href='/year?y={monday.year}'>Year report ›</a></div>"""
    return page(app, f"Week {monday.isocalendar()[1]}", body, token, "week")


def timesheet_rows(app, monday: date) -> dict:
    days = app.week(monday)
    if days:
        return report.timesheet(days, app.config.get("roundMinutes", 15))
    seal = app.record.latest(monday)
    if not seal:
        return {}
    step = app.config.get("roundMinutes", 15)
    out = {}
    for x in seal["data"]["days"]:
        mins = {}
        for r in x["stretches"]:
            p = app.named(r["project"])
            mins[p] = mins.get(p, 0.0) + r["minutes"]
        out[date.fromisoformat(x["date"])] = {p: round(m / step) * step / 60 for p, m in sorted(mins.items())
                                              if round(m / step) > 0}
    return out


def project_code(app, p: str) -> str:
    v = app.config.get("projects", {}).get(app.named(p))
    return str(v.get("code", "")) if isinstance(v, dict) else ""


def report_lines(app, monday: date) -> list:
    """The week as lines for a reporting system such as Visma: one line per day and project, with your code for it,
    and one line per day off with your absence code. Hours are rounded the way the timesheet rounds them."""
    ts = timesheet_rows(app, monday)
    codes = app.config.get("absenceCodes") or {}
    day_hours = round(float(app.config.get("weeklyHours", 37.5)) / 5, 2)
    lines = []
    for i in range(7):
        day = monday + timedelta(days=i)
        off = app.day_off(day)
        if off and day.weekday() < 5:
            lines.append({"date": day, "code": str(codes.get(off, "")), "what": OFF[off], "hours": day_hours, "off": True})
        for p, h in ts.get(day, {}).items():
            lines.append({"date": day, "code": project_code(app, p), "what": p, "hours": h, "off": False})
    return lines


def timesheet_page(app, d: date, token: str) -> str:
    monday = monday_of(d)
    lines = report_lines(app, monday)
    days = sorted({x["date"] for x in lines})
    keys = list(dict.fromkeys((x["code"], x["what"], x["off"]) for x in sorted(lines, key=lambda x: (x["off"], x["what"]))))
    hrs = {(x["code"], x["what"], x["off"], x["date"]): x["hours"] for x in lines}
    head = "".join(f"<th class='num'>{day:%a %d}</th>" for day in days)
    rows = "".join(f"<tr{' class=off' if off else ''}><td class='code'>{escape(code)}</td><td>{escape(what)}</td>"
                   + "".join(f"<td class='num'>{hrs[(code, what, off, day)]:g}</td>" if hrs.get((code, what, off, day))
                             else "<td></td>" for day in days)
                   + f"<td class='num'><b>{sum(hrs.get((code, what, off, day), 0) for day in days):g}</b></td></tr>"
                   for code, what, off in keys)
    totals = ("<tr class='total'><td></td><td>Total</td>" + "".join(
        f"<td class='num'>{sum(x['hours'] for x in lines if x['date'] == day):g}</td>" for day in days)
        + f"<td class='num'>{sum(x['hours'] for x in lines):g}</td></tr>") if lines else ""
    tsv = "\n".join(["Code\tWhat\t" + "\t".join(f"{day:%a %d.%m}" for day in days) + "\tTotal"] +
                    [f"{cell(code)}\t{cell(what)}\t" + "\t".join(f"{hrs.get((code, what, off, day), 0):g}" for day in days)
                     + f"\t{sum(hrs.get((code, what, off, day), 0) for day in days):g}" for code, what, off in keys])
    table = (f"<section class='panel'><table><tr><th>Code</th><th>{roles.word(app.config)}</th>{head}"
             f"<th class='num'>Total</th></tr>{rows}{totals}</table></section>"
             if lines else "<section class='done'><span class='ok open'></span><div><b>Nothing recorded this week.</b></div></section>")
    body = f"""<h1>Timesheet</h1><p class='sub'>{week_title(monday)} · <a href='/week?date={monday}'>back to the week</a></p>
{table}<div class='buttons noprint'><a class='btn' href='/export.csv?date={monday}&amp;format=excel'>Download for Excel</a>
<a class='btn' href='/export.csv?date={monday}'>Download CSV</a><button class='btn' data-copy='tsv'>Copy table</button>
<button class='btn' data-toggle='codes'>Codes</button><button class='btn' data-print>Print</button></div>
<textarea id='tsv' class='offscreen' aria-hidden='true' tabindex='-1'>{escape(tsv)}</textarea>
{codes_section(app, sorted({x["what"] for x in lines if not x["off"]}))}
<p class='note'>Hours rounded to {app.config.get('roundMinutes', 15)} minutes per {roles.word(app.config).lower()} and day.
A day off counts as {round(float(app.config.get('weeklyHours', 37.5)) / 5, 2):g} hours. Time you never answered uses Synla's
best guess. Download for Excel opens in Excel with Norwegian settings. Download CSV is for reporting systems that import
files: one line per day and {roles.word(app.config).lower()}, with your code.</p>"""
    return page(app, "Timesheet", body, token)


def codes_section(app, projects: list) -> str:
    """Your codes in the reporting system, for example Visma: per project, and per kind of day off."""
    own = app.config.get("absenceCodes") or {}
    rows = "".join(f"<label class='kname'><span>{escape(p)}</span><input class='pcode' data-project='{escape(p, quote=True)}' "
                   f"maxlength='20' value='{escape(project_code(app, p), quote=True)}'></label>"
                   for p in projects if p in app.projects)
    rows += "".join(f"<label class='kname'><span>{v}</span><input class='acode' data-kind='{k}' maxlength='20' "
                    f"value='{escape(str(own.get(k, '')), quote=True)}'></label>" for k, v in OFF.items())
    return (f"<section class='panel noprint' id='codes' data-panel hidden><h3>Codes</h3><p class='note'>The project and "
            f"absence codes your reporting system uses, so the lines match what you type in or import.</p>"
            f"<div class='knames'>{rows}</div><div class='buttons'><button class='btn' data-savecodes>Save</button></div>"
            f"</section>")


def indicators_page(app, d: date, token: str) -> str:
    monday = monday_of(d)
    v = week_view(app, monday)
    if not v:
        return page(app, "Indicators", f"<h1>Workload</h1><p class='sub'>{week_title(monday)}</p><p>Nothing recorded this week.</p>", token)
    a = v["assessed"]
    rows = []
    for r in a["rows"]:
        lvl = workload.LEVELS[r["level"]]
        y, red = r["limits"]
        word = "from" if r["direction"] == "up" else "below"
        rows.append(f"<tr><td>{escape(r['name'])}<div class='note'>{escape(r['what'])}</div></td>"
                    f"<td class='num'>{workload.show(r['value'], r['format'])}</td>"
                    f"<td class='lvl'><span class='dot {lvl}'></span> {lvl.capitalize()}</td>"
                    f"<td class='note lim'>yellow {word} {workload.show(y, r['format'])}, red {word} {workload.show(red, r['format'])}</td></tr>")
    tiles = "".join(f"<a class='tile' href='/week?date={r['monday']}'>week {r['monday'].isocalendar()[1]}"
                    f"<b><span class='dot {r['level']}'></span> {r['hours']:.0f} h</b>{r['unplanned']:.0%} unplanned</a>"
                    for r in app.trend(monday))
    inv = "".join(unplanned_row(app, k, w, h, r) for k, w, h, r in v["top"])
    body = f"""<h1>Workload</h1><p class='sub'>{week_title(monday)} · <a href='/week?date={monday}'>back to the week</a></p>
<div class='verdict {a['level']}'><span class='dot {a['level']} big'></span><div><b>{a['level'].capitalize()} week.</b>
{escape(workload.sentence(a))}</div></div>
<section class='panel'><table><tr><th>Indicator</th><th class='num'>Value</th><th>Level</th><th>Limits</th></tr>{''.join(rows)}</table></section>
<p class='note'>Leading indicators: the conditions that come before overload, not the overload itself. The week is red when
two indicators are red or the score reaches 6, and yellow when one is red or the score reaches 3. Set your own limits under
"indicators" in config.json.</p>
<h2>The last 8 weeks</h2><div class='tiles'>{tiles}</div>
<h2 id='unplanned'>Where the unplanned work came from</h2>
<section class='panel'>{push_note(app, v)}{f"<ul class='inv'>{inv}</ul>" if inv else "<p class='note'>None this week.</p>"}</section>
<h2>How often Synla is right</h2><section class='panel'>{accuracy_panel(app)}</section>"""
    return page(app, "Workload", body, token)


def spot_line(app) -> str:
    a = app.accuracy()
    return f"In spot checks it was right {a['spot_right']} of {a['spot']} times. " if a.get("spot") else ""


def accuracy_panel(app) -> str:
    """From your answers of the last 90 days. Spot checks are picked at random from time Synla sorted on its own, so
    they show how much to trust that time. Questions are about unsure time, so Synla's guesses there are often wrong."""
    a = app.accuracy()
    rows = []
    if a.get("spot"):
        rows.append(f"<li><b>{a['spot_right']} of {a['spot']}</b> spot checks right ({a['spot_right'] / a['spot']:.0%}). "
                    f"This is how much to trust the time Synla sorts on its own.</li>")
    if a.get("asked"):
        rows.append(f"<li><b>{a['asked_right']} of {a['asked']}</b> guesses right when Synla asked. It asks when it is "
                    f"unsure, so these are the hard ones.</li>")
    if a.get("bulk"):
        rows.append(f"<li>{a['bulk']} approved in one click, {a['bulk_changed']} of them changed later. Not counted as "
                    f"right, since you didn't check them one by one.</li>")
    if not rows:
        return "<p class='note'>No answers yet. One spot check a day shows how often Synla is right when it doesn't ask.</p>"
    return f"<ul class='plain'>{''.join(rows)}</ul>"


# ---- the record book and the year ------------------------------------------------------------------------------------
def _when_iso(s: str) -> str:
    t = datetime.fromisoformat(s).astimezone()
    return f"{t.day} {t:%b %Y %H:%M}"


def _when(t):
    return f"{t.astimezone().day} {t.astimezone():%b %Y}" if t else "nothing yet"


def record_page(app, token: str) -> str:
    v = app.record.verify()
    seals = list(reversed(app.record.seals()))
    to = app.config.get("recordEmail", "")
    if v["ok"]:
        check = (f"<span class='ok'>✓</span><div><b>{'All ' + str(v['seals']) + ' records check' if v['seals'] > 1 else 'The record checks'}"
                 f" out.</b><p>Nothing was changed after it was sealed.</p></div>" if v["seals"] else
                 "<span class='ok open'></span><div><b>No sealed weeks yet.</b><p>Seal a week from the Week page, or let "
                 "Synla seal it a week after it ends.</p></div>")
    else:
        check = ("<span class='ok warn'>!</span><div><b class='bad'>Some records do not check out.</b><ul class='plain'>"
                 + "".join(f"<li>{escape(p)}</li>" for p in v["problems"]) + "</ul></div>")
    rows = "".join(
        f"<tr><td><a href='/week?date={s['week']}'>{date.fromisoformat(s['week']).isocalendar()[1]}</a></td>"
        f"<td>{_when_iso(s['sealedAt'])}{' (automatic)' if s['data'].get('sealedBy') == 'auto' else ''}</td>"
        f"<td class='num'>{s['version']}</td><td class='num'>{s['data']['totalHours']:.1f} h</td>"
        f"<td class='num'>{s['data']['unconfirmedHours']:.1f} h</td><td class='fp'>{s['fingerprint'][:16]}</td>"
        f"<td class='noprint'><a href='/record/statement.json?seq={s['seq']}'>Download</a> · "
        f"<a href='{escape(record.mailto(to, s), quote=True)}'>Email</a></td></tr>" for s in seals)
    years = sorted({date.fromisoformat(s["week"]).year for s in seals} | {date.today().year}, reverse=True)
    body = f"""<h1>Record book</h1><p class='sub'>Your own record of every week, sealed so it can't be changed afterwards.</p>
<section class='done'>{check}</section>
<section class='panel'><h3>Sealed weeks</h3><table><tr><th>Week</th><th>Sealed</th><th class='num'>Version</th>
<th class='num'>Hours</th><th class='num'>By Synla</th><th>Fingerprint</th><th></th></tr>{rows or "<tr><td colspan=7 class='note'>None yet.</td></tr>"}</table>
<div class='buttons'>{''.join(f"<a class='btn' href='/year?y={y}'>Year report {y}</a>" for y in years)}</div></section>
<section class='panel noprint'><h3>Your private email</h3><p class='note'>For emailing each week's fingerprint to
yourself. Synla never sends anything itself: it opens a ready email in your mail program, and you press send. If that
program is your work email, the company keeps a copy of what you send. Saving a copy, or pasting the text into your
private email, keeps it out of work email.</p><div class='field'><input type='email' id='email' value='{escape(to, quote=True)}' placeholder='you@example.com'
aria-label='Private email'><button class='btn' data-saveemail>Save</button></div></section>
<h2>How it works</h2><section class='panel'><ul class='plain'>
<li>Each week's record holds the times, projects and kinds of work, and who decided each one: you, time you added, or
Synla's guess. It holds no window names.</li>
<li>Each record gets a fingerprint made from the record and the fingerprint before it. Changing an old record breaks the
chain, and the check above shows where.</li>
<li>You can still correct a sealed week. The correction becomes a new version, and the old one stays.</li>
<li>The chain lives on this computer, so on its own it can't rule out that someone rebuilt it. A copy you keep somewhere
else can, for example in your private email, where your provider keeps the date.</li>
<li>Records are kept for {app.config.get('keepRecordYears', 5):g} years. This is your own record. Your employer keeps the
official record of working hours.</li></ul></section>"""
    return page(app, "Record book", body, token, "record")


def year_page(app, y: int, token: str) -> str:
    rows = app.year(y)
    weekly = float(app.config.get("weeklyHours", 37.5))
    tot = {k: sum(r[k] for r in rows) for k in ("hours", "over", "outside", "unconfirmed")}
    projects = {}
    for r in rows:
        for p, h in r["projects"].items():
            projects[p] = projects.get(p, 0.0) + h
    body_rows = "".join(
        f"<tr><td><a href='/week?date={r['monday']}'>{r['monday'].isocalendar()[1]}</a></td><td>{r['monday'].day} {r['monday']:%b}</td>"
        f"<td class='num'>{r['hours']:.1f}</td><td class='num'>{r['over']:.1f}</td><td class='num'>{r['outside']:.1f}</td>"
        f"<td class='num'>{r['unplanned']:.0%}</td><td><span class='dot {r['level']}'></span> {r['level'].capitalize()}</td>"
        f"<td>{'version ' + str(r['seal']['version']) if r['seal'] else 'not sealed yet'}</td>"
        f"<td class='fp'>{r['seal']['fingerprint'][:12] if r['seal'] else ''}</td></tr>" for r in rows)
    pmax = max(projects.values() or [1])
    prow = "".join(f"<div class='pr'><span>{escape(p)}</span><span class='pb'><span style='width:{h / pmax * 100:.0f}%;"
                   f"background:{color(app, p)}'></span></span><span class='pv'>{h:.0f} h</span></div>"
                   for p, h in sorted(projects.items(), key=lambda kv: -kv[1]))
    body = f"""<div class='dayhead'><div><div class='date'><a class='step' href='/year?y={y - 1}' aria-label='Previous year'>‹</a>
Year report<a class='step{' off' if y >= app.last_day().year else ''}' href='/year?y={y + 1}' aria-label='Next year'>›</a></div>
<div class='big'>{y}</div></div><div class='buttons noprint'><button class='btn' data-print>Print or save as PDF</button>
<a class='btn' href='/year.csv?y={y}'>CSV</a></div></div>
<div class='cards'><div class='card'><div class='n'>{tot['hours']:.{0 if tot['hours'] >= 100 else 1}f} h</div><div class='l'>active time in {len(rows)} week{'s' if len(rows) != 1 else ''}</div></div>
<div class='card'><div class='n'>{tot['over']:.1f} h</div><div class='l'>above {weekly:g} h in a week</div></div>
<div class='card'><div class='n'>{tot['outside']:.1f} h</div><div class='l'>outside working hours</div></div>
<div class='card'><div class='n'>{sum(r['level'] == 'red' for r in rows)}</div><div class='l'>red weeks</div></div></div>
<section class='panel'><table><tr><th>Week</th><th>Monday</th><th class='num'>Hours</th><th class='num'>Above {weekly:g}</th>
<th class='num'>Outside hours</th><th class='num'>Unplanned</th><th>Workload</th><th>Record</th><th>Fingerprint</th></tr>
{body_rows or "<tr><td colspan=9 class='note'>Nothing this year.</td></tr>"}</table></section>
<section class='panel'><h3>{roles.word(app.config, True)}</h3>{prow or "<p class='note'>Nothing yet.</p>"}</section>
<p class='note'>Active time on the computer, plus time you added. It is not the same as paid working time. Sorted by Synla
on its own: {tot['unconfirmed']:.1f} h. Made from your record book on {date.today().day} {date.today():%B %Y}.</p>"""
    return page(app, f"Year {y}", body, token)


def year_csv(app, y: int) -> str:
    f = io.StringIO()
    wr = csv.writer(f)
    wr.writerow(["week", "monday", "hours", "aboveWeeklyHours", "outsideHours", "unplannedShare", "workload", "version",
                 "fingerprint"])
    for r in app.year(y):
        wr.writerow([r["monday"].isocalendar()[1], r["monday"].isoformat(), f"{r['hours']:.2f}", f"{r['over']:.2f}",
                     f"{r['outside']:.2f}", f"{r['unplanned']:.4f}", r["level"], r["seal"]["version"] if r["seal"] else "",
                     r["seal"]["fingerprint"] if r["seal"] else ""])
    return f.getvalue()


def statement_file(app, seq: int) -> tuple[str, str]:
    s = next((x for x in app.record.seals() if x["seq"] == seq), None)
    if s is None:
        raise Problem("That record is not in the record book.")
    doc = {"fingerprint": s["fingerprint"], "previous": s["prev"], "record": s["data"],
           "howToCheck": "SHA-256 of previous + record written as JSON with sorted keys and no spaces equals fingerprint."}
    return f"synla-week-{s['week']}-v{s['version']}.json", json.dumps(doc, ensure_ascii=False, indent=1)


# ---- your data -------------------------------------------------------------------------------------------------------
def data_page(app, token: str) -> str:
    i = app.data_info()
    p = i["privacy"]
    recording = ("paused until " + i["paused_until"].astimezone().strftime("%H:%M")) if i["paused_until"] else \
        ("on" if i["recorder_alive"] or i["recorder_last"] and datetime.now(timezone.utc) - i["recorder_last"] <
         timedelta(minutes=2) else "not running")
    aw_note = ("<p class='note'>ActivityWatch keeps its own history, separate from Synla. Synla can't delete it; "
               "you can, in ActivityWatch.</p>") if i.get("reads_aw") else ""
    start, signin = "", ""
    if WINDOWS:
        if recording == "not running":
            start = "<button class='btn' data-startrec>Start recording</button>"
        on = recorder.autostart_on(app.store.other_dir())
        signin = (f"<p class='note'>Starts when you sign in to Windows: {'yes' if on else 'no'}. "
                  f"<button class='link' data-autostart='{0 if on else 1}'>{'Turn off' if on else 'Turn on'}</button></p>")
    body = f"""<h1>Your data</h1><p class='sub'>What Synla keeps, where, for how long, and how to delete it.</p>
<section class='panel'><h3>Where</h3><div class='fp'>{escape(i['folder'])}</div>
<p class='note'>Only on this computer. Nothing is sent anywhere, and there is no account, server or admin.
{'Window names are encrypted with your Windows login.' if i['encrypted'] else 'Only your account can open the folder.'}</p></section>
<section class='panel'><h3>Recording: {recording}</h3><div class='buttons'>{start}<button class='btn' data-pause='60'>Pause 1 hour</button>
<button class='btn' data-pause='rest'>Pause until tomorrow</button><button class='btn' data-pause='0'>Resume</button></div>
{signin}</section>
<section class='panel'><h3>Microphone check for calls: {'on' if i['detectCalls'] else 'off'}</h3><p class='note'>When it is
on, Synla asks Windows whether Teams, Zoom, Webex, Slack or Skype is using the microphone, the same as the microphone icon
in the taskbar, so a call counts as a meeting even when another window is in front. It never touches the microphone or
any sound. Dictation and browser tabs do not count.</p><div class='buttons'><button class='btn' data-setting='detectCalls'
data-value='{0 if i['detectCalls'] else 1}'>{'Turn off' if i['detectCalls'] else 'Turn on'}</button></div></section>
<section class='panel'><h3>Questions about time away: {'on' if i['askAboutGaps'] else 'off'}</h3>
<p class='note'>When you are away from the computer for 30 minutes or more, starting and ending inside your workday, Synla
asks what it was. Never about mornings, evenings, weekends, lunch or paused time. Not work is enough, and Synla never asks
why.</p><div class='buttons'><button class='btn' data-gaps='{0 if i['askAboutGaps'] else 1}'>{'Turn off' if i['askAboutGaps'] else 'Turn on'}</button></div></section>
<section class='panel'><h3>Daily spot check: {'on' if i['spotChecks'] else 'off'}</h3>
<p class='note'>Once a day, Synla asks about one stretch it sorted on its own, picked at random. Your answers show how often
Synla is right when it doesn't ask, under Workload.</p><div class='buttons'><button class='btn' data-setting='spotChecks'
data-value='{0 if i['spotChecks'] else 1}'>{'Turn off' if i['spotChecks'] else 'Turn on'}</button></div></section>
<section class='panel'><h3>What is stored</h3><table><tr><th>What</th><th class='num'>Amount</th><th>Oldest</th><th>Kept for</th></tr>
<tr><td>Window names and times from the recorder</td><td class='num'>{i['recorder_rows']:,}</td><td class='nw'>{_when(i['recorder_first'])}</td><td>{i['keepWindowDays']} days</td></tr>
<tr><td>Your answers: project and kind for each stretch of time</td><td class='num'>{i['answers']:,}</td><td class='nw'>{_when(i['answers_first'])}</td><td>{i['keepDays']} days</td></tr>
<tr><td>Time you added away from the computer, and if it was a meeting (no notes)</td><td class='num'>{i['added']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Answers about time away: when, and whether it was work. Never why</td><td class='num'>{i['away']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Pauses: when each started and ended, so Synla never asks about them</td><td class='num'>{i['pauses']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Unplanned work by source, as one-way codes, to see what keeps coming back</td><td class='num'>{i['repeats']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Answer log: what you answered and what Synla had guessed</td><td class='num'>{i['log_entries']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Learned clues, stored as one-way codes, not readable names</td><td class='num'>{i['clues']:,}</td><td></td><td>until you delete them</td></tr>
<tr><td>Record book: sealed weeks with times, projects and kinds, no window names</td><td class='num'>{i['seals']:,}</td><td class='nw'>{_when(datetime.fromisoformat(i['seals_first'])) if i['seals_first'] else 'nothing yet'}</td><td>{i['keepRecordYears']:g} years</td></tr></table>
<p class='note'>After {i['keepWindowDays']} days the window names are gone. Your timesheet and record book stay.</p>{aw_note}</section>
<section class='panel'><h3>Never recorded</h3><ul class='plain'><li>Keystrokes, screenshots, the clipboard, file contents or sound</li>
<li>Web addresses (only the window title)</li>
<li>Private windows: apps {escape(', '.join(p.get('skipApps', [])))}; titles with {escape(', '.join(p.get('skipTitleWords', [])))}.
Change the list in config.json.</li></ul></section>
<section class='panel noprint'><h3>Delete</h3><div class='buttons'>
<button class='btn' data-delete='windows' data-confirm='Delete all window names now? Your answers, timesheets and record book stay.'>Delete all window names</button>
<button class='btn' data-delete='all' data-confirm='Delete window names, answers, added time, the log and learned clues? Your record book stays. This cannot be undone.'>Delete everything except the record book</button>
<button class='btn' data-delete='record' data-confirm='Delete the record book with all sealed weeks? Emails you sent yourself are not affected. This cannot be undone.'>Delete the record book</button></div>
<p class='note'>Deleting is final. Your settings (projects and keywords) stay. The recorder keeps recording from now
on: pause it first if you want a break.</p></section>
<section class='panel'><h3>Built for you, not for monitoring</h3><ul class='plain'>
<li>No account, server or admin mode, and no way to collect or compare data from several people.</li>
<li>Nothing leaves this computer unless you copy it out yourself, for example the weekly summary.</li>
<li>Every number comes from your own recording and your own answers, and you can see and delete all of it.</li>
<li>Other people's names and email addresses in window titles are hidden before anything is stored: a chat with a person
is kept as "Chat | (person)".</li>
<li>On a work computer, IT can in principle reach any file on it, as with all software. Synla has no view for a manager,
a colleague or HR.</li></ul></section>"""
    return page(app, "Your data", body, token)


def csv_text(app, d: date, excel: bool = False) -> str:
    """One line per day and project, and per day off. excel: semicolons and decimal commas, as Excel with Norwegian
    settings expects, with a byte order mark so æ, ø and å come out right."""
    f = io.StringIO()
    wr = csv.writer(f, delimiter=";" if excel else ",", lineterminator="\r\n" if excel else "\n")
    wr.writerow(["date", "code", "project", "hours"])
    for x in report_lines(app, monday_of(d)):
        h = f"{x['hours']:g}"
        wr.writerow([x["date"].isoformat(), cell(x["code"]), cell(x["what"]), h.replace(".", ",") if excel else h])
    return ("\ufeff" if excel else "") + f.getvalue()


# ---- the server ------------------------------------------------------------------------------------------------------
BODY_MAX = 1_000_000            # bytes; what a page sends is a few kilobytes
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069\ud800-\udfff]")
CODE = re.compile(r"^[\w:.+\-]{1,80}$")
TEXT = "text/plain; charset=utf-8"
HOST_ONLY = "Synla only answers on 127.0.0.1 or localhost."
OOPS = "Something went wrong in Synla. It is noted in errors.log in Synla's folder."
LOCKED = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Open Synla again · Synla</title>
<style>:root{color-scheme:light dark}body{font:17px/1.55 system-ui,sans-serif;max-width:34em;margin:14vh auto;padding:0 16px;
color:#1f2b26;background:#f3f4ef}@media (prefers-color-scheme:dark){body{color:#ece8de;background:#171a18}}</style></head>
<body><h1>Open Synla again</h1><p>For your privacy, Synla's pages only open from Synla itself, in the browser it opened
them in. Open Synla from the link in its reminder, or run <code>python -m synla</code>, and this page opens.</p></body></html>"""


def proof(key: str, text: str) -> str:
    """Shows that whoever answers knows the key, without giving the key away."""
    return hmac.new(key.encode("ascii"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def cookie_name(key: str) -> str:
    """The cookie that holds the key. Its name follows from the key, so the demo and your own Synla, each with its
    own folder and key, never overwrite each other's, whichever port they run on."""
    return "synla" + proof(key, "cookie name")[:10]


def same(a, b: str) -> bool:
    """Compares secrets in constant time."""
    return isinstance(a, str) and secrets.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8"))


def clean(v, limit: int) -> str:
    """Text from a page, safe to keep and show: no control or text direction characters, single spaces, not too long."""
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise Problem("Something in the request was not text.")
    return " ".join(CONTROL.sub(" ", str(v)).split())[:limit]


def cell(v) -> str:
    """A text cell for CSV and copied tables: on one line, and shown as text by Excel, never run as a formula."""
    s = " ".join(str(v).split())
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def day_of(v) -> date:
    try:
        d = date.fromisoformat(str(v))
    except ValueError:
        raise Problem("That is not a date Synla knows.") from None
    if not 2000 <= d.year <= 2100:
        raise Problem("Synla shows the years 2000 to 2100.")
    return d


def number_of(v) -> int:
    try:
        return int(str(v))
    except ValueError:
        raise Problem("That is not a number Synla knows.") from None


def year_of(q, app) -> int:
    y = number_of(q.get("y", [app.today().year])[0])
    if not 2000 <= y <= 2100:
        raise Problem("Synla shows the years 2000 to 2100.")
    return y


def project_in(app, v) -> str:
    name = clean(v, 60)
    if not name:
        raise Problem("Pick a project, or Not work.")
    if name == NOT_WORK:
        return NOT_WORK
    if name not in app.projects:
        raise Problem(f"Pick a {roles.word(app.config).lower()} from the list, or add it first.")
    return name


def kind_in(v) -> str:
    """A kind of work, or "" when you leave it out."""
    if v in (None, ""):
        return ""
    if not isinstance(v, str) or v not in KINDS:
        raise Problem("Pick a kind of work, or leave it out.")
    return v


def code_in(v) -> str:
    if not isinstance(v, str) or not CODE.match(v):
        raise Problem("The request did not say which question it answers.")
    return v


def ids_in(v) -> list:
    if not isinstance(v, list) or len(v) > 5000 or not all(isinstance(x, str) and len(x) <= 200 for x in v):
        raise Problem("The request did not say which time it is about.")
    return v


def words_in(v) -> list:
    """The words for a project, with commas between. A word needs two letters or more: one letter would match
    almost every window."""
    out = []
    for w in re.split(r"[,;]", clean(v, 600)):
        w = w.strip()[:40]
        if len(w) >= 2 and w.lower() not in (x.lower() for x in out):
            out.append(w)
    return out[:30]


def hours_in(v) -> float:
    try:
        h = float(str(v).replace(",", "."))
    except ValueError:
        raise Problem("Write the hours as a number, for example 37.5.") from None
    if not math.isfinite(h):
        raise Problem("Write the hours as a number, for example 37.5.")
    return h


def minutes_in(v) -> float | None:
    if v == "rest":
        now = datetime.now().astimezone()
        return (datetime.combine(now.date() + timedelta(days=1), time(0), now.tzinfo) - now).total_seconds() / 60
    try:
        m = float(v or 0)
    except (TypeError, ValueError):
        raise Problem("Pause for a number of minutes.") from None
    if not math.isfinite(m) or not 0 <= m <= 7 * 24 * 60:
        raise Problem("Pause for up to a week.")
    return m or None


def make_handler(app, token: str, port: int, seen: list | None = None, *, key: str):
    """The pages and the actions behind their buttons. Every request must name this computer (stops DNS rebinding)
    and carry the key cookie; every change must also carry the page token, sent as JSON."""
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    cookie = cookie_name(key)
    lock = threading.RLock()        # one request at a time, so two clicks at once cannot mix up your answers

    class H(BaseHTTPRequestHandler):
        timeout = 20                # seconds: a connection that stops sending is closed
        reply = None

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
            self.reply = (code, body, ctype, extra or {})

        def _flush(self):
            code, body, ctype, extra = self.reply or (500, OOPS, TEXT, {})
            data = body.encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Security-Policy", CSP)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)
            except OSError:         # the browser went away
                self.close_connection = True

        def _json(self, obj):
            return self._send(200, json.dumps(obj), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def _has_key(self) -> bool:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == cookie and same(value, key):
                    if seen is not None:
                        seen[0] = clock.monotonic()
                    return True
            return False

        def _date(self, q):
            v = q.get("date", [None])[0]
            return day_of(v) if v else app.today()

        def do_GET(self):
            self.reply = None
            with lock:
                self._get()
            self._flush()

        def do_POST(self):
            self.reply = None
            body = self._body()             # read before taking the lock, so a slow sender holds up nobody
            if body is not None:
                with lock:
                    self._post(body)
            self._flush()

        # ---- reading pages ----
        def _get(self):
            if not self._host_ok():
                return self._send(403, HOST_ONLY, TEXT)
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/ping":           # open, so Synla can see it runs; the answer shows it knows the key
                n = q.get("n", [""])[0][:64]
                return self._send(200, f"synla {proof(key, 'ping:' + n)}" if n else "synla", TEXT)
            if "key" in q:                  # Synla opened this page: keep the key in this browser, then drop it
                if not same(q["key"][0], key):
                    return self._send(401, LOCKED)
                rest = urlencode([(k, v) for k, vs in q.items() if k != "key" for v in vs])
                where = "/" + u.path.lstrip("/") + (f"?{rest}" if rest else "")
                return self._send(303, "", TEXT, {"Location": where, "Set-Cookie": f"{cookie}={key}; Max-Age=31536000; "
                                                                                 f"Path=/; HttpOnly; SameSite=Strict"})
            if not self._has_key():
                return self._send(401, LOCKED)
            try:
                self._route_get(u.path, q)
            except Problem as e:
                self._send(400, str(e), TEXT)
            except Exception as e:          # show a plain message, never the inside of the program
                app.store.error(f"GET {u.path}", e)
                self._send(500, OOPS, TEXT)

        def _route_get(self, path, q):
            if path == "/":
                if app.needs_setup():
                    return self._send(303, "", TEXT, {"Location": "/start"})
                return self._send(200, today_page(app, app.today(), token))
            if path == "/start":
                if not app.needs_setup():          # once set up, changes go through the Projects page
                    return self._send(303, "", TEXT, {"Location": "/"})
                return self._send(200, setup_page(app, token))
            if path == "/projects":
                return self._send(200, projects_page(app, token))
            if path == "/day":
                return self._send(200, today_page(app, self._date(q), token))
            if path == "/week":
                return self._send(200, week_page(app, self._date(q), token))
            if path == "/week/timesheet":
                return self._send(200, timesheet_page(app, self._date(q), token))
            if path == "/week/indicators":
                return self._send(200, indicators_page(app, self._date(q), token))
            if path == "/record":
                return self._send(200, record_page(app, token))
            if path == "/year":
                return self._send(200, year_page(app, year_of(q, app), token))
            if path == "/year.csv":
                y = year_of(q, app)
                return self._send(200, year_csv(app, y), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f"attachment; filename=synla-year-{y}.csv"})
            if path == "/record/statement.json":
                name, text = statement_file(app, number_of(q.get("seq", [""])[0]))
                return self._send(200, text, "application/json; charset=utf-8",
                                  {"Content-Disposition": f"attachment; filename={name}"})
            if path == "/role":
                return self._send(200, role_page(app, token))
            if path == "/data":
                return self._send(200, data_page(app, token))
            if path == "/export.csv":
                d = self._date(q)
                excel = q.get("format", [""])[0] == "excel"
                return self._send(200, csv_text(app, d, excel), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f"attachment; filename=timesheet-{monday_of(d)}"
                                                          f"{'-excel' if excel else ''}.csv"})
            if path == "/summary.txt":
                return self._send(200, report.text(app.summary(self._date(q))), TEXT)
            return self._send(404, "Not found", TEXT)

        # ---- changes ----
        def _body(self):
            """The request as a dict, or None when it is refused (the refusal is then the reply)."""
            if not self._host_ok():
                return self._send(403, HOST_ONLY, TEXT)
            if not self._has_key():
                return self._send(403, "Open Synla again from its reminder or with python -m synla, then try again.", TEXT)
            if not same(self.headers.get("X-Synla-Token", ""), token) or \
                    not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(403, "This request did not come from Synla's own page.", TEXT)
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n < 0:
                self.close_connection = True
                return self._send(400, "The request did not say how long it is.", TEXT)
            if n > BODY_MAX:
                self.close_connection = True
                return self._send(413, "The request is too large.", TEXT)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except (OSError, ValueError):
                self.close_connection = True
                return self._send(400, "The request was not valid JSON.", TEXT)
            if not isinstance(body, dict):
                return self._send(400, "The request was not valid JSON.", TEXT)
            return body

        def _post(self, body):
            path = urlparse(self.path).path
            try:
                self._route_post(path, body)
            except Problem as e:
                self._send(400, str(e), TEXT)
            except Exception as e:
                app.store.error(f"POST {path}", e)
                self._send(500, OOPS, TEXT)

        def _route_post(self, path, body):
            if path == "/api/answer":
                d, ids = day_of(body.get("date")), ids_in(body.get("ids"))
                project, kind = project_in(app, body.get("project")), kind_in(body.get("kind"))
                if project == NOT_WORK:
                    kind = ""                      # not work has no kind of work
                how = body.get("how") if body.get("how") in ("block", "window", "split", "spot") else "block"
                app.answer(d, ids, project, kind, how, asked=bool(body.get("asked")))
                return self._json({"ok": True, "undo": app.last_undo})
            if path == "/api/approve":
                count = app.approve_confident(day_of(body.get("date")))
                return self._json({"ok": True, "undo": app.last_undo, "count": count})
            if path == "/api/skip":
                app.skip(day_of(body.get("date")), code_in(body.get("code")))
                return self._json({"ok": True, "undo": app.last_undo})
            if path == "/api/gap":
                d, code = day_of(body.get("date")), code_in(body.get("code"))
                if body.get("notWork"):
                    app.gap_not_work(d, code)
                else:
                    project, kind = project_in(app, body.get("project")), kind_in(body.get("kind"))
                    app.answer_gap(d, code, clean(body.get("start"), 8), clean(body.get("end"), 8), project, kind,
                                   bool(body.get("meeting")))
                return self._json({"ok": True, "undo": app.last_undo})
            if path == "/api/codes":
                given, absence = body.get("projects") or {}, body.get("absence") or {}
                if not isinstance(given, dict) or not isinstance(absence, dict):
                    raise Problem("The codes were not sent as a list.")
                c = app.store.config()
                for p, code in given.items():
                    if p in c.get("projects", {}) and isinstance(c["projects"][p], dict):
                        if clean(code, 20):
                            c["projects"][p]["code"] = clean(code, 20)
                        else:
                            c["projects"][p].pop("code", None)
                c["absenceCodes"] = {k: clean(v, 20) for k, v in absence.items() if k in OFF and clean(v, 20)}
                app.store.save_config(c)
                app.reload()
                return self._json({"ok": True})
            if path == "/api/dayoff":
                kind = body.get("kind") or ""
                if not isinstance(kind, str) or kind and kind not in OFF:
                    raise Problem("Pick vacation, sick, sick child or time off.")
                app.set_day_off(day_of(body.get("date")), kind or None)
                return self._json({"ok": True, "undo": app.last_undo})
            if path == "/api/more":
                app.ask_more(day_of(body.get("date")))
            elif path == "/api/undo":
                app.undo(clean(body.get("token"), 64))
            elif path == "/api/project":
                name = clean(body.get("name"), 60)
                if not name:
                    raise Problem("Give the project a name.")
                if reserved(name):
                    raise Problem("Synla uses that name itself. Pick another.")
                name = app.add_project(name)
                return self._json({"ok": True, "name": name, "color": color(app, name)})
            elif path == "/api/pause":
                app.store.pause(minutes_in(body.get("minutes")))
            elif path == "/api/time":
                d = day_of(body.get("date"))
                project, kind = project_in(app, body.get("project")), kind_in(body.get("kind"))
                start = app.add_time(d, clean(body.get("start"), 8), clean(body.get("end"), 8), project, kind,
                                     bool(body.get("meeting")))
                return self._json({"ok": True, "id": start})
            elif path == "/api/time/remove":
                app.remove_time(clean(body.get("start"), 64))
            elif path == "/api/seal":
                s = app.seal(monday_of(day_of(body.get("week"))))
                return self._json({"ok": True, "fingerprint": s["fingerprint"]})
            elif path == "/api/settings":
                c = app.store.config()
                if "recordEmail" in body:
                    email = clean(body.get("recordEmail"), 254)
                    if email and not EMAIL.match(email):
                        raise Problem("That does not look like an email address.")
                    c["recordEmail"] = email
                for k in ("askAboutGaps", "spotChecks", "detectCalls"):
                    if k in body:
                        c[k] = bool(body[k])
                if "role" in body:
                    if not isinstance(body["role"], str) or body["role"] not in roles.ROLES:
                        raise Problem("Unknown role.")
                    c["role"] = body["role"]
                if "kindNames" in body:
                    given = body["kindNames"] if isinstance(body["kindNames"], dict) else {}
                    c["kindNames"] = {k: clean(v, 40) for k, v in given.items() if k in KINDS and clean(v, 40)}
                app.store.save_config(c)
                app.reload()
            elif path == "/api/setup":
                given = body.get("projects")
                if not isinstance(given, list) or len(given) > 100 or not all(isinstance(r, dict) for r in given):
                    raise Problem("The list of projects did not come through. Reload the page and try again.")
                projects = []
                for r in given:
                    name = clean(r.get("name"), 60)
                    if name:
                        projects.append((name, words_in(r.get("words")), clean(r.get("code"), 20)))
                role = body.get("role") or roles.DEFAULT
                app.setup(role if isinstance(role, str) else "", projects, clean(body.get("start"), 5),
                          clean(body.get("end"), 5), hours_in(body.get("weekly")), bool(body.get("calls")))
                started = False
                if WINDOWS and body.get("record"):
                    recorder.autostart(bool(body.get("autostart")), app.store.other_dir())
                    started = recording(app) or recorder.start_background(app.store)
                return self._json({"ok": True, "started": started})
            elif path == "/api/projects":
                given = body.get("projects")
                if not isinstance(given, list) or len(given) > 1000 or not all(isinstance(r, dict) for r in given):
                    raise Problem("The list did not come through. Reload the page and try again.")
                rows = []
                for r in given:
                    old = r.get("old")
                    if old is not None and not isinstance(old, str):
                        raise Problem("The list did not come through. Reload the page and try again.")
                    name = clean(r.get("name"), 60)
                    if old is None and not name:
                        continue                   # an empty new line
                    rows.append({"old": old, "name": name, "words": words_in(r.get("words")),
                                 "code": clean(r.get("code"), 20), "archived": bool(r.get("archived"))})
                app.save_projects(rows)
            elif path == "/api/record":
                if not WINDOWS:
                    raise Problem("Synla's own recorder works on Windows. On Mac and Linux, run ActivityWatch: "
                                  "Synla reads it on its own.")
                if not recording(app) and not recorder.start_background(app.store):
                    raise Problem("The recorder did not start. Another Synla recorder may be running already. "
                                  "Details may be in errors.log in Synla's folder.")
            elif path == "/api/autostart":
                if not WINDOWS:
                    raise Problem("Starting at sign-in is for Windows.")
                recorder.autostart(bool(body.get("on")), app.store.other_dir())
            elif path == "/api/delete":
                what = body.get("what")
                if what == "windows":
                    recorder.prune(app.store, 0)
                    app.reload()
                elif what == "all":
                    kept = app.store.delete_everything()
                    app.reload()
                    if kept:
                        return self._send(409, "Synla deleted everything except " + ", ".join(kept) +
                                          ", which another program has open. Try again in a minute.", TEXT)
                elif what == "record":
                    app.record.delete_all()
                else:
                    raise Problem("Unknown delete request.")
            else:
                return self._send(404, "Not found", TEXT)
            return self._json({"ok": True})
    return H


class Server(ThreadingHTTPServer):
    """Listens on this computer only. On Windows the port is held exclusively, so no other program can take it over
    while Synla runs and see what your browser sends."""
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)        # skips a name lookup that can take seconds on some networks
        self.server_name, self.server_port = self.server_address[:2]


def page_token(key: str) -> str:
    """The token Synla's own pages send with every change. It follows from the key, so an open page keeps working
    when Synla starts again."""
    return proof(key, "page token")[:32]


def with_key(url: str, key: str) -> str:
    return url + ("&" if "?" in url else "?") + "key=" + key


def serve(app, port=5700, open_browser=True, start=None, idle_minutes=None):
    """Runs Synla's pages until Ctrl+C, or until nobody has used them for idle_minutes."""
    key = app.store.page_key()
    httpd = None
    for p in range(port, port + 10):           # the next port if something else uses this one
        try:
            httpd = Server(("127.0.0.1", p), None)
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"Ports {port} to {port + 9} are all in use. Try: python -m synla --port 6800")
    port = httpd.server_address[1]
    seen = [clock.monotonic()]
    httpd.RequestHandlerClass = make_handler(app, page_token(key), port, seen, key=key)
    info = app.store.dir / "server.json"
    write_whole(info, json.dumps({"port": port, "pid": os.getpid()}))
    url = with_key(f"http://127.0.0.1:{port}/" + (f"day?date={start}" if start else ""), key)
    print(f"Synla is running. If your browser did not open, open this link (Ctrl+C to stop):\n{url}", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    if idle_minutes:
        def watch():
            while clock.monotonic() - seen[0] < idle_minutes * 60:
                clock.sleep(20)
            httpd.shutdown()
        threading.Thread(target=watch, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        try:                                   # only if it is still ours: a second Synla may have taken over
            if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info.unlink()
        except (OSError, ValueError):
            pass
