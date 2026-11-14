"""Explicit sampling-only policy for the pinned breakfast task's unused slicing.

The installed toast has no cutting parts. Native USDObject initialization drops
that unsupported ability, while the online sampler rejects the model first.
This opt-in policy makes sampling use the transport-capable object consistently.
No asset, goal, contact, placement, collision or scoring predicate is changed.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path

TASK = 'prepare_a_breakfast_bar'
DEFINITION_SHA256 = 'aa9c5cc9b7a08e146e8e84a79672522311a48cb989838036ae1c79bbc3d8c00c'
TOAST_USD_SHA256 = 'f885d64e046154dd00d9a6f6896a1ba361be670119239d59d43f364beaf23625'


@contextmanager
def sampling_policy(knowledgebase, task, definition, asset_root):
    """Apply only to this exact non-processing task and diagnosed toast asset."""
    definition = Path(definition)
    asset_root = Path(asset_root)
    if task != TASK or hashlib.sha256(definition.read_bytes()).hexdigest() != DEFINITION_SHA256:
        raise ValueError('Toast sampling policy requires the unchanged breakfast-bar definition')
    usd = asset_root/'objects/toast/ygginu/usd/ygginu.encrypted.usd'
    if hashlib.sha256(usd.read_bytes()).hexdigest() != TOAST_USD_SHA256:
        raise ValueError('Toast sampling policy does not cover this asset version')
    metadata = json.loads((asset_root/'objects/toast/ygginu/misc/metadata.json').read_text())
    if metadata.get('object_parts'):
        raise ValueError('Toast now has cutting parts; revalidate without the old sampling policy')
    synset = knowledgebase.get_synset('toast.n.01')
    original = synset.abilities
    if 'sliceable' not in original:
        raise ValueError('Unexpected original toast abilities')
    adjusted = deepcopy(original)
    removed = adjusted.pop('sliceable')
    synset.__dict__['abilities'] = adjusted
    record = dict(policy='breakfast_toast_unused_sliceable_sampling_v1', task=task,
                  definition_sha256=DEFINITION_SHA256, asset_sha256=TOAST_USD_SHA256,
                  removed_ability=dict(sliceable=removed), other_abilities_unchanged=True,
                  asset_files_changed=False, task_goals_changed=False,
                  scope='Sampling only; native object initialization also rejects this unsupported ability')
    try:
        yield record
    finally:
        synset.__dict__['abilities'] = original
