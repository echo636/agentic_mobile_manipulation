"""OmniGibson adapter for local manipulation; GT stays executor-private."""
import json
import logging
import math
from ..contracts import SkillError
from ..records import now
from .local_manipulation import LocalOperation
from types import SimpleNamespace
from .visual_target import LockedTarget


class LocalRGBManipulation:
    def _local_pose(self):
        from .visual_target import rotate
        pos,quat=self.robot.get_position_orientation()
        forward=rotate((1.,0.,0.),quat)
        return float(pos[0]),float(pos[1]),math.atan2(forward[1],forward[0])

    def _local_record(self, **data):
        if getattr(self, '_local_trace_unavailable', False):return
        try:
            with (self.output/'local_manipulation.jsonl').open('a') as stream:
                stream.write(json.dumps({'at':now(),'parent_request_id':getattr(self,'_active_request_id',None),
                                        'env_step':self.steps,'audience':'executor_private',**data})+'\n')
        except OSError as exc:
            # Optional tracing must not abort an action after physical motion.
            # Preserve the failure in the process log; core run records stay strict.
            self._local_trace_unavailable = True
            logging.getLogger(__name__).warning('Local manipulation trace disabled at %s: %s', self.output, exc)

    def _resolve_local_target(self,binding):
        obj,point=binding.resolve(self.env.scene.objects)
        base=self.robot.get_position_orientation()[0]
        return obj,self.torch.tensor(point,dtype=base.dtype,device=base.device)

    def _local_ready(self,primitive,obj,point,operation):
        return math.dist(self._local_pose()[:2],point[:2])<=operation.limits.reach_m

    def _local_reposition(self,primitive,obj,point,operation):
        anchor=SimpleNamespace(get_position_orientation=lambda:(point,None))
        return self._navigate(anchor,operation.step_limit-(self.steps-operation.start_steps),
                              for_manipulation=True)

    def _execute_local_manipulation(self,primitive,obj,point,grounding,max_steps,options):
        binding=LockedTarget.bind(obj,point,grounding)
        operation=LocalOperation(self,primitive,binding,max_steps,options)
        self._local_operation=operation
        try:return operation.run()
        finally:self._local_operation=None
