"""Check MP4 decodability, frame ledger, every-env-step coverage and model isolation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manipulation_agent.tools import tool_specs

p=argparse.ArgumentParser();p.add_argument('run_dir',type=Path);p.add_argument('--ffmpeg',default='ffmpeg');p.add_argument('--ffprobe',default='ffprobe');a=p.parse_args()
r=json.loads((a.run_dir/'run.json').read_text());v=json.loads((a.run_dir/'video.json').read_text())
frames=[json.loads(s) for s in (a.run_dir/'video_frames.jsonl').read_text().splitlines()]
probe=json.loads(subprocess.check_output([a.ffprobe,'-v','error','-count_frames','-show_streams','-of','json',str(a.run_dir/v['file'])],text=True))
stream=probe['streams'][0]
decode=subprocess.run([a.ffmpeg,'-v','error','-i',str(a.run_dir/v['file']),'-f','null','-'],capture_output=True,text=True)
steps=[f['env_step'] for f in frames if f['kind']=='env_step']
events=[json.loads(s) for s in (a.run_dir/'events.jsonl').read_text().splitlines()]
public=[e['result']['observation'] for e in events if e['kind']=='tool_result' and 'observation' in e['result']]
expected_views={'front','back','left','right','spectator'} if 'front' in v['views'] else {'head','left_wrist','right_wrist','spectator'}
public_views=expected_views-{'spectator'}
allowed_tools={t['name'] for t in tool_specs(r['config'].get('agent_profile','skills'))}
checks={
 'video_record_passed':v['status']=='passed',
 'frame_ledger_contiguous':[f['frame_index'] for f in frames]==list(range(len(frames))),
 'decoded_frame_count_matches':int(stream['nb_read_frames'])==len(frames)==v['frame_count'],
 'all_control_steps_present':steps==list(range(1,r['sim_steps']+1)) and len(steps)==v['recorded_env_steps'],
 'h264_browser_pixel_format':stream['codec_name']=='h264' and stream['pix_fmt']=='yuv420p',
 'decodes_without_errors':decode.returncode==0 and not decode.stderr.strip(),
 'file_hash_matches':hashlib.sha256((a.run_dir/v['file']).read_bytes()).hexdigest()==v['sha256'],
 'configured_rgb_views_recorded':set(v['views'])==expected_views and all(set(f['rgb_sha256'])==expected_views for f in frames),
 'spectator_never_in_public_observation':bool(public) and all({i['view'] for i in o['images']}==public_views for o in public),
 'profile_tools_only':all(e['name'] in allowed_tools for e in events if e['kind']=='tool_call')}
out={'status':'passed' if all(checks.values()) else 'failed','run_id':r['run_id'],'checks':checks,'frame_count':len(frames),'control_steps':len(steps),'fps':v['fps'],'duration_seconds':v['duration_seconds'],'bytes':v['bytes'],'codec':stream['codec_name'],'video_sha256':v['sha256'],'task_success_separate':r.get('task_success')}
(a.run_dir/'video_validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
raise SystemExit(0 if out['status']=='passed' else 2)
