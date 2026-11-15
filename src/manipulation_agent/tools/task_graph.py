"""Read-only general precondition and cost-aware task graph planner tool (Harness option)."""
from __future__ import annotations

from .base import ToolMetadata, register
from ..contracts import NULL_STRING, STRING


def _closed_object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        'type': 'object',
        'properties': properties,
        'required': list(required),
        'additionalProperties': False,
    }


def _nullable_closed_object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        'type': ['object', 'null'],
        'properties': properties,
        'required': list(required),
        'additionalProperties': False,
    }


PREDICATE_SCHEMA = _closed_object(
    {
        'var': STRING,
        'op': {'type': ['string', 'null'], 'enum': ['eq', 'ne', 'le', 'lt', 'ge', 'gt', 'known', None]},
        'value': {'type': ['number', 'null']},
        'flag': {'type': ['boolean', 'null']},
        'label': NULL_STRING,
    },
    required=('var',),
)

EFFECT_SCHEMA = _closed_object(
    {
        'var': STRING,
        'op': {'type': ['string', 'null'], 'enum': ['set', 'add', 'max', 'min', None]},
        'value': {'type': ['number', 'null']},
        'flag': {'type': ['boolean', 'null']},
        'label': NULL_STRING,
    },
    required=('var',),
)

LOCATION_SCHEMA = _closed_object(
    {
        'id': STRING,
        'capacity': {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 1000},
        'place_preconditions': {'type': ['array', 'null'], 'maxItems': 20, 'items': PREDICATE_SCHEMA},
    },
    required=('id',),
)

TRAVEL_COST_SCHEMA = _closed_object(
    {
        'from_location': STRING,
        'to_location': STRING,
        'cost_s': {'type': 'number', 'minimum': 0, 'maximum': 36000},
        'bidirectional': {'type': ['boolean', 'null']},
    },
    required=('from_location', 'to_location', 'cost_s'),
)

STATE_VAR_SCHEMA = _closed_object(
    {
        'name': STRING,
        'value': {'type': ['number', 'null']},
        'flag': {'type': ['boolean', 'null']},
        'label': NULL_STRING,
        'unknown': {'type': ['boolean', 'null']},
        'domain': {'type': ['array', 'null'], 'maxItems': 20, 'items': {'type': 'number'}},
        'label_domain': {'type': ['array', 'null'], 'maxItems': 20, 'items': STRING},
        'probabilities': {'type': ['array', 'null'], 'maxItems': 20, 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}},
    },
    required=('name',),
)

ENTITY_SCHEMA = _closed_object(
    {
        'id': STRING,
        'location': NULL_STRING,
        'movable': {'type': ['boolean', 'null']},
        'capacity': {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 1000},
        'size': {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 100},
        'place_preconditions': {'type': ['array', 'null'], 'maxItems': 20, 'items': PREDICATE_SCHEMA},
    },
    required=('id',),
)

OPERATION_SCHEMA = _closed_object(
    {
        'id': STRING,
        'location': NULL_STRING,
        'preconditions': {'type': ['array', 'null'], 'maxItems': 30, 'items': PREDICATE_SCHEMA},
        'effects': {'type': ['array', 'null'], 'maxItems': 30, 'items': EFFECT_SCHEMA},
        'passive_effects': {'type': ['array', 'null'], 'maxItems': 30, 'items': EFFECT_SCHEMA},
        'active_duration_s': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 7200},
        'occupies_hand': {'type': ['boolean', 'null']},
        'passive_duration_s': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 14400},
        'resolves_unknowns': {'type': ['array', 'null'], 'maxItems': 20, 'items': STRING},
        'repeatable': {'type': ['boolean', 'null']},
        'status': {'type': ['string', 'null'], 'enum': ['pending', 'active_passive', 'done', None]},
        'started_at_s': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 100000},
    },
    required=('id',),
)

GOALS_SCHEMA = _nullable_closed_object(
    {
        'predicates': {'type': ['array', 'null'], 'maxItems': 60, 'items': PREDICATE_SCHEMA},
        'entity_locations': {
            'type': ['array', 'null'],
            'maxItems': 60,
            'items': _closed_object(
                {'entity_id': STRING, 'location': STRING},
                required=('entity_id', 'location'),
            ),
        },
        'require_empty_hand': {'type': ['boolean', 'null']},
    },
    required=(),
)

TASK_GRAPH_INPUT_SCHEMA = _nullable_closed_object(
    {
        'agent_location': NULL_STRING,
        'hand_entity': NULL_STRING,
        'manip_duration_s': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 600},
        'locations': {'type': ['array', 'null'], 'maxItems': 32, 'items': LOCATION_SCHEMA},
        'travel_cost': {'type': ['array', 'null'], 'maxItems': 120, 'items': TRAVEL_COST_SCHEMA},
        'state_vars': {'type': ['array', 'null'], 'maxItems': 80, 'items': STATE_VAR_SCHEMA},
        'entities': {'type': ['array', 'null'], 'maxItems': 60, 'items': ENTITY_SCHEMA},
        'operations': {'type': ['array', 'null'], 'maxItems': 60, 'items': OPERATION_SCHEMA},
        'goals': GOALS_SCHEMA,
    },
    required=(),
)

PLAN_TASK_GRAPH_PARAMETERS = _closed_object(
    {
        'graph': TASK_GRAPH_INPUT_SCHEMA,
        'completed_operations': {'type': ['array', 'null'], 'maxItems': 80, 'items': STRING},
        'started_passive_operations': {'type': ['array', 'null'], 'maxItems': 40, 'items': STRING},
        'remove_operations': {'type': ['array', 'null'], 'maxItems': 40, 'items': STRING},
        'remove_entities': {'type': ['array', 'null'], 'maxItems': 40, 'items': STRING},
        'reset': {'type': ['boolean', 'null']},
    },
    required=(),
)

PLAN_TASK_GRAPH_DESCRIPTION = (
    'Solve or incrementally update a general precondition and cost-aware task graph (0 action steps). '
    'Accepts four universal physical primitives: locations and point-to-point travel_cost (seconds, fused with '
    'measured public travel times), state_vars and entities (with generic movable and capacity attributes), '
    'operations (location, preconditions, effects, active_duration_s, occupies_hand, passive_duration_s, '
    'resolves_unknowns), and terminal goals. Supports lightweight delta merge: omit unchanged entries on follow-up '
    'calls and pass only newly observed state_vars, entities, operations, or completed_operations.'
)


@register(
    'plan_task_graph',
    PLAN_TASK_GRAPH_DESCRIPTION,
    PLAN_TASK_GRAPH_PARAMETERS,
    metadata=ToolMetadata(read_only=True),
)
def plan_task_graph(
    ctx,
    graph=None,
    completed_operations=None,
    started_passive_operations=None,
    remove_operations=None,
    remove_entities=None,
    reset=None,
):
    session = ctx.harness.task_graph_session
    session.sync_from_state_memory(getattr(ctx.harness, 'state_memory', None))
    return session.update_and_solve({
        'graph': graph,
        'completed_operations': completed_operations,
        'started_passive_operations': started_passive_operations,
        'remove_operations': remove_operations,
        'remove_entities': remove_entities,
        'reset': reset,
    })
