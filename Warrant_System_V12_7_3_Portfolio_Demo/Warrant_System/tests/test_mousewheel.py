import unittest
import wms_wheel as W


class Wheel(unittest.TestCase):
    def test_steps(self):
        self.assertEqual([W.wheel_steps(d) for d in (120, -120, 240, 30, -30, 0)], [1, -1, 2, 1, -1, 0])
        self.assertEqual(W.wheel_steps(0, 4), 1)
        self.assertEqual(W.wheel_steps(0, 5), -1)


if __name__ == "__main__":
    unittest.main()
