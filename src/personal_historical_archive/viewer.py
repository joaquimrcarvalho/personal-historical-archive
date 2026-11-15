"""The shared page-view shell used by `pha serve` and `pha bench serve`.

One stylesheet, one HTML document wrapper, one escaping helper. Kept
stdlib-only on purpose: SEARCH_WEB_SPEC.md names this module as the shared
viewer layer (it is vendored byte-identically by any out-of-tree consumer), so
it must not import pha or FastAPI.
"""
from __future__ import annotations

import html

# One tiny stylesheet, inlined: the viewer must work offline and with no build
# step or external asset. Transcriptions are plain text, never interpreted.
CSS = """\
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#faf9f7;color:#1b1b1b}
header,footer{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;
  padding:.55rem .9rem;background:#f1efe9;border-bottom:1px solid #ddd}
footer{border-bottom:0;border-top:1px solid #ddd;margin-top:auto;font-size:13px}
.spacer{flex:1}
main{flex:1;display:flex;align-items:center;justify-content:center;padding:1rem}
main.plain{display:block}
main.reader{display:grid;
  grid-template-columns:minmax(280px,var(--media-w,55%)) 12px minmax(22rem,1fr);
  align-items:start;justify-content:stretch;gap:0 1rem;width:100%;max-width:96rem;
  margin:0 auto;--media-w:55%}
.reader-media{display:flex;justify-content:center;align-items:flex-start;min-width:0;
  position:sticky;top:.5rem;align-self:start;max-height:calc(100vh - 1rem);
  overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable}
.reader-media img.page{width:100%;height:auto;max-height:none}
.reader-text{min-width:0}
.splitter{grid-column:2;align-self:stretch;min-height:60vh;position:relative;
  cursor:col-resize;touch-action:none;border:0;background:transparent;padding:0}
.splitter::before{content:"";position:absolute;left:5px;top:0;bottom:0;width:2px;
  background:#c9c5bc;border-radius:2px}
.splitter:hover::before,.splitter:focus-visible::before{background:#5f5c55;width:4px;left:4px}
.splitter::after{content:"⋮";position:absolute;left:-1px;top:50%;transform:translateY(-50%);
  color:#8a857b;font-size:14px;line-height:1;padding:.35rem .12rem;background:#faf9f7;
  border:1px solid #d8d4cb;border-radius:6px}
.splitter:focus-visible{outline:2px solid #0b57d0;outline-offset:2px;border-radius:4px}
.reading{background:#fff;border:1px solid #e4e1da;border-radius:10px;
  padding:.85rem 1rem;margin-bottom:1rem}
.reading:last-child{margin-bottom:0}
.reading h2{margin:.1rem 0 .15rem}
.provenance{margin:.15rem 0 .6rem;color:#777;font-size:13px}
.tabs{display:flex;gap:.35rem;flex-wrap:wrap;margin:0 0 .8rem}
.tab{padding:.22rem .6rem;border:1px solid #bbb;border-radius:6px;
  text-decoration:none;color:inherit;white-space:nowrap}
.tab.on{border-color:transparent;background:#e8890c;color:#fff}
.record{background:#fff;border:1px solid #e4e1da;border-radius:10px;
  padding:.6rem .8rem;margin:.6rem 0}
.record h3{margin:.1rem 0 .3rem;font-size:.95rem}
.record table.fields{border-collapse:collapse;margin:.3rem 0;font-size:13px}
.record table.fields td{vertical-align:top;padding:1px 8px 1px 0;line-height:1.45}
.badge{display:inline-block;margin-left:.35rem;padding:.02rem .35rem;
  border:1px solid #d8d2c5;border-radius:999px;font-size:11px;
  color:#6d6658;vertical-align:.08em}
pre.transcript{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;
  font:13.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  max-height:calc(100vh - 12rem);overflow:auto;margin:.4rem 0 0;
  padding:.65rem .75rem;background:#fbfaf7;border:1px solid #eee;
  border-radius:7px}
img.page{max-width:100%;max-height:calc(100vh - 8.5rem);box-shadow:0 1px 8px rgba(0,0,0,.28)}
a.btn,span.btn{padding:.22rem .55rem;border:1px solid #bbb;border-radius:6px;
  text-decoration:none;color:inherit;white-space:nowrap}
span.btn{border-color:#e6e4df;color:#b3b0aa}
a{color:#0b5fff}
.muted{color:#777;font-size:13px}
h1{font-size:1.15rem;margin:.2rem 0}
h2{font-size:1rem;margin:1.4rem 0 .4rem}
.inline-form{display:inline-flex;gap:.3rem;align-items:center}
input[type=number]{width:5.5rem;padding:.2rem .35rem;font:inherit}
button{font:inherit;padding:.22rem .55rem}
.ranges{display:flex;flex-wrap:wrap;gap:.35rem}
.wrap{max-width:64rem;width:100%;margin:0 auto}
.notice{border:1px solid #e0d6c0;background:#fdf6e3;border-radius:8px;padding:1rem 1.2rem;max-width:32rem}
@media (prefers-color-scheme:dark){
  body{background:#17181a;color:#e9e7e4}
  header,footer{background:#202226;border-color:#333}
  span.btn{border-color:#2b2b2b;color:#6a6a6a}
  a{color:#7aa7ff}
  .muted{color:#9a9a9a}
  .reading{background:#1d1f22;border-color:#333}
  .tab{border-color:#444}
  .record{background:#1d1f22;border-color:#333}
  .provenance{color:#9a9a9a}
  .badge{border-color:#444;color:#b8b1a4}
  pre.transcript{background:#17181a;border-color:#333}
  .notice{border-color:#4a4335;background:#2a2620}
}
@media (max-width:900px){
  main.reader{display:block;padding:.75rem}
  .reader-media{margin-bottom:.9rem;position:static;max-height:none;overflow:visible}
  .splitter{display:none}
  img.page{max-height:70vh}
  pre.transcript{max-height:none}
}
"""



