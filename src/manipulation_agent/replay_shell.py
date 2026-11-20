"""Dependency-free presentation renderer, also used by the Python 3.10 report host."""
import html
from pathlib import Path


def render_replay_shell(embedded: str, *, title: str = 'Episode',
                        back_url: str = '/compare100_20261002/') -> str:
    """Refresh presentation around an already escaped, unchanged JSON archive."""
    assets = Path(__file__).with_name('replay_assets')
    page = (assets / 'index.html').read_text().replace('@@TITLE@@', html.escape(title))
    page = page.replace('@@CSS@@', ''.join((assets / name).read_text() for name in
        ('style.css', 'transcript.css', 'trace.css', 'goal_tree.css')))
    page = page.replace('href="/compare100_20261002/"', 'href="' + html.escape(back_url, quote=True) + '"')
    page = page.replace('@@JS@@', '\n'.join((assets / name).read_text() for name in
        ('timing.js', 'transcript.js', 'navigation_target.js', 'goals.js',
         'player.js', 'trace.js', 'trace_mode.js')))
    # Insert evidence last so literal template tokens inside authored text are not rewritten.
    return page.replace('@@DATA@@', embedded)

