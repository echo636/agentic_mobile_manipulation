"""Check referenced scene/model paths without importing or starting OmniGibson."""
import json
from pathlib import Path

from .omnigibson_backend import select_compatible_scene, restore_static_floor_geometry


def inspect_scene_assets(template, instance, asset_root, scene):
    asset_root = Path(asset_root)
    selected, data, _ = select_compatible_scene(Path(template), Path(instance))
    full = selected.with_name(selected.name.replace('-partial_rooms', ''))
    if full != selected and full.is_file():
        restore_static_floor_geometry(data, json.loads(full.read_text()))
    dependencies = set()
    for entry in data['objects_info']['init_info'].values():
        if entry.get('class_name') != 'DatasetObject':
            continue
        args = entry['args']
        category, model = args['category'], args['model']
        if any('/' in v or v in ('.','..') for v in (category,model)):
            raise ValueError('Invalid dataset asset identifier')
        dependencies.add(f'objects/{category}/{model}/usd/{model}.encrypted.usd')
    missing = [p for p in sorted(dependencies) if not (asset_root / p).is_file()]
    layout = asset_root / 'scenes' / scene / 'layout'
    return {'status': 'passed' if not missing and layout.is_dir() else 'failed',
            'level': 'template_bindings_referenced_model_usds_and_scene_layout_paths_only',
            'selected_template': str(selected), 'model_usds_checked': len(dependencies),
            'missing_model_usds': missing, 'scene_layout': str(layout),
            'scene_layout_exists': layout.is_dir(), 'full_simulator_load_verified': False}
