"""CPU RGB contract fixture, explicitly not simulator/perception evidence."""
import binascii
import hashlib
import struct
import zlib
from pathlib import Path
from ..records import now
from .rig import DIRECTIONS


def png_fixture(on=False):
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',binascii.crc32(kind+data)&0xffffffff)
    w,h=96,64
    color=bytes((30,200,50) if on else (220,30,30))
    raw=b''.join(b'\0'+b''.join(color if 24<x<72 and 16<y<48 else b'\xff\xff\xff' for x in range(w)) for y in range(h))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

class MockRGBBackend:
    mode='rgb_only'
    def __init__(self,output):
        self.output=output;self.steps=0;self.on=False;self.capture=0;self.images={};self.fail_next=False
    def observe(self):
        self.capture+=1
        images=[]
        for view in DIRECTIONS:
            ref=f'mock-rgb-{self.capture}-{view}'
            data=png_fixture(self.on);self.images[ref]=data
            images.append({'image_ref':ref,'view':view,'width':96,'height':64,
                'mime_type':'image/png','sha256':hashlib.sha256(data).hexdigest()})
        return {'observation_mode':self.mode,'images':images,
                'capture':{'capture_id':f'mock-capture-{self.capture}', 'captured_at':now(),
                           'sim_step':self.steps,'sim_time_seconds':self.steps/30}}
    def image_bytes(self,ref):return self.images[ref],'image/png'
    def execute_visual(self,primitive,target,max_steps,**kwargs):
        from ..contracts import SkillError
        self.steps+=1
        if self.fail_next:
            self.fail_next=False
            raise SkillError('execution_error','PRIVATE_OBJECT_NAME /World/secret REAL_STATE=true',changed=True)
        if primitive=='toggle_on':self.on=True
        return {'private_object':'PRIVATE_OBJECT_NAME','private_pose':[1,2,3]}
    def evaluate(self):return {'task_success':self.on,'official_task_success':False,'protocol':'mock_rgb_contract_only','official_submission_eligible':False}
    def provenance(self):return {'name':'mock_rgb','observation_mode':self.mode,'validation_level':'cpu_rgb_contract_only'}
    def close(self):pass
