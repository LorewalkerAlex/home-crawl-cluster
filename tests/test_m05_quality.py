import unittest

from apps.proxypool.service.quality import wilson_preview


class CandidateScoreTests(unittest.TestCase):
    def test_documented_reference_values_and_no_evidence(self):
        self.assertIsNone(wilson_preview(0, 0))
        self.assertAlmostEqual(wilson_preview(1, 1), 20.654329, places=5)
        self.assertAlmostEqual(wilson_preview(10, 10), 72.245984, places=5)
        self.assertAlmostEqual(wilson_preview(49, 50), 89.50, places=2)
        self.assertEqual(wilson_preview(0, 10), 0)

    def test_more_evidence_and_more_failures_have_the_expected_effect(self):
        self.assertGreater(wilson_preview(10, 10), wilson_preview(1, 1))
        self.assertLess(wilson_preview(9, 10), wilson_preview(10, 10))
        for values in [(-1, 3), (4, 3), (True, 1), (1, -1)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                wilson_preview(*values)


if __name__ == "__main__":
    unittest.main()
