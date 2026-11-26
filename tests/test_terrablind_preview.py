"""Check offline geometric facts and preserve the boundary between facts and control."""
import copy
import json
import unittest

from isaac_jev.mock import sample_frame
from scripts.preview_terrablind_transfer import attack_relations, motion_relation, preview, threat_relations


def scene():
    frame = sample_frame(10)
    frame["player"]["position"] = {"x": 100, "y": 100}
    frame["player"]["velocity"] = {"x": 0, "y": 0}
    frame["player"]["size"] = 10
    frame["entities"] = [{"uid": "target", "kind": "enemy", "type": 244, "variant": 0,
                          "position": {"x": 300, "y": 100}, "velocity": {"x": -2, "y": 0},
                          "size": 10, "hp": 10, "max_hp": 10, "vulnerable": True}]
    return frame


class OfflineTransferTests(unittest.TestCase):
    def test_approach_reports_first_entry_instead_of_closest_time(self):
        relation = motion_relation((30, 0), (-2, 0), 10, horizon=20)
        self.assertEqual(relation["first_contact_frames_estimate"], 10)
        self.assertEqual(relation["closest_at_frames_estimate"], 15)

    def test_crossing_axis_projections_do_not_imply_contact(self):
        relation = motion_relation((10, 0), (-1, 2), 5, horizon=10)
        self.assertIsNone(relation["first_contact_frames_estimate"])
        self.assertGreater(relation["minimum_gap_px_estimate"], 0)

    def test_equal_velocity_and_tangent_are_defined(self):
        self.assertIsNone(motion_relation((20, 0), (0, 0), 5)["first_contact_frames_estimate"])
        self.assertEqual(motion_relation((10, 5), (-1, 0), 5, horizon=12)["first_contact_frames_estimate"], 10)

    def test_relative_motion_is_translation_invariant(self):
        frame = scene()
        translated = copy.deepcopy(frame)
        for item in [translated["player"], *translated["entities"]]:
            item["position"]["x"] += 1000
            item["position"]["y"] -= 300
        self.assertEqual(threat_relations(frame), threat_relations(translated))

    def test_missing_velocity_stays_unknown(self):
        frame = scene()
        frame["entities"][0].pop("velocity")
        self.assertIsNone(threat_relations(frame)["shown"][0]["motion_estimate"])

    def test_pit_geometry_does_not_become_a_guaranteed_attack_blocker(self):
        frame = scene()
        frame["room"]["grid"] = [{"index": 1, "kind": "pit", "collision": 1,
                                   "position": {"x": 200, "y": 100}}]
        target = attack_relations(frame)["targets"][0]
        self.assertEqual(target["intervening_grid_geometry"][0]["kind"], "pit")
        self.assertIsNone(target["can_hit_now"])

    def test_cleared_room_fire_remains_a_fact_without_selecting_an_action(self):
        frame = scene()
        frame["room"]["clear"] = True
        frame["entities"][0].update(type=33, active_enemy=False, vulnerable=False, collision_class=4)
        result = preview(frame)
        self.assertEqual(result["state"]["immediate_threats"]["shown"][0]["semantic_kind"], "live_fireplace_contact_hazard")
        self.assertEqual(len(result["state"]["movement_options"]), 9)
        self.assertNotIn("recommended_action", result)

    def test_future_labels_and_controls_do_not_enter_preview_or_mutate_source(self):
        frame = scene()
        frame["outcome"] = {"label": "future_only_marker"}
        frame["human_input"] = {"move_x": 1, "move_y": 1, "future_only_marker": True}
        original = copy.deepcopy(frame)
        result = preview(frame)
        state_text = json.dumps(result["state"])
        self.assertNotIn("future_only_marker", state_text)
        self.assertNotIn("Desired outcome condition", state_text)
        for key in ("previous_controls", "human_input", "use_active"):
            self.assertNotIn('"' + key + '"', state_text)
        self.assertEqual(frame, original)
        self.assertFalse(result["audit"]["deployable_contract"])


if __name__ == "__main__":
    unittest.main()
