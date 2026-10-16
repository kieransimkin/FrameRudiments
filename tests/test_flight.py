import unittest

from framerudiments.flight import simulate_stall


class FlightModelTests(unittest.TestCase):
    def test_stall_and_recovery_are_observable(self):
        samples = simulate_stall()
        states = [state for _, state, _, _, _ in samples]
        self.assertIn("climb", states)
        self.assertIn("mush", states)
        self.assertIn("stall", states)
        self.assertIn("drop", states)
        self.assertIn("recovery", states)
        self.assertIn("swoop", states)
        stall_speeds = [speed for _, state, speed, _, _ in samples if state == "stall"]
        recovery_speeds = [speed for _, state, speed, _, _ in samples if state == "recovery"]
        self.assertTrue(stall_speeds and recovery_speeds)
        self.assertGreater(max(recovery_speeds), min(stall_speeds))

    def test_stall_has_nose_high_pause_then_substantial_drop(self):
        samples = simulate_stall()
        stall = [(y, pitch) for _, state, _, y, pitch in samples if state == "stall"]
        drop = [(y, pitch) for _, state, _, y, pitch in samples if state == "drop"]
        self.assertLess(min(pitch for _, pitch in stall), 0.0)
        self.assertGreater(max(pitch for _, pitch in drop), 0.0)
        self.assertGreater(max(y for y, _ in drop) - min(y for y, _ in stall), 0.10)


if __name__ == "__main__":
    unittest.main()
