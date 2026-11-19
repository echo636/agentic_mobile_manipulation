"""Memory derived from delivered RGB pixels and the robot's own image selections."""
import base64
from collections import deque
import hashlib
import io
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

INSPECT_TOOL = {
    'name': 'inspect_target',
    'description': 'Magnify a point you choose in a current RGB image without moving. Coordinates in act always refer to a full current view.',
    'inputSchema': {'type': 'object', 'properties': {
        'revision': {'type': 'integer', 'minimum': 0}, 'image_ref': {'type': 'string'},
        'point': {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': 1},
                  'minItems': 2, 'maxItems': 2}},
        'required': ['revision', 'image_ref', 'point'], 'additionalProperties': False},
    'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False},
}

SENSOR_INSTRUCTION = """
A returned action means the request was processed. Use current RGB to judge which object changed, what was released where, and the resulting material state. The hand signal, when available, reports occupancy.
"""
EVIDENCE_INSTRUCTION = """
Selection panels show the source view, its selected patch, and the same image location now. Identify a held tool from its pickup image and the current scene before using it. Use inspect_target on your own candidate before acting when its outline or processing history is uncertain. Camera motion, motion of objects, and tool approach can change which object occupies a pixel. After an irreversible change, keep products apart from remaining work or obtain a view that separates them. After release or switching, check the visible effect at the intended destination or control. When two actions repeatedly restore prior views, identify the missing evidence and change viewpoint or consult the planner if available.
"""
RELEASE = {'release', 'place_inside', 'place_on_top', 'place_next_to', 'place_under', 'attach', 'hang'}
PROCESS = {'cut', 'wipe', 'sweep', 'spray', 'spread', 'soak', 'vacuum', 'pour', 'scoop', 'fold'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_point(p):
    return (isinstance(p, (list, tuple)) and len(p) == 2 and
            all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in p))


