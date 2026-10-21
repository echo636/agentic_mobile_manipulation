#!/usr/bin/env python3
"""Compile four complete v2 contracts and physical references from pinned native bindings."""
from pathlib import Path
import argparse,copy,hashlib,json,sys
R=Path(__file__).resolve().parents[1]
if not (R/'task_designs').exists():R=Path('/mnt/nas/home--xujingyi/agentic_mobile_manip/repos/manipulation-agentic-system-long-horizon')
sys.path.insert(0,str(R/'src'))
from manipulation_agent.custom_tasks import validate_spec

NOT=lambda x:{'not':x}
ALL=lambda *x:{'all':list(x)}
ANY=lambda *x:{'any':list(x)}

def action(primitive,role=None,**kw):return dict(primitive=primitive,**({'role':role} if role else {}),**kw)
def take(r):return action('grasp',r)
def put(r,uv=(.5,.5),**kw):return action('place_on_top',r,uv=list(uv),**kw)
def inside(r):return action('place_inside',r)
def transfer(r,t,uv=(.5,.5),into=False):return [take(r),inside(t) if into else put(t,uv)]

class Builder:
 def __init__(self,key,base,inventory,models):
  self.catalog=json.loads((R/'task_designs/nonrepetitive_v2/catalog.json').read_text())
  self.design=next(t for t in self.catalog['tasks'] if t['id'].startswith(key))
  self.inventory=inventory;self.models=models;self.base=base;self.dirty_roles=[]
  self.s=dict(schema_version=3,id=self.design['id'],instance_id=self.design['id']+'_seed0_r1',status='complete_recipe_physics_pending',benchmark_ready=False,official_submission_eligible=False,
   base=dict(task=base,instance=301,seed=0),roles={},asset_imports=[],native_assets=[],fixtures=[],initial_layout=[],initial_assertions={},future_roles=[],replacements=[],
   public_instruction=self.design['public_instruction_zh'],public_context=[],requirements=[],
   evaluation=dict(predicates={},terminal={},milestones={},state_guards=[],motion_guards=[],score_type='custom_joint_terminal_and_public_history_v2'),
   acceptance_cases=dict(reference_orders=['order_1','order_2'],physical_negative_cases=['omit_process','omit_delivery','omit_reset'],fresh_restore_repetitions=2,max_reference_action_attempts=600),
   reference_plan=dict(kind='full_workflow',phases={},orders={},negative_omissions={}),settle_steps=30,
   design_source=dict(path='task_designs/nonrepetitive_v2/catalog.json',sha256=hashlib.sha256((R/'task_designs/nonrepetitive_v2/catalog.json').read_bytes()).hexdigest(),conditional_design_count=self.design['declared_conditional_operations']))
  self.p=self.s['evaluation']['predicates'];self.e=self.s['evaluation'];self.roles=self.s['roles'];self.layout=self.s['initial_layout']
 def bind(self,role,name):self.roles[role]=name;return role
 def existing(self,role,name):return self.bind(role,name)
 def imported(self,role,task,name=None,category=None,index=0,**kw):
  template=self.inventory['templates'][task]
  if name is None:name=[n for n,o in template['objects'].items() if o['args']['category']==category][index]
  entity='fv2_'+role;self.bind(role,entity)
  row=dict(name=entity,source_task=task,source_object=name,source_sha256=template['sha256'],position=[50.+len(self.roles)*3,50.,2.],orientation=[0.,0.,0.,1.]);row.update(kw)
  self.s['asset_imports'].append(row);return role
 def native(self,role,category,model=None,**kw):
  m=self.models[category]
  if isinstance(m,list):m=next((x for x in m if x['model']==model),m[0])
  entity='fv2_'+role;self.bind(role,entity)
  row=dict(name=entity,category=category,model=m['model'],usd_sha256=m.get('sha256',m.get('usd_sha256')),prim_type=0,position=[50.+len(self.roles)*3,50.,2.]);row.update(kw);self.s['native_assets'].append(row);return role
 def fixture(self,role,xy,size=(1.6,.7,.65)):
  name='fv2_'+role;self.bind(role,name);self.s['fixtures'].append(dict(name=name,position=[*xy,size[2]/2],size=list(size),rgba=[.32,.42,.52,1.],fixed_base=True));return role
 def place(self,r,t,xy=None,orientation=None):
  row=dict(object=r,target=t)
  if xy is None:row.update(place_on_support=True,support_uv=[.5,.5],auto_slot=True)
  else:row.update(place_on_support=True,xy=xy)
  if orientation:row['orientation']=orientation
  self.layout.append(row)
 def state(self,r,state,value=True,target=None,system=None,**kw):
  row=dict(object=r,state=state,value=value,**kw)
  if target:row['target']=target
  if system:row['system']=system
  self.layout.append(row)
 def pred(self,name,r,state,target=None,system=None,**kw):
  row=dict(object=r,state=state,**kw)
  if target:row['target']=target
  if system:row['system']=system
  self.p[name]=row;return name
 def rel(self,r,t,kind='OnTop'):return self.pred(r+'_'+kind.lower()+'_'+t,r,kind,t)
 def clean(self,r,system='dust',initial=True):
  name=self.pred(r+'_'+system,r,'Covered',system=system)
  if initial:
   self.state(r,'Covered',True,system=system,max_sampling_attempts=3,settle_steps=1);self.s['initial_assertions'][name]=name;self.dirty_roles.append(r)
  return NOT(name)
 def terminal(self,key,expr):self.e['terminal'][key]=expr;return key
 def milestone(self,key,expr,after=()):self.e['milestones'][key]=dict(when=expr,after=list(after));return key
 def guard(self,key,expr,until=None):self.e['state_guards'].append(dict(id=key,require=expr,until=until));return key
 def requirement(self,key,text,terminal=(),milestones=(),constraints=()):
  row=dict(id=key,text=text)
  for n,v in [('terminal',terminal),('milestones',milestones),('constraints',constraints)]:
   if v:row[n]=list(v)
  self.s['requirements'].append(row);self.s['public_context'].append(text)
 def phase(self,key,actions,after=()):self.s['reference_plan']['phases'][key]=dict(actions=actions,after=list(after))
 def replacement(self,parent,category,model):
  children=[]
  for i in range(2):
   role=parent+'_half'+str(i);self.bind(role,'half_'+self.roles[parent]+'_'+str(i));self.s['future_roles'].append(role);children.append(dict(role=role,category=category,model=model[i] if isinstance(model,list) else model))
  self.s['replacements'].append(dict(parent=parent,children=children));return self.pred(parent+'_replaced',parent,'Replaced')
 def finalise(self,orders,negatives):
  from collections import defaultdict
  groups=defaultdict(list)
  for row in self.layout:
   if row.get('auto_slot'):groups[row['target']].append(row)
  for rows in groups.values():
   cols=min(4,len(rows));nrows=(len(rows)+cols-1)//cols
   for i,row in enumerate(rows):
    row['support_uv']=[(i%cols+.5)/cols,(i//cols+.5)/nrows];row.pop('auto_slot')
  positioned={row['object'] for row in self.layout if row.get('place_on_support') or 'position' in row}
  added={row['name'] for row in [*self.s['native_assets'],*self.s['asset_imports']] if not row.get('fixed_base',False)}
  unstaged=[role for role,name in self.roles.items() if name in added and role not in positioned]
  support=next((role for role in ['stage','source','laundry','tool_home'] if role in self.roles),None)
  if unstaged and support is None:raise ValueError('Unpositioned native objects require a real staging support')
  for i,role in enumerate(unstaged):
   self.layout.insert(i,dict(object=role,target=support,place_on_support=True,support_uv=[.15+.23*(i%4),.25+.5*((i//4)%2)],construction_staging=True))
  self.s['reference_plan']['orders']=dict(zip(['order_1','order_2'],orders));self.s['reference_plan']['negative_omissions']=dict(zip(['omit_process','omit_delivery','omit_reset'],negatives))
  last_relations={}
  for row in self.layout:
   if row.get('place_on_support'):last_relations[row['object']]=('OnTop',row['target'])
   elif row.get('state')=='Inside' and row.get('value'):last_relations[row['object']]=('Inside',row['target'])
  for role,(state,target) in last_relations.items():
   if any(row.get('name')==self.roles[role] and row.get('fixed_base') for row in self.s['asset_imports']):continue
   key=self.pred('initial_'+role+'_'+state,role,state,target)
   self.s['initial_assertions'][key]=key
  for replacement in self.s['replacements']:
   key=replacement['parent']+'_replaced';self.s['initial_assertions']['intact_'+replacement['parent']]=NOT(key)
  self.s['initial_assertions']['hand_empty']=self.pred('hand_empty',next(iter(self.roles)),'HandEmpty')
  self.terminal('hand_empty','hand_empty');self.requirement('hand_empty','完成后放下所持物品。',terminal=['hand_empty'])
  self.s['compiled_action_count']=sum(len(p['actions']) for p in self.s['reference_plan']['phases'].values())
  self.s['compiled_count_includes_waits']=True
  self.s['compiled_manipulation_count']=sum(a['primitive'] not in ('wait','wait_for','navigate_to') for p in self.s['reference_plan']['phases'].values() for a in p['actions'])
  self.s['design_source']['recovery_status']=self.catalog.get('recovery_status','original_file')
  validate_spec(self.s)
  return self.s


def kitchen(inv,models):
 b=Builder('kitchen','preparing_lunch_box',inv,models)
 for r,n in dict(box='packing_box_210',tea='bottle_of_tea_208',sandwich='club_sandwich_209',cookie='chocolate_chip_cookie_207',board='chopping_board_211',prep='countertop_kelker_0',storage='countertop_kelzer_0',fridge='fridge_dszchb_0',microwave='microwave_abzvij_0',floor='floors_qgmjvd_0',table='breakfast_table_rhjoby_0',cabinet='bottom_cabinet_fancyy_0').items():b.existing(r,n)
 b.s['construction_asset_scales']=[{'object':'board','scale':[1.,1.,1.],'reason':'Use full native afwefw cutting board dimensions; source atomic task scaled it about 2x.'}]
 b.fixture('stage',[6.,.5],[1.8,.7,.65]);b.fixture('tool_home',[8.6,1.45],[1.8,.6,.55])
 b.imported('bin','picking_up_trash','trash_can_116',scale=[1.5]*3);b.place('bin','floor',[5.2,4.2])
 b.native('apple','apple');b.native('knife','carving_knife')
 b.imported('brush','clean_a_keyboard','pipe_cleaner_120');b.imported('broom','clean_a_patio','broom_172')
 b.imported('can','picking_up_trash','can_of_soda_114')
 for i in (1,2):
  b.imported('hotdog'+str(i),'cook_hot_dogs','hotdog_'+str(206+i))
  b.imported('plate'+str(i),'setting_the_table','plate_'+str(242+i))
  b.imported('fork'+str(i),'setting_the_table','tablefork_'+str(246+i))
  b.imported('cup'+str(i),'setting_the_table','water_glass_bbpraa_0')
 b.state('cabinet','Open',True,kwargs={'fully':True});ware=['plate1','fork1','cup1','plate2','fork2','cup2','knife','board']
 for r in ware:b.state(r,'Inside',True,target='cabinet',max_sampling_attempts=3)
 b.state('cabinet','Open',False,kwargs={'fully':True})
 b.state('fridge','Open',True,kwargs={'fully':True})
 for r in ['hotdog1','hotdog2']:b.state(r,'Inside',True,target='fridge',max_sampling_attempts=3);b.state(r,'Cooked',False)
 b.state('fridge','Open',False,kwargs={'fully':True})
 for r,t in [('box','storage'),('apple','stage'),('tea','stage'),('cookie','storage'),('sandwich','storage'),('can','table'),('brush','tool_home'),('broom','tool_home')]:b.place(r,t)
 # The countertop AABB includes its native sink cutout; use solid surface slots.
 # Keep the full broom footprint supported and clear of the neighboring bar stools.
 initial_slots={'box':(1/6,.5),'cookie':(.65,.5),'sandwich':(5/6,.5),'brush':(.2,.3),'broom':(.7,.6)}
 for row in b.layout:
  if row['object'] in initial_slots and row.get('auto_slot'):
   row.pop('auto_slot');row['support_uv']=list(initial_slots[row['object']])
 for r in ['microwave']:b.state(r,'ToggledOn',False);b.state(r,'Open',False,kwargs={'fully':True})
 cleans={r:b.clean(r) for r in [*ware,'cabinet','table','prep','storage','floor']}
 cooked=[b.pred(r+'_cooked',r,'Cooked') for r in ['hotdog1','hotdog2']]
 burnt=[b.pred(r+'_burnt',r,'Burnt') for r in ['hotdog1','hotdog2']]
 for r in ['fridge','cabinet','microwave']:b.pred(r+'_open',r,'Open')
 b.pred('microwave_on','microwave','ToggledOn');b.replacement('apple','half_apple','sguztn')
 slots={r:(.16+.22*(i%4),.25+.5*(i//4)) for i,r in enumerate(ware)}
 b.phase('stage_ware',[action('open','cabinet'),*[a for r in ware for a in transfer(r,'stage',slots[r])]])
 b.phase('clean_ware',[take('brush'),*[action('wipe',r) for r in [*ware,'cabinet']],put('tool_home',(.2,.3))],['stage_ware'])
 b.phase('surfaces',transfer('can','bin',into=True)+[take('brush'),*[action('wipe',r) for r in ['table','prep','storage']],put('tool_home',(.2,.3))])
 table_slots={'plate1':(.25,.3),'plate2':(.7,.3),'fork1':(.12,.3),'fork2':(.88,.3),'cup1':(.25,.75),'cup2':(.7,.75),'tea':(.5,.75)}
 b.phase('set_table',[a for r,uv in table_slots.items() for a in transfer(r,'table',uv)],['clean_ware','surfaces'])
 b.phase('carrier_ready',transfer('box','prep',(.5,.5)),['surfaces'])
 fruit=transfer('board','prep',(.5,.8))+transfer('apple','board')+[take('knife'),action('cut','apple'),put('stage',slots['knife'])]
 fruit += [a for r in ['apple_half0','apple_half1'] for a in transfer(r,'box',into=True)]
 fruit += transfer('knife','cabinet',into=True)
 b.phase('fruit',fruit,['clean_ware','carrier_ready'])
 hot=[action('open','fridge')]
 for i in (1,2):hot+=transfer('hotdog'+str(i),'stage',(.2+.5*(i-1),.6))
 hot += [action('close','fridge'),action('open','microwave')]
 for i in (1,2):hot+=transfer('hotdog'+str(i),'microwave',into=True)
 hot += [action('close','microwave'),action('toggle_on','microwave'),action('wait_for',condition=ALL(*cooked),seconds=10.,attempts=18),action('toggle_off','microwave'),action('open','microwave')]
 for i in (1,2):hot+=transfer('hotdog'+str(i),'box',into=True)
 hot += [action('close','microwave')];b.phase('hot_food',hot,['carrier_ready'])
 serve=transfer('box','table',(.5,.5))
 for i in (1,2):serve+=transfer('hotdog'+str(i),'plate'+str(i),(.4,.5))+transfer('apple_half'+str(i-1),'plate'+str(i),(.7,.5))
 b.phase('serve',serve,['fruit','hot_food','set_table'])
 b.phase('cold_storage',transfer('box','storage',(1/6,.5))+transfer('cookie','box',into=True)+transfer('sandwich','box',into=True)+[action('open','fridge')]+transfer('box','fridge',into=True)+[action('close','fridge')],['serve','surfaces'])
 b.phase('restore_supplies',transfer('board','cabinet',into=True)+[action('close','cabinet')],['fruit','set_table'])
 b.phase('floor',[take('broom'),action('sweep','floor'),put('tool_home',(.7,.6))])
 food=['hotdog1','hotdog2','apple_half0','apple_half1'];packed=ALL(*[b.rel(r,'box','Inside') for r in food],*cooked,'apple_replaced')
 staged=b.rel('box','prep');delivered=b.rel('box','table');b.milestone('meal_packed',ALL(staged,packed));b.milestone('meal_delivered',ALL(delivered,packed),['meal_packed'])
 served=ALL(*[b.rel('hotdog'+str(i),'plate'+str(i)) for i in (1,2)],*[b.rel('apple_half'+str(i-1),'plate'+str(i)) for i in (1,2)],*[b.rel('plate'+str(i),'table') for i in (1,2)])
 b.milestone('meal_served',served,['meal_delivered']);b.milestone('carrier_released',ALL(b.rel('box','storage'),*[NOT(b.rel(r,'box','Inside')) for r in food]),['meal_served'])
 cold=ALL(b.rel('box','fridge','Inside'),b.rel('cookie','box','Inside'),b.rel('sandwich','box','Inside'));b.milestone('leftovers_chilled',cold,['carrier_released'])
 b.e['motion_guards'].append(dict(id='complete_meal_transport',object='box',since='meal_packed',until='meal_delivered',require=packed))
 b.guard('cook_before_pack',ALL(*[ANY(NOT(b.rel('hotdog'+str(i),'box','Inside')),cooked[i-1]) for i in (1,2)]))
 b.guard('clean_table_receives_food',ANY(NOT(ANY(*[b.rel(r,'table') for r in ['box',*table_slots]])),cleans['table']))
 b.terminal('complete_servings',ALL(served,*cooked,*[NOT(p) for p in burnt]));b.terminal('cold_storage',cold)
 b.terminal('ware_set',ALL(*[b.rel(r,'table') for r in table_slots],*[cleans[r] for r in ware if r not in ['knife','board']]))
 b.terminal('supplies_reset',ALL(b.rel('knife','cabinet','Inside'),b.rel('board','cabinet','Inside'),b.rel('brush','tool_home'),b.rel('broom','tool_home'),b.rel('can','bin','Inside')))
 b.terminal('surfaces_clean',ALL(*cleans.values()));b.terminal('appliances_safe',ALL(NOT('fridge_open'),NOT('cabinet_open'),NOT('microwave_open'),NOT('microwave_on')))
 b.requirement('cook_and_deliver','将整苹果真实切成两半；两根热狗在微波炉中煮熟但不烤焦。四份食物在准备台同装一个饭盒并完整送达餐桌，再每盘分一根热狗和半个苹果。',terminal=['complete_servings'],milestones=['meal_packed','meal_delivered','meal_served'],constraints=['complete_meal_transport','cook_before_pack'])
 b.requirement('leftovers','分餐后将同一空饭盒返回储存台，再装入既有曲奇和三明治，整盒放入冰箱。',terminal=['cold_storage'],milestones=['carrier_released','leftovers_chilled'])
 b.requirement('setting','清洁后的两盘、两叉、两杯和茶瓶摆在干净餐桌上；食品接收时桌面必须干净。',terminal=['ware_set'],constraints=['clean_table_receives_food'])
 b.requirement('reset','刀板放回柜中，清洁工具归还，空罐入垃圾桶；八件用品、柜面、餐桌、准备台、储存台和地面全部去尘；柜门、冰箱、微波炉关闭且微波炉停机。',terminal=['supplies_reset','surfaces_clean','appliances_safe'])
 orders=[['stage_ware','clean_ware','surfaces','set_table','carrier_ready','fruit','hot_food','serve','cold_storage','restore_supplies','floor'],['surfaces','carrier_ready','hot_food','stage_ware','clean_ware','fruit','floor','set_table','serve','restore_supplies','cold_storage']]
 return b.finalise(orders,['clean_ware','set_table','restore_supplies'])



def table_parts(b,parts_xy,jig_xy,destination_xy,compact=False):
 """Six actual furniture components; the temporary central jig must be released."""
 b.fixture('parts',parts_xy,[2.1 if compact else 2.4,2.,.65]);b.fixture('jig',jig_xy,[.18,.18,.86])
 for role,category in [('table','desk_top'),('bracket','desk_bracket')]+[('leg'+str(i),'desk_leg') for i in range(1,5)]:b.native(role,category)
 roles=['table','bracket','leg1','leg2','leg3','leg4']
 # Parts occupy separate strips; one table, one bracket and four structural legs.
 xy=[[-.62,0.],[.35,0.],[-.75,-.85],[-.05,-.85],[.65,-.85],[.65,.85]]
 if compact:xy=[[-.5,0.],[.43,0.],[-.48,-.85],[.48,-.85],[-.48,.85],[.48,.85]]
 for role,offset in zip(roles,xy):b.place(role,'parts',[parts_xy[i]+offset[i] for i in range(2)])
 clean={r:b.clean(r) for r in roles}
 assembled=ALL(b.rel('bracket','table','AttachedTo'),*[b.rel('leg'+str(i),'bracket','AttachedTo') for i in range(1,5)])
 upright=b.pred('table_upright','table','Upright',max_tilt_degrees=10.)
 # Real floor contact is required on every leg. A tabletop resting on the jig cannot pass.
 usable=ALL(assembled,upright,*[b.rel('leg'+str(i),'floor') for i in range(1,5)])
 for role,parent in [('bracket','table')]+[('leg'+str(i),'bracket') for i in range(1,5)]:
  b.guard('clean_before_attach_'+role,ANY(NOT(b.rel(role,parent,'AttachedTo')),ALL(clean[role],clean[parent])))
 actions=[take('brush'),*[action('wipe',r) for r in roles],put('tool_home',(.2,.3))]
 actions+=transfer('table','jig')+[take('bracket'),action('attach','table')]
 for i in range(1,5):actions += [take('leg'+str(i)),action('attach','bracket')]
 actions += [take('table'),action('place_on_top','floor',xy=destination_xy)]
 b.terminal('usable_table',ALL(usable,*clean.values()))
 b.milestone('table_assembled',assembled)
 b.requirement('table_assembly','六个有尘部件先清洁，再用原生接合点装成一张桌子；完成后桌面正立、四条腿各自接触地板，移离临时装配支撑。',terminal=['usable_table'],milestones=['table_assembled'],constraints=['clean_before_attach_'+r for r in roles[1:]])
 return actions,usable


def garden(inv,models):
 b=Builder('garden','spraying_for_bugs',inv,models)
 for r,n in dict(plant1='pot_plant_172',plant2='pot_plant_173',sprayer='insectifuge_atomizer_174',floor='floors_ghetev_0',lawn='lawn_aztwla_0').items():b.existing(r,n)
 for r,xy,size in [('source',[11.8,4.],[1.8,1.,.55]),('work',[17.4,4.],[1.7,.9,.55]),('display',[12.9,13.65],[2.2,.8,.6]),('stage',[12.8,8.7],[1.8,1.3,.65]),('tool_home',[15.55,4.1],[1.7,1.1,.6]),('food_stage',[18.1,5.4],[1.6,1.2,.6])]:b.fixture(r,xy,size)
 b.existing('background_lounger','garden_chair_lraplz_0');b.place('background_lounger','floor',[18.3,12.7])
 for r in ['trowel','shears','pillow1','pillow2','apple','knife']:
  b.native(r,{'pillow1':'pillow','pillow2':'pillow','knife':'carving_knife'}.get(r,r))
 b.imported('protected','spraying_for_bugs','pot_plant_172')
 b.imported('brush','clean_a_keyboard','pipe_cleaner_120');b.imported('broom','clean_a_patio','broom_172',scale=[1.,1.,1.])
 for r,t,n in [('book','clean_up_your_desk','paperback_book_225'),('can','picking_up_trash','can_of_soda_114'),('bin','picking_up_trash','trash_can_116'),('tea','preparing_lunch_box','bottle_of_tea_208'),('board','preparing_lunch_box','chopping_board_211'),('plate','setting_the_table','plate_243')]:b.imported(r,t,n)
 for i in (1,2):
  b.imported('cup'+str(i),'setting_the_table','water_glass_bbpraa_0')
  b.imported('lamp'+str(i),'turning_out_all_lights_before_sleep','table_lamp_428')
  b.imported('seat'+str(i),'setting_the_table',category='straight_chair',index=i-1)
  b.place('seat'+str(i),'floor',[13.2,11.8] if i==1 else [14.9,12.5])
  b.place('pillow'+str(i),'work');b.place('lamp'+str(i),'stage');b.state('lamp'+str(i),'ToggledOn',False)
 b.place('plant1','source',[11.4,4.25]);b.place('plant2','source',[12.2,4.25]);b.place('protected','lawn',[18.6,4.]);b.place('bin','lawn',[18.6,6.5])
 for r,t in [('book','work'),('can','work'),('trowel','source'),('shears','source'),('sprayer','source'),('brush','tool_home'),('broom','tool_home'),('tea','food_stage'),('apple','food_stage'),('plate','food_stage'),('cup1','food_stage'),('cup2','food_stage'),('knife','food_stage'),('board','food_stage')]:b.place(r,t)
 # Separate large boards / plants from small tools using measured native extents.
 initial_slots={'pillow1':(.3,.65),'pillow2':(.7,.65),'book':(.25,.15),'can':(.75,.15),'trowel':(.18,.17),'shears':(.5,.17),'sprayer':(.82,.17),
                'board':(.25,.65),'knife':(.25,.13),'tea':(.75,.8),'apple':(.9,.6),
                'plate':(.65,.5),'cup1':(.7,.2),'cup2':(.9,.2),'lamp1':(.2,.2),'lamp2':(.8,.2),'brush':(.7,.2),'broom':(.7,.95)}
 for row in b.layout:
  if row['object'] in initial_slots and row.get('auto_slot'):
   row.pop('auto_slot');row['support_uv']=list(initial_slots[row['object']])
 b.s['robot_start']={'position':[16.,12.7,.03],'orientation':[0.,0.,0.,1.]}
 b.s['scene_setup_note']='Stations arranged from measured native geometry. Original trees, lights, grill and patio furniture retained; lounger moved east before the episode to clear the assembled table destination.'
 clean={r:b.clean(r) for r in ['work','display','pillow1','pillow2','cup1','cup2','trowel','shears','sprayer','floor']}
 b.state('protected','Covered',False,system='insectifuge')
 protected_clean=NOT(b.pred('protected_contaminated','protected','Covered',system='insectifuge'))
 b.pred('protected_at_start','protected','NearInitial',distance=.03)
 # NearInitial is a read-only pose predicate added by the complete-workflow adapter.
 b.guard('protected_unchanged',ALL(protected_clean,'protected_at_start'))
 treated=[]
 for i in (1,2):
  r='plant'+str(i);b.state(r,'Covered',False,system='insectifuge');treated.append(b.pred(r+'_treated',r,'Covered',system='insectifuge'))
 b.replacement('apple','half_apple','sguztn')
 tool_slots={'board':(.23,.5),'brush':(.7,.2),'trowel':(.7,.4),'shears':(.7,.6),'sprayer':(.95,.4),'knife':(.7,.8),'broom':(.7,.95)}
 b.phase('clear',transfer('book','stage',(.5,.5))+transfer('can','bin',into=True)+transfer('pillow1','stage',(.25,.75))+transfer('pillow2','stage',(.75,.75))+[take('broom'),action('sweep','floor'),put('tool_home',tool_slots['broom'])])
 tools=[]
 for i,r in enumerate(['trowel','shears','sprayer']):tools+=transfer(r,'work',(.2+.3*i,.5))
 tools += [take('brush'),*[action('wipe',r) for r in ['trowel','shears','sprayer']],put('tool_home',tool_slots['brush'])]
 for r in ['trowel','shears']:tools+=transfer(r,'tool_home',tool_slots[r])
 b.phase('tools',tools,['clear'])
 b.phase('isolate_plants',[a for i,r in enumerate(['plant1','plant2']) for a in transfer(r,'work',(.25+.5*i,.5))],['tools'])
 b.phase('treat',[take('sprayer'),action('spray','plant1'),action('spray','plant2'),put('tool_home',tool_slots['sprayer'])],['isolate_plants'])
 b.phase('display_clean',[take('brush'),action('wipe','display'),put('tool_home',tool_slots['brush'])])
 b.phase('return_plants',[a for i,r in enumerate(['plant1','plant2']) for a in transfer(r,'display',(.38+.26*i,.5))],['treat','display_clean'])
 b.phase('work_reset',[take('brush'),action('wipe','work'),put('tool_home',tool_slots['brush'])],['return_plants'])
 assembly,usable=table_parts(b,[14.,5.7],[15.4,8.1],[14.1,11.7]);b.phase('assemble',assembly)
 for row in assembly:
  if row['primitive']=='place_on_top' and row.get('role')=='tool_home':row['uv']=list(tool_slots['brush'])
 b.phase('seats',[take('brush'),action('wipe','pillow1'),action('wipe','pillow2'),put('tool_home',tool_slots['brush'])]+transfer('pillow1','seat1')+transfer('pillow2','seat2'),['clear'])
 serving=[take('brush'),action('wipe','cup1'),action('wipe','cup2'),put('tool_home',tool_slots['brush'])]
 for r,uv in [('cup1',(.25,.7)),('cup2',(.75,.7)),('tea',(.5,.8)),('plate',(.5,.35))]:serving+=transfer(r,'table',uv)
 b.phase('tea',serving,['assemble'])
 fruit=transfer('board','food_stage',(.5,.4))+transfer('apple','board')+[take('knife'),action('cut','apple'),put('food_stage',(.5,.9))]
 for r in ['apple_half0','apple_half1']:fruit+=transfer(r,'plate',(.3 if r.endswith('0') else .7,.5))
 fruit+=transfer('board','tool_home',tool_slots['board'])+transfer('knife','tool_home',tool_slots['knife']);b.phase('fruit',fruit,['tea'])
 b.phase('reading',transfer('book','table',(.5,.55))+transfer('lamp1','display',(.12,.5))+transfer('lamp2','display',(.89,.5))+[action('toggle_on','lamp1'),action('toggle_on','lamp2')],['clear','assemble','return_plants'])
 for i in (1,2):b.guard('clean_display_'+str(i),ANY(NOT(b.rel('plant'+str(i),'display')),ALL(treated[i-1],clean['display'])))
 b.guard('assembled_before_food',ANY(NOT(ANY(*[b.rel(r,'table') for r in ['cup1','cup2','tea','plate']])),usable))
 b.terminal('plants_ready',ALL(*treated,*[b.rel('plant'+str(i),'display') for i in (1,2)]))
 b.terminal('guest_ready',ALL(*[b.rel('pillow'+str(i),'seat'+str(i)) for i in (1,2)],*[b.rel(r,'table') for r in ['cup1','cup2','tea','plate','book']],*[b.rel('apple_half'+str(i),'plate') for i in (0,1)],'apple_replaced',*[b.pred('lamp'+str(i)+'_on','lamp'+str(i),'ToggledOn') for i in (1,2)],*[b.rel('lamp'+str(i),'display') for i in (1,2)]))
 b.terminal('reset',ALL(*clean.values(),*[b.rel(r,'tool_home') for r in ['trowel','shears','sprayer','brush','broom','board','knife']],b.rel('can','bin','Inside')))
 b.requirement('plants','两盆植物在独立作业台护理后放到当前干净的展示架；免处理植物整个过程保持原位且不沾药剂。',terminal=['plants_ready'],constraints=['protected_unchanged','clean_display_1','clean_display_2'])
 b.requirement('guests','两座垫清洁归座；装配合格的桌上摆两杯、茶瓶、果盘和书，整苹果真实切两半装盘，两盏灯归展示架并开启。',terminal=['guest_ready'],constraints=['assembled_before_food'])
 b.requirement('reset','两园艺工具、喷壶、刀板和清洁工具归还；工作台、展示架、座垫、杯具、园艺工具、喷壶外表和地面清洁，空罐入桶。',terminal=['reset'])
 return b.finalise([['clear','tools','isolate_plants','treat','display_clean','return_plants','work_reset','assemble','seats','tea','fruit','reading'],['assemble','display_clean','clear','seats','tea','fruit','tools','isolate_plants','treat','return_plants','reading','work_reset']],['treat','tea','work_reset'])


def workshop(inv,models):
 b=Builder('workshop','outfit_a_basic_toolbox',inv,models)
 b.existing('box','toolbox_191');b.existing('floor','floors_hkdmte_0')
 b.s['scene_exclusions']=['car_ssxsje_0']
 b.s['scene_setup_note']='Native garage prepared as an empty workshop before the episode; the background car is absent from the frozen scene.'
 b.s['robot_start']={'position':[-13.2,10.7,.02],'orientation':[0.,0.,0.,1.]}
 for role,xy,size in [('source',[-13.2,9.4],[1.8,1.,.65]),('bench',[-12.3,10.9],[.7,1.8,.7]),('shelf',[-13.85,17.7],[1.4,.7,.7])]:b.fixture(role,xy,size)
 # Native garage walls narrow in the middle. Use measured floor-map clearance
 # and keep an east-side passage beside the compact component staging surface.
 b.fixture('tool_home',[-14.3,11.],[1.,1.8,.6]);b.fixture('products',[-12.2,17.5],[.7,1.6,.6])
 for r,t,n in [('brush','clean_a_keyboard','pipe_cleaner_120'),('axe','chopping_wood','axe_173'),('block','chopping_wood','chopping_block_172'),('crate','assembling_gift_baskets','wicker_basket_89'),('book','clean_up_your_desk','paperback_book_225'),('can','picking_up_trash','can_of_soda_114'),('bin','picking_up_trash','trash_can_116'),('broom','clean_a_patio','broom_172')]:b.imported(r,t,n,**({'scale':[1.,1.,1.]} if r=='broom' else {}))
 b.native('paintbrush','rag');b.native('ink','ink_bottle');b.native('detergent_basin','ink_bottle',scale=[3.,3.,2.]);b.place('detergent_basin','tool_home');b.state('detergent_basin','Filled',True,system='detergent')
 b.s['physics']={'use_gpu_dynamics':True}
 b.s['representation']={'coating_tool':'native_rigid_rag','reason':'Native paintbrush lacks ParticleRemover/Saturated; rag absorbs ink only after detergent preparation.'}
 for i in (1,2):b.imported('log'+str(i),'chopping_wood','log_'+str(173+i));b.place('log'+str(i),'shelf')
 for r,t in [('box','source'),('axe','source'),('brush','source'),('paintbrush','source'),('book','bench'),('can','bench'),('crate','shelf'),('ink','source'),('broom','tool_home')]:b.place(r,t)
 initial_slots={'box':(.22,.5),'axe':(.7,.25),'brush':(.7,.5),'paintbrush':(.7,.8),'ink':(.95,.8),'detergent_basin':(.5,.08),'broom':(.5,.93)}
 for row in b.layout:
  if row['object'] in initial_slots and row.get('auto_slot'):
   row.pop('auto_slot');row['support_uv']=list(initial_slots[row['object']])
 b.place('bin','floor',[-12.1,9.1]);b.place('block','floor',[-13.9,12.]);b.state('box','Open',False,kwargs={'fully':True})
 b.state('ink','Filled',True,system='ink')
 clean={r:b.clean(r) for r in ['bench','source','floor']}
 b.pred('box_open','box','Open')
 tools=['axe','brush','paintbrush'];kit=ALL(*[b.rel(r,'box','Inside') for r in tools],NOT('box_open'))
 b.phase('clear_bench',transfer('book','shelf',(.5,.85))+transfer('can','bin',into=True)+[take('brush'),action('wipe','bench'),put('source',(.7,.5))])
 b.phase('pack_kit',[action('open','box'),*[a for r in tools for a in transfer(r,'box',into=True)],action('close','box')],['clear_bench'])
 b.phase('deliver_kit',transfer('box','bench',(.5,.25)),['pack_kit'])
 tool_slots={'axe':(.5,.3),'brush':(.5,.5),'paintbrush':(.5,.73)}
 b.phase('inventory',[action('open','box'),*[a for r in tools for a in transfer(r,'tool_home',tool_slots[r])]],['deliver_kit'])
 assembly,usable=table_parts(b,[-13.8,13.5],[-12.25,15.],[-13.6,15.8],compact=True);b.phase('assemble',assembly,['inventory','clear_bench'])
 # The shared assembly helper returns its brush to the same dedicated tool slot.
 for row in assembly:
  if row['primitive']=='place_on_top' and row.get('role')=='tool_home':row['uv']=list(tool_slots['brush'])
 for i in (1,2):b.replacement('log'+str(i),'half_log',['bbieuz','rvtvqc'])
 # Native half model is resolved from actual source metadata before scene build.
 b.phase('crate_ready',transfer('crate','bench',(.5,.75)),['clear_bench'])
 cut=[]
 for i in (1,2):
  r='log'+str(i);cut+=transfer(r,'block')+[take('axe'),action('cut',r),put('tool_home',tool_slots['axe'])]
  for j in (0,1):cut+=transfer(r+'_half'+str(j),'crate',into=True)
 b.phase('cut_logs',cut,['inventory','crate_ready'])
 b.phase('coat',transfer('ink','bench',(.5,.5))+[take('paintbrush'),action('soak','detergent_basin'),action('soak','ink'),action('spread','table'),put('tool_home',tool_slots['paintbrush'])]+transfer('ink','source',(.95,.8)),['assemble','inventory'])
 children=['log'+str(i)+'_half'+str(j) for i in (1,2) for j in (0,1)]
 delivery=transfer('crate','products',(.5,.2))
 for i,r in enumerate(children):delivery+=transfer(r,'products',(.5,.4+.16*i))
 delivery+=transfer('crate','shelf',(.2,.5));b.phase('deliver_wood',delivery,['cut_logs'])
 b.phase('clean_return',[take('brush'),action('wipe','source'),put('tool_home',(.5,.5))],['inventory'])
 b.phase('return_kit',[*[a for r in tools for a in transfer(r,'box',into=True)],action('close','box')]+transfer('box','source',(.3,.5)),['coat','deliver_wood','clean_return'])
 b.phase('floor',[take('broom'),action('sweep','floor'),put('tool_home',(.5,.93))])
 packed=b.milestone('kit_packed',ALL(kit,b.rel('box','source')));delivered=b.milestone('kit_delivered',ALL(kit,b.rel('box','bench')),[packed])
 inventory=b.milestone('inventory_unpacked',ALL(*[b.rel(r,'tool_home') for r in tools]),[delivered])
 wood=ALL(*[b.rel(r,'crate','Inside') for r in children],'log1_replaced','log2_replaced')
 b.milestone('wood_packed',ALL(wood,b.rel('crate','bench')));b.milestone('wood_delivered',ALL(wood,b.rel('crate','products')),['wood_packed'])
 b.milestone('wood_unloaded',ALL(*[b.rel(r,'products') for r in children]),['wood_delivered'])
 b.milestone('empty_crate_returned',ALL(b.rel('crate','shelf'),*[NOT(b.rel(r,'crate','Inside')) for r in children]),['wood_unloaded'])
 b.e['motion_guards'] += [dict(id='closed_complete_kit_transport',object='box',since=packed,until=delivered,require=kit),dict(id='complete_wood_transport',object='crate',since='wood_packed',until='wood_delivered',require=wood)]
 b.guard('clean_complete_return',ANY(NOT(ALL(kit,b.rel('box','source'))),clean['source']),until=None)
 # The initial source is dusty but the box is initially empty; it must be cleaned
 # before the return, not before the first departure. Guard activates after delivery.
 b.e['state_guards']=[g for g in b.e['state_guards'] if g['id']!='clean_complete_return']
 b.milestone('complete_kit_returned',ALL(kit,b.rel('box','source'),clean['source']),['inventory_unpacked'])
 coated=b.pred('table_coated','table','Covered',system='ink')
 b.terminal('processed_products',ALL(usable,coated,*[b.rel(r,'products') for r in children],'log1_replaced','log2_replaced'))
 b.terminal('kit_and_material_reset',ALL(kit,b.rel('box','source'),b.rel('ink','source'),b.rel('crate','shelf'),b.rel('book','shelf'),b.rel('can','bin','Inside'),b.rel('broom','tool_home'),*clean.values()))
 b.requirement('kit','斧头、除尘刷、涂布布完整闭箱外运至工作台，展开同时盘点；工作后完整重装闭箱归还当前干净的原位。',terminal=['kit_and_material_reset'],milestones=['kit_packed','kit_delivered','inventory_unpacked','complete_kit_returned'],constraints=['closed_complete_kit_transport'])
 b.requirement('products','桌面以原生油墨材料涂布；两根木料真实切成四块，完整装入同一周转箱送到柴架，卸货后空箱归还。',terminal=['processed_products'],milestones=['wood_packed','wood_delivered','wood_unloaded','empty_crate_returned'],constraints=['complete_wood_transport'])
 return b.finalise([['clear_bench','pack_kit','deliver_kit','inventory','assemble','crate_ready','cut_logs','coat','deliver_wood','clean_return','return_kit','floor'],['clear_bench','pack_kit','deliver_kit','inventory','crate_ready','cut_logs','deliver_wood','clean_return','assemble','coat','floor','return_kit']],['coat','deliver_wood','return_kit'])


def study(inv,models):
 b=Builder('study','clean_up_your_desk',inv,models)
 for r,n in dict(desk='desk_bhkhxo_0',shelf='shelf_amyper_2',floor_a='floors_qnvzkr_0',floor_b='floors_htolat_0',washer='washer_ynwamu_0',dryer='clothes_dryer_xsuyua_0',laundry='countertop_ikwqer_0',source='countertop_sjxber_0',sink='tabletop_sink_qnequy_0',bath='bathtub_pdcrfs_0',toilet='toilet_udiezm_1',amenities='bottom_cabinet_xqmipa_0',bedside='nightstand_wbxekb_0',pencil='pencil_case_224',book1='paperback_book_225',book2='paperback_book_226').items():b.existing(r,n)
 b.imported('drawer','clean_up_your_desk','bottom_cabinet_rhdbzv_0',fixed_base=True,scale=[1.,.3,1.])
 b.imported('cabinet','clean_up_your_desk','bottom_cabinet_fancyy_0',fixed_base=True)
 b.existing('closet_floor','floors_yrqspn_0');b.existing('room_bed','bed_rrcvaq_1')
 b.existing('closet_door','sliding_door_tprpvb_4');b.existing('shelf_door','sliding_door_tprpvb_1')
 b.existing('desk_pen','pen_229')
 b.s['scene_exclusions']=['nightstand_wbxekb_2','wardrobe_lrokhl_0']
 b.s['scene_setup_note']='Replace non-articulated nightstand and open wardrobe shells with native articulated storage furniture before freezing.'
 b.place('drawer','floor_a',[20.6,14.5]);b.place('cabinet','closet_floor',[20.28,24.15])
 b.s['robot_start']={'position':[22.9,14.2,.02],'orientation':[0.,0.,0.,1.]}
 b.fixture('tool_home',[21.8,14.5],[1.3,.65,.6]);b.fixture('delivery',[22.3,22.5],[1.,.7,.65]);b.fixture('desk_stage',[23.75,14.6],[.8,.8,.65])
 # A dedicated supply surface accommodates the carrier and six guest items;
 # the original narrow countertop remains in the native utility room.
 b.fixture('source',[23.1,-1.05],[1.5,.8,.65])
 for r,t,n in [('keyboard','clean_a_keyboard','keyboard_121'),('brush','clean_a_keyboard','pipe_cleaner_120'),('vacuum','vacuuming_floors','vacuum_89'),('toy','putting_away_toys','toy_figure_242'),('toy_box','putting_away_toys','toy_box_240'),('carrier','assembling_gift_baskets','wicker_basket_89'),('cup','setting_the_table','water_glass_bbpraa_0')]:b.imported(r,t,n,**({'scale':[1.8,1.8,1.4]} if r=='carrier' else {}))
 # Laptop screen supplies the native switchable monitor used for maintenance.
 b.existing('monitor','laptop_233')
 for r,c in [('shirt','sock'),('shorts','boxers'),('rag','rag'),('water','bottle_of_water'),('soap','bar_soap'),('paper','toilet_paper')]:b.native(r,c,**{})
 b.s['physics']={'use_gpu_dynamics':True}
 b.s['representation']={'garments':'native_full_size_rigid_sock_and_boxers','deformable_cloth_validated':False,'process':'native_washer_and_dryer_rules_with_observed_clean_wet_dry_history'}
 for i in (1,2):
  b.existing('lamp'+str(i),'table_lamp_uupwiq_0' if i==1 else 'table_lamp_qgdwbh_1');b.place('lamp'+str(i),'floor_a' if i==1 else 'floor_b',[22.55,13.1] if i==1 else [22.15,23.6]);b.state('lamp'+str(i),'ToggledOn',True)
  b.imported('picture'+str(i),'hanging_pictures','poster_73')
  b.imported('nail'+str(i),'hanging_pictures','wall_nail_wlnail_0',fixed_base=True,position=[24.241,13.55+.5*(i-1),1.7],orientation=[0.,-.70710678,0.,.70710678])
  b.place('picture'+str(i),'desk_stage',orientation=[0.,.70710678,0.,.70710678])
 b.place('book1','floor_a',[22.3,13.9]);b.place('book2','floor_b',[22.3,24.3]);b.place('toy','floor_b',[22.2,25.1]);b.place('toy_box','floor_b',[21.,22.8])
 b.place('pencil','room_bed',[21.7,13.3]);b.place('desk_pen','desk')
 for r,t in [('keyboard','desk'),('monitor','desk'),('brush','tool_home'),('vacuum','tool_home'),('rag','tool_home'),('shirt','source'),('shorts','source'),('carrier','source'),('cup','source'),('water','source'),('soap','source'),('paper','source')]:b.place(r,t)
 initial_slots={'keyboard':(.5,.86),'monitor':(.5,.407),'desk_pen':(.9,.18),'picture1':(.35,.25),'picture2':(.35,.75),'brush':(.2,.5),'vacuum':(.75,.5),'rag':(.5,.7),'carrier':(.18,.5),'shirt':(.5,.25),'shorts':(.8,.28),'cup':(.5,.75),'water':(.65,.75),'soap':(.8,.75),'paper':(.92,.75)}
 for row in b.layout:
  if row['object'] in initial_slots and row.get('auto_slot'):
   row.pop('auto_slot');row['support_uv']=list(initial_slots[row['object']])
 # Access passages are open in the prepared scene, including the native
 # closet that actually supports the replacement articulated cabinet.
 for r in ['closet_door','shelf_door']:b.state(r,'Open',True,kwargs={'fully':True})
 for r in ['washer','dryer']:b.state(r,'ToggledOn',False);b.state(r,'Open',False,kwargs={'fully':True});b.pred(r+'_open',r,'Open');b.pred(r+'_on',r,'ToggledOn')
 b.state('monitor','ToggledOn',True);b.pred('monitor_on','monitor','ToggledOn')
 for r in ['drawer','cabinet']:b.state(r,'Open',False,kwargs={'fully':True});b.pred(r+'_open',r,'Open')
 clean={r:b.clean(r) for r in ['shelf','desk','keyboard','monitor','cabinet','floor_a','floor_b']}
 bath_clean={r:b.clean(r,'stain') for r in ['sink','bath','toilet']}
 # Solvent and dirt are authored only before policy; process waits use read-only predicates.
 b.state('washer','Filled',True,system='detergent');b.state('sink','Filled',True,system='detergent')
 garment_clean={r:b.clean(r,'stain') for r in ['shirt','shorts']}
 for r in ['shirt','shorts']:b.state(r,'Saturated',False,system='water');b.pred(r+'_wet',r,'Saturated',system='water')
 dry=ALL(*garment_clean.values(),NOT('shirt_wet'),NOT('shorts_wet'));washed=ALL(*garment_clean.values(),'shirt_wet','shorts_wet')
 b.phase('storage',[action('open','drawer')]+transfer('pencil','drawer',into=True)+[action('close','drawer'),take('brush'),action('wipe','shelf'),put('tool_home',(.2,.3))])
 b.phase('clear_rooms',transfer('book1','shelf',(.25,.5))+transfer('book2','shelf',(.75,.5))+transfer('toy','toy_box',into=True),['storage'])
 b.phase('desk',transfer('keyboard','desk_stage',(.82,.5))+[action('toggle_off','monitor'),take('brush'),action('wipe','desk'),action('wipe','keyboard'),action('wipe','monitor'),put('tool_home',(.2,.5))]+transfer('keyboard','desk',(.5,.86))+[action('toggle_on','monitor')])
 b.phase('lamps',transfer('lamp1','desk',(.5,.08))+[action('toggle_off','lamp1')]+transfer('lamp2','bedside',(.5,.75))+[action('toggle_off','lamp2')])
 b.phase('vacuum',[take('vacuum'),action('vacuum','floor_a'),action('vacuum','floor_b'),put('tool_home',(.8,.5))],['clear_rooms','lamps'])
 b.phase('cabinet',[action('open','cabinet'),take('brush'),action('wipe','cabinet'),put('tool_home',(.2,.3))])
 b.phase('wash',[action('open','washer')]+transfer('shirt','washer',into=True)+transfer('shorts','washer',into=True)+[action('close','washer'),action('toggle_on','washer'),action('wait_for',condition=washed,attempts=18,seconds=10.),action('toggle_off','washer')])
 b.phase('dry',[action('open','washer'),action('open','dryer')]+transfer('shirt','dryer',into=True)+transfer('shorts','dryer',into=True)+[action('close','washer'),action('close','dryer'),action('toggle_on','dryer'),action('wait_for',condition=dry,attempts=18,seconds=10.),action('toggle_off','dryer'),action('open','dryer')],['wash'])
 b.phase('bathroom',[take('rag'),action('soak','sink'),action('wipe','sink'),action('wipe','bath'),action('wipe','toilet'),put('tool_home',(.5,.7))])
 contents=['shirt','shorts','cup','water','soap','paper']
 b.phase('pack',transfer('carrier','laundry')+[a for r in contents for a in transfer(r,'carrier',into=True)]+[action('close','dryer')],['dry'])
 deliver=transfer('carrier','delivery')+transfer('shirt','cabinet',into=True)+transfer('shorts','cabinet',into=True)+transfer('cup','bedside',(.25,.22))+transfer('water','bedside',(.7,.22))+transfer('soap','amenities',(.2,.5))+transfer('paper','amenities',(.7,.5))+transfer('carrier','source',(.18,.5))+[action('close','cabinet')]
 b.phase('deliver',deliver,['pack','cabinet','bathroom'])
 b.phase('pictures',[take('picture1'),action('attach','nail1'),take('picture2'),action('attach','nail2')])
 b.milestone('washed',ALL(washed,*[b.rel(r,'washer','Inside') for r in ['shirt','shorts']]))
 b.milestone('dried',ALL(dry,*[b.rel(r,'dryer','Inside') for r in ['shirt','shorts']]),['washed'])
 packed=ALL(dry,*[b.rel(r,'carrier','Inside') for r in contents]);b.milestone('amenities_packed',ALL(packed,b.rel('carrier','laundry')),['dried'])
 b.milestone('amenities_delivered',ALL(packed,b.rel('carrier','delivery'),clean['cabinet'],*bath_clean.values()),['amenities_packed'])
 distributed=ALL(b.rel('shirt','cabinet','Inside'),b.rel('shorts','cabinet','Inside'),b.rel('cup','bedside'),b.rel('water','bedside'),b.rel('soap','amenities'),b.rel('paper','amenities'))
 b.milestone('amenities_distributed',distributed,['amenities_delivered']);b.milestone('carrier_returned',ALL(b.rel('carrier','source'),*[NOT(b.rel(r,'carrier','Inside')) for r in contents]),['amenities_distributed'])
 b.e['motion_guards'].append(dict(id='complete_dry_delivery',object='carrier',since='amenities_packed',until='amenities_delivered',require=packed))
 # This condition checks the maintenance event (dirty -> clean), not merely final power.
 b.e['transition_guards']=[dict(id='monitor_off_when_cleaned',predicate='monitor_dust',from_value=True,to_value=False,require=NOT('monitor_on'))]
 b.e['state_guards'].append(dict(id='cabinet_clean_receives_garments',until=None,require=ANY(NOT(ANY(b.rel('shirt','cabinet','Inside'),b.rel('shorts','cabinet','Inside'))),clean['cabinet'])))
 b.terminal('room_reset',ALL(*[b.rel(r,'shelf') for r in ['book1','book2']],b.rel('toy','toy_box','Inside'),b.rel('pencil','drawer','Inside'),b.rel('keyboard','desk'),b.rel('monitor','desk'),'monitor_on',b.rel('lamp1','desk'),b.rel('lamp2','bedside'),*[NOT(b.pred('lamp'+str(i)+'_on','lamp'+str(i),'ToggledOn')) for i in (1,2)],*[b.rel(r,'tool_home') for r in ['brush','vacuum','rag']],*clean.values(),*bath_clean.values()))
 b.terminal('guest_package',ALL(dry,distributed,b.rel('carrier','source')))
 b.terminal('pictures_attached',ALL(b.rel('picture1','nail1','AttachedTo'),b.rel('picture2','nail2','AttachedTo')))
 b.terminal('safe_appliances',ALL(*[NOT(r+'_'+state) for r in ['washer','dryer'] for state in ['open','on']],NOT('cabinet_open'),NOT('drawer_open')))
 b.requirement('rooms','两本书、一玩具、笔袋归位；显示器关闭后清洁，工作位恢复后开启；桌面、键盘、显示器、书架及两房地面清洁，两灯归位并关闭，清洁工具归还。',terminal=['room_reset'],constraints=['monitor_off_when_cleaned'])
 b.requirement('laundry','袜子和短裤在原生洗衣机完成去污并湿润，再在烘干机中变干；两件干净干燥衣物与杯、水瓶、肥皂、纸卷完整装箱交付，衣柜及卫浴当前清洁后分配，空箱回原位。',terminal=['guest_package','safe_appliances'],milestones=['washed','dried','amenities_packed','amenities_delivered','amenities_distributed','carrier_returned'],constraints=['complete_dry_delivery','cabinet_clean_receives_garments'])
 b.requirement('pictures','两幅画通过兼容的原生挂点真正接合。',terminal=['pictures_attached'])
 return b.finalise([['storage','clear_rooms','desk','lamps','vacuum','cabinet','wash','dry','bathroom','pack','deliver','pictures'],['wash','storage','clear_rooms','lamps','cabinet','bathroom','dry','pack','deliver','desk','pictures','vacuum']],['bathroom','deliver','pictures'])


def main():
 p=argparse.ArgumentParser();p.add_argument('--inventory',type=Path,required=True);p.add_argument('--models',type=Path,required=True);p.add_argument('--only',choices=['kitchen','garden','workshop','study']);a=p.parse_args()
 inv=json.loads(a.inventory.read_text());models=json.loads(a.models.read_text());dest=R/'task_designs/complete_v2';dest.mkdir(exist_ok=True)
 functions={'kitchen':kitchen,'garden':garden,'workshop':workshop,'study':study}
 for name,fn in functions.items():
  if a.only and name!=a.only:continue
  spec=fn(inv,models);target=dest/(spec['id']+'.json');target.write_text(json.dumps(spec,ensure_ascii=False,indent=2)+'\n');print(name,spec['compiled_action_count'],len(spec['evaluation']['predicates']),target)
if __name__=='__main__':main()
