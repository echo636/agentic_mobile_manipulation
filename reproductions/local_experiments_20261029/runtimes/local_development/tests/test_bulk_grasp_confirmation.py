import unittest
from types import SimpleNamespace

from manipulation_agent.executors.carry import bulk_grasp_reason


class BulkGraspConfirmation(unittest.TestCase):
    def test_loaded_basket_needs_confirmation_before_motion(self):
        basket = SimpleNamespace(aabb=([0, 0, 0], [0.6, 0.4, 0.25]))
        self.assertEqual(bulk_grasp_reason(basket, [object()] * 6), 'multiple_payloads')

    def test_furniture_needs_confirmation_even_without_payload(self):
        table = SimpleNamespace(aabb=([0, 0, 0], [1.8, 0.8, 0.8]))
        self.assertEqual(bulk_grasp_reason(table, []), 'furniture_size')

    def test_small_item_and_plate_with_one_item_remain_direct(self):
        plate = SimpleNamespace(aabb=([0, 0, 0], [0.35, 0.35, 0.08]))
        self.assertIsNone(bulk_grasp_reason(plate, [object()]))
        self.assertIsNone(bulk_grasp_reason(plate, []))


if __name__ == '__main__':
    unittest.main()
