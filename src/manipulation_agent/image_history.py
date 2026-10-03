"""Versioned RGB attachments for the native loop; never change the raw history."""
import base64
import hashlib


class ImageHistory:
    def __init__(self, retained_captures=0):
        if isinstance(retained_captures, bool) or retained_captures < 0:
            raise ValueError('retained_captures must be nonnegative; zero keeps all images')
        self.retained_captures = retained_captures
        self.attachments = {}

    def append(self, history, observation, read_image):
        frames = observation.get('images', [])
        if not frames:
            return
        content = []
        for frame in frames:
            data, mime = read_image(frame['image_ref'])
            if mime != frame['mime_type'] or hashlib.sha256(data).hexdigest() != frame['sha256']:
                raise RuntimeError('RGB attachment does not match its recorded observation')
            content.extend([
                {'type': 'input_text', 'text': f"Robot RGB: {frame['view']} / {frame['image_ref']}"},
                {'type': 'input_image', 'image_url': 'data:' + mime + ';base64,' +
                 base64.b64encode(data).decode(), 'detail': 'high'},
            ])
        index = len(history)
        self.attachments[index] = {
            'revision': observation['revision'],
            'capture': dict(observation.get('capture', {})),
            'images': [dict(frame) for frame in frames],
        }
        history.append({'role': 'user', 'content': content})

    def outbound(self, history):
        indices = list(self.attachments)
        keep = set(indices[-self.retained_captures:]) if self.retained_captures else set(indices)
        messages = []
        for index, message in enumerate(history):
            if index not in self.attachments or index in keep:
                messages.append(message)
                continue
            # Keep the historical observation identity, but never silently replace
            # it with a new capture or manufacture a visual summary.
            attachment = self.attachments[index]
            refs = ', '.join(frame['image_ref'] for frame in attachment['images'])
            messages.append({'role': 'user', 'content': [{
                'type': 'input_text',
                'text': f'Historical RGB pixels omitted by configured image retention: {refs}. '
                        'The original images remain in the episode record. Only current image refs '
                        'can target actions; do not infer details from omitted images.',
            }]})
        visible = [self.attachments[i] for i in indices if i in keep]
        return messages, visible, len(indices) - len(keep)
