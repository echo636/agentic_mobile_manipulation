from manipulation_agent.contracts import validate
from manipulation_agent.tools import tool_specs


def test_action_defaults_are_optional_without_relaxing_target_validation():
    schema = next(row['inputSchema'] for row in tool_specs('planning')
                  if row['name'] == 'act')
    assert schema['required'] == ['primitive', 'target', 'revision', 'decision']
    arguments = {
        'primitive': 'navigate_to',
        'target': {'image_ref': 'rgb-00001-front', 'point': [0.5, 0.5]},
        'revision': 0,
        'decision': {key: 'known' for key in
                     ('state', 'active_subgoal', 'reason', 'expected')},
    }
    validate(arguments, schema, 'arguments')
