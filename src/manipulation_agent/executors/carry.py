"""Native grasp ownership and one-time object state actions.

No body pose is projected during subsequent physics steps.
"""
import contextlib
import json
import math
from ..contracts import SkillError
from ..records import now
from .particle_payload import snapshot_particles, relocate_particles, verify_particles, particle_count


@contextlib.contextmanager
def suppress_slicing_rules(backend):
    """Temporarily suspend passive SlicingRule/DicingRule during non-cut placement settling.

    Explicit `cut` invokes SlicingRule/DicingRule directly via CheckedMaterialActions._checked_cut.
    During placement, a knife settling next to or onto sliceable food would otherwise destroy the food in
    the post-settle env.step() and also abort MAS_PLACE_TRANSACTIONAL rollback (`scene_objects_changed`).
    Other transition rules (cooking, heating, washing, etc.) stay active.
    """
    env = getattr(backend, 'env', None)
    scene = getattr(env, 'scene', None)
    api = getattr(scene, 'transition_rule_api', None)
    active = getattr(api, 'active_rules', None)
    if not isinstance(active, set) or not active:
        yield
        return
    try:
        from omnigibson.transition_rules import DicingRule, SlicingRule
        rule_types = (SlicingRule, DicingRule)
    except Exception:
        yield
        return
    removed = [rule for rule in tuple(active) if isinstance(rule, rule_types)]
    if not removed:
        yield
        return
    for rule in removed:
        active.discard(rule)
    try:
        yield
    finally:
        live = getattr(api, 'active_rules', None)
        if isinstance(live, set):
            for rule in removed:
                live.add(rule)


def support_closure(root, objects, relation):
    """Collect only the root's transitive rigid payload, with no task lookup."""
    seen={id(root)};queue=[root];edges=[]
    for parent in queue:
        for child in objects:
            if id(child) in seen:continue
            kind=relation(child,parent)
            if kind:
                seen.add(id(child));queue.append(child);edges.append((child,parent,kind))
    return edges


def attachment_relation(candidate, member):
    """Traverse an attachment in either direction without changing its joint.

    Payload edges point away from the selected grasp, which need not be the
    native attachment root. Preserve the direction for later state checks.
    """
    from omnigibson.object_states import AttachedTo
    state=getattr(candidate,'states',{}).get(AttachedTo)
    if state is not None and state.get_value(member):
        kind='AttachedTo'
    else:
        state=getattr(member,'states',{}).get(AttachedTo)
        kind='AttachedToParent' if state is not None and state.get_value(candidate) else None
    if kind and getattr(candidate,'fixed_base',True):
        raise SkillError('fixed_object','The selected assembly is attached to a fixed object')
    return kind


