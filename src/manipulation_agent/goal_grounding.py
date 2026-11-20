"""Scoped optimization of pinned BDDL 3.7 grounding, without changing goal logic.

The upstream Cartesian product compiles the same literal millions of times.
Intern literals within ONE grounding invocation; retain all options, duplicate
literals, ordering, and scope. Never cache truth across simulation changes.
"""
from contextlib import contextmanager
import itertools


def frozen(value):
    return tuple(frozen(v) for v in value) if isinstance(value, (list, tuple)) else value


def ground_options(compiled_state, scope=None, object_map=None):
    from bddl.condition_evaluation import compile_state
    cache = {}
    options = []
    for combination in itertools.product(*(c.flattened_condition_options for c in compiled_state)):
        literals = list(itertools.chain.from_iterable(combination))
        keys = [frozen(literal) for literal in literals]
        present = set(keys)
        if any(key[0] == 'not' and key[1] in present for key in keys):
            continue
        option = []
        for literal, key in zip(literals, keys):
            if key not in cache:
                cache[key] = compile_state([literal], scope=scope, object_map=object_map)[0]
            option.append(cache[key])
        options.append(option)
    return sorted(options, key=len)


@contextmanager
def efficient_grounding():
    """Patch only during this adapter's environment construction, then restore."""
    import bddl.activity as activity
    import bddl.condition_evaluation as conditions
    previous = activity.get_ground_state_options, conditions.get_ground_state_options
    activity.get_ground_state_options = conditions.get_ground_state_options = ground_options
    try:
        yield
    finally:
        activity.get_ground_state_options, conditions.get_ground_state_options = previous


@contextmanager
def evaluate_once_per_literal(options):
    """Memoize within one read-only scoring pass; keep official metric code intact."""
    originals = {}
    values = {}
    # Grounding interns nodes. This also supports older non-interned instances.
    for option in options:
        for node in option:
            if id(node) not in originals:
                had_instance_method = 'evaluate' in vars(node)
                original = node.evaluate
                originals[id(node)] = (node, original, had_instance_method)
                key = (id(node.scope), frozen(node.body))
                def evaluate(fn, original=original, key=key):
                    cache_key = (key, fn)
                    if cache_key not in values:
                        values[cache_key] = original(fn)
                    return values[cache_key]
                node.evaluate = evaluate
    try:
        yield
    finally:
        for node, method, had_instance_method in originals.values():
            if had_instance_method:
                node.evaluate = method
            else:
                del node.evaluate
