"""Oracle geometry and motor tools on the existing serialized harness."""
import copy

from .contracts import SkillError
from .oracle_contracts import ACTION_TOOLS, PROFILE, public_observation
from .records import write_json
from .vision_harness import VisionHarness


class OracleWaypointHarness(VisionHarness):
    ACTION_TOOLS = ACTION_TOOLS
    PROFILE = PROFILE

    def __init__(self, backend, recorder, budget, *, manip_root=None, **kwargs):
        from .manip_runtime import ManipRecipes, source_root
        # New episodes freeze operator-owned recipes; existing snapshots stay put.
        self.manip_recipes = ManipRecipes(manip_root if manip_root is not None else source_root()).copy_to(
            recorder.output / 'manip_snapshot')
        super().__init__(backend, recorder, budget, profile=self.PROFILE,
                         goal_skill="none", unbounded_episode=True, **kwargs)
        recorder.run["observation_contract"] = "oracle_3d_waypoints_v2"
        recorder.run["config"].update(observation_mode="oracle_3d_waypoints",
                                     agent_profile=self.PROFILE, privileged_observation=True,
                                     oracle_transport_policy=getattr(backend, "oracle_transport_policy", "native_only"))
        from .geometry_pick_protocol import enabled
        from .model_pose_protocol import enabled as pose_enabled, ENGINE, PLANNER
        if pose_enabled():
            recorder.run['config'].update(manip_engine=ENGINE, goal_source='model_authored',
                planner=PLANNER, motion_source='curobo_from_model_eef_goal',
                grasping_mode=getattr(backend, 'oracle_grasping_mode', 'native_assisted'),
                execution_mode='open_loop_rollback', empty_navigation='ideal_kinematic',
                held_navigation='forbidden')
        if enabled():
            recorder.run['config'].update(manip_engine='geometry_pick_v1',
                grasp_candidate_source='collision_mesh_cross_sections',
                planner='StanfordVL/curobo@78612f45cef52c3fa0298de243a54cd7ca614414',
                motion_source='backend_generated', grasping_mode='native_assisted',
                empty_navigation='ideal_kinematic', held_navigation='forbidden')
        from .manip_programs import enabled as program_enabled, ManipPrograms, ENGINE as PROGRAM_ENGINE
        if program_enabled():
            import os
            from .program_library import configured_program_root
            self.manip_programs = ManipPrograms(os.environ.get('MAS_MANIP_PROGRAM_ROOT') or
                                                recorder.output / 'manip_programs',
                                                library_root=configured_program_root())
            recorder.run['config'].update(manip_engine=PROGRAM_ENGINE,
                goal_source='model_pose_or_selected_or_authored_program', motion_source='model_goal_or_selected_program_then_curobo',
                program_root=str(self.manip_programs.root),
                shared_geometry_library=(str(self.manip_programs.shared.library.repository)
                    if self.manip_programs.shared is not None else None))
        write_json(recorder.output / "run.json", recorder.run)

    def refresh(self):
        self.snapshot = public_observation(self.backend.observe(), self.revision)
        return copy.deepcopy(self.snapshot)

    def initialize(self):
        from .manip_programs import enabled as program_enabled, ENGINE as PROGRAM_ENGINE
        if program_enabled():
            from .manip_references import catalog as reference_catalog
            return {'initialized': True, 'observation': copy.deepcopy(self.snapshot),
                'control': self.backend.control_contract(), 'notes': self.task_notes.read(),
                'manip_engine': PROGRAM_ENGINE, 'programs': self.manip_programs.catalog(),
                'geometry_references': reference_catalog(),
                'manip_workflow': 'Judge simple or complex. Simple: mode=pose with a direct waypoint. For saved whole-body recovery, read/select the shared recovery program, or use arm=both with all18 joint_positions_rad, hold, contact_target=null. Complex: list/read and directly select an applicable existing program; write/adapt only when none fits. Programs may generate one phase sequence or ranked EEF candidates, with no mandatory candidate count. Query current geometry as needed. Each phase is planned, validated, executed and verified before planning the next from measured state. Both modes use open-loop execution and whole-call rollback.'}
        from .geometry_pick_protocol import enabled
        from .model_pose_protocol import enabled as pose_enabled, ENGINE
        if pose_enabled():
            return {'initialized': True, 'observation': copy.deepcopy(self.snapshot),
                'control': self.backend.control_contract(), 'notes': self.task_notes.read(),
                'manip_engine': ENGINE,
                'manip_workflow': 'Use task and current Oracle3D to choose arm and EEF goal; manip(arm,frame=robot_base,goal={position_m,quaternion_xyzw},gripper,contact_target,revision). cuRobo plans that exact goal; no automatic grasp/lift/torso goal.'}
        if enabled():
            return {'initialized': True, 'observation': copy.deepcopy(self.snapshot),
                'control': self.backend.control_contract(), 'notes': self.task_notes.read(),
                'manip_engine': 'geometry_pick_v1',
                'manip_workflow': 'navigate while empty, then manip(operation=pick,target,arm=auto,lift_m=0.10,revision); geometry and continuous trajectories are backend generated'}
        return {"initialized": True, "observation": copy.deepcopy(self.snapshot),
                "control": self.backend.control_contract(),
                "experience": self.skills.recommendations(),
                "notes": self.task_notes.read(),
                "manip_recipes": self.manip_recipes.catalog(),
                "manip_workflow": "navigate while empty, inspect current geometry/RGB, read and select a recipe, then manip; default no base motion"}

    def _dispatch_tool(self, context, name, arguments):
        from .manip_programs import enabled as program_enabled
        if program_enabled():
            if name == 'get_placement_state':
                if arguments['revision'] != self.revision:
                    raise SkillError('stale_observation', 'Query placement at the current revision')
                from .executors.native_transport import placement_state
                return dict(placement_state(self.backend, arguments['object_id'], arguments['container_id']), revision=self.revision)
            if name == 'list_manip_references':
                from .manip_references import catalog
                return {'references': catalog()}
            if name == 'read_manip_reference':
                from .manip_references import read
                return read(arguments['reference'])
            if name == 'get_manip_geometry':
                if arguments['revision'] != self.revision:
                    raise SkillError('stale_observation', 'Query meshes at the current revision')
                known = {o['id'] for o in self.snapshot['oracle_3d']['objects']}
                if any(name not in known for name in arguments['object_ids']):
                    raise SkillError('unknown_object', 'Select mesh targets from the current Oracle catalog')
                geometry = self.backend.get_manip_geometry(arguments['object_ids'])
                cache = getattr(self, '_manip_geometry_cache', {})
                if cache.get('revision') != self.revision:
                    cache = {'revision': self.revision, 'objects': {}}
                cache['objects'].update({o['id']: copy.deepcopy(o) for o in geometry['objects']})
                cache['arms'] = copy.deepcopy(geometry.get('robot', {}).get('arms', {}))
                self._manip_geometry_cache = cache
                return {**geometry, 'revision': self.revision, 'source': 'native_collision_mesh_read_no_step',
                        'capture': copy.deepcopy(self.snapshot.get('capture')),
                        'included_in_next_program_input': True}
            if name == 'list_manip_programs':
                return {'programs': self.manip_programs.catalog()}
            if name == 'read_manip_program':
                return self.manip_programs.read(arguments['program'])
            if name == 'write_manip_program':
                result = self.manip_programs.write(**arguments)
                self.recorder.event('manip_program_written', result)
                return result
            if name == 'manip':
                from .manip_programs import checked_invocation
                arguments = checked_invocation(arguments)
                if arguments['revision'] != self.revision:
                    raise SkillError('stale_observation', 'Use the latest revision before generating goals')
                if arguments['mode'] == 'pose':
                    return self.perform_motor('manip', _parent_request_id=context.request_id,
                                              **{k: v for k, v in arguments.items() if k != 'mode'})
                config = self.recorder.run['config']
                oracle = copy.deepcopy(self.snapshot['oracle_3d'])
                cache = getattr(self, '_manip_geometry_cache', {})
                if cache.get('revision') == self.revision:
                    for obj in oracle['objects']:
                        if obj['id'] in cache['objects']:
                            obj['collision_mesh'] = copy.deepcopy(cache['objects'][obj['id']]['collision_mesh'])
                    for arm, fields in cache.get('arms', {}).items():
                        oracle['robot']['arms'][arm].update(copy.deepcopy(fields))
                phases, evidence = self.manip_programs.generate(
                    arguments['program'], arguments['source_sha256'],
                    task=config.get('instruction') or config.get('task') or '',
                    oracle=oracle,
                    parameters=arguments['parameters'], output=self.recorder.output)
                self.recorder.event('manip_program_generated', evidence)
                return self.perform_motor('execute_model_phases', revision=arguments['revision'],
                    _parent_request_id=context.request_id, phases=phases, program=evidence)
        if name == 'list_manip_recipes':
            return {'recipes': self.manip_recipes.catalog()}
        if name == 'read_manip_recipe':
            return self.manip_recipes.read(arguments['recipe'])
        if name == 'manip':
            from .geometry_pick_protocol import enabled
            from .model_pose_protocol import enabled as pose_enabled
            if enabled() or pose_enabled():
                return self.perform_motor('manip', _parent_request_id=context.request_id, **arguments)
            from .manip_runtime import run_trusted
            return run_trusted(self, self.manip_recipes, arguments, context.request_id)
        if name == "initialize":
            return self.initialize()
        if name == "observe_scene":
            return {"observation": self.refresh()}
        if name == "finish":
            return self.finish(**arguments)
        if name == "list_skills":
            return {"skills": self.skills.catalog(), "bundle_sha256": self.skills.digest,
                    "recommendations": self.skills.recommendations()}
        if name == "read_skill":
            return self.skills.read(**arguments)
        if name == "task_notes":
            return super()._dispatch_tool(context, name, arguments)
        return self.perform_motor(name, _parent_request_id=context.request_id, **arguments)

    def perform_motor(self, name, revision, *, _parent_request_id=None, **arguments):
        if revision != self.revision:
            raise SkillError("stale_observation", "Use the latest returned observation revision")
        self.deadline.check()
        self.actions += 1
        self.backend._active_request_id = _parent_request_id
        details, error = None, None
        before_steps = self.backend.steps
        try:
            details = self.backend.execute_motor(name, **arguments)
        except SkillError as exc:
            error = {"code": exc.code, "message": str(exc),
                     "world_may_have_changed": exc.changed or self.backend.steps != before_steps}
        finally:
            self.backend._active_request_id = None
            self.revision += 1
            if not self.deadline.expired and not self.deadline.stop_requested:
                self.refresh()
        if details is not None:
            self.recorder.event("oracle_motor_result", {"tool": name, "execution": details})
            if not details.get("reached", True):
                error = {"code": "motor_target_not_reached",
                         "message": "The motor stopped before reaching its target; inspect actual state.",
                         "world_may_have_changed": True}
            elif "verified" in details and details["verified"] is not True:
                error = {"code": "primitive_verification_failed" if details["verified"] is False else
                                 "primitive_verification_unavailable",
                         "message": "Motion execution did not establish the requested physical result; inspect verification evidence.",
                         "world_may_have_changed": True}
        return {"ok": error is None, **({"error": error} if error else {}),
                "execution": details, "observation": self.snapshot, "actions_used": self.actions}
