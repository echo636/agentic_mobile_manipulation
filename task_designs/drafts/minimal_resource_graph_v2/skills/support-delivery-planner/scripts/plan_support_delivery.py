"""Bounded method enumeration for 2–3 co-located supported objects.

This module contains no optimal-path search. It enumerates source support
forests and delivery orders, expands legal actions, and reports their costs.
"""
import argparse
from itertools import permutations, product
import json
import math
from pathlib import Path


def validate(model):
    if not isinstance(model,dict) or set(model)!={'parents','goals','distances'}:
        raise ValueError('Expected parents, goals and distances only')
    parents,goals,distances=(model[k] for k in ['parents','goals','distances'])
    if not isinstance(parents,list) or not 2<=len(parents)<=3:raise ValueError('Supports exactly 2 or 3 objects')
    n=len(parents)
    if any(type(p) is not int or not -1<=p<i for i,p in enumerate(parents)):
        raise ValueError('Each parent must be -1 or a smaller object index')
    if not isinstance(distances,list) or not 2<=len(distances)<=4:raise ValueError('Supports 2–4 locations')
    m=len(distances)
    if any(not isinstance(row,list) or len(row)!=m for row in distances):raise ValueError('Expected square distance matrix')
    if any(type(x) not in (int,float) or not math.isfinite(x) for row in distances for x in row):raise ValueError('Distances must be finite real numbers')
    if not isinstance(goals,list) or len(goals)!=n or any(type(x) is not int or not 0<=x<m for x in goals):raise ValueError('Each object needs a valid destination')
    for i in range(m):
        for j in range(m):
            x=distances[i][j]
            if (i==j and x!=0) or (i!=j and x<=0):raise ValueError('Distances need zero diagonal and positive other entries')
            if abs(x-distances[j][i])>1e-9:raise ValueError('Distances must be symmetric')
            for k in range(m):
                if x>distances[i][k]+distances[k][j]+1e-9:raise ValueError('Distances must satisfy the triangle inequality')
    return model


def initial(model):return (0,(0,)*len(model['parents']),tuple(model['parents']),-1)
def descendants(parents,root):
    selected={root}
    for child,parent in enumerate(parents):
        if parent in selected:selected.add(child)
    return selected


def goal(state,model):
    return state[1]==tuple(model['goals']) and all(p==-1 for p in state[2]) and state[3]==-1


def choices(state,model):
    robot,positions,parents,hand=state;n=len(positions);result={}
    def add(action,where=robot,pos=positions,par=parents,held=hand,cost=1):
        result[action]=((where,tuple(pos),tuple(par),held),cost)
    for location in range(len(model['distances'])):
        if location==robot:continue
        pos=list(positions)
        if hand!=-1:
            for child in descendants(parents,hand):pos[child]=location
        add('move_'+str(location),where=location,pos=pos,cost=model['distances'][robot][location])
    if hand==-1:
        for obj in range(n):
            if positions[obj]==robot:
                par=list(parents);par[obj]=-1;add('pick_'+str(obj),par=par,held=obj)
    else:
        add('put_surface',held=-1)
        for parent in range(hand):
            if positions[parent]==robot:
                par=list(parents);par[hand]=parent;add('stack_on_'+str(parent),par=par,held=-1)
    return result


def replay(model,actions):
    validate(model);state=initial(model);cost=0;history=[];error=None
    if not isinstance(actions,list) or len(actions)>60:error='Expected a plan of at most 60 strings'
    else:
        for i,action in enumerate(actions,1):
            options=choices(state,model)
            if not isinstance(action,str) or action not in options:
                error=dict(step=i,action=action,state=state,reason='Unknown action or unsatisfied precondition');break
            state,weight=options[action];cost+=weight;history.append(dict(step=i,action=action,after=state,cost=cost))
    complete=error is None and goal(state,model)
    return dict(valid=error is None,complete=complete,cost=cost if complete else None,executed_cost=cost,final_state=state,history=history,error=error)


def candidates(model):
    validate(model);n=len(model['parents']);rows=[]
    for forest in product(*(range(-1,child) for child in range(n))):
        state=initial(model);prefix=[]
        for child in reversed(range(n)):
            if state[2][child]==forest[child]:continue
            for action in ['pick_'+str(child),'put_surface' if forest[child]==-1 else 'stack_on_'+str(forest[child])]:
                state,_=choices(state,model)[action];prefix.append(action)
        def expand(order,index,current,actions,methods):
            if index==n:
                checked=replay(model,actions)
                if checked['complete']:rows.append(dict(actions=actions,cost=checked['cost'],source_forest=list(forest),order=list(order),method=methods,assembly_actions=list(prefix)))
                return
            target=order[index];destination=model['goals'][target]
            # Already delivered independent objects need no redundant pick/place.
            if current[1][target]==destination and current[2][target]==-1:
                expand(order,index+1,current,actions,methods+[dict(target=target,carrier=None,already_complete=True)]);return
            roots=[target];parent=current[2][target]
            while parent!=-1:roots.append(parent);parent=current[2][parent]
            completed=set(order[:index])
            for root in roots:
                if descendants(current[2],root)&completed:continue
                local=current;extra=[]
                def emit(action):
                    nonlocal local
                    local,_=choices(local,model)[action];extra.append(action)
                if local[0]!=local[1][root]:emit('move_'+str(local[1][root]))
                emit('pick_'+str(root))
                if local[0]!=destination:emit('move_'+str(destination))
                emit('put_surface')
                if root!=target:emit('pick_'+str(target));emit('put_surface')
                expand(order,index+1,local,actions+extra,methods+[dict(target=target,carrier=root)])
        for order in permutations(range(n)):expand(order,0,state,list(prefix),[])
    if not rows:raise RuntimeError('No complete method candidate')
    assert len(rows)<=62
    return rows


def plan(model):
    rows=candidates(model);ordered=sorted(enumerate(rows),key=lambda p:(p[1]['cost'],len(p[1]['actions']),p[0]));unique=[];seen=set()
    for index,row in ordered:
        key=tuple(row['actions'])
        if key in seen:continue
        seen.add(key);unique.append(dict(candidate_index=index,**row))
    return dict(status='ok',method='source support forest and delivery-order enumeration',candidate_count=len(rows),unique_plan_count=len(unique),best_cost=unique[0]['cost'],actions=unique[0]['actions'],alternatives=unique[:3],optimality_proven=False,assumptions='All objects start at location 0; empty hand; lower-index objects may support higher-index objects; retention and unlimited staging are guaranteed; all final objects must rest separately on surfaces. Movement uses supplied metric; other actions cost 1.',scope='A bounded abstract method, no physics validation, private scene data or exact oracle')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path);a=parser.parse_args()
    try:result=plan(json.loads(a.input.read_text()))
    except (ValueError,TypeError,KeyError) as exc:result=dict(status='unsupported_input',error=str(exc))
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.output:a.output.write_text(text)
    else:print(text,end='')
    return 0 if result['status']=='ok' else 2


if __name__=='__main__':raise SystemExit(main())
