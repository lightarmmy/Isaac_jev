import unittest
from isaac_jev.pickups import acquisition_confirmed, pickup_consumed, pickup_available


class PickupEvidenceTests(unittest.TestCase):
    def test_health_loss_or_unrelated_resource_does_not_prove_collection(self):
        before={"health":{"red":6},"inventory":{"keys":0,"coins":0}}
        after={"health":{"red":5},"inventory":{"keys":1,"coins":0}}
        self.assertFalse(acquisition_confirmed({"pickup":{"kind":"heart"}},before,after))
        self.assertFalse(acquisition_confirmed({"pickup":{"kind":"coin"}},before,after))
        self.assertTrue(acquisition_confirmed({"pickup":{"kind":"key"}},before,after))

    def test_duplicate_collectible_requires_count_increase(self):
        target={"pickup":{"kind":"collectible","item_id":379}}
        before={"inventory":{"collectibles":[{"id":379,"count":1}]}}
        self.assertFalse(acquisition_confirmed(target,before,before))
        self.assertTrue(acquisition_confirmed(target,before,{"inventory":{"collectibles":[{"id":379,"count":2}]}}))

    def test_blind_item_evidence_uses_owned_gain_not_timer_charge(self):
        target={"pickup":{"kind":"collectible","item_id":None}}
        before={"active":{"id":45,"charge":1}}
        self.assertFalse(acquisition_confirmed(target,before,{"active":{"id":45,"charge":2}}))
        self.assertTrue(acquisition_confirmed(target,before,{"active":{"id":58,"charge":2}}))
        self.assertTrue(pickup_consumed(target,{"kind":"pickup","variant":100,"subtype":45},before,{"active":{"id":58}}))

    def test_only_empty_collectible_pedestals_are_filtered(self):
        self.assertFalse(pickup_available({"kind":"pickup","variant":100,"subtype":0}))
        self.assertTrue(pickup_available({"kind":"pickup","variant":20,"subtype":0}))
        self.assertTrue(pickup_available({"kind":"pickup","variant":100,"subtype":None,"pickup_blind":True}))
        self.assertTrue(pickup_consumed({"pickup":{"item_id":58}},{"kind":"pickup","variant":100,"subtype":45}))
