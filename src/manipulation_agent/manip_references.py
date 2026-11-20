"""Inspectable geometry references, with original authorship, for model programs."""
import ast
import hashlib
from pathlib import Path

from .contracts import SkillError


def source():
    text = Path(__file__).with_name('grasp_geometry.py').read_text()
    tree = ast.parse(text)
    parts = ['from dataclasses import dataclass', 'import numpy as np']
    for name in ('GraspCandidate', 'bottle_grasps'):
        node = next(n for n in tree.body if getattr(n, 'name', None) == name)
        start = min([node.lineno, *[d.lineno for d in getattr(node, 'decorator_list', [])]])
        parts.append('\n'.join(text.splitlines()[start-1:node.end_lineno]))
    return '\n\n'.join(parts) + '\n'


def read(name):
    if name != 'bottle_grasps':
        raise SkillError('unknown_reference', 'Unknown manipulation reference: ' + name)
    code = source()
    return {'reference': name, 'author': 'harness_reference', 'source': code,
            'source_sha256': hashlib.sha256(code.encode()).hexdigest(),
            'description': 'Existing upright elongated-object mesh-section grasp sampler. '
                'Returns ranked contact/EEF/pregrasp candidates, jaw width and score. '
                'Read, reuse, adapt or replace it; writing a program does not require starting from scratch.',
            'usage': 'from manip_reference import bottle_grasps; '
                'use current robot_base mesh vertices, EEF position, measured finger offset and aperture. '
                'Convert each rotation matrix to quaternion xyzw in your code; return ranked candidates.',
            'validation': 'Historical geometry helper, not a guarantee of reachability or native grasp success'}


def catalog():
    return [{k: v for k, v in read('bottle_grasps').items() if k != 'source'}]
