"""Programmatic time- & carrier-aware schedule planner + public receipt ledger.

Strictly Harness-side (CPU-only, zero simulator imports, zero private scene or
evaluator access). Computes minimum-robot-time schedules from caller-declared
beliefs (places, travel distances/times, unattended timed processes, item
transfers, carriers, and local area work) and maintains a purely public ledger
of elapsed robot_clock seconds, public process messages, and model-declared
verified states.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import math
from typing import Any

from ..contracts import SkillError

DEFAULT_SPEED_M_S = 0.5
DEFAULT_TURN_S = 3.0
DEFAULT_ACT_S = 10.0


def _clean_label(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise SkillError('invalid_schedule_input', f'{field} must be a non-empty string (<= 80 chars)')
    return value.strip()


def _all_pairs_seconds(
    places: list[str],
    edges: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], float], list[str]]:
    idx = {p: i for i, p in enumerate(places)}
    n = len(places)
    dist = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
    direct = [[i == j for j in range(n)] for i in range(n)]
    for k, edge in enumerate(edges):
        u = _clean_label(edge.get('from_place'), f'travel_edges[{k}].from_place')
        v = _clean_label(edge.get('to_place'), f'travel_edges[{k}].to_place')
        if u not in idx or v not in idx:
            raise SkillError(
                'invalid_schedule_input',
                f"travel_edges[{k}] references undeclared place ({u!r} -> {v!r}); declare all places first.",
            )
        if u == v:
            continue
        sec = edge.get('seconds')
        meters = edge.get('meters')
        if sec is None and meters is None:
            raise SkillError('invalid_schedule_input', f'travel_edges[{k}] requires either meters or seconds.')
        val_s = float(sec) if sec is not None else float(meters) / DEFAULT_SPEED_M_S + DEFAULT_TURN_S
        if not math.isfinite(val_s) or val_s < 0:
            raise SkillError('invalid_schedule_input', f'travel_edges[{k}] distance/time must be >= 0.')
        i, j = idx[u], idx[v]
        if val_s < dist[i][j]:
            dist[i][j] = dist[j][i] = val_s
            direct[i][j] = direct[j][i] = True

    for m in range(n):
        for i in range(n):
            for j in range(n):
                if dist[i][m] + dist[m][j] < dist[i][j] - 1e-9:
                    dist[i][j] = dist[i][m] + dist[m][j]

    assumptions: list[str] = []
    out: dict[tuple[str, str], float] = {}
    for i, u in enumerate(places):
        for j, v in enumerate(places):
            if math.isfinite(dist[i][j]):
                out[(u, v)] = round(dist[i][j], 2)
                if i < j and not direct[i][j]:
                    assumptions.append(f'travel {u} <-> {v} estimated via shortest chain: {dist[i][j]:.1f}s')
            else:
                # Conservative fallback when graph is disconnected: 25.0s cross-area leg
                out[(u, v)] = 25.0
                if i < j:
                    assumptions.append(f'no distance given between {u} and {v}; using default 25.0s')
    return out, assumptions


def optimize_schedule(
    spec: dict[str, Any],
    *,
    public_elapsed_s: float = 0.0,
    completed_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Compute a concrete, minimum-robot-time phase schedule from caller beliefs."""
    places_raw = spec.get('places') or []
    places: list[str] = []
    for i, p in enumerate(places_raw):
        label = _clean_label(p, f'places[{i}]')
        if label not in places:
            places.append(label)
    if not places:
        raise SkillError('invalid_schedule_input', 'places must list at least one area/room label.')

    robot_place = spec.get('robot_place')
    cur_place = _clean_label(robot_place, 'robot_place') if robot_place is not None else places[0]
    if cur_place not in places:
        places.insert(0, cur_place)

    travel_s, assumptions = _all_pairs_seconds(places, spec.get('travel_edges') or [])

    # Parse carriers
    carriers: list[dict[str, Any]] = []
    seen_carriers: set[str] = set()
    for i, c in enumerate(spec.get('carriers') or []):
        cid = _clean_label(c.get('id'), f'carriers[{i}].id')
        if cid in seen_carriers:
            raise SkillError('invalid_schedule_input', f'Duplicate carrier id {cid!r}')
        seen_carriers.add(cid)
        loc = _clean_label(c.get('place'), f'carriers[{i}].place')
        if loc not in places:
            raise SkillError('invalid_schedule_input', f"Carrier {cid!r} place {loc!r} is not in places.")
        cap_raw = c.get('capacity')
        cap = int(cap_raw) if cap_raw is not None else 4
        if cap < 1:
            raise SkillError('invalid_schedule_input', f"Carrier {cid!r} capacity must be >= 1.")
        final_place = c.get('final_place')
        if final_place is not None:
            final_place = _clean_label(final_place, f'carriers[{i}].final_place')
            if final_place not in places:
                raise SkillError('invalid_schedule_input', f"Carrier {cid!r} final_place {final_place!r} is not in places.")
        carriers.append({
            'id': cid,
            'place': loc,
            'capacity': cap,
            'final_place': final_place,
        })

    # Parse timed processes
    timed_jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, tj in enumerate(spec.get('timed_processes') or []):
        tid = _clean_label(tj.get('id'), f'timed_processes[{i}].id')
        if tid in seen_ids:
            raise SkillError('invalid_schedule_input', f'Duplicate job id {tid!r}')
        seen_ids.add(tid)
        place = _clean_label(tj.get('place'), f'timed_processes[{i}].place')
        if place not in places:
            raise SkillError('invalid_schedule_input', f"timed_processes[{i}] place {place!r} is not in places.")
        raw_dur = tj.get('duration_seconds')
        if raw_dur is None:
            dur = 180.0
            assumptions.append(f"timed_process {tid!r} duration_seconds was null; using conservative 180.0s overlap window.")
        else:
            dur = float(raw_dur)
        setup_s = float(tj.get('setup_seconds') if tj.get('setup_seconds') is not None else 4 * DEFAULT_ACT_S)
        finish_s = float(tj.get('finish_seconds') if tj.get('finish_seconds') is not None else 2 * DEFAULT_ACT_S)
        if dur <= 0 or setup_s < 0 or finish_s < 0:
            raise SkillError('invalid_schedule_input', f"timed_processes[{i}] durations must be valid positive numbers.")
        status = tj.get('status') or 'pending'
        started_at = tj.get('started_at_robot_s')
        after_id = tj.get('depends_on')
        if after_id is not None:
            after_id = _clean_label(after_id, f'timed_processes[{i}].depends_on')
        timed_jobs.append({
            'id': tid,
            'place': place,
            'duration_s': dur,
            'setup_s': setup_s,
            'finish_s': finish_s,
            'status': status,
            'started_at_s': float(started_at) if started_at is not None else None,
            'depends_on': after_id,
        })

    # Parse deliveries (cross-room item transfers)
    deliveries: list[dict[str, Any]] = []
    for i, d in enumerate(spec.get('deliveries') or []):
        did = _clean_label(d.get('id'), f'deliveries[{i}].id')
        if did in seen_ids:
            raise SkillError('invalid_schedule_input', f'Duplicate job id {did!r}')
        seen_ids.add(did)
        src = _clean_label(d.get('from_place'), f'deliveries[{i}].from_place')
        dst = _clean_label(d.get('to_place'), f'deliveries[{i}].to_place')
        if src not in places or dst not in places:
            raise SkillError('invalid_schedule_input', f"deliveries[{i}] places ({src!r} -> {dst!r}) must be in places.")
        cnt_raw = d.get('item_count')
        cnt = int(cnt_raw) if cnt_raw is not None else 1
        if cnt < 1:
            raise SkillError('invalid_schedule_input', f"deliveries[{i}].item_count must be >= 1.")
        cb_raw = d.get('can_use_carrier')
        can_batch = True if cb_raw is None else bool(cb_raw)
        after_id = d.get('depends_on')
        if after_id is not None:
            after_id = _clean_label(after_id, f'deliveries[{i}].depends_on')
        status = d.get('status') or 'pending'
        deliveries.append({
            'id': did,
            'from_place': src,
            'to_place': dst,
            'item_count': cnt,
            'can_use_carrier': can_batch,
            'depends_on': after_id,
            'status': status,
        })

    # Parse local work blocks
    local_jobs: list[dict[str, Any]] = []
    for i, lj in enumerate(spec.get('local_work') or []):
        lid = _clean_label(lj.get('id'), f'local_work[{i}].id')
        if lid in seen_ids:
            raise SkillError('invalid_schedule_input', f'Duplicate job id {lid!r}')
        seen_ids.add(lid)
        place = _clean_label(lj.get('place'), f'local_work[{i}].place')
        if place not in places:
            raise SkillError('invalid_schedule_input', f"local_work[{i}] place {place!r} is not in places.")
        work_s = float(lj.get('work_seconds') if lj.get('work_seconds') is not None else 2 * DEFAULT_ACT_S)
        if work_s < 0:
            raise SkillError('invalid_schedule_input', f"local_work[{i}].work_seconds must be >= 0.")
        after_id = lj.get('depends_on')
        if after_id is not None:
            after_id = _clean_label(after_id, f'local_work[{i}].depends_on')
        status = lj.get('status') or 'pending'
        local_jobs.append({
            'id': lid,
            'place': place,
            'work_s': work_s,
            'depends_on': after_id,
            'status': status,
        })

    # Validate dependencies exist and have no cycle
    all_deps: dict[str, str | None] = {}
    for row in [*timed_jobs, *deliveries, *local_jobs]:
        dep = row['depends_on']
        if dep is not None and dep not in seen_ids:
            raise SkillError('invalid_schedule_input', f"Job {row['id']!r} depends_on unknown id {dep!r}.")
        all_deps[row['id']] = dep
    for jid in all_deps:
        vis: set[str] = set()
        cur = jid
        while cur is not None:
            if cur in vis:
                raise SkillError('invalid_schedule_input', f'Dependency cycle involving {cur!r}.')
            vis.add(cur)
            cur = all_deps.get(cur)

    # Group pending deliveries by (from_place, to_place, depends_on, can_use_carrier) and compare carrier vs direct
    done_ids = {
        r['id'] for r in [*timed_jobs, *deliveries, *local_jobs] if r['status'] == 'done'
    }
    if completed_ids:
        done_ids |= set(completed_ids)
    carrier_state = {c['id']: c['place'] for c in carriers}
    # If deliveries that used a carrier are already marked done, reflect carrier movement when final_place matches
    for d in deliveries:
        if d['id'] in done_ids and d['can_use_carrier']:
            for c in carriers:
                if c['place'] == d['from_place'] and c['final_place'] == d['to_place']:
                    carrier_state[c['id']] = d['to_place']

    # Build atomic work units to schedule:
    # 1. Start timed_process (unlocks background timer!)
    # 2. Finish/advance timed_process (after timer ready)
    # 3. Delivery batches (grouped by route & dependency)
    # 4. Local work blocks
    grouped_deliv: dict[tuple[str, str, str | None, bool], list[dict[str, Any]]] = defaultdict(list)
    for d in deliveries:
        if d['status'] != 'done':
            grouped_deliv[(d['from_place'], d['to_place'], d['depends_on'], d['can_use_carrier'])].append(d)

    delivery_units: list[dict[str, Any]] = []
    naive_delivery_extra_s = 0.0
    carrier_recommendations: list[dict[str, Any]] = []

    for (src, dst, dep, can_batch), group in grouped_deliv.items():
        total_items = sum(g['item_count'] for g in group)
        member_ids = [g['id'] for g in group]
        one_way_s = travel_s[(src, dst)]
        # Direct cost starting from src and ending at dst:
        # N items => N trips src->dst + (N-1) return trips dst->src + 2*N acts (grasp + place)
        direct_work_s = total_items * (2 * DEFAULT_ACT_S) + total_items * one_way_s + max(0, total_items - 1) * one_way_s
        best_mode = 'direct_single_item'
        best_carrier = None
        best_work_s = direct_work_s
        best_batches = total_items

        if can_batch and carriers and src != dst and total_items >= 2:
            for c in carriers:
                cap = c['capacity']
                batches = math.ceil(total_items / cap)
                c_loc = carrier_state[c['id']]
                fetch_s = travel_s[(src, c_loc)] if c_loc != src else 0.0
                # If carrier's required final_place is dst, moving carrier during batch delivery saves a separate carrier trip!
                reposition_credit_s = (one_way_s + 2 * DEFAULT_ACT_S) if (c['final_place'] == dst and c_loc != dst) else 0.0
                carrier_cost_s = max(
                    batches * one_way_s + total_items * (2 * DEFAULT_ACT_S),
                    fetch_s
                    + total_items * (3 * DEFAULT_ACT_S)
                    + batches * (2 * DEFAULT_ACT_S)
                    + batches * one_way_s
                    + max(0, batches - 1) * one_way_s
                    - reposition_credit_s,
                )
                if carrier_cost_s + 1.0 < best_work_s:
                    best_work_s = carrier_cost_s
                    best_mode = 'carrier_batch'
                    best_carrier = c['id']
                    best_batches = batches

        saved_s = max(0.0, round(direct_work_s - best_work_s, 1))
        naive_delivery_extra_s += saved_s
        if best_carrier is not None:
            carrier_state[best_carrier] = dst
        carrier_recommendations.append({
            'transfers': member_ids,
            'from_place': src,
            'to_place': dst,
            'item_count': total_items,
            'recommended_mode': best_mode,
            'carrier': best_carrier,
            'batches': best_batches,
            'direct_seconds_est': round(direct_work_s, 1),
            'recommended_seconds_est': round(best_work_s, 1),
            'saved_seconds_est': saved_s,
        })
        delivery_units.append({
            'kind': 'delivery',
            'ids': member_ids,
            'start_place': src,
            'end_place': dst,
            'work_s': best_work_s,
            'depends_on': dep,
            'mode': best_mode,
            'carrier': best_carrier,
            'item_count': total_items,
            'batches': best_batches,
        })

    # Greedy-optimal event simulation prioritizing:
    # Priority 1: Start any ready unstarted timed_process immediately (maximizes overlap window!)
    # Priority 2: Advance/finish any timed_process whose timer has ALREADY completed (if another timed_process depends on it, highest priority!)
    # Priority 3: While timed_processes are running (or none left to start), pick ready local_work or delivery_unit
    #             with smallest travel_s from cur_place (clustering same-room work first!)
    # Priority 4: Only when NO ready start / local_work / delivery_unit exists, wait the exact remaining seconds for the next active timed_process!

    sim_t = float(public_elapsed_s)
    active_timers: dict[str, float] = {}  # tid -> ready_at_s
    unstarted_timed: dict[str, dict[str, Any]] = {}
    unfinished_timed: dict[str, dict[str, Any]] = {}

    for tj in timed_jobs:
        tid = tj['id']
        if tj['status'] == 'done':
            continue
        elif tj['status'] == 'running':
            start_s = tj['started_at_s'] if tj['started_at_s'] is not None else sim_t
            active_timers[tid] = start_s + tj['duration_s']
            unfinished_timed[tid] = tj
        elif tj['status'] == 'completed_uncollected':
            active_timers[tid] = sim_t
            unfinished_timed[tid] = tj
        else:
            unstarted_timed[tid] = tj
            unfinished_timed[tid] = tj

    pending_local = [copy.deepcopy(x) for x in local_jobs if x['status'] != 'done']
    pending_deliv = copy.deepcopy(delivery_units)
    phases: list[dict[str, Any]] = []
    total_wait_s = 0.0
    total_travel_s = 0.0
    warnings: list[str] = []

    def dep_satisfied(dep_id: str | None) -> bool:
        return dep_id is None or dep_id in done_ids

    def has_downstream_timed(tid: str) -> bool:
        return any(u['depends_on'] == tid for u in unstarted_timed.values())

    safety_guard = 0
    while unstarted_timed or unfinished_timed or pending_local or pending_deliv:
        safety_guard += 1
        if safety_guard > 200:
            break

        # 1. Check ready-to-collect timed processes that unlock a downstream timed process!
        ready_chain_collect = [
            tj for tid, tj in unfinished_timed.items()
            if tid in active_timers and active_timers[tid] <= sim_t + 1e-6 and has_downstream_timed(tid)
        ]
        if ready_chain_collect:
            tj = min(ready_chain_collect, key=lambda x: travel_s[(cur_place, x['place'])])
            leg_s = travel_s[(cur_place, tj['place'])]
            sim_t += leg_s + tj['finish_s']
            total_travel_s += leg_s
            cur_place = tj['place']
            done_ids.add(tj['id'])
            unfinished_timed.pop(tj['id'], None)
            active_timers.pop(tj['id'], None)
            phases.append({
                'phase': len(phases) + 1,
                'action_type': 'collect_and_advance_stage',
                'jobs': [tj['id']],
                'place': cur_place,
                'travel_seconds': round(leg_s, 1),
                'work_seconds': round(tj['finish_s'], 1),
                'wait_seconds': 0.0,
                'est_clock_after_s': round(sim_t, 1),
                'why': f"Timer for {tj['id']} is already complete; collect immediately to unlock downstream timed stage.",
            })
            continue

        # 2. Start any ready unstarted timed_process (longest duration / chain-root first!)
        ready_starts = [tj for tj in unstarted_timed.values() if dep_satisfied(tj['depends_on'])]
        if ready_starts:
            tj = max(
                ready_starts,
                key=lambda x: (has_downstream_timed(x['id']), x['duration_s'] - travel_s[(cur_place, x['place'])]),
            )
            leg_s = travel_s[(cur_place, tj['place'])]
            sim_t += leg_s + tj['setup_s']
            total_travel_s += leg_s
            cur_place = tj['place']
            unstarted_timed.pop(tj['id'])
            active_timers[tj['id']] = sim_t + tj['duration_s']
            chain_note = (
                ' Visually confirm in close-up RGB that you are loading the upstream stage-1 appliance '
                '(not an adjacent downstream stage-2 appliance) before placing items inside!'
                if has_downstream_timed(tj['id'])
                else ''
            )
            phases.append({
                'phase': len(phases) + 1,
                'action_type': 'start_timed_process',
                'jobs': [tj['id']],
                'place': cur_place,
                'travel_seconds': round(leg_s, 1),
                'work_seconds': round(tj['setup_s'], 1),
                'wait_seconds': 0.0,
                'est_clock_after_s': round(sim_t, 1),
                'timer_ready_at_s': round(active_timers[tj['id']], 1),
                'why': f"Start unattended {tj['id']} ({tj['duration_s']:.0f}s) early so travel and other work overlap its run time.{chain_note}",
            })
            continue

        # 3. Collect any other timed process whose timer has already finished AND is in current room (0 travel)
        ready_here_collect = [
            tj for tid, tj in unfinished_timed.items()
            if tid in active_timers and active_timers[tid] <= sim_t + 1e-6 and tj['place'] == cur_place
        ]
        if ready_here_collect:
            tj = ready_here_collect[0]
            sim_t += tj['finish_s']
            done_ids.add(tj['id'])
            unfinished_timed.pop(tj['id'], None)
            active_timers.pop(tj['id'], None)
            phases.append({
                'phase': len(phases) + 1,
                'action_type': 'finish_timed_process',
                'jobs': [tj['id']],
                'place': cur_place,
                'travel_seconds': 0.0,
                'work_seconds': round(tj['finish_s'], 1),
                'wait_seconds': 0.0,
                'est_clock_after_s': round(sim_t, 1),
                'why': f"{tj['id']} is complete and robot is already at {cur_place}.",
            })
            continue

        # 4. Execute ready local_work or ready delivery_units (nearest start_place first to cluster room passes!)
        ready_loc = [x for x in pending_local if dep_satisfied(x['depends_on'])]
        ready_del = [x for x in pending_deliv if dep_satisfied(x['depends_on'])]
        if ready_loc or ready_del:
            candidates: list[tuple[float, str, Any]] = []
            for x in ready_loc:
                candidates.append((travel_s[(cur_place, x['place'])], 'local', x))
            for x in ready_del:
                candidates.append((travel_s[(cur_place, x['start_place'])], 'deliv', x))
            candidates.sort(key=lambda item: (item[0], 0 if item[1] == 'local' else 1))
            leg_s, kind, chosen = candidates[0]
            if kind == 'local':
                pending_local.remove(chosen)
                sim_t += leg_s + chosen['work_s']
                total_travel_s += leg_s
                cur_place = chosen['place']
                done_ids.add(chosen['id'])
                phases.append({
                    'phase': len(phases) + 1,
                    'action_type': 'local_area_work',
                    'jobs': [chosen['id']],
                    'place': cur_place,
                    'travel_seconds': round(leg_s, 1),
                    'work_seconds': round(chosen['work_s'], 1),
                    'wait_seconds': 0.0,
                    'est_clock_after_s': round(sim_t, 1),
                    'why': (
                        'Overlaps active background timer(s) without idle waiting.'
                        if active_timers else f'Clustered local work in {cur_place}.'
                    ),
                })
            else:
                pending_deliv.remove(chosen)
                sim_t += leg_s + chosen['work_s']
                total_travel_s += leg_s
                cur_place = chosen['end_place']
                for jid in chosen['ids']:
                    done_ids.add(jid)
                mode_desc = (
                    f"using carrier '{chosen['carrier']}' ({chosen['batches']} batch(es) for {chosen['item_count']} items; "
                    f"place carrier on a clear central spot of destination surface and verify in RGB no small item slid off)"
                    if chosen['mode'] == 'carrier_batch'
                    else f"direct hand transfer ({chosen['item_count']} item(s))"
                )
                phases.append({
                    'phase': len(phases) + 1,
                    'action_type': 'cross_room_transfer',
                    'jobs': chosen['ids'],
                    'from_place': chosen['start_place'],
                    'place': cur_place,
                    'mode': chosen['mode'],
                    'carrier': chosen['carrier'],
                    'travel_seconds': round(leg_s, 1),
                    'work_seconds': round(chosen['work_s'], 1),
                    'wait_seconds': 0.0,
                    'est_clock_after_s': round(sim_t, 1),
                    'why': f"Transfer {chosen['start_place']} -> {cur_place} {mode_desc}.",
                })
            continue

        # 5. Collect any finished timer in another room
        ready_any_collect = [
            tj for tid, tj in unfinished_timed.items()
            if tid in active_timers and active_timers[tid] <= sim_t + 1e-6
        ]
        if ready_any_collect:
            tj = min(ready_any_collect, key=lambda x: travel_s[(cur_place, x['place'])])
            leg_s = travel_s[(cur_place, tj['place'])]
            sim_t += leg_s + tj['finish_s']
            total_travel_s += leg_s
            cur_place = tj['place']
            done_ids.add(tj['id'])
            unfinished_timed.pop(tj['id'], None)
            active_timers.pop(tj['id'], None)
            phases.append({
                'phase': len(phases) + 1,
                'action_type': 'finish_timed_process',
                'jobs': [tj['id']],
                'place': cur_place,
                'travel_seconds': round(leg_s, 1),
                'work_seconds': round(tj['finish_s'], 1),
                'wait_seconds': 0.0,
                'est_clock_after_s': round(sim_t, 1),
                'why': f"Timer for {tj['id']} finished during parallel work; zero wait needed.",
            })
            continue

        # 6. No independent work is ready; travel to the soonest-finishing active timer and wait ONLY the residual!
        if active_timers:
            next_tid = min(active_timers, key=lambda k: active_timers[k])
            tj = unfinished_timed[next_tid]
            leg_s = travel_s[(cur_place, tj['place'])]
            arrival_t = sim_t + leg_s
            total_travel_s += leg_s
            cur_place = tj['place']
            wait_needed = max(0.0, active_timers[next_tid] - arrival_t)
            total_wait_s += wait_needed
            sim_t = arrival_t + wait_needed + tj['finish_s']
            done_ids.add(next_tid)
            unfinished_timed.pop(next_tid, None)
            active_timers.pop(next_tid, None)
            phases.append({
                'phase': len(phases) + 1,
                'action_type': 'wait_and_finish_timed_process',
                'jobs': [next_tid],
                'place': cur_place,
                'travel_seconds': round(leg_s, 1),
                'wait_seconds': round(wait_needed, 1),
                'work_seconds': round(tj['finish_s'], 1),
                'est_clock_after_s': round(sim_t, 1),
                'why': (
                    f"All independent parallel work is exhausted; travel ({leg_s:.1f}s) + "
                    f"residual wait ({wait_needed:.1f}s) finishes {next_tid}."
                ),
            })
            continue

        # Unresolvable dependency (e.g. depends on something not runnable)
        blocked = [
            r['id'] for r in [*unstarted_timed.values(), *pending_local, *pending_deliv]
            if 'id' in r
        ]
        warnings.append(f'Unscheduled jobs with unsatisfied dependencies: {blocked}')
        break

    # Carrier final return if needed
    for c in carriers:
        fp = c['final_place']
        ret_jid = f"return_{c['id']}"
        if ret_jid in done_ids:
            if fp is not None:
                carrier_state[c['id']] = fp
            continue
        if fp is not None and carrier_state[c['id']] != fp:
            c_cur = carrier_state[c['id']]
            leg_to_c = travel_s[(cur_place, c_cur)]
            leg_ret = travel_s[(c_cur, fp)]
            ret_s = leg_to_c + leg_ret + 2 * DEFAULT_ACT_S
            sim_t += ret_s
            total_travel_s += leg_to_c + leg_ret
            cur_place = fp
            carrier_state[c['id']] = fp
            phases.append({
                'phase': len(phases) + 1,
                'action_type': 'return_carrier',
                'jobs': [f"return_{c['id']}"],
                'from_place': c_cur,
                'place': fp,
                'travel_seconds': round(leg_to_c + leg_ret, 1),
                'work_seconds': round(2 * DEFAULT_ACT_S, 1),
                'wait_seconds': 0.0,
                'est_clock_after_s': round(sim_t, 1),
                'why': f"Return carrier '{c['id']}' to its required final place '{fp}'.",
            })

    # Naive sequential baseline estimate (waiting idly for every timed process + single-item trips)
    naive_idle_wait_s = sum(tj['duration_s'] for tj in timed_jobs if tj['status'] != 'done')
    parallel_wait_saved_s = max(0.0, round(naive_idle_wait_s - total_wait_s, 1))
    total_saved_vs_naive_s = round(parallel_wait_saved_s + naive_delivery_extra_s, 1)

    if active_timers or any(tj['status'] != 'done' for tj in timed_jobs):
        warnings.append(
            'Do NOT call act(primitive="wait") until all independent local_area_work and cross_room_transfer phases before wait_and_finish_timed_process are completed.'
        )
        warnings.append(
            'Never toggle_off or open a running timed process based on estimated duration_seconds alone; wait until an action receipt explicitly reports completion (any_process_running=false).'
        )

    return {
        'schedule_basis': 'robot_time_seconds',
        'starting_robot_clock_s': round(public_elapsed_s, 1),
        'estimated_finish_clock_s': round(sim_t, 1),
        'estimated_remaining_s': round(sim_t - public_elapsed_s, 1),
        'estimated_idle_wait_s': round(total_wait_s, 1),
        'estimated_savings_vs_naive': {
            'parallel_wait_saved_s': parallel_wait_saved_s,
            'carrier_batch_saved_s': round(naive_delivery_extra_s, 1),
            'total_saved_s': total_saved_vs_naive_s,
        },
        'carrier_comparisons': carrier_recommendations,
        'ordered_phases': phases,
        'next_recommended_phase': phases[0] if phases else None,
        'assumptions': assumptions,
        'scheduling_rules': warnings,
    }


