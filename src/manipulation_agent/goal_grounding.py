"""Scoped optimization of pinned BDDL 3.7 grounding, without changing goal logic.

The upstream Cartesian product compiles the same literal millions of times.
Intern literals within ONE grounding invocation; retain all options, duplicate
literals, ordering, and scope. Never cache truth across simulation changes.
"""
from contextlib import contextmanager
from collections.abc import Sequence
import itertools
import math
import os


class FactoredGroundOptions(Sequence):
    """The exact Cartesian product of positive, uniform-length factors.

    Only its best binding is materialized for research scoring. Native condition
    trees still determine task termination; no truth is cached across calls.
    """
    protocol = 'exact_positive_uniform_factor_product_v1'

    def __init__(self, factors):
        self.factors = factors
        self.count = math.prod(map(len, factors))

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        chosen = []
        for factor in reversed(self.factors):
            index, offset = divmod(index, len(factor))
            chosen.append(factor[offset])
        return list(itertools.chain.from_iterable(reversed(chosen)))

    def unique_options(self):
        return [option for factor in self.factors for option in factor]

    def best_values(self, evaluate_fn):
        cache = {}
        result = []
        for factor in self.factors:
            values = []
            for option in factor:
                row = []
                for node in option:
                    key = frozen(node.body)
                    if key not in cache:
                        cache[key] = bool(node.evaluate(evaluate_fn))
                    row.append(cache[key])
                values.append(row)
            result.extend(max(values, key=sum))
        return result


def factored_ground_options(compiled_state, scope=None, object_map=None, *, threshold=100000):
    """Factor only when every combination is consistent and equally sized.

    Negations and nonuniform factors retain the original exhaustive algorithm.
    Duplicates, factor order and every binding remain available via Sequence.
    """
    from bddl.condition_evaluation import compile_state
    raw = [c.flattened_condition_options for c in compiled_state]
    if (math.prod(map(len, raw)) < threshold or any(
            not options or len({len(option) for option in options}) != 1 or any(
                literal[0] == 'not' for option in options for literal in option)
            for options in raw)):
        return ground_options(compiled_state, scope, object_map)
    cache = {}
    factors = []
    for options in raw:
        factor = []
        for option in options:
            nodes = []
            for literal in option:
                key = frozen(literal)
                if key not in cache:
                    cache[key] = compile_state([literal], scope=scope, object_map=object_map)[0]
                nodes.append(cache[key])
            factor.append(nodes)
        factors.append(factor)
    return FactoredGroundOptions(factors)


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
    implementation = (factored_ground_options if os.environ.get('MAS_FACTORED_RESEARCH_GOALS') == '1'
                      else ground_options)
    activity.get_ground_state_options = conditions.get_ground_state_options = implementation
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
    unique = options.unique_options() if isinstance(options, FactoredGroundOptions) else options
    for option in unique:
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
