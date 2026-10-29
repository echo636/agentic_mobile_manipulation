"""Scene adapter disabling the unused precomputed navigation-map loader.

Imported only after OmniGibson startup. In the pinned 3.9.2 source the parent
``_load`` only loads ``layout/floor_trav*.png``; object loading, task state and
room segmentation happen separately and remain inherited.
"""
from functools import lru_cache


@lru_cache(maxsize=1)
def register_online_scene():
    from omnigibson.scenes.interactive_traversable_scene import InteractiveTraversableScene

    class ObservedNavigationScene(InteractiveTraversableScene):
        def _load(self):
            # No precomputed layout map is loaded, even during initialization.
            pass

        @property
        def trav_map(self):
            raise RuntimeError('Precomputed traversability is disabled for online depth navigation')

    # OG's recreation metadata stores module + class name. Keep the lazy class
    # addressable by that name as well as in its automatic scene registry.
    globals()['ObservedNavigationScene'] = ObservedNavigationScene
    return ObservedNavigationScene