class PublicScheduleLedger:
    """Harness-side public episode ledger (zero simulator state access).

    Records ONLY:
    - Public `robot_time.total_s` returned by `RobotClock`;
    - Public `effect.process` messages returned in `act` receipts, along with
      the public `robot_time.total_s` when each message first appeared;
    - Model-authored `verified_closed` compartment labels and latest `schedule_plan`.
    """

    def __init__(self) -> None:
        self.robot_clock_s: float = 0.0
        self.process_events: list[dict[str, Any]] = []
        self.active_process_since_s: float | None = None
        self.any_process_running: bool = False
        self.verified_closed: list[str] = []
        self.completed_jobs: list[str] = []
        self.last_plan_spec: dict[str, Any] | None = None
        self.last_schedule: dict[str, Any] | None = None

    def observe_public_action(self, name: str, arguments: dict[str, Any] | None, result: dict[str, Any]) -> None:
        rt = result.get('robot_time')
        if isinstance(rt, dict) and isinstance(rt.get('total_s'), (int, float)):
            self.robot_clock_s = float(rt['total_s'])
        if not result.get('ok') or name != 'act':
            return
        effect = result.get('effect')
        if not isinstance(effect, dict):
            return
        proc = effect.get('process')
        if isinstance(proc, dict):
            msgs = proc.get('messages') or ([proc['message']] if isinstance(proc.get('message'), str) else [])
            running = bool(proc.get('any_process_running', False))
            if running and self.active_process_since_s is None:
                self.active_process_since_s = self.robot_clock_s
            elif not running:
                self.active_process_since_s = None
            self.any_process_running = running
            for m in msgs:
                self.process_events.append({
                    'at_robot_s': round(self.robot_clock_s, 1),
                    'primitive': effect.get('primitive'),
                    'message': m,
                    'any_process_running': running,
                })
            if len(self.process_events) > 20:
                self.process_events = self.process_events[-20:]

    def update_from_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan_input = arguments.get('plan')
        closed = arguments.get('verified_closed_compartments')
        if isinstance(closed, list):
            base_closed = [] if isinstance(plan_input, dict) and not closed else list(self.verified_closed)
            for item in closed[:40]:
                label = _clean_label(item, 'verified_closed_compartments[]')
                if label not in base_closed:
                    base_closed.append(label)
            self.verified_closed = base_closed[-40:]
        done = arguments.get('completed_job_ids')
        if isinstance(done, list):
            base_done = [] if isinstance(plan_input, dict) and not done else list(self.completed_jobs)
            for item in done[:80]:
                jid = _clean_label(item, 'completed_job_ids[]')
                if jid not in base_done:
                    base_done.append(jid)
            self.completed_jobs = base_done[-80:]

        if isinstance(plan_input, dict):
            spec = copy.deepcopy(plan_input)
            for group_key in ('timed_processes', 'deliveries', 'local_work'):
                for row in spec.get(group_key) or []:
                    if isinstance(row, dict) and row.get('id') in self.completed_jobs:
                        row['status'] = 'done'
            self.last_plan_spec = spec
            self.last_schedule = optimize_schedule(
                spec, public_elapsed_s=self.robot_clock_s, completed_ids=set(self.completed_jobs)
            )
        elif self.last_plan_spec is not None:
            spec = copy.deepcopy(self.last_plan_spec)
            for group_key in ('timed_processes', 'deliveries', 'local_work'):
                for row in spec.get(group_key) or []:
                    if isinstance(row, dict) and row.get('id') in self.completed_jobs:
                        row['status'] = 'done'
            self.last_plan_spec = spec
            self.last_schedule = optimize_schedule(
                spec, public_elapsed_s=self.robot_clock_s, completed_ids=set(self.completed_jobs)
            )

        return {
            'public_ledger': self.public_summary(),
            'schedule': self.last_schedule,
        }

    def public_summary(self) -> dict[str, Any]:
        elapsed_since_active = (
            round(max(0.0, self.robot_clock_s - self.active_process_since_s), 1)
            if self.any_process_running and self.active_process_since_s is not None
            else None
        )
        summary: dict[str, Any] = {
            'episode_robot_time_s': round(self.robot_clock_s, 1),
            'any_process_reported_running': self.any_process_running,
            'elapsed_robot_s_since_process_started': elapsed_since_active,
            'recent_process_messages': self.process_events[-4:],
            'verified_closed_compartments': list(self.verified_closed),
            'completed_job_ids': list(self.completed_jobs),
        }
        if self.last_schedule and self.last_schedule.get('next_recommended_phase'):
            summary['next_scheduled_phase'] = self.last_schedule['next_recommended_phase']
        return summary