def crop_at(image, point, side=112):
    w, h = image.size
    x, y = (round(point[0] * (w - 1)), round(point[1] * (h - 1)))
    left = min(max(0, x - side // 2), max(0, w - side))
    top = min(max(0, y - side // 2), max(0, h - side))
    box = (left, top, min(w, left + side), min(h, top + side))
    return image.crop(box), (x, y), box


def png(im):
    out = io.BytesIO()
    im.save(out, format='PNG')
    return out.getvalue()


def load_rgb(frame):
    return Image.open(io.BytesIO(base64.b64decode(frame['data']))).convert('RGB')


def sample(frame, point, primitive, revision):
    rgb = load_rgb(frame)
    crop, pixel, box = crop_at(rgb, point)
    context = rgb.copy()
    context.thumbnail((176, 176), Image.Resampling.LANCZOS)
    return dict(view=frame['view'], image_ref=frame['image_ref'], sha256=frame['sha256'],
                point=list(point), pixel=list(pixel), box=list(box), primitive=primitive, revision=revision,
                context=base64.b64encode(png(context)).decode('ascii'),
                crop=base64.b64encode(png(crop)).decode('ascii'))


def signature(rgb):
    # Luminance of both the scene and the neighbourhood surrounding the selected pixel.
    return list(rgb.convert('L').resize((16, 16), Image.Resampling.BOX).tobytes())


def distance(left, right):
    return sum(abs(a-b) for a, b in zip(left, right)) / len(left)


def similar_action_views(previous, current, selection):
    """Compare delivered pixels, including a small target that disappears in a full-view average."""
    old = {frame['view']: frame for frame in previous.values()}
    if not old or set(old) != {frame['view'] for frame in current}:
        return False

    def similar(left, right, size):
        a = left.convert('L').resize(size, Image.Resampling.BOX)
        b = right.convert('L').resize(size, Image.Resampling.BOX)
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] <= 2

    for frame in current:
        before, after = load_rgb(old[frame['view']]), load_rgb(frame)
        if before.size != after.size or not similar(before, after, (32, 32)):
            return False
        if selection and frame['view'] == selection['view']:
            box = tuple(selection['box'])
            old_patch, new_patch = before.crop(box), after.crop(box)
            if not similar(old_patch, new_patch, (24, 24)):
                return False
            # A few changing coloured pixels can be diluted in the mean, even inside a crop.
            delta = ImageChops.difference(old_patch.resize((28, 28), Image.Resampling.BOX),
                                          new_patch.resize((28, 28), Image.Resampling.BOX))
            if max(peak for _, peak in delta.filter(ImageFilter.BoxBlur(1)).getextrema()) > 12:
                return False
    return True


def repeated_access(history):
    if len(history) < 4:
        return False
    a, b, c, d = list(history)[-4:]
    if not (a['primitive'] == c['primitive'] != b['primitive'] == d['primitive']
            and len({v['view'] for v in (a, b, c, d)}) == 1):
        return False
    separation = min(distance(a['scene'], b['scene']), distance(c['scene'], d['scene']))
    pairs = ((a, c), (b, d))
    drift = max(distance(first['scene'], again['scene']) for first, again in pairs)
    if separation < 2 or drift > 8:
        return False
    if all(math.dist(first['point'], again['point']) <= .045 and
           distance(first['area'], again['area']) <= 13 for first, again in pairs):
        return True
    # A large moving panel can be selected at different pixels on each visit.
    # Compare signed changed tiles in camera coordinates. Reject global brightness
    # shifts, changes in a different part of the image, and poor phase separation.
    if drift > separation / 3:
        return False
    def changed_tiles(first, second):
        return {(i, y > x) for i, (x, y) in enumerate(zip(first['scene'], second['scene']))
                if abs(y - x) >= 16}
    first_change = changed_tiles(a, b)
    next_change = changed_tiles(c, d)
    footprint = first_change | next_change
    return (2 <= len(footprint) <= len(a['scene']) * .7 and
            len(first_change & next_change) >= .7 * len(footprint))


def clean_sample(row):
    return {k: v for k, v in row.items() if k not in ('crop','context')}


class EvidenceMemory:
    def __init__(self, state=None):
        state = state or {}
        self.grasp = state.get('grasp')
        self.processed = deque(state.get('processed', []), maxlen=2)
        self.last = state.get('last')
        self.revision = state.get('revision', -1)
        self.access = deque(state.get('access', []), maxlen=8)
        self.occupancy = state.get('occupancy', 'unavailable')
        self.similar_command = state.get('similar_command')
        self.similar_count = state.get('similar_count', 0)

    def state(self):
        return dict(grasp=self.grasp, processed=list(self.processed), last=self.last,
                    access=list(self.access), occupancy=self.occupancy, revision=self.revision,
                    similar_command=self.similar_command, similar_count=self.similar_count)

    def observation(self, hand):
        self.occupancy = hand
        if hand == 'empty':
            self.grasp = None

    def action(self, primitive, target, previous, current, revision, hand):
        src = previous.get((target or {}).get('image_ref'))
        row = None
        if src and valid_point(target.get('point')):
            row = sample(src, target['point'], primitive, revision)
        # Appearance is useful for recovering the pickup selection. Material changes
        # can succeed repeatedly while leaving almost the entire image unchanged.
        compare_hand = hand == 'unavailable' and (primitive == 'grasp' or primitive in RELEASE)
        similar = compare_hand and similar_action_views(previous, current, row)
        if primitive == 'grasp' and similar:
            self.similar_count = self.similar_count + 1 if self.similar_command == primitive else 1
            self.similar_command = primitive
        else:
            self.similar_command, self.similar_count = None, 0
        if (primitive == 'grasp' and row and hand != 'empty' and self.occupancy != 'holding'
                and (hand == 'holding' or not self.grasp or not similar)):
            # Keep the previous selection available when another unmeasured attempt barely changed the images.
            self.grasp = row
        if hand == 'unavailable' and primitive in RELEASE and not similar:
            self.grasp = None
        self.observation(hand)
        if row and primitive in PROCESS:
            self.processed.append(row)
        self.revision = revision
        self.last = row
        if row and primitive in {'open', 'close'}:
            rgb = load_rgb(src)
            area, _, _ = crop_at(rgb, row['point'], side=256)
            self.access.append(dict(primitive=primitive, view=row['view'], point=row['point'],
                                    revision=revision, scene=signature(rgb), area=signature(area), selection=row))

    def panel(self, current, inspection=None):
        rows = []
        def add(role, row):
            if row and not any(row['image_ref'] == r['image_ref'] and row['point'] == r['point'] for _, r in rows):
                rows.append((role, row))
        add('Your inspection point', inspection)
        add('Earlier grasp request' if self.occupancy == 'unavailable' else 'Grasp selection', self.grasp)
        if self.last and self.last['primitive'] not in PROCESS:
            add('Last ' + self.last['primitive'] + ' selection', self.last)
        access = list(self.access)
        current_access = bool(self.last and self.last['primitive'] in {'open', 'close'} and access)
        recent = [r for r in access if self.revision - r['revision'] < 8] if current_access else []
        cycling = (len(recent) >= 4 and recent[-1]['revision'] - recent[-4]['revision'] == 3
                   and repeated_access(recent))
        repeated = []
        if cycling:
            # The last opposite command began in the phase the current command tried to restore.
            add('Earlier access selection', recent[-2].get('selection'))
        elif recent:
            last = recent[-1]
            repeated = [r for r in recent if r.get('selection') and r['primitive'] == last['primitive']
                        and r['view'] == last['view'] and distance(r['scene'], last['scene']) <= 4]
            if len(repeated) >= 3:
                add('Earlier access selection', repeated[0].get('selection'))
        for row in reversed(self.processed):
            if self.revision - row['revision'] <= 3:
                add('Prior ' + row['primitive'] + ' selection', row)
        if not rows:
            return {}, None
        thumb, header = 176, 28
        panel = Image.new('RGB', (thumb * 3, (thumb + header) * len(rows)), '#202124')
        draw = ImageDraw.Draw(panel)
        by_view = {frame['view']: frame for frame in current.values()}
        descriptors = []
        for i, (role, row) in enumerate(rows):
            ref = by_view[row['view']]
            current_crop, current_pixel, current_box = crop_at(load_rgb(ref), row['point'])
            past = Image.open(io.BytesIO(base64.b64decode(row['crop']))).convert('RGB')
            y = i * (thumb + header)
            draw.text((3, y+2), 'Original view', fill='white')
            if row.get('context'):
                context = Image.open(io.BytesIO(base64.b64decode(row['context']))).convert('RGB')
                ox, oy = (thumb-context.width)//2, y+header+(thumb-context.height)//2
                panel.paste(context, (ox, oy))
                x = ox+round(row['point'][0]*(context.width-1))
                cy = oy+round(row['point'][1]*(context.height-1))
                draw.ellipse((x-5,cy-5,x+5,cy+5), outline='#ff4050', width=2)
            draw.text((thumb+3, y+2), role, fill='white')
            draw.text((3, y+14), row['image_ref'], fill='white')
            draw.text((thumb+3, y+14), 'Selected patch', fill='white')
            draw.text((2*thumb+3, y+2), 'Current at same coordinates', fill='white')
            draw.text((2*thumb+3, y+14), ref['image_ref'], fill='white')
            for j, (crop, pos, box) in enumerate(((past, row['pixel'], row['box']),
                                                   (current_crop, current_pixel, current_box))):
                panel.paste(crop.resize((thumb, thumb), Image.Resampling.NEAREST), ((j+1)*thumb, y+header))
                x = (j+1)*thumb + (pos[0]-box[0]+.5) * thumb/crop.width
                cy = y+header + (pos[1]-box[1]+.5) * thumb/crop.height
                # Detached ticks leave the selected pixel visible even for tiny targets.
                for dx, dy in ((0,1), (0,-1), (1,0), (-1,0)):
                    draw.line((x+dx*8, cy+dy*8, x+dx*13, cy+dy*13), fill='#ff4050', width=2)
            descriptors.append(dict(label=role, original=clean_sample(row),
                                    current=dict(image_ref=ref['image_ref'], sha256=ref['sha256'],
                                                 pixel=list(current_pixel), box=list(current_box))))
        data = png(panel)
        info = dict(rows=descriptors, sha256=digest(data),
                    interpretation='Ticks mark your selected image location. Camera and objects may move. Use current full views for action coordinates.')
        if self.similar_command == 'grasp' and self.similar_count >= 2:
            info['repeated_similar_images'] = dict(command=self.similar_command, count=self.similar_count,
                message='You made consecutive grasp requests with similar before and after images. Hand occupancy is unmeasured. Compare the earlier pickup selection with the current views before choosing your next action.')
        if cycling:
            info['repeated_access'] = dict(revisions=[r['revision'] for r in recent[-4:]],
                message='Your alternating access commands have revisited similar views twice. Compare the visible changes and the locations you tried. What changed toward the goal? Inspect from another view or revise the plan if more evidence is needed.')
        elif len(repeated) >= 3:
            info['recent_access_requests'] = dict(command=repeated[-1]['primitive'], view=repeated[-1]['view'],
                selections=[{'revision': r['revision'], 'image_ref': r['selection']['image_ref'], 'point': r['point']}
                            for r in repeated],
                message='These requests used the same command from similar camera views. Compare your earlier selections and the visible effects before choosing the next action.')
        return info, data