class ControlledCarry:

    def _initialize_manipulation_arm(self):
        """Use the grasp posture from the first observation and navigation plan."""
        arm = self.primitives.arm
        indices = self.robot.arm_control_idx[arm]
        ready = self.robot.untucked_default_joint_pos[indices]
        if self.robot.model == 'r1pro':
            # Keep the hand upright and in front of the torso. The native
            # untucked arm extends sideways beyond the navigation footprint;
            # the reset arm instead holds containers against the base.
            ready = ready.new_tensor([math.pi / 3, 0., 0., -2 * math.pi / 3,
                                      0., -math.pi / 6, 0.])
        self.robot.set_joint_positions(ready, indices=indices)
        self.primitives._arm_targets['arm_' + arm] = ready.clone()
        self.og.sim.sync_physx_to_fabric()
        self.frames_revision = -1

    def _grasp_state(self, obj, max_steps, point):
        """Transfer once, then let OG's assisted-grasp joint own the object."""
        from omnigibson.utils import transform_utils as T
        held = self._get_held()
        if held is obj:
            return {'primitive': 'grasp', 'postcondition': 'selected_object_already_held'}
        arm = self.primitives.arm
        original = obj.get_position_orientation()
        # rc1 A8: remember the resting orientation before this grasp; place_on_top levels to it
        self._pregrasp_orientations = {**(getattr(self, '_pregrasp_orientations', None) or {}),
                                       id(obj): [float(v) for v in original[1]]}
        # rc1 A6 (carrier_registry.py): grasping a registered item on its own takes it off its carrier; grasping a
        # carrier carries its registered contents (no external-contact filter), unregistered ones keep the v12b rule.
        from . import carrier_registry as CR
        CR.unregister_on_grasp(self, obj)
        registered = CR.grasp_contents(self, obj)
        dependencies = self._grasp_carried_dependencies(obj, self._grasp_dependencies(obj), registered)
        contents = [(child, T.relative_pose_transform(*child.get_position_orientation(), *original))
                    for child, _, _ in dependencies]
        particles = snapshot_particles(self.env.scene, [obj, *[child for child, _ in contents]],
                                       include_supported=True)
        position, orientation, contact = self._grasp_pose(obj, point, dependencies)
        obj.set_position_orientation(position=position, orientation=orientation)
        self._relocate_contents(obj, contents)
        relocate_particles(self.env.scene, particles)
        self.robot._establish_grasp(obj, obj.root_link_name, arm, contact, 'FixedJoint')
        self._grasped_payload = (obj, list(dependencies), list(contents))
        self._grasped_registered_ids = {id(child) for child, _, _ in registered}
        CR.record_tilts(self, 'carrier_grasp_tilts', obj, [child for child, _ in contents])   # rc2 pose hold record
        if max_steps > 0:
            self._step(self.primitives._empty_action())
        if self._get_held() is not obj:
            self._grasped_payload = None
            raise SkillError('postcondition_error', 'Native grasp did not hold the selected object', changed=True)
        self.frames_revision = -1
        from omnigibson.utils.constants import PrimType
        cloth = obj.prim_type == PrimType.CLOTH
        return {'primitive': 'grasp',
                'implementation': 'native_cloth_attachment_and_gripper_joint' if cloth else 'native_assisted_grasp_joint',
                'postcondition': 'selected_object_in_native_grasp', 'fixed_joint_created': True,
                'payload_transfer': 'once_at_grasp', 'subsequent_motion': 'native_physics'}

    def _grasp_carried_dependencies(self, obj, dependencies, registered=()):
        """Carry an OnTop rider only when the grasped assembly alone supports it.
        rc1 A6: `registered` contents (carrier_registry) are always carried and count as assembly members; the
        external-contact filter applies only to the unregistered ones."""
        registered = list(registered)
        reg_ids = {id(child) for child, _, _ in registered}
        dependencies = [d for d in dependencies if id(d[0]) not in reg_ids]
        if (not dependencies and not registered) or not hasattr(self, '_placement_contacts') or not hasattr(self, 'robot'):
            return list(dependencies) + registered
        kept, left = list(dependencies), []
        rejected = set(reg_ids)
        while True:
            kept += self._contact_riders(obj, kept + registered, rejected)
            kept = [d for d in kept if id(d[0]) not in reg_ids]
            members = [obj, *[child for child, _, _ in kept], *[child for child, _, _ in registered]]
            allowed = {link.prim_path for body in (*members, self.robot) for link in body.links.values()}
            ids = {id(body) for body in members}
            keep, drop = [], []
            for child, parent, kind in kept:
                external = ['support_left_in_place'] if id(parent) not in ids else (
                    [body for body in self._placement_contacts(child) if body not in allowed]
                    if kind in {'OnTop', 'RestingOn'} else [])
                (drop if external else keep).append((child, parent, kind, external))
            kept = [(child, parent, kind) for child, parent, kind, _ in keep]
            if not drop:
                break
            left += drop
            rejected |= {id(child) for child, _, _, _ in drop}
        if left and hasattr(self, 'output'):
            self._carry_record(event='grasp_payload_left_in_place', grasped=obj.name,
                               carried=[[child.name, parent.name, kind] for child, parent, kind in kept],
                               left=[{'object': child.name, 'parent': parent.name, 'relation': kind,
                                      'external_contacts': sorted(external)[:12]}
                                     for child, parent, kind, external in left])
        return kept + registered

    def _rider_candidates(self, members):
        """Loose rigid bodies whose AABB meets the assembly's AABB (+2 cm)."""
        env = getattr(self, 'env', None)
        scene = getattr(env, 'scene', None)
        if scene is None:
            return []
        from omnigibson.utils.constants import PrimType
        if not all(hasattr(body, 'aabb') for body in members):
            return []
        ids = {id(body) for body in members}
        boxes = [body.aabb for body in members]
        lo = self.torch.stack([b[0] for b in boxes]).amin(0) - .02
        hi = self.torch.stack([b[1] for b in boxes]).amax(0) + .02
        found = []
        for child in scene.objects:
            if id(child) in ids or child is self.robot or getattr(child, 'fixed_base', True):
                continue
            if getattr(child, 'prim_type', None) == PrimType.CLOTH or not hasattr(child, 'aabb'):
                continue
            clo, chi = child.aabb
            if bool(((chi >= lo) & (clo <= hi)).all()):
                found.append(child)
        return found

    def _contact_riders(self, obj, kept, rejected=()):
        """Carry a loose body supported only by the grasped assembly.

        Native OnTop misses a body that leans across two payload items (for
        example, the second of two logs piled on a tray). Contacts decide:
        every contact must be a payload member or the robot, and at least one
        a payload member. No task binding or object category is read.
        """
        if not hasattr(self, '_placement_contacts') or not hasattr(self, 'robot'):
            return []
        added = []
        while True:
            members = [obj, *[child for child, _, _ in kept], *[child for child, _, _ in added]]
            member_paths = {link.prim_path: body for body in members for link in body.links.values()}
            robot_paths = {link.prim_path for link in self.robot.links.values()}
            new = []
            for child in self._rider_candidates(members):
                if id(child) in rejected:
                    continue
                contacts = list(self._placement_contacts(child))
                supports = [member_paths[path] for path in contacts if path in member_paths]
                if supports and all(path in member_paths or path in robot_paths for path in contacts):
                    new.append((child, supports[0], 'RestingOn'))
            if not new:
                return added
            added += new

    def _grasp_pose(self, obj, point, dependencies):
        """Rank fingertip poses; approximate boxes cannot reject a grasp."""
        from omnigibson.utils import transform_utils as T
        from .placement_overlap import assembly_overlap_ranker
        original = obj.get_position_orientation()
        arm = self.primitives.arm
        direction = T.quat2mat(self.robot.get_eef_orientation(arm))[:, 2]
        contact = (self.robot.get_eef_position(arm) + direction *
                   max(self.robot.eef_to_fingertip_lengths[arm].values()))
        # Grasp the selected object on its support plane facing the fingers.
        # An arbitrary visible point can leave the rest of the object behind
        # the fingertip plane, intersecting the fingers and palm.
        vertices = self.torch.as_tensor(self._collision_vertices(obj),
                                       dtype=point.dtype, device=point.device)
        hand_names = {self.robot.eef_link_names[arm], *self.robot.finger_link_names[arm],
                      *self.robot.gripper_link_names[arm]}
        overlap = assembly_overlap_ranker(self, obj, [obj, *[child for child, _, _ in dependencies]],
            ignored_robot_links=[self.robot.links[name] for name in hand_names])
        # Preserve the original yaw when it fits. A long tool can otherwise
        # reach through a wall when transferred to the fingertips. Compare
        # upright yaw alternatives without moving any body during the search.
        proposals = []
        for yaw in (0., math.pi / 2, -math.pi / 2, math.pi):
            turn = T.euler2quat(original[1].new_tensor([0., 0., yaw]))
            rotation = T.quat2mat(turn)
            rotated = (vertices - original[0]) @ rotation.T + original[0]
            selected = rotation @ (point - original[0]) + original[0]
            orientation = T.quat_multiply(turn, original[1])
            depths = rotated @ direction
            surface = rotated[depths <= depths.min() + 1e-5]
            selected_contact = surface[self.torch.linalg.vector_norm(surface - selected, dim=1).argmin()]
            # Yaw alone cannot lift a tall object's bottom out of a countertop.
            # Compare contact heights on the same finger-facing plane, without
            # moving the object or stepping physics to try each proposal.
            for anchor in (selected_contact, rotated[rotated[:, 2].argmin()],
                           rotated[rotated[:, 2].argmax()]):
                grasp_point = anchor + direction * (depths.min() - self.torch.dot(anchor, direction))
                position = original[0] + contact - grasp_point
                proposals.append((position, orientation, contact))
        # The enclosure may overlap where the actual collider does not. Use
        # it only to prefer a pose; native grasp and physics own the result.
        return min(proposals, key=lambda pose: overlap(*pose[:2]) if overlap is not None else 0)

    def _place_state(self, primitive, target, max_steps, point, yaw_degrees=None, *, roll_degrees=None):
        """Snapshot transported material once; never pin it during settling."""
        held=self._get_held()
        contents=self._carry_contents
        from . import placement_transaction as TX
        # rc2 MAS_PLACE_TRANSACTIONAL (default on): one snapshot before the placement; a failure after the release
        # restores it, so the item is back in the hand and the receipt is the existing not_placed.
        transaction=TX.snapshot(self) if TX.enabled() and held is not None else None
        try:
            return self._place_state_once(primitive, target, max_steps, point, yaw_degrees,
                                          roll_degrees=roll_degrees, held=held, contents=contents)
        except SkillError as exc:
            released = held is not None and self._get_held() is not held
            if transaction is None or not TX.should_roll_back(exc, released=released):
                raise
            ok, reason = TX.restore(self, transaction)
            self._placement_record({'status': 'transaction_rolled_back' if ok else 'transaction_not_rolled_back',
                'primitive': primitive, 'held': getattr(held, 'name', None), 'target': getattr(target, 'name', None),
                'original_code': exc.code, 'original_stage': exc.stage, 'reason': reason})
            if not ok:
                raise
            # Native transitions and evaluation see the restored world once, as after any placement.
            # When rolling back a local_work_budget exhaustion, the step/sampling counters are already at limit;
            # pause the local-operation budget check for this single commit step so rollback finishes cleanly.
            saved_local = getattr(self, '_local_operation', None)
            if saved_local is not None and exc.code in TX.BUDGET_ROLLBACK_CODES:
                self._local_operation = None
                try:
                    self._step(self.primitives._empty_action())
                finally:
                    self._local_operation = saved_local
            else:
                self._step(self.primitives._empty_action())
            raise SkillError('pre_condition_error', 'Placement did not hold; the scene was restored with the item '
                             'still in the hand', changed=False, stage='released_rolled_back') from exc

    def _place_state_once(self, primitive, target, max_steps, point, yaw_degrees=None, *, roll_degrees=None,
                          held=None, contents=()):
        from omnigibson.utils.constants import PrimType
        self._placement_particle_payload=(held,snapshot_particles(
            self.env.scene,[held,*[child for child,_ in contents]],include_supported=True))
        try:
            with suppress_slicing_rules(self):
                if held.prim_type == PrimType.CLOTH and primitive in {'place_on_top', 'place_next_to'}:
                    result = self._place_cloth_surface(primitive, held, target, max_steps, point,
                                                       yaw_degrees, roll_degrees=roll_degrees)
                elif primitive=='place_inside':
                    result = self._checked_place_inside(target,max_steps,point=point)
                elif primitive=='place_under':
                    result = self._checked_place_under(target,max_steps)
                elif primitive=='place_on_top':
                    result = self._checked_place_on_top(target,max_steps,point,yaw_degrees,
                                                        roll_degrees=roll_degrees)
                else:
                    result = self._checked_place_next_to(target,max_steps,point,yaw_degrees)
            from . import carrier_registry as CR
            # rc2 (MAS_CARRIER_POSE_HOLD, default off since 2026-11-22): carried contents keep their pose relative to the carrier when
            # the carrier is put down; tilts before/after go to the private carry_diagnostics record.
            CR.hold_pose_after_place(self, held, list(contents))
            if primitive in {'place_on_top','place_inside'}:
                # rc1 A6: a passed placement onto a movable object registers held -> target (carrier_registry.py).
                CR.register_after_place(self, held, target, primitive)
            return result
        finally:
            self._placement_particle_payload=(None,[])

    def _state_action(self, primitive, obj, max_steps):
        from omnigibson.object_states import Open,ToggledOn
        state=Open if primitive in {'open','close'} else ToggledOn
        wanted=primitive in {'open','toggle_on'}
        if state not in obj.states:
            raise SkillError('pre_condition_error','Selected object does not support this operation',
                             stage='unsupported_object_action')
        # Open.set_value teleports articulated joints. Drawer contents must move
        # with their supporting volume, not be left at the previous world pose.
        payload=self._container_payload(obj) if state is Open else []
        particles=snapshot_particles(self.env.scene,[obj,*[child for child,_,_ in payload]]) if state is Open else []
        from .particle_payload import particle_link_motion, particle_containment_observation
        moved_particles=particle_link_motion(particles)
        # rc1 articulation_hold (benchmark side): an explicit open releases a door held closed by an earlier close
        # (before the open goal is computed); contact physics can no longer push a held door open.
        from .articulation_hold import unlock
        hold_record = {}
        if primitive=='open':
            hold_record['released'] = unlock(self, obj)
        open_goal=None
        opening_feedback={}
        if primitive=='open':
            from .articulation_goal import native_open_goal, opened_joints
            open_goal,controlled_joints=native_open_goal(self,obj,feedback=opening_feedback)
        samples=[]
        def capture(phase):
            if state is Open:
                from .articulation_diagnostics import articulation_sample
                samples.append(articulation_sample(self,obj,phase))
                # A native CUDA/process fault cannot reach report(). Persist
                # each boundary while its joint/contact evidence is available.
                self._placement_record(dict(status='articulation_phase',
                    request_id=getattr(self,'_active_request_id',None),
                    target=obj.name,primitive=primitive,**samples[-1]))
        def report(status,**details):
            if state is Open:
                from .articulation_diagnostics import record_articulation
                record_articulation(self,obj,primitive,samples,status=status,**details)
        capture('before_setter')
        operated_open_joints = ()
        if state is Open:
            if primitive == 'open':
                operated_open_joints = {joint for joint, _ in controlled_joints}
            else:
                info = getattr(obj.states[Open], 'relevant_joints_info', None)
                operated_open_joints = set(info[1]) if info and info[1] else ()
        self._active_open_joints = operated_open_joints
        # rc1 articulation_hold (benchmark side): an explicit open releases a door held closed by an earlier close;
        # contact physics can no longer push a closed door open or finger contact re-toggle a switch.
        from .articulation_hold import lock_closed, install_toggle_guard
        install_toggle_guard()
        try:
            # Apply the native annotated opening to the selected panel; close fully.
            if open_goal is not None:
                obj.set_joint_positions(open_goal)
                obj.states[state].clear_cache();obj.state_updated()
                accepted=True
            else:
                accepted=obj.states[state].set_value(wanted,**({'fully':True} if state is Open else {}))
            if not accepted:raise SkillError('execution_error','Object state setter rejected the selected operation',changed=True)
            # Position setters retain joint velocities. A closed door must not
            # inherit the preceding swing; subsequent motion remains native physics.
            if state is Open:obj.keep_still()
            if primitive == 'close':
                hold_record['held'] = lock_closed(self, obj)
            self._relocate_container_payload(payload)
            transported_particles=moved_particles()
            # A door setter moves only its articulated support frames. Leave
            # material in stationary shelves/bottles to ordinary physics rather
            # than resetting its velocities or demanding zero natural settling.
            relocate_particles(self.env.scene,transported_particles)
            verify_particles(self.env.scene,transported_particles)
            capture('after_setter')
            self._step(self.primitives._empty_action())
            capture('after_step')
            if primitive == 'close' and any(h.get('step_hook') for h in hold_record.get('held') or []):
                # rc1 A1: with the hold active, "did not remain stable" means the hold failed. The pre-step hook
                # runs before the next physics step, so apply it now and read the state it maintains; the angle
                # contact pushed the door to within the step stays in private diagnostics.
                from .articulation_hold import clamp_now
                hold_record['after_step_clamp'] = clamp_now(obj)
                obj.states[state].clear_cache();obj.state_updated()
                capture('after_hold_clamp')
            actual=opened_joints(obj,controlled_joints) if primitive=='open' else bool(obj.states[state].get_value())
        finally:
            self._active_open_joints = ()
        if actual!=wanted:
            report('failed',reason='state_reopened_or_reclosed_after_step',
                   payload=[child.name for child,_,_ in payload],**({'articulation_hold':hold_record} if hold_record else {}))
            raise SkillError('postcondition_error','Requested object operation did not remain stable',changed=True)
        self._verify_container_payload(obj,payload,stage='articulation_contents_unconfirmed')
        if particles:
            self._placement_record({'status':'articulation_particle_settle',
                'target':obj.name,'primitive':primitive,
                'transported_particles':particle_count(transported_particles),
                'particles':particle_containment_observation(self.env.scene,particles,outer=obj)})
        # Still require containment for material this setter actually
        # transported. Unmoved background material is not an open/close
        # postcondition and is never restored to conceal its real settling.
        verify_particles(self.env.scene,transported_particles)
        report('passed',payload=[child.name for child,_,_ in payload],**({'articulation_hold':hold_record} if hold_record else {}))
        return {'primitive':primitive,'implementation':'official_state_setter_with_link_payload',
                'fully_open_or_closed':primitive=='close','opening_policy':'native_joint_metadata' if primitive=='open' else None,
                **opening_feedback,
                'postcondition':'official_state_and_containment_after_step',
                'preserved_contained_objects':len(payload),'failure_policy':'retain_physical_result'}

    def _checked_attach(self, parent, max_steps):
        """Attach the carried child to the visually selected compatible parent."""
        from omnigibson.object_states import AttachedTo
        child=self._get_held()
        if child is None:
            raise SkillError('empty_hand','No carried object')
        if child is parent or AttachedTo not in child.states or AttachedTo not in parent.states:
            raise SkillError('unsupported_relation','Selected objects do not support attachment')
        state=child.states[AttachedTo]
        if state.parent is not None and state.parent is not parent:
            raise SkillError('pre_condition_error','Carried object is attached to another parent')
        if state.parent is not parent:
            child_link, parent_link = state._find_attachment_links(parent, bypass_alignment_checking=True)
            if child_link is None or parent_link is None:
                raise SkillError('unsupported_relation','Selected parent has no available compatible attachment link')
        contents=list(self._carry_contents)
        dependencies=list(self._carry_dependencies)
        try:
            # The new fixed joint must move the connected bodies together.
            # Repeatedly projecting only the parent's pose fights the native
            # constraint solver and can inject energy into the whole assembly.
            # Native joint constraints and contacts determine the outcome.
            self._carry_detach()
            # The native stability probe steps physics and may detach/rewind
            # before queued joint-break callbacks run. Set the joint once;
            # ordinary environment steps below process breaks and verify it.
            accepted=state.set_value(parent,True,bypass_alignment_checking=True,
                                     check_physics_stability=False)
            if not accepted:
                raise SkillError('execution_error','Attachment state setter rejected the selected pair',changed=True)
            self._relocate_contents(child,contents)
            for _ in range(min(30,max_steps)):
                self._step(self.primitives._empty_action())
            if not state.get_value(parent):
                raise SkillError('postcondition_error','Attachment did not remain after settling',changed=True)
            self._verify_payload(dependencies)
        finally:
            self.frames_revision=-1
        return {'primitive':'attach','implementation':'official_AttachedTo_state_setter',
                'postcondition':'AttachedTo.get_value_after_settling',
                'preserved_carried_objects':len(contents),'failure_policy':'retain_physical_result'}

    def _container_payload(self, container):
        """Snapshot rigid contents relative to their support or fillable link.

        This observes executor geometry only; no task bindings or goals are read.
        """
        from omnigibson.object_states import Inside
        from omnigibson.utils import transform_utils as T
        links=[link for link in container.links.values() if link.is_meta_link and
               link.meta_link_type in {'fillable','openfillable'}]
        if not links:return []
        payload=[]
        lo,hi=container.aabb
        for child in self.env.scene.objects:
            if child in (container,self.robot) or getattr(child,'fixed_base',True) or Inside not in getattr(child,'states',{}):continue
            a,b=child.aabb;center=(a+b)/2
            if not bool(((center>=lo)&(center<=hi)).all()) or not child.states[Inside].get_value(container):continue
            link=next((link for link in links if bool(link.check_points_in_volume(center.unsqueeze(0)).item())),None)
            if link is None:
                # 2026-11-21: native Inside holds but the centre lies outside every annotated volume (a dish on
                # an oven rack above the fillable box, C2 r3 closing the oven); track it with the nearest volume
                # instead of refusing the door motion.
                link=min(links,key=lambda l:float(((l.get_position_orientation()[0]-center)**2).sum()))
            relative=T.relative_pose_transform(*child.get_position_orientation(),*link.get_position_orientation())
            payload.append((child,link,relative))
        if payload and getattr(container,'n_joints',0):
            from .container_support import support_frames
            frames=support_frames(self,container,[child for child,_,_ in payload])
            payload=[(child,frames.get(child,link),T.relative_pose_transform(
                *child.get_position_orientation(),*frames.get(child,link).get_position_orientation()))
                for child,link,_ in payload]
        return payload

    def _relocate_container_payload(self,payload):
        from omnigibson.utils import transform_utils as T
        for child,link,relative in payload:
            child.set_position_orientation(*T.pose_transform(*link.get_position_orientation(),*relative));child.keep_still()

    def _verify_container_payload(self,container,payload,*,stage=None):
        from omnigibson.object_states import Inside
        # 2026-11-23: an item still inside by the task-declared cavity acceptance (``released_inside``, v8) is
        # not displaced; native Inside alone flagged a fish resting unmoved on the oven rack after the door closed
        # (C2 r4 reference, displacement 0.000 m).
        accept=getattr(self,'released_inside',None)
        escaped=[record for record in payload if not record[0].states[Inside].get_value(container)
                 and not (accept is not None and accept(record[0],container))]
        if escaped:
            if hasattr(self, 'output'):
                from omnigibson.utils import transform_utils as T
                details=[]
                for child,link,relative in escaped:
                    position=child.get_position_orientation()[0]
                    expected=T.pose_transform(*link.get_position_orientation(),*relative)[0]
                    lo,hi=child.aabb;center=(lo+hi)/2
                    details.append({'object':child.name,'anchor_link':link.prim_path,
                        'position':position.tolist(),'expected_link_relative_position':expected.tolist(),
                        'displacement_m':float(self.torch.linalg.vector_norm(position-expected)),
                        'aabb_center_in_anchor_volume':bool(link.check_points_in_volume(center.unsqueeze(0)).item())
                            if link.is_meta_link else None,
                        'linear_velocity':child.get_linear_velocity().tolist()})
                self._carry_record(event='container_payload_violation',container=container.name,
                                   escaped_contents=details)
            raise SkillError('postcondition_error','Operation displaced existing contents from the container',
                             changed=True,stage=stage)

    def _active_grasped_payload(self, held):
        saved = getattr(self, '_grasped_payload', None)
        if saved is None or saved[0] is not held:
            return None
        env = getattr(self, 'env', None)
        scene = getattr(env, 'scene', None)
        live_ids = {id(o) for o in scene.objects} if scene is not None else None
        dependencies, contents = saved[1], saved[2]
        if live_ids is not None:
            dependencies = [(c, p, k) for c, p, k in dependencies
                            if id(c) in live_ids and id(p) in live_ids]
            kept_ids = {id(c) for c, _, _ in dependencies}
            contents = [(c, rel) for c, rel in contents if id(c) in kept_ids]
        return dependencies, contents

    def _drop_grasped_payload_objects(self, removed):
        saved = getattr(self, '_grasped_payload', None)
        if saved is None or not removed:
            return
        removed_ids = {id(o) for o in removed}
        obj, dependencies, contents = saved
        dependencies = [(c, p, k) for c, p, k in dependencies
                        if id(c) not in removed_ids and id(p) not in removed_ids]
        kept_ids = {id(c) for c, _, _ in dependencies}
        contents = [(c, rel) for c, rel in contents if id(c) in kept_ids]
        self._grasped_payload = (obj, dependencies, contents)

    @property
    def _carry_dependencies(self):
        held = self._get_held()
        if held is None:
            return []
        saved = self._active_grasped_payload(held)
        if saved is not None:
            return list(saved[0])
        return self._grasp_dependencies(held)

    @property
    def _carry_contents(self):
        held = self._get_held()
        if held is None:
            return []
        saved = self._active_grasped_payload(held)
        if saved is not None:
            return list(saved[1])
        dependencies = self._grasp_dependencies(held)
        if not dependencies:
            return []
        from omnigibson.utils import transform_utils as T
        pose = held.get_position_orientation()
        return [(child, T.relative_pose_transform(*child.get_position_orientation(), *pose))
                for child, _, _ in dependencies]

    def _get_held(self):
        return self.primitives._get_obj_in_hand()

    def _carry_record(self, **data):
        with (self.output/'carry_diagnostics.jsonl').open('a') as f:
            f.write(json.dumps({'at':now(),'env_step':self.steps,'audience':'executor_private',**data})+'\n')


    def _relocate_contents(self, held, contents):
        from omnigibson.utils import transform_utils as T
        for obj,relative in contents:
            obj.set_position_orientation(*T.pose_transform(*held.get_position_orientation(),*relative));obj.keep_still()
        root,particles=getattr(self,'_placement_particle_payload',(None,[]))
        if root is held:relocate_particles(self.env.scene,particles)


    def _carry_detach(self):
        self._grasped_payload = None
        self._grasped_registered_ids = None
        for arm in self.robot.arm_names:
            self.robot.release_grasp_immediately(arm=arm)

    def _check_placement_objects(self):
        watched=getattr(self,'_placement_watch',())
        if watched:
            live={id(obj) for obj in self.env.scene.objects}
            if any(id(obj) not in live for obj in watched):
                self.frames_revision=-1
                raise SkillError('target_changed','Placement object changed during simulation',changed=True)

    def _grasp_dependencies(self,obj):
        """Read-only closure shared by grasp and loaded-departure prediction."""
        from omnigibson.object_states import Inside,OnTop,Touching
        from omnigibson.utils.constants import PrimType
        candidates=[child for child in self.env.scene.objects if child not in (obj,self.robot)
                    and hasattr(child,'states')]
        def capacity(container):
            return sum(float(link.volume) for link in container.links.values()
                       if link.is_meta_link and link.meta_link_type in {'fillable','openfillable'})
        def relation(child,parent):
            # Native OnTop/Inside do not define cloth as a support/container.
            # Objects resting on fabric remain subject to ordinary contacts.
            if parent.prim_type == PrimType.CLOTH:return None
            attached=attachment_relation(child,parent)
            if attached:return attached
            if getattr(child,'fixed_base',True):return None
            lo,hi=parent.aabb;clo,chi=child.aabb;center=(clo+chi)/2
            if Inside in child.states and bool(((center>=lo-.02)&(center<=hi+.02)).all()) and child.states[Inside].get_value(parent):
                # Native Inside tests only the AABB center: nested containers
                # can contain each other's centers. Keep the smaller cavity as
                # payload, never carry its outer vessel through that ambiguity.
                reciprocal=(child.prim_type != PrimType.CLOTH and Inside in parent.states
                            and parent.states[Inside].get_value(child))
                if not reciprocal or capacity(child)<capacity(parent):return 'Inside'
            overlap=bool(((chi[:2]>=lo[:2])&(clo[:2]<=hi[:2])).all())
            if overlap and float(clo[2])>=float(lo[2])-.02 and float(clo[2])<=float(hi[2])+.08:
                if OnTop in child.states and Touching in child.states and child.states[OnTop].get_value(parent) and child.states[Touching].get_value(parent):return 'OnTop'
            return None
        return support_closure(obj,candidates,relation)


    def _verify_payload(self, dependencies):
        """Attachment assembly check; ordinary placement does not require this."""
        from omnigibson.object_states import Inside,OnTop,AttachedTo
        verify_particles(self.env.scene,getattr(self,'_placement_particle_payload',(None,[]))[1])
        for child,parent,kind in dependencies:
            if kind=='RestingOn':
                # A contact rider has no native relation to re-check; it must
                # still touch the assembly it was carried with.
                from omnigibson.object_states import Touching
                if Touching in child.states and child.states[Touching].get_value(parent):
                    continue
                kind='OnTop'
            if kind=='AttachedToParent':
                child,parent=parent,child
                kind='AttachedTo'
            state={'Inside':Inside,'OnTop':OnTop,'AttachedTo':AttachedTo}[kind]
            if state not in child.states or not child.states[state].get_value(parent):
                # A rigid item can settle below a shallow tray's annotated
                # volume while remaining physically supported by that tray.
                # Preserve the carrier, rather than its initial predicate.
                if (kind=='Inside' and OnTop in child.states
                        and child.states[OnTop].get_value(parent)):
                    self._placement_record({'status':'payload_support_preserved',
                        'child':child.name,'parent':parent.name,
                        'original_relation':kind,'current_relation':'OnTop'})
                    continue
                self._placement_record({'status':'payload_relation_failed',
                    'child':child.name,'parent':parent.name,'relation':kind,
                    'child_position':child.get_position_orientation()[0].tolist(),
                    'parent_position':parent.get_position_orientation()[0].tolist(),
                    'child_orientation':child.get_position_orientation()[1].tolist(),
                    'parent_orientation':parent.get_position_orientation()[1].tolist(),
                    'child_linear_velocity':child.get_linear_velocity().tolist()})
                raise SkillError('postcondition_error','Attachment payload lost its original support, containment or attachment',changed=True)
