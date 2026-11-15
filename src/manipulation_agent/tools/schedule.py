"""Read-only schedule optimizer and public episode ledger tool (Harness option)."""
from __future__ import annotations

from .base import ToolMetadata, register
from ..contracts import NULL_STRING, STRING, object_schema


def _opt_object(properties: dict, required: list[str] | tuple[str, ...]) -> dict:
    return {
        'type': 'object',
        'properties': properties,
        'required': list(required),
        'additionalProperties': False,
    }


PLAN_INPUT_SCHEMA = {
    'type': ['object', 'null'],
    'properties': {
        'places': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': STRING},
        'robot_place': NULL_STRING,
        'travel_edges': {
            'type': ['array', 'null'],
            'maxItems': 60,
            'items': _opt_object(
                {
                    'from_place': STRING,
                    'to_place': STRING,
                    'meters': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1000},
                    'seconds': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 3600},
                },
                required=('from_place', 'to_place'),
            ),
        },
        'carriers': {
            'type': ['array', 'null'],
            'maxItems': 8,
            'items': _opt_object(
                {
                    'id': STRING,
                    'place': STRING,
                    'capacity': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 20},
                    'final_place': NULL_STRING,
                },
                required=('id', 'place'),
            ),
        },
        'timed_processes': {
            'type': ['array', 'null'],
            'maxItems': 12,
            'items': _opt_object(
                {
                    'id': STRING,
                    'place': STRING,
                    'duration_seconds': {'type': ['number', 'null'], 'minimum': 1, 'maximum': 7200},
                    'setup_seconds': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 600},
                    'finish_seconds': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 600},
                    'status': {'type': ['string', 'null'], 'enum': ['pending', 'running', 'completed_uncollected', 'done', None]},
                    'started_at_robot_s': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 100000},
                    'depends_on': NULL_STRING,
                },
                required=('id', 'place'),
            ),
        },
        'deliveries': {
            'type': ['array', 'null'],
            'maxItems': 30,
            'items': _opt_object(
                {
                    'id': STRING,
                    'from_place': STRING,
                    'to_place': STRING,
                    'item_count': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 30},
                    'can_use_carrier': {'type': ['boolean', 'null']},
                    'depends_on': NULL_STRING,
                    'status': {'type': ['string', 'null'], 'enum': ['pending', 'done', None]},
                },
                required=('id', 'from_place', 'to_place'),
            ),
        },
        'local_work': {
            'type': ['array', 'null'],
            'maxItems': 40,
            'items': _opt_object(
                {
                    'id': STRING,
                    'place': STRING,
                    'work_seconds': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1800},
                    'depends_on': NULL_STRING,
                    'status': {'type': ['string', 'null'], 'enum': ['pending', 'done', None]},
                },
                required=('id', 'place'),
            ),
        },
    },
    'required': ['places'],
    'additionalProperties': False,
}


@register(
    'schedule_plan',
    (
        'Compute or incrementally update a minimum-robot-time phase schedule and episode state ledger (0 action steps). '
        'Only minimal fields are required (e.g. places plus whichever timed_processes, carriers, deliveries, or '
        'local_work you want to schedule; optional subfields like depends_on, status, duration_seconds, setup_seconds, '
        'or completed_job_ids can be omitted). You can call this concisely with high-level room/process/carrier '
        'blocks and update incrementally as you explore new rooms or finish phases (pass plan=null to just mark '
        'completed_job_ids or verified_closed_compartments). Never toggle_off or open a running timed process before '
        'an action receipt explicitly reports completion.'
    ),
    _opt_object(
        {
            'plan': PLAN_INPUT_SCHEMA,
            'completed_job_ids': {'type': ['array', 'null'], 'maxItems': 80, 'items': STRING},
            'verified_closed_compartments': {'type': ['array', 'null'], 'maxItems': 40, 'items': STRING},
        },
        required=(),
    ),
    metadata=ToolMetadata(read_only=True),
)
def schedule_plan(ctx, plan=None, completed_job_ids=None, verified_closed_compartments=None):
    return ctx.harness.schedule_ledger.update_from_tool({
        'plan': plan,
        'completed_job_ids': completed_job_ids,
        'verified_closed_compartments': verified_closed_compartments,
    })
