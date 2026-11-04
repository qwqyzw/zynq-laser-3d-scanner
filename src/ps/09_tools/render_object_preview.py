"""Render measured/reconstructed CSV points into an offline, equal-scale 3-D viewer.

This tool never connects to the scanner, fills a hole, or fits a surface. It only
renders finite CSV coordinates. A separate metadata JSON describes the provenance
and calibration limitations of those coordinates.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
from pathlib import Path
from urllib.parse import urlsplit


COORDINATE_SETS = (("x", "y", "z"), ("x_mm", "y_mm", "z_mm"),
                   ("X", "Y", "Z"), ("X_mm", "Y_mm", "Z_mm"))


def finite_points(path: Path, columns=None):
    points, skipped, total = [], 0, 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames or []
        if columns is None:
            columns = next((item for item in COORDINATE_SETS
                            if all(name in names for name in item)), None)
        if columns is None or not all(name in names for name in columns):
            raise ValueError("CSV must contain x,y,z or x_mm,y_mm,z_mm; "
                             "otherwise specify all three --*-column options")
        for row in reader:
            total += 1
            try:
                point = [float(row[name]) for name in columns]
                if not all(math.isfinite(value) for value in point):
                    raise ValueError("non-finite coordinate")
            except (ValueError, TypeError, KeyError):
                skipped += 1
                continue
            # No geometry threshold or crop is applied by the visualization.
            points.append(point)
    if not points:
        raise ValueError("CSV contains no finite 3-D points")
    return points, {"input_rows": total, "finite_points": len(points),
                    "skipped_invalid_rows": skipped,
                    "coordinate_columns": list(columns)}


def safe_image_href(value: str, meta_dir: Path, output_dir: Path):
    """Accept local evidence paths, not executable or network URL schemes."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    # Windows drive letters are not URL schemes for this purpose.
    is_drive_path = len(value) >= 3 and value[1] == ":" and value[2] in "\\/"
    if not is_drive_path and urlsplit(value).scheme:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (meta_dir / path).resolve()
    if not path.is_file():
        return None
    try:
        return path.relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        return path.as_uri()


def render(csv_path: Path, meta_path: Path, output: Path, *, columns=None,
           max_points=24000, fragment=False):
    if max_points < 1:
        raise ValueError("--max-points must be positive")
    all_points, stats = finite_points(csv_path, columns)
    metadata = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    # Deterministic, order-spanning sampling is display only. The original CSV
    # is retained and its complete point bounds determine all three axis scales.
    if len(all_points) > max_points:
        points = [all_points[(i * len(all_points)) // max_points]
                  for i in range(max_points)]
    else:
        points = all_points
    bounds = [[min(p[axis] for p in all_points), max(p[axis] for p in all_points)]
              for axis in range(3)]
    center = [(low + high) / 2 for low, high in bounds]
    radius = max(math.sqrt(sum((p[a] - center[a]) ** 2 for a in range(3)))
                 for p in all_points)
    source_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    title = str(metadata.get("title", "实物扫描 · 三维点云"))
    units = str(metadata.get("units", "坐标单位未指定"))
    geometry_status = str(metadata.get("geometry_status", "几何与毫米精度尚未验收"))
    coverage_note = str(metadata.get("coverage_note", "仅显示有效测量点；空白区域未测得，不补面。"))
    data = {"points": [[round(v, 8) for v in p] for p in points],
            "bounds": bounds, "center": center, "radius": radius or 1.0,
            "units": units, "finiteCount": len(all_points),
            "displayedCount": len(points), "sourceSha256": source_sha}
    stats.update({"displayed_points": len(points), "bounds": bounds,
                  "csv_sha256": source_sha, "geometry_status": geometry_status,
                  "surface_fitted": False, "synthetic_points_added": 0})
    links = []
    missing_images = 0
    for index, item in enumerate(metadata.get("source_images", [])):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        href = safe_image_href(item.get("path", ""), meta_path.parent, output.parent)
        if not href:
            missing_images += 1
            continue
        label = str(item.get("label", f"原始照片 {index + 1}"))
        links.append('<li><a href="' + html.escape(href, quote=True) + '" '
                     'target="_blank" rel="noopener">' + html.escape(label) + '</a></li>')
    stats["linked_images"] = len(links)
    stats["unavailable_image_links"] = missing_images
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    root_id = "object-preview-" + source_sha[:12]
    sampling = (f"显示 {len(points):,} / {len(all_points):,} 个点（仅显示抽样）"
                if len(points) != len(all_points) else f"{len(all_points):,} 个有效测量点")
    axis_rows = "".join(f"<tr><th>{axis}</th><td>{low:.3f}</td><td>{high:.3f}</td>"
                        f"<td>{high-low:.3f}</td></tr>"
                        for axis, (low, high) in zip("XYZ", bounds))
    images = ("<details><summary>原始照片</summary><ul>" + "".join(links) + "</ul></details>") if links else ""
    invalid = f"，忽略 {stats['skipped_invalid_rows']} 行无效坐标" if stats["skipped_invalid_rows"] else ""
    angle_value = metadata.get("scan_angles_deg")
    angle_note = ""
    if isinstance(angle_value, list) and angle_value:
        angle_note = '<p class="op-small">采集角度：' + html.escape(", ".join(str(v) for v in angle_value)) + '°</p>'
    content = TEMPLATE.replace("__ROOT_ID__", root_id).replace("__TITLE__", html.escape(title))
    replacements = {"__POINT_LABEL__": html.escape(sampling + invalid),
                    "__STATUS__": html.escape(geometry_status),
                    "__COVERAGE__": html.escape(coverage_note),
                    "__UNITS__": html.escape(units), "__AXIS_ROWS__": axis_rows,
                    "__ANGLE_NOTE__": angle_note, "__IMAGES__": images,
                    "__DATA__": payload, "__SHA__": source_sha}
    for key, value in replacements.items():
        content = content.replace(key, value)
    if not fragment:
        content = ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width,initial-scale=1">'
                   '<meta name="color-scheme" content="light dark">'
                   '<title>' + html.escape(title) + '</title></head><body>\n' +
                   content + '\n</body></html>\n')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    return stats


