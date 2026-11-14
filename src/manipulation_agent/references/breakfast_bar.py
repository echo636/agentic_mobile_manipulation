"""Whole-method reference plans for native breakfast-bar admission, not a policy."""

BINDINGS = {
    'table':'breakfast_table.n.01_1', 'counter':'countertop.n.01_1',
    'fridge':'electric_refrigerator.n.01_1', 'cabinet':'cabinet.n.01_1',
    'main_tray':'tray.n.01_1', 'waffle_tray':'tray.n.01_2',
    'pastry_tray':'tray.n.01_3', 'pancake_tray':'tray.n.01_4',
    'juice':'carton__of__orange_juice.n.01_1',
    **{f'platter{i}':f'platter.n.01_{i}' for i in (1,2,3)},
    'pancake1':'buttermilk_pancake.n.01_1', 'pancake2':'buttermilk_pancake.n.01_2',
    'waffle1':'waffle.n.01_1', 'waffle2':'waffle.n.01_2',
    'muffin':'muffin.n.01_1', 'toast':'toast.n.01_1',
    'danish':'danish.n.02_1', 'scone':'scone.n.01_1',
}
GROUPS = (('platter1', ('pancake1','pancake2')),
          ('platter2', ('waffle1','waffle2')),
          ('platter3', ('muffin','toast','danish','scone')))
# Geometric hints, not teleportation or guaranteed capacities. Every point must
# pass the unchanged executor's reach/collision/settling and native OnTop checks.
TABLE_SLOTS = {'platter1':(.23,.28), 'platter2':(.52,.28), 'platter3':(.79,.28),
               'main_tray':(.55,.73), 'juice':(.14,.72),
               'waffle_tray':(.78,.70), 'pastry_tray':(.28,.72)}


def method_plan(method):
    if method not in ('direct', 'original_carriers', 'source_platters'):
        raise ValueError('Unknown breakfast method')
    rows = []
    def transfer(item, destination, slot=None):
        rows.extend([dict(primitive='grasp', role=item),
                     dict(primitive='place_on_top', role=destination, item=item, slot=slot)])
    def fill(platter, foods):
        for i, food in enumerate(foods):
            transfer(food, platter, i)
    # Appliance opening is a prerequisite, conditional on actual state. The
    # original task has no door-closure goal, so these plans do not invent one.
    rows.extend([dict(primitive='open_if_closed', role='cabinet'),
                 dict(primitive='open_if_closed', role='fridge')])
    if method in ('direct','original_carriers'):
        for platter, _ in GROUPS:
            transfer(platter, 'table', platter)
        for platter, foods in GROUPS:
            if method == 'original_carriers' and platter != 'platter1':
                tray = 'waffle_tray' if platter == 'platter2' else 'pastry_tray'
                transfer(tray, 'table', tray)
            fill(platter, foods)
    else:
        transfer('platter1','table','platter1')
        fill(*GROUPS[0])
        for platter, foods in GROUPS[1:]:
            transfer(platter,'counter',platter)
            fill(platter, foods)
            transfer(platter,'table',platter)
    transfer('juice','table','juice')
    transfer('main_tray','table','main_tray')
    return rows
