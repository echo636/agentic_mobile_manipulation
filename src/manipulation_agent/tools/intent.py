"""Public per-call intent, kept in the trace and excluded from motor arguments."""
from ..contracts import SkillError

PROTOCOL = 'public_tool_intent_v1'
LEGACY_PROTOCOL = 'legacy_no_tool_intent'
MAX_INTENT_LENGTH = 600
INTENT_DESCRIPTION = (
    'One concise public sentence stating the immediate purpose of THIS tool call. '
    'Required for every tool, including initialize, skill reading, notes and finish. '
    'Use the task and already available evidence; no private reasoning transcript is requested.'
)
INTENT_SCHEMA = {'type': 'string', 'minLength': 1, 'maxLength': MAX_INTENT_LENGTH,
                 'description': INTENT_DESCRIPTION}


def validate_intent(arguments):
    intent = arguments.get('intent') if isinstance(arguments, dict) else None
    if not isinstance(intent, str) or not intent.strip() or len(intent) > MAX_INTENT_LENGTH:
        raise SkillError('invalid_intent',
                         f'Include intent: a nonblank public purpose for this tool call, '
                         f'at most {MAX_INTENT_LENGTH} characters.')


def supervisor_finish_arguments(reason, *, require_intent=False):
    """The supervisor declares its own purpose; never fill in a model's intent."""
    arguments = {'outcome': 'aborted', 'reason': reason}
    if require_intent:
        arguments['intent'] = 'Close the episode: ' + reason
    return arguments