def esc(text: str | None) -> str:
    """Escape text for HTML (the only way model text reaches a page)."""
    return html.escape(text or "")


def html_page(title: str, body: str, head_extra: str = "") -> bytes:
    """The HTML document shell both servers render into."""
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{esc(title)}</title>\n"
        f"<style>{CSS}</style>\n"
        f"{head_extra}</head>\n<body>\n{body}\n</body>\n</html>\n"
    ).encode("utf-8")


SPLITTER_JS = """<script>(function(){
const wb=document.querySelector('main.reader'),d=wb&&wb.querySelector('.splitter');
if(!d)return;
const KEY='pha-serve-media',clamp=p=>Math.min(80,Math.max(25,p));
const saved=localStorage.getItem(KEY);if(saved)wb.style.setProperty('--media-w',saved);
let drag=false;
const pct=ev=>{const r=wb.getBoundingClientRect();
const x=(ev.clientX!==undefined?ev.clientX:(ev.touches&&ev.touches[0].clientX))-r.left;
return clamp(x/r.width*100);};
const apply=p=>wb.style.setProperty('--media-w',p.toFixed(1)+'%');
d.addEventListener('pointerdown',e=>{drag=true;d.setPointerCapture(e.pointerId);e.preventDefault();});
d.addEventListener('pointermove',e=>{if(drag)apply(pct(e));});
const end=()=>{if(!drag)return;drag=false;
localStorage.setItem(KEY,wb.style.getPropertyValue('--media-w'));};
d.addEventListener('pointerup',end);d.addEventListener('pointercancel',end);
d.addEventListener('dblclick',()=>{wb.style.removeProperty('--media-w');localStorage.removeItem(KEY);});
d.addEventListener('keydown',e=>{const cur=parseFloat(wb.style.getPropertyValue('--media-w'))||55;
let p=null;if(e.key==='ArrowLeft')p=clamp(cur-2);if(e.key==='ArrowRight')p=clamp(cur+2);
if(p!==null){e.preventDefault();apply(p);localStorage.setItem(KEY,p.toFixed(1)+'%');}});
})();</script>"""
