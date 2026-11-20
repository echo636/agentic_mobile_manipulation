#!/usr/bin/env python3
"""Render existing private navigation records; does not run the simulator.

Usage: python scripts/render_online_map_progress.py RECORD_DIR OUTPUT_PREFIX
       --provenance operations/.../map_visualization.json
Requires Pillow and NumPy. All displayed pixels come from saved occupancy maps;
all motion points come from actual_position records, not commanded/planned poses.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('records', type=Path)
    parser.add_argument('output_prefix', type=Path)
    parser.add_argument('--provenance', type=Path, required=True)
    args = parser.parse_args()
    p = args.records
    read = lambda name: json.loads((p / name).read_text())
    result, before, after = read('component_result.json'), read('initial_map_diagnostics.json'), read('final_map_diagnostics.json')
    plans = [json.loads(x) for x in (p/'navigation_plans.jsonl').read_text().splitlines()]
    motion = [json.loads(x) for x in (p/'base_motion.jsonl').read_text().splitlines()]
    snapshots = []
    source_files = ['component_result.json', 'initial_map_diagnostics.json', 'final_map_diagnostics.json',
                    'navigation_plans.jsonl', 'base_motion.jsonl']
    for diagnostic in (before, after):
        filename = f"online_mapping/occupancy_{diagnostic['sequence']}.bin"
        source_files.append(filename)
        raw = (p/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != diagnostic['occupancy_sha256']:
            raise ValueError('Occupancy map differs from recorded diagnostic hash')
        snapshots.append(np.frombuffer(raw, dtype=np.uint8).reshape(diagnostic['height'], diagnostic['width']))
    # This real run has a shared extent; reject incompatible maps rather than
    # comparing different crops as if their image pixels represented one place.
    for key in ('width', 'height', 'resolution', 'origin'):
        if before[key] != after[key]:
            raise ValueError('This renderer expects the recorded maps to share their world extent')
    resolution = before['resolution']
    xmin, ymin = [v-resolution/2 for v in before['origin']]
    extent = (xmin, ymin, xmin+before['width']*resolution, ymin+before['height']*resolution)
    initial_goal = tuple(plans[0]['plan']['goal'])
    if any(np.linalg.norm(np.array(row['plan']['goal'])-initial_goal) > 1e-6 for row in plans):
        raise ValueError('The displayed run did not keep a fixed approach goal')
    start = before['robot_world_position'][:2]
    target = plans[0]['plan']['target'][:2]
    actual = [start] + [row['actual_position'][:2] for row in motion]
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    bold_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    fonts = {s: ImageFont.truetype(font_path, s) for s in (13, 14, 15, 16, 18, 21)}
    bold = {s: ImageFont.truetype(bold_path, s) for s in (18, 22, 29)}
    image = Image.new('RGB', (1280, 830), '#f7f9fc')
    draw = ImageDraw.Draw(image)
    def text(x, y, value, size=16, color='#334155', heavy=False):
        draw.text((x,y), value, font=(bold if heavy else fonts)[size], fill=color)
    text(45, 25, 'Sensor-built map: before and after real navigation', 29, '#12243a', True)
    text(45, 72, 'OmniGibson component probe r2 | 4 depth views + private pose | No precomputed walkability map', 16)
    text(45, 108, f"{result['actual_displacement_m']:.3f} m actual displacement     {result['replans']} replans     "
         f"Maps {before['sequence']} to {after['sequence']}     {result['simulator_steps']} simulator steps", 18, '#126557', True)
    colors = {0: (255,255,255), 100: (36,55,70), 255: (221,228,235)}
    newly_free = (snapshots[0] == 255) & (snapshots[1] == 0)
    panel_width = 550
    panel_height = round(panel_width * before['height'] / before['width'])
    for index, (left, diagnostic, raw) in enumerate(zip((45, 685), (before, after), snapshots)):
        top = 228
        text(left, 163, f"{'Initial' if index == 0 else 'After movement'} | map {diagnostic['sequence']}", 22, '#12243a', True)
        free_area = int((raw == 0).sum()) * resolution**2
        text(left, 200, f"Observed free: {free_area:.2f} m²   |   occupied: {int((raw==100).sum())} cells", 15)
        rgb = np.zeros((*raw.shape, 3), dtype=np.uint8)
        for value, color in colors.items():
            rgb[raw == value] = color
        if index:
            rgb[newly_free] = (165,222,199)
        raster = Image.fromarray(rgb[::-1]).resize((panel_width,panel_height),Image.Resampling.NEAREST)
        image.paste(raster, (left,top))
        draw.rectangle((left,top,left+panel_width,top+panel_height),outline='#bac6d4',width=1)
        def xy(point):
            return (left+(point[0]-extent[0])/(extent[2]-extent[0])*panel_width,
                    top+panel_height-(point[1]-extent[1])/(extent[3]-extent[1])*panel_height)
        route = [xy(v) for v in (actual if index else plans[0]['plan']['points'])]
        draw.line(route, fill='#176bea' if index else '#8192aa', width=4 if index else 2)
        current = actual[-1] if index else start
        cx,cy = xy(current)
        radius = before['robot_radius_m']/(extent[2]-extent[0])*panel_width
        draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),outline='#176bea',width=2)
        for point, label, color, offset, style in (
            (start, 'Start', '#176bea', (54,15), 'circle'),
            (initial_goal, 'Reached goal' if index else 'Approach goal', '#bc6700', (55,-5), 'cross'),
            (target, 'RGB floor target', '#b42356', (45,-16), 'diamond'),
        ):
            x,y = xy(point)
            if style == 'circle':
                draw.ellipse((x-5,y-5,x+5,y+5),fill=color,outline='white',width=1)
            elif style == 'cross':
                draw.line((x-6,y,x+6,y),fill=color,width=3)
                draw.line((x,y-6,x,y+6),fill=color,width=3)
            else:
                draw.polygon(((x,y-6),(x+6,y),(x,y+6),(x-6,y)),fill=color)
            tx,ty = x+offset[0],y+offset[1]
            draw.line((x+7,y,tx-5,ty+7),fill=color,width=1)
            box = draw.textbbox((tx,ty),label,font=fonts[14])
            draw.rectangle((box[0]-3,box[1]-2,box[2]+3,box[3]+2),fill='white')
            text(tx,ty,label,14,color)
        scale = panel_width/(extent[2]-extent[0])
        sx,sy=left+18,top+panel_height-25
        draw.rectangle((sx-5,sy-23,sx+scale+5,sy+8),fill='white')
        draw.line((sx,sy,sx+scale,sy),fill='#12243a',width=3)
        text(sx+2,sy-21,'1 m',13)
        text(left, top+panel_height+12, 'Planned approach + starting footprint' if not index else
             'Actual recorded trajectory + final footprint', 14, '#526579')
    legend = [('Unknown','#dde4eb'),('Observed free','#ffffff'),('Occupied','#243746'),('Newly observed free','#a5dec7')]
    for x, (label, color) in zip((45,245,485,695),legend):
        draw.rectangle((x,716,x+17,733),fill=color,outline='#8292a6')
        text(x+25,715,label,15)
    text(45,755,'Both panels share the same world XY extent; raw occupancy resolution is 5 cm. Robot circle: 0.402 m radius.',15)
    text(45,786,'Private executor diagnostic. No LLM in this probe. Navigation reached its goal; this is not BEHAVIOR task success (Q = 0).',15,'#8d4f14')
    args.output_prefix.parent.mkdir(parents=True,exist_ok=True)
    png=args.output_prefix.with_suffix('.png');svg=args.output_prefix.with_suffix('.svg')
    image.save(png,optimize=True)
    data=base64.b64encode(png.read_bytes()).decode()
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="830" viewBox="0 0 1280 830" role="img">'
        '<title>Real sensor-built occupancy before and after navigation</title>'
        '<desc>Recorded private executor maps 1 and 11, planned approach, actual robot trajectory and fixed goal. '
        'No precomputed map, no LLM, navigation component only.</desc>'
        f'<image width="1280" height="830" href="data:image/png;base64,{data}"/></svg>\n')
    provenance={'status':'generated','source_run':str(p.resolve()),'source_commit':result['source_commit'],
        'map_sequences':[before['sequence'],after['sequence']], 'world_extent':extent,
        'resolution_m':resolution,'actual_motion_samples':len(motion),'fixed_goal':initial_goal,
        'newly_observed_free_cells':int(newly_free.sum()),'llm_in_this_probe':False,
        'benchmark_task_success':False,'no_gt_map':True,'outputs':{},
        'source_sha256':{name:hashlib.sha256((p/name).read_bytes()).hexdigest() for name in source_files}}
    for file in (png,svg):
        provenance['outputs'][str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    args.provenance.parent.mkdir(parents=True,exist_ok=True)
    args.provenance.write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps({'status':'generated','svg':str(svg),'png':str(png),'newly_observed_free_cells':int(newly_free.sum())}))


if __name__=='__main__':
    main()
