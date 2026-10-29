"""Run native broken-joint cleanup after PhysX releases its callback locks."""
from collections import deque
from functools import wraps


def install_attachment_break_deferral():
    import omnigibson as og
    from omnigibson.object_states import AttachedTo

    native_break = AttachedTo.on_joint_break
    if not getattr(native_break, '_mas_deferred_break', False):
        @wraps(native_break)
        def on_break(state, joint_path):
            sim = og.sim
            if not sim.currently_stepping:
                return native_break(state, joint_path)
            # Simulator is constructed lazily. Install its drain on the first
            # break, without changing the installed OmniGibson source.
            if not hasattr(sim, '_mas_attachment_breaks'):
                pending = sim._mas_attachment_breaks = deque()
                report = sim._report_step_exceptions

                @wraps(report)
                def drain():
                    report()
                    # Both step() and step_physics() report exceptions only
                    # after the native step/fetch_results call has returned.
                    while pending:
                        state, path = pending.popleft()
                        native_break(state, path)

                sim._report_step_exceptions = drain
            sim._mas_attachment_breaks.append((state, joint_path))

        on_break._mas_deferred_break = True
        AttachedTo.on_joint_break = on_break
    return {'status': 'configured', 'scope': 'native_AttachedTo_joint_break_cleanup',
            'change': 'Defer USD deletion until native physics returns',
            'joint_limits_contacts_and_detachment': 'unchanged'}