TEMPLATE = r'''<section id="__ROOT_ID__" class="object-preview">
<style>
#__ROOT_ID__{--op-bg:light-dark(#f8fafb,#111a22);--op-fg:light-dark(#182b38,#e5edf3);--op-muted:light-dark(#526675,#b4c3ce);--op-grid:light-dark(#d3dde4,#344958);--op-point:light-dark(#087e9b,#60cee5);color:var(--op-fg);background:transparent;font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;max-width:1400px;margin:0 auto;padding:14px;box-sizing:border-box}
#__ROOT_ID__ *{box-sizing:border-box}#__ROOT_ID__ h2{font-size:22px;font-weight:500;margin:0 0 8px}#__ROOT_ID__ p{margin:6px 0 10px}#__ROOT_ID__ .op-layout{display:grid;grid-template-columns:minmax(0,1fr) 255px;gap:18px}#__ROOT_ID__ .op-canvas-wrap{position:relative;min-width:0}#__ROOT_ID__ canvas{width:100%;height:550px;display:block;background:var(--op-bg);touch-action:none;border:1px solid var(--op-grid);border-radius:6px;cursor:grab}#__ROOT_ID__ canvas:active{cursor:grabbing}#__ROOT_ID__ .op-controls{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0}#__ROOT_ID__ button{border:1px solid var(--op-grid);background:var(--op-bg);color:var(--op-fg);font:inherit;padding:7px 13px;border-radius:5px;cursor:pointer}#__ROOT_ID__ button:hover{filter:brightness(.96)}#__ROOT_ID__ button:focus-visible,#__ROOT_ID__ canvas:focus-visible{outline:2px solid var(--op-point);outline-offset:2px}#__ROOT_ID__ .op-small{color:var(--op-muted);font-size:12px;overflow-wrap:anywhere}#__ROOT_ID__ .op-legend{display:flex;align-items:center;gap:8px}#__ROOT_ID__ .op-dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--op-point)}#__ROOT_ID__ table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}#__ROOT_ID__ th,#__ROOT_ID__ td{padding:5px 3px;text-align:right;border-bottom:1px solid var(--op-grid);font-size:12px;font-weight:400}#__ROOT_ID__ th:first-child{text-align:left}#__ROOT_ID__ a{color:var(--op-point)}#__ROOT_ID__ summary{cursor:pointer}#__ROOT_ID__ .op-position{position:absolute;bottom:10px;left:12px;pointer-events:none;color:var(--op-muted);font-size:12px;background:var(--op-bg);padding:2px 5px;border-radius:3px}
@media(max-width:760px){#__ROOT_ID__ .op-layout{grid-template-columns:minmax(0,1fr)}#__ROOT_ID__ canvas{height:420px}#__ROOT_ID__ aside{max-width:100%}}
@media(max-width:390px){#__ROOT_ID__{padding:6px}#__ROOT_ID__ canvas{height:350px}#__ROOT_ID__ button{padding:9px 10px}}
</style>
<h2>__TITLE__</h2>
<p class="op-legend"><span class="op-dot" aria-hidden="true"></span><span>__POINT_LABEL__</span></p>
<div class="op-layout">
<div>
<div class="op-canvas-wrap"><canvas tabindex="0" aria-label="可旋转的三维点云，拖动旋转，滚轮缩放，方向键旋转，加减号缩放">当前环境需要支持 Canvas 才能显示点云。</canvas><span class="op-position" aria-live="polite">等比例投影</span></div>
<div class="op-controls" aria-label="点云视角"><button type="button" data-view="reset">复位</button><button type="button" data-view="top">俯视</button><button type="button" data-view="side">侧视</button><button type="button" data-view="front">正视</button><button type="button" data-view="in" aria-label="放大点云">＋</button><button type="button" data-view="out" aria-label="缩小点云">−</button></div>
<p class="op-small">拖动旋转 · 滚轮缩放 · 方向键旋转 · 三轴保持同一比例</p>
</div>
<aside><p><strong>__STATUS__</strong></p><p>__COVERAGE__</p><p class="op-small">空白表示缺少有效点，未生成封闭外壳。</p><table aria-label="点云坐标范围"><thead><tr><th>轴</th><th>最小</th><th>最大</th><th>跨度</th></tr></thead><tbody>__AXIS_ROWS__</tbody></table><p class="op-small">单位：__UNITS__。范围为点云包围盒，物体尺寸与坐标偏差取决于标定。</p>__ANGLE_NOTE____IMAGES__<details><summary>数据来源</summary><p class="op-small">CSV SHA-256：__SHA__</p></details></aside>
</div>
<script type="application/json" class="op-data">__DATA__</script>
<script>
(()=>{"use strict";
const root=document.getElementById('__ROOT_ID__');
const data=JSON.parse(root.querySelector('.op-data').textContent);
const canvas=root.querySelector('canvas'),ctx=canvas.getContext('2d');
const position=root.querySelector('.op-position');
let width=0,height=0,zoom=1,yaw=-.65,pitch=.45,drag=null,renderPending=false;
const points=data.points.map(p=>p.map((v,i)=>v-data.center[i]));
const radius=data.radius;
function colors(){const c=getComputedStyle(root);return {bg:c.getPropertyValue('--op-bg').trim(),fg:c.getPropertyValue('--op-fg').trim(),muted:c.getPropertyValue('--op-muted').trim(),grid:c.getPropertyValue('--op-grid').trim(),point:c.getPropertyValue('--op-point').trim()};}
// Resolve light-dark() through actual element computed styles before Canvas use.
function resolved(){const probe=document.createElement('span');probe.style.display='none';root.appendChild(probe);const values=colors(),out={};for(const [k,v]of Object.entries(values)){probe.style.color=v;out[k]=getComputedStyle(probe).color;}probe.remove();return out;}
function rotate(p){const ca=Math.cos(yaw),sa=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);const x=p[0]*ca-p[1]*sa,y=p[0]*sa+p[1]*ca;return [x,y*sp-p[2]*cp,y*cp+p[2]*sp];}
function screen(p){const q=rotate(p),scale=Math.min(width,height)*.39/radius*zoom;return [width/2+q[0]*scale,height/2+q[1]*scale,q[2]];}
function line(a,b,c,dash){const p=screen(a),q=screen(b);ctx.beginPath();ctx.strokeStyle=c;ctx.lineWidth=1;ctx.setLineDash(dash||[]);ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke();ctx.setLineDash([]);}
function draw(){renderPending=false;const c=resolved();ctx.clearRect(0,0,width,height);ctx.fillStyle=c.bg;ctx.fillRect(0,0,width,height);
const min=data.bounds.map((b,i)=>b[0]-data.center[i]),max=data.bounds.map((b,i)=>b[1]-data.center[i]);
const corners=[];for(let i=0;i<8;i++)corners.push([i&1?max[0]:min[0],i&2?max[1]:min[1],i&4?max[2]:min[2]]);
for(let i=0;i<8;i++)for(let axis=0;axis<3;axis++){const j=i^(1<<axis);if(i<j)line(corners[i],corners[j],c.grid);}
const plotted=points.map(screen).sort((a,b)=>a[2]-b[2]);const dot=Math.max(.9,Math.min(2.6,1.15*Math.sqrt(zoom)));ctx.fillStyle=c.point;
for(const p of plotted){if(p[0]<-dot||p[0]>width+dot||p[1]<-dot||p[1]>height+dot)continue;ctx.globalAlpha=.55+.4*((p[2]/radius+1)/2);ctx.beginPath();ctx.arc(p[0],p[1],dot,0,Math.PI*2);ctx.fill();}ctx.globalAlpha=1;
// Compact orientation triad has the same rotation and no ambiguous color coding.
const axisLength=39;ctx.font='12px system-ui';for(let a=0;a<3;a++){const v=[0,0,0];v[a]=1;const q=rotate(v),x=54+q[0]*axisLength,y=height-57+q[1]*axisLength;ctx.strokeStyle=c.muted;ctx.beginPath();ctx.moveTo(54,height-57);ctx.lineTo(x,y);ctx.stroke();ctx.fillStyle=c.fg;ctx.fillText('XYZ'[a],x+4,y-4);}
position.textContent='等比例投影 · '+Math.round(zoom*100)+'%';root.dataset.renderedPoints=String(plotted.length);}
function requestDraw(){if(!renderPending){renderPending=true;requestAnimationFrame(draw);}}
function resize(){const r=canvas.getBoundingClientRect(),dpr=Math.min(2,window.devicePixelRatio||1);width=r.width;height=r.height;canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);requestDraw();}
function applyZoom(factor){zoom=Math.max(.15,Math.min(12,zoom*factor));requestDraw();}
canvas.addEventListener('pointerdown',e=>{drag={id:e.pointerId,x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;yaw+=(e.clientX-drag.x)*.008;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,pitch+(e.clientY-drag.y)*.008));drag.x=e.clientX;drag.y=e.clientY;requestDraw();});
for(const type of ['pointerup','pointercancel'])canvas.addEventListener(type,()=>{drag=null;});
canvas.addEventListener('wheel',e=>{e.preventDefault();applyZoom(Math.exp(-e.deltaY*.001));},{passive:false});
canvas.addEventListener('keydown',e=>{let used=true;if(e.key==='ArrowLeft')yaw-=.1;else if(e.key==='ArrowRight')yaw+=.1;else if(e.key==='ArrowUp')pitch=Math.min(Math.PI/2,pitch+.1);else if(e.key==='ArrowDown')pitch=Math.max(-Math.PI/2,pitch-.1);else if(e.key==='+'||e.key==='=')applyZoom(1.2);else if(e.key==='-')applyZoom(1/1.2);else used=false;if(used){e.preventDefault();requestDraw();}});
root.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{const v=b.dataset.view;if(v==='in'){applyZoom(1.25);return;}if(v==='out'){applyZoom(.8);return;}zoom=1;if(v==='top'){yaw=0;pitch=Math.PI/2;}else if(v==='side'){yaw=Math.PI/2;pitch=0;}else if(v==='front'){yaw=0;pitch=0;}else{yaw=-.65;pitch=.45;}requestDraw();}));
new ResizeObserver(resize).observe(canvas);
if(window.matchMedia)window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',requestDraw);
new MutationObserver(requestDraw).observe(document.documentElement,{attributes:true,attributeFilter:['class','style','data-theme']});
resize();
})();
</script>
</section>
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Real reconstructed point CSV")
    parser.add_argument("meta", type=Path, help="Provenance and limitations JSON")
    parser.add_argument("output", type=Path, help="Self-contained output HTML")
    parser.add_argument("--max-points", type=int, default=24000)
    parser.add_argument("--fragment", action="store_true", help="Write an inline HTML fragment")
    parser.add_argument("--x-column")
    parser.add_argument("--y-column")
    parser.add_argument("--z-column")
    args = parser.parse_args()
    overrides = (args.x_column, args.y_column, args.z_column)
    if any(overrides) and not all(overrides):
        parser.error("specify --x-column, --y-column, and --z-column together")
    stats = render(args.csv.resolve(), args.meta.resolve(), args.output.resolve(),
                   columns=overrides if all(overrides) else None,
                   max_points=args.max_points, fragment=args.fragment)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
