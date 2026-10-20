"""Publish a compact, evidence-bound task explorer to the existing report server."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

REPO = Path(__file__).resolve().parents[1]

PRESENTATION = {
    'garden': dict(name='花园 · 处理与分配', scene_name='庭院', cover='left', scale='20 盆处理 + 4 盆对照',
        summary='逐盆处理，分台陈列，全程保护对照组。', tool='喷壶',
        phases=[('送入处理台','每盆从蓝色来源台搬到黄色处理台。',40,['source0','source1','stage']),
                ('逐盆喷洒','在处理台喷洒指定材料，记录每盆处理历史。',20,['stage','tool_home']),
                ('直立分配','两张绿台各放 10 盆，倾角不超过 15°；同款盆栽可互换。',40,['destination0','destination1']),
                ('收尾与保护','关闭喷壶并归还灰台；红台 4 盆始终留在原台且不沾材料。',None,['controls','tool_home'])]),
    'hotel': dict(name='酒店 · 鞋履护理', scene_name='酒店套房', cover='left', scale='20 只鞋 → 10 个鞋位',
        summary='逐只去尘，成对归位，保持处理顺序。', tool='护理刷',
        phases=[('送入护理台','将蓝台的积尘鞋逐只搬到黄色护理台。',40,['source','stage']),
                ('逐只去尘','拿取护理刷，清除当前鞋上的灰尘，再归还刷子。',20,['stage','tool_home']),
                ('成对归位','每个绿色鞋位恰好两只，相互邻近；不按隐藏主人编号配对。',40,['pair'+str(i).zfill(2) for i in range(10)]),
                ('工具归还','最终护理刷回到灰台，手空。',None,['tool_home'])]),
    'bedroom': dict(name='卧室 · 清理与复原', scene_name='双卧室', cover='left', scale='2 间卧室 · 24 件物品',
        summary='先腾空，再吸尘、归位，最后关闭四盏灯。', tool='吸尘器',
        phases=[('分房腾空','每间 12 件杂物全部搬到本房黄台。',48,['a_stage','b_stage']),
                ('清扫地面','该房杂物全部暂存后，才可吸尘；每个独立地面计一次。',2,['floor','floor_b','tool_home']),
                ('分房归位','本房地面干净后，把物品逐件搬到本房绿台。',48,['a_destination','b_destination']),
                ('最后关灯','两个房间完成后关闭独立灰台上的 4 盏任务灯，吸尘器关机归还。',4,['a_lamp0_stand','a_lamp1_stand','b_lamp0_stand','b_lamp1_stand','tool_home'])]),
    'garage': dict(name='车库 · 木料加工', scene_name='车库与院落', cover='front', scale='16 根整木 → 32 块半木',
        summary='先清扫，再逐根劈开，追踪并收纳全部产物。', tool='斧头',
        phases=[('清扫砂尘','先清理指定车库地面，砂与尘合计一次有效清扫。',1,['sweep_floor','tool_home']),
                ('逐根上墩','将 16 根整木逐根搬到黄色基座上的劈木墩。',32,['source','block_base','block']),
                ('原生切割','用斧头切割，验证原木消失及对应两半真实生成。',16,['block','block_base','tool_home']),
                ('追踪收纳','32 块半木逐件归入绿台；斧头、扫帚归还灰台。',64,['destination','tool_home'])]),
}
GATE_NOTES = {
    'kitchen': '已找到热狗熟制模板，仍需验证批量加热、熟而未焦及容器容量。',
    'gallery': '已找到挂画模板，仍需验证拆下、搬运和重新固定的组合能力。',
    'office': '已有键盘模板，尚未在已检查目录中找到显示器清洁模板。',
    'bathroom': '已有浴室整理模板，尚未确认所需污渍清除能力。',
    'laundry': '尚未定位匹配洗衣模板，也未确认洗涤和干燥状态转换。',
    'wood': '尚未定位家具染色模板，也未确认材料转移能力。',
}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(batch, path):
    path = Path(path).resolve()
    if not path.is_relative_to(batch):
        raise ValueError('Artifact must be inside the requested batch')
    return path


def role_label(role, family):
    fixed = {'source':'来源台','source0':'来源台 A','source1':'来源台 B','stage':'处理台',
        'destination':'收纳台','destination0':'展示台 A','destination1':'展示台 B',
        'controls':'对照台','tool_home':'工具台','tool':PRESENTATION[family]['tool'],
        'block':'劈木墩','block_base':'劈木墩基座','broom':'扫帚','floor':'地面 A' if family=='bedroom' else '地面',
        'floor_b':'地面 B','sweep_floor':'待清扫地面'}
    if role in fixed:return fixed[role]
    for prefix, label in [('plant','盆栽'),('control','对照盆栽'),('shoe','鞋'),('pair','鞋位'),('log','整木')]:
        if role.startswith(prefix) and role[len(prefix):].isdigit():return label+' '+str(int(role[len(prefix):])+1).zfill(2)
    if role.startswith(('a_','b_')):
        room=role[0].upper(); rest=role[2:]
        if rest.startswith('item'):return room+' 室物品 '+str(int(rest[4:])+1).zfill(2)
        if rest=='stage':return room+' 室暂存台'
        if rest=='destination':return room+' 室归位台'
        if rest.startswith('lamp'):return room+' 室灯 '+str(int(rest[4])+1)+(' 台座' if rest.endswith('_stand') else '')
    return role


def scene_explainer(batch, output, family, frozen, spec, story, inputs):
    """Publish only reviewed annotations bound to the exact rendered RGB."""
    annotations_path=REPO/'web/long_horizon/scene_explainers.json'
    explanation=read(annotations_path)[family]
    if explanation['spec_sha256']!=frozen['spec_sha256']:
        raise ValueError('Scene annotations refer to a different frozen task')
    root=checked_path(batch,batch/'runs'/explanation['run_id'])
    capture=read(root/'scene_views.json');record=read(root/'run_record.json')
    if (capture['status']!='passed' or record['status']!='passed' or record.get('process_exit_code')!=0
            or not capture['object_poses_unchanged'] or capture['task_actions']!=0
            or capture['spec_sha256']!=frozen['spec_sha256']
            or capture['scene_sha256']!=frozen['scene_sha256']
            or record['script_sha256']!=explanation['capture_script_sha256']):
        raise ValueError('Inspection capture identity or render-only validation failed')
    for name in ['scene_views.json','run_record.json']:
        path=root/name;inputs[str(path.relative_to(batch))]=sha(path)
    frames={v['id']:v for v in capture['views']};published=[]
    for view in explanation['views']:
        frame=frames[view['id']]
        source=checked_path(batch,root/frame['file'])
        if (not view['visually_reviewed'] or sha(source)!=view['frame_sha256']
                or view['frame_sha256']!=frame['sha256']):
            raise ValueError('Inspection RGB changed or has not been visually reviewed')
        if not view['pins'] or len({p['id'] for p in view['pins']})!=len(view['pins']):
            raise ValueError('Each inspected view needs unique visible locations')
        for pin in view['pins']:
            if (not pin['roles'] or not set(pin['roles']).issubset(spec['roles'])
                    or len(pin['point'])!=2 or not all(0<=v<=1 for v in pin['point'])
                    or pin['color'] not in ['blue','yellow','green','red','gray','floor']):
                raise ValueError('Invalid reviewed scene location')
        target=output/'media'/family/('inspection-'+source.name)
        if not target.exists() or sha(target)!=frame['sha256']:shutil.copyfile(source,target)
        inputs[str(source.relative_to(batch))]=sha(source)
        published.append(dict(id=view['id'],label=view['label'],caption=view['caption'],
            step=view['step'],pins=view['pins'],url=str(target.relative_to(output)),
            sha256=frame['sha256'],width=frame['width'],height=frame['height']))
    if len(explanation['rules'])!=len(story['rules']) or len(explanation['steps'])!=4:
        raise ValueError('Every task rule and process step needs a scene location')
    if any(target['rule_title']!=rule['title'] for target,rule in zip(explanation['rules'],story['rules'])):
        raise ValueError('Re-review scene links after changing or reordering success rules')
    for target in explanation['steps']+explanation['rules']:
        view=next((v for v in published if v['id']==target['view']),None)
        if view is None or target['pin'] not in {p['id'] for p in view['pins']}:
            raise ValueError('Task requirement does not resolve to a reviewed scene location')
    if any(not 0<=x['step']<4 for x in published+explanation['rules']):
        raise ValueError('Invalid scene step link')
    return dict(views=published,steps=explanation['steps'],rules=explanation['rules'],
        run_id=explanation['run_id'],spec_sha256=frozen['spec_sha256'],
        annotation_sha256=sha(annotations_path),capture_script_sha256=record['script_sha256'],
        source='Rendered frozen initial state; inspection camera only; no task actions',
        object_poses_unchanged=True,task_actions=0)


def publish(batch, output):
    batch=batch.resolve(); output=output.resolve()
    validation=read(batch/'validation.json')
    catalog=read(REPO/'task_designs/proposals_100plus/diverse_catalog.json')['tasks']
    frozen={t['task']:t for t in read(batch/'frozen_tasks.json')['tasks']}
    stories=read(REPO/'web/long_horizon/task_stories.json')
    rows={t['task']:t for t in validation['tasks']}
    tasks=[]; inputs={}
    output.mkdir(parents=True,exist_ok=True)
    for family, presentation in PRESENTATION.items():
        task_id=next(t for t in frozen if t.startswith(family+'_'))
        f=frozen[task_id]; row=rows[task_id]; root=batch/'runs'/f['build']
        if row['latest_attempt']['run_id']!=f['build']:
            raise ValueError('Refresh the frozen artifact index before publishing a newer construction')
        spec_path=checked_path(batch,f['spec']); spec=read(spec_path)
        if sha(spec_path)!=f['spec_sha256']:raise ValueError('Frozen spec hash mismatch')
        story=stories[family]
        if story['spec_sha256']!=f['spec_sha256']:
            raise ValueError('Review the task explanation against the new frozen scoring contract')
        requirement_ids={r['id'] for r in spec['requirements']}
        explained={r for rule in story['rules'] for r in rule['requirements']}
        if explained!=requirement_ids:
            raise ValueError('Task explanation must cover every public requirement')
        inputs[str(spec_path.relative_to(batch))]=sha(spec_path)
        geometry=read(root/'geometry.json'); world=read(root/'scene_geometry.json')
        build=read(root/'validation.json'); obs=read(root/'initial_observation.json')
        frames=[]; media=output/'media'/family;media.mkdir(parents=True,exist_ok=True)
        for frame in obs['images']:
            source=root/'initial_views'/(frame['image_ref']+'.jpg')
            if sha(source)!=frame['sha256']:raise ValueError('Initial RGB hash mismatch')
            target=media/(frame['view']+'.jpg')
            if not target.exists() or sha(target)!=frame['sha256']:shutil.copyfile(source,target)
            frames.append(dict(view=frame['view'],url=str(target.relative_to(output)),sha256=frame['sha256']))
        fixtures=[]; name_to_role={v:k for k,v in spec['roles'].items()}
        for fixture in spec['fixtures']:
            role=name_to_role[fixture['name']];g=geometry[role]
            fixtures.append(dict(role=role,label=role_label(role,family),aabb=g['aabb'],rgba=fixture['rgba']))
        objects=[]
        for role,g in geometry.items():
            if not g or g['fixed'] or role.startswith('background_'):continue
            objects.append(dict(role=role,label=role_label(role,family),aabb=g['aabb'],position=g['pose'][0]))
        robot=next((v['aabb'] for v in world.values() if v['category']=='agent'),None)
        if robot is None:
            robot=next((v['aabb'] for k,v in world.items() if k.startswith('robot_')),None)
        bounds_objects=[x['aabb'] for x in fixtures+objects]+([robot] if robot else [])
        # Keep the independently swept garage floor visible too.
        if family=='garage':bounds_objects.append(geometry['sweep_floor']['aabb'])
        bounds=[min(a[0][0] for a in bounds_objects)-.7,min(a[0][1] for a in bounds_objects)-.7,
                max(a[1][0] for a in bounds_objects)+.7,max(a[1][1] for a in bounds_objects)+.7]
        background=[]
        for name,g in world.items():
            low,high=g['aabb']
            if name in name_to_role or name.startswith('robot_') or g['category'] in ['floors','lawn','driveway','walls','ceilings','background']:continue
            if high[2]<0 or low[2]>2 or low[0]>bounds[2] or high[0]<bounds[0] or low[1]>bounds[3] or high[1]<bounds[1]:continue
            if (high[0]-low[0])*(high[1]-low[1])>60:continue
            background.append(dict(category=g['category'],aabb=g['aabb']))
        references=[]
        for attempt in row['reference_attempts']:
            path=batch/'runs'/attempt['run_id']/'reference_trajectory.jsonl'
            actions=[]
            if path.exists():
                for line in path.read_text().splitlines():
                    event=json.loads(line)
                    if event.get('event')!='action_finished':continue
                    actions.append({k:event.get(k) for k in ['index','primitive','role','status','seconds','error','code']})
                    actions[-1]['target_label']=role_label(event.get('role') or '',family)
                inputs[str(path.relative_to(batch))]=sha(path)
            references.append(dict(id=attempt['run_id'],status=attempt['status'],order=attempt.get('order'),
                error=attempt.get('error'),actions=actions,source_commit=attempt.get('source_commit'),
                successful_calls=sum(a['status']=='passed' for a in actions),
                manipulation_calls=sum(a['status']=='passed' and a['primitive'] not in ['navigate_to','look','wait'] for a in actions)))
        phases=[dict(title=title,description=description,calls=calls,roles=roles) for title,description,calls,roles in presentation['phases']]
        if sum(p['calls'] or 0 for p in phases)!=spec['design_ledger']['conditional_useful_calls']:
            raise ValueError('Displayed design ledger disagrees with frozen recipe')
        explainer=scene_explainer(batch,output,family,f,spec,story,inputs)
        tasks.append(dict(id=task_id,family=family,**{k:v for k,v in presentation.items() if k!='phases'},
            story=story,explainer=explainer,
            instruction=spec['public_instruction'],requirements=[r['text'] for r in spec['requirements']],
            phases=phases,declared_calls=spec['design_ledger']['conditional_useful_calls'],
            model_budget=spec['design_ledger']['proposed_model_act_look_budget'],frames=frames,
            map=dict(bounds=bounds,fixtures=fixtures,objects=objects,robot=robot,background=background,
                     floors=[dict(role=role,label=role_label(role,family),aabb=g['aabb']) for role,g in geometry.items() if g and role in ['floor','floor_b','sweep_floor']]),
            references=references,reference_status=row['physical_reference_status'],
            gates=[dict(name='静态契约',status=build['contract_validation']['status']),
                   dict(name='场景与初态',status=build['status']),
                   dict(name='几何与出生位置',status=row['scene_validation_status']),
                   dict(name='正序完整参考',status=row.get('reference_order_status',{}).get('forward','planned')),
                   dict(name='逆序完整参考',status=row.get('reference_order_status',{}).get('reverse','planned')),
                   dict(name='三个物理负例',status=row['physical_negatives_status']),
                   dict(name='RGB / MCP 接口',status=row['rgb_mcp_status']),
                   dict(name='自主模型测评',status=row['model_status'])],
            initial_assertions=len(spec['initial_assertions']),benchmark_ready=False,
            build_id=f['build'],instance=f['instance'],source_commit=f['source_commit'],spec_sha256=f['spec_sha256'],
            scene_sha256=f['scene_sha256'],scene=build['backend']['task_instance']['scene'],
            packages=build['backend'].get('packages',{}),design_deviations=spec.get('design_deviations',[])))
    pending=[dict(id=t['id'],name=t['name_zh'],calls=t['declared_useful_calls'],
                  note=GATE_NOTES[t['id'].split('_')[0]]) for t in catalog if t['id'] not in frozen]
    data=dict(batch=batch.name,updated_at=validation['at'],published_at=datetime.now(timezone.utc).isoformat(),
        tasks=tasks,pending=pending,benchmark_ready=False,source_commit=validation['source_commit'],
        cpu_tests=read(batch/'cpu_final_validation.json')['tests'],
        evidence_scope='Frozen initial RGB, reviewed inspection views and measured geometry; actual privileged reference calls. No autonomous success claimed.')
    # Only allowlisted display fields are published; no environment or full evaluator payload.
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    (output/'data.json').write_text(payload+'\n')
    source=REPO/'web/long_horizon'
    html=(source/'index.html').read_text().replace('@@DATA@@',payload.replace('<','\\u003c'))
    for name in ['style.css','app.js']:
        shutil.copyfile(source/name,output/name)
        html=html.replace('"'+name+'"','"'+name+'?v='+sha(source/name)[:12]+'"')
    temporary=output/'index.html.tmp';temporary.write_text(html);temporary.replace(output/'index.html')
    return dict(status='passed',output=str(output),tasks=len(tasks),images=sum(len(t['frames']) for t in tasks),
                inspection_images=sum(len(t['explainer']['views']) for t in tasks),
                published_data_sha256=sha(output/'data.json'),input_sha256=inputs,
                validation_level='evidence binding and static publication; browser validation separate',benchmark_ready=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=publish(args.batch,args.output)
    (args.batch/'dashboard_publication.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (args.batch/'journal.jsonl').open('a') as f:
        f.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),event='task_dashboard_published',**result),ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='input_sha256'},ensure_ascii=False))
