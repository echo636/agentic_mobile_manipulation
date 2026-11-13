"""Benchmark-side timed appliance processes driven by robot_clock.

Appliances such as microwaves, ovens, dishwashers, washers, dryers and stoves
advance only ~1/30 s per ordinary action in native PhysX, which prevents real
parallel scheduling ("start an appliance, do other work elsewhere while it runs").
When a custom task declares ``timed_processes``, each declared process advances by
the elapsed ``robot_clock`` seconds (travel + manipulation + wait) whenever its
appliance conditions hold:

  - item is contained in/on the appliance (``Inside`` or ``OnTop``) and not held;
  - appliance door is closed (when ``require_closed`` is true);
  - appliance is switched on (when ``require_toggled_on`` is true).

Public feedback crosses the model boundary only as factual sensory text
(``running_message``, ``completed_message``, ``stopped_early_message``) with no
entity IDs, coordinates or remaining-time numbers. No simulator module is
imported at module import time so CPU unit tests run without OmniGibson.
"""
from __future__ import annotations

import json
import math
from typing import Any

from ..records import now
from ..runtime.robot_clock import clock_of

VERSION = 'timed_processes_robot_clock_v1'
ALLOWED_CONTAINMENT = frozenset({'Inside', 'OnTop'})
ALLOWED_OBJECT_STATES = frozenset({None, 'Cooked'})


def validate_timed_processes(spec: dict[str, Any]) -> None:
    """CPU-only contract validation for optional ``timed_processes`` in custom specs."""
    processes = spec.get('timed_processes')
    if processes is None:
        return
    if not isinstance(processes, list) or not processes:
        raise ValueError('timed_processes must be a nonempty list when present')
    roles = spec.get('roles', {})
    seen_ids: set[str] = set()
    for row in processes:
        if not isinstance(row, dict):
            raise ValueError('Each timed_process entry must be an object')
        pid = row.get('id')
        if not isinstance(pid, str) or not pid.strip() or pid in seen_ids:
            raise ValueError(f'Invalid or duplicate timed_process id: {pid!r}')
        seen_ids.add(pid)
        appliance = row.get('appliance')
        if appliance not in roles:
            raise ValueError(f'timed_process {pid} references unknown appliance role: {appliance!r}')
        items = row.get('items')
        if (not isinstance(items, list) or not items or len(set(items)) != len(items)
                or any(item not in roles or item == appliance for item in items)):
            raise ValueError(f'timed_process {pid} requires distinct valid item roles')
        containment = row.get('containment', 'Inside')
        if containment not in ALLOWED_CONTAINMENT:
            raise ValueError(f'timed_process {pid} unsupported containment: {containment!r}')
        for flag in ('require_closed', 'require_toggled_on'):
            if flag in row and type(row[flag]) is not bool:
                raise ValueError(f'timed_process {pid} {flag} must be boolean')
        duration = row.get('duration_seconds')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError(f'timed_process {pid} duration_seconds must be a positive finite number')
        obj_state = row.get('set_object_state')
        if obj_state not in ALLOWED_OBJECT_STATES:
            raise ValueError(f'timed_process {pid} unsupported set_object_state: {obj_state!r}')
        for msg_key in ('running_message', 'completed_message', 'stopped_early_message'):
            msg = row.get(msg_key)
            if not isinstance(msg, str) or not msg.strip():
                raise ValueError(f'timed_process {pid} missing nonempty {msg_key}')


def normalize_rule(raw: dict[str, Any]) -> dict[str, Any]:
    containment = raw.get('containment', 'Inside')
    return {
        'id': raw['id'],
        'appliance': raw['appliance'],
        'items': list(raw['items']),
        'containment': containment,
        'require_closed': raw.get('require_closed', containment == 'Inside'),
        'require_toggled_on': raw.get('require_toggled_on', True),
        'duration_seconds': float(raw['duration_seconds']),
        'set_object_state': raw.get('set_object_state'),
        'running_message': raw['running_message'].strip(),
        'completed_message': raw['completed_message'].strip(),
        'stopped_early_message': raw['stopped_early_message'].strip(),
    }


