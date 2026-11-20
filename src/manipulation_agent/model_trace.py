"""Export provider-returned reasoning summaries from this run's exact session only.

No encrypted reasoning, system prompts, credentials or unrelated sessions are
exported. Summaries are provider output, not raw internal chain of thought.
"""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re


def export_summaries(controller, events, started_at, finished_at, sessions_root=None):
    thread_ids = {e.get('thread_id') for e in events if e.get('type')=='thread.started'}
    result={'status':'unavailable','source':'codex_session.reasoning.summary',
            'summary_count':0,'encrypted_items':0,'raw_internal_reasoning_exposed':False}
    if len(thread_ids)!=1:
        return {**result,'reason':'No unique controller thread ID'}
    thread_id=next(iter(thread_ids))
    if not isinstance(thread_id,str) or not re.fullmatch(r'[0-9a-fA-F-]{36}',thread_id):
        return {**result,'reason':'Invalid controller thread ID'}
    root=Path(sessions_root) if sessions_root else Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'sessions'
    begin=datetime.fromisoformat(started_at).date()-timedelta(days=1)
    end=datetime.fromisoformat(finished_at).date()+timedelta(days=1)
    if (end-begin).days>4:
        return {**result,'reason':'Unexpected controller time range'}
    candidates=[]
    while begin<=end:
        folder=root/begin.strftime('%Y/%m/%d')
        if folder.is_dir():candidates.extend(folder.glob('*'+thread_id+'.jsonl'))
        begin+=timedelta(days=1)
    if len(candidates)!=1:
        return {**result,'reason':'Exact run session file unavailable','thread_id':thread_id}
    path=candidates[0];rows=[];digest=hashlib.sha256()
    with path.open('rb') as stream:
        for number,line in enumerate(stream,1):
            digest.update(line)
            try:event=json.loads(line)
            except ValueError:continue
            payload=event.get('payload',{})
            if event.get('type')!='response_item' or payload.get('type')!='reasoning':continue
            result['encrypted_items']+=bool(payload.get('encrypted_content'))
            for index,part in enumerate(payload.get('summary') or []):
                if part.get('type')=='summary_text' and isinstance(part.get('text'),str):
                    rows.append({'text':part['text'],'at':event.get('timestamp'),
                        'source_line':number,'summary_index':index,'source':'provider_returned_reasoning_summary',
                        'verbatim':True,'translated':False})
    (controller/'model_reasoning_summaries.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    return {**result,'status':'passed' if rows else 'unavailable','summary_count':len(rows),
            'thread_id':thread_id,'session_sha256':digest.hexdigest(),
            'reason':None if rows else 'Provider returned no readable reasoning summary'}
