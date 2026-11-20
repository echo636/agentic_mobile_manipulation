"""Existing selectable geometry programs; never attributed to the model."""
import hashlib
import json
from pathlib import Path


def read(name):
    if name not in ('bottle_grasps', 'place_in_open_bin'):
        return None
    source = (Path(__file__).with_name('geometry_programs')/(name+'.py')).read_text()
    if name == 'place_in_open_bin':
        entry = {'program': name, 'source': source, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'version': 1, 'author': 'harness_curated_from_model_demonstration', 'status': 'draft',
            'description': 'Direct placement into an upright open bin. Requires fresh object/container meshes and actual retention. '
                'parameters: object_id,container_id,arm,control_steps (model duration estimate),release (explicit bool, default false); '
                'optional clearance_m,bottom_clearance_m,yaw_degrees,tilt_degrees. One alignment goal per candidate, '
                'full payload fit and measured hand/object offset. release=true opens only after reached endpoint. '
                'Choose a reachable station first; wait and check native Inside/released afterwards.',
            'provenance': {'run': 'instance_309_18', 'program': 'held_bottle_lower_arc_transfer_v14',
                'source_sha256': 'afa170207a960a13f66ab89676b599d23373acc6be0908d366136ab82cb132da',
                'adaptation': 'curated mouth_targets geometry; removed fixed16-phase recovery path; explicit optional release'},
            'validation': 'New adaptation; no native qualification yet'}
        release = Path(__file__).with_name('geometry_programs') / (name + '.release.json')
        if release.exists():
            published = json.loads(release.read_text())
            if (published['source_sha256'] != entry['source_sha256']
                    or published['version'] != entry['version']):
                raise ValueError('Published geometry source changed; publish a new version')
            entry['library_release'] = published
        return entry
    return {'program': name, 'source': source, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'author': 'harness_reference', 'status': 'draft',
            'description': 'Existing upright elongated-object mesh-section grasp program. '
                'Select directly with parameters.object_id and arm after get_manip_geometry. '
                'Optional parameters.control_steps={pregrasp: estimated steps, contact_close: estimated steps}. '
                'Uses the original bottle_grasps sampler and native measured contact offset/aperture; '
                'returns ranked pregrasp/contact candidates. Hand must already be open. '
                'Reuse as-is, adapt it or write another program when task/shape differs.',
            'validation': 'Existing geometric algorithm plus adapter; native candidate executor not yet qualified'}


def catalog():
    return [{k: v for k, v in read(name).items() if k != 'source'}
            for name in ('bottle_grasps', 'place_in_open_bin')]