class TimedProcesses:
    """Tracks per-item accumulated robot_clock seconds across declared appliances."""

    def __init__(self, rules: list[dict[str, Any]], roles: dict[str, str]):
        self.rules = [normalize_rule(r) for r in rules]
        self.by_id = {r['id']: r for r in self.rules}
        self.roles = dict(roles)
        self.elapsed: dict[str, dict[str, float]] = {
            r['id']: {item: 0.0 for item in r['items']} for r in self.rules
        }
        self.completed: dict[str, dict[str, bool]] = {
            r['id']: {item: False for item in r['items']} for r in self.rules
        }
        self.clock_seen = 0.0
        self.pending_messages: list[str] = []
        self.anomalies: list[str] = []

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> TimedProcesses | None:
        raw = spec.get('timed_processes')
        if not raw:
            return None
        validate_timed_processes(spec)
        return cls(raw, spec['roles'])

    def _anomaly(self, where: str, exc: BaseException) -> None:
        if len(self.anomalies) < 50:
            self.anomalies.append(f'{where}: {type(exc).__name__}: {exc}')

    def is_completed(self, process_id: str, item_role: str) -> bool:
        return bool(self.completed.get(process_id, {}).get(item_role, False))

    # ------------------------------------------------------------------ scene queries
    def _live_map(self, backend: Any) -> dict[str, Any]:
        mock = getattr(backend, 'mock_objects', None)
        if mock is not None:
            return mock
        env = getattr(backend, 'env', None)
        if env is None or getattr(env, 'scene', None) is None:
            return {}
        return {obj.name: obj for obj in env.scene.objects}

    def _role_obj(self, backend: Any, role: str, live: dict[str, Any] | None = None) -> Any:
        live = self._live_map(backend) if live is None else live
        name = self.roles.get(role)
        return live.get(name) if name else None

    def _is_open(self, obj: Any) -> bool:
        if hasattr(obj, 'mock_states'):
            return bool(obj.mock_states.get('Open', False))
        from omnigibson.object_states import Open
        return bool(Open in obj.states and obj.states[Open].get_value())

    def _is_toggled_on(self, obj: Any) -> bool:
        if hasattr(obj, 'mock_states'):
            return bool(obj.mock_states.get('ToggledOn', False))
        from omnigibson.object_states import ToggledOn
        return bool(ToggledOn in obj.states and obj.states[ToggledOn].get_value())

    def _is_contained(self, item_obj: Any, appliance_obj: Any, mode: str) -> bool:
        if hasattr(item_obj, 'mock_relations'):
            return appliance_obj in item_obj.mock_relations.get(mode, set())
        from omnigibson.object_states import Inside, OnTop
        state_cls = Inside if mode == 'Inside' else OnTop
        try:
            if state_cls in item_obj.states and item_obj.states[state_cls].get_value(appliance_obj):
                return True
        except Exception:
            pass
        try:
            (ilo, ihi), (alo, ahi) = item_obj.aabb, appliance_obj.aabb
            cx = float(ilo[0] + ihi[0]) * 0.5
            cy = float(ilo[1] + ihi[1]) * 0.5
            cz = float(ilo[2] + ihi[2]) * 0.5
            xy_in = (float(alo[0]) - 0.02 <= cx <= float(ahi[0]) + 0.02
                     and float(alo[1]) - 0.02 <= cy <= float(ahi[1]) + 0.02)
            if mode == 'Inside':
                return xy_in and float(alo[2]) - 0.02 <= cz <= float(ahi[2]) + 0.02
            return xy_in and float(ahi[2]) - 0.05 <= float(ilo[2]) <= float(ahi[2]) + 0.15
        except Exception:
            return False

    def is_item_active(self, backend: Any, process_id: str, item_role: str,
                       *, live: dict[str, Any] | None = None) -> bool:
        rule = self.by_id[process_id]
        live = self._live_map(backend) if live is None else live
        appliance = self._role_obj(backend, rule['appliance'], live)
        item = self._role_obj(backend, item_role, live)
        if appliance is None or item is None:
            return False
        held = backend._get_held() if hasattr(backend, '_get_held') else None
        if held is item or held is appliance:
            return False
        if rule['require_closed'] and self._is_open(appliance):
            return False
        if rule['require_toggled_on'] and not self._is_toggled_on(appliance):
            return False
        return self._is_contained(item, appliance, rule['containment'])

    # ------------------------------------------------------------------ state sync
    def _sync_cooked(self, item_obj: Any, done: bool) -> None:
        if hasattr(item_obj, 'mock_states'):
            item_obj.mock_states['Cooked'] = bool(done)
            item_obj.mock_states['Burnt'] = False
            return
        from omnigibson.object_states import Burnt, Cooked, MaxTemperature, Temperature
        if Cooked not in item_obj.states:
            return
        cook_temp = float(item_obj.states[Cooked].cook_temperature)
        burn_temp = float(item_obj.states[Burnt].burn_temperature) if Burnt in item_obj.states else cook_temp + 130.0
        target_temp = min(cook_temp + 5.0, burn_temp - 20.0) if done else (cook_temp - 5.0)
        changed = False
        if Temperature in item_obj.states:
            cur_t = float(item_obj.states[Temperature].get_value())
            if (not done and cur_t >= cook_temp - 1.0) or (done and cur_t >= burn_temp - 10.0):
                item_obj.states[Temperature].set_value(target_temp if done else min(cur_t, cook_temp - 5.0))
                changed = True
        if MaxTemperature in item_obj.states:
            cur_max = float(item_obj.states[MaxTemperature].get_value())
            want_change = (cur_max < cook_temp) if done else (cur_max >= cook_temp)
            if want_change or (done and cur_max >= burn_temp):
                item_obj.states[MaxTemperature].set_value(target_temp)
                changed = True
        if changed:
            for st in item_obj.states.values():
                st.clear_cache()

    def sync_all_states(self, backend: Any) -> None:
        """Keep simulator object states aligned with robot-clock completion."""
        try:
            live = self._live_map(backend)
            if not live:
                return
            for rule in self.rules:
                if rule['set_object_state'] == 'Cooked':
                    for item_role in rule['items']:
                        item_obj = self._role_obj(backend, item_role, live)
                        if item_obj is not None:
                            self._sync_cooked(item_obj, self.completed[rule['id']][item_role])
        except Exception as exc:
            self._anomaly('sync_states', exc)

    # ------------------------------------------------------------------ action hooks
    def before_action(self, backend: Any) -> dict[str, Any]:
        clock = clock_of(backend)
        clock_before = float(clock.total_s) if clock is not None else self.clock_seen
        travel_before = float(clock.travel_s) if clock is not None else 0.0
        live = self._live_map(backend)
        active_before: dict[tuple[str, str], bool] = {}
        done_before: dict[tuple[str, str], bool] = {}
        for rule in self.rules:
            pid = rule['id']
            for item in rule['items']:
                try:
                    active_before[(pid, item)] = self.is_item_active(backend, pid, item, live=live)
                except Exception as exc:
                    self._anomaly('before_action', exc)
                    active_before[(pid, item)] = False
                done_before[(pid, item)] = self.completed[pid][item]
        return {
            'clock_before': clock_before,
            'travel_before': travel_before,
            'active_before': active_before,
            'done_before': done_before,
        }

    def _selected_matches_rule(self, backend: Any, rule: dict[str, Any], selected_prim: str,
                               live: dict[str, Any]) -> bool:
        if not selected_prim:
            return False
        for role in (rule['appliance'], *rule['items']):
            obj = self._role_obj(backend, role, live)
            prim_path = str(getattr(obj, 'prim_path', '') or '')
            if prim_path and (selected_prim == prim_path or selected_prim.startswith(prim_path + '/')):
                return True
        return False

    def after_action(self, backend: Any, snap: dict[str, Any], primitive: str,
                     *, details: dict[str, Any] | None = None,
                     error: BaseException | None = None) -> dict[str, Any] | None:
        clock = clock_of(backend)
        now_s = float(clock.total_s) if clock is not None else snap['clock_before']
        delta_total = max(0.0, now_s - snap['clock_before'])
        travel_now = float(clock.travel_s) if clock is not None else snap.get('travel_before', 0.0)
        travel_delta = min(delta_total, max(0.0, travel_now - snap.get('travel_before', 0.0)))
        self.clock_seen = max(self.clock_seen, now_s)

        live = self._live_map(backend)
        active_now: dict[tuple[str, str], bool] = {}
        just_completed_rules: set[str] = set()

        for rule in self.rules:
            pid = rule['id']
            for item in rule['items']:
                try:
                    now_act = self.is_item_active(backend, pid, item, live=live)
                except Exception as exc:
                    self._anomaly('after_action_active', exc)
                    now_act = False
                active_now[(pid, item)] = now_act
                was_act = snap['active_before'].get((pid, item), False)
                dt = (delta_total if (was_act and now_act)
                      else (travel_delta if (was_act and not now_act) else 0.0))
                if dt > 0.0:
                    self.elapsed[pid][item] += dt
                if not self.completed[pid][item] and self.elapsed[pid][item] >= rule['duration_seconds']:
                    self.completed[pid][item] = True
                    just_completed_rules.add(pid)

        self.sync_all_states(backend)

        for rule in self.rules:
            if rule['id'] in just_completed_rules and rule['completed_message'] not in self.pending_messages:
                self.pending_messages.append(rule['completed_message'])

        selected_prim = str(((details or {}).get('private_grounding') or {}).get('selected_object_prim') or '')
        transient_messages: list[str] = []
        any_unfinished_running = False

        for rule in self.rules:
            pid = rule['id']
            unfinished_running_now = any(
                active_now[(pid, item)] and not self.completed[pid][item] for item in rule['items']
            )
            if unfinished_running_now:
                any_unfinished_running = True
            if error is not None or pid in just_completed_rules:
                continue
            stopped_unfinished = any(
                snap['active_before'].get((pid, item), False)
                and not snap['done_before'].get((pid, item), False)
                and not self.completed[pid][item]
                and not active_now[(pid, item)]
                for item in rule['items']
            )
            if stopped_unfinished:
                transient_messages.append(rule['stopped_early_message'])
            elif unfinished_running_now and (
                primitive in {'wait', 'toggle_on', 'look'}
                or self._selected_matches_rule(backend, rule, selected_prim, live)
            ):
                transient_messages.append(rule['running_message'])

        self._write_log(backend, primitive, error, delta_total, active_now)

        if error is not None:
            return None
        delivered = [*self.pending_messages, *transient_messages]
        self.pending_messages.clear()
        if not delivered:
            return None
        return {
            'messages': delivered,
            'message': ' '.join(delivered),
            'any_process_running': any_unfinished_running,
        }

    def _write_log(self, backend: Any, primitive: str, error: BaseException | None,
                   delta_s: float, active_now: dict[tuple[str, str], bool]) -> None:
        output = getattr(backend, 'output', None)
        if output is None:
            return
        try:
            row = {
                'at': now(),
                'env_step': getattr(backend, 'steps', None),
                'primitive': primitive,
                'ok': error is None,
                'code': getattr(error, 'code', None) if error is not None else None,
                'clock_seen_s': round(self.clock_seen, 3),
                'delta_s': round(delta_s, 3),
                'elapsed_s': {
                    pid: {k: round(v, 3) for k, v in items.items()}
                    for pid, items in self.elapsed.items()
                },
                'completed': {pid: dict(items) for pid, items in self.completed.items()},
                'active': {
                    pid: {item: active_now.get((pid, item), False) for item in rule['items']}
                    for rule in self.rules for pid in (rule['id'],)
                },
                'audience': 'evaluator_private',
            }
            with (output / 'timed_processes.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        except Exception as exc:
            self._anomaly('log', exc)

    def summary(self, backend: Any | None = None) -> dict[str, Any]:
        live = self._live_map(backend) if backend is not None else {}
        return {
            'version': VERSION,
            'clock_seen_s': round(self.clock_seen, 3),
            'processes': {
                rule['id']: {
                    'appliance': rule['appliance'],
                    'duration_seconds': rule['duration_seconds'],
                    'elapsed_s': {k: round(v, 3) for k, v in self.elapsed[rule['id']].items()},
                    'completed': dict(self.completed[rule['id']]),
                    'active': ({item: self.is_item_active(backend, rule['id'], item, live=live)
                                for item in rule['items']} if backend is not None and live else None),
                }
                for rule in self.rules
            },
            'anomalies': list(self.anomalies),
        }
