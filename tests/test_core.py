"""Numpy ML primitives."""
import unittest

import numpy as np

from vdi.models import core


class TestCore(unittest.TestCase):
    def test_ridge_recovers_coefficients(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(2000, 3))
        y = 2.0 + X @ np.array([1.5, -2.0, 0.5]) + rng.normal(0, 0.01, 2000)
        b = core.ridge_fit(X, y, alpha=1e-6)
        np.testing.assert_allclose(b, [2.0, 1.5, -2.0, 0.5], atol=0.01)

    def test_logistic_and_auc(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(4000, 2)).astype(np.float32)
        y = (X[:, 0] + 0.5 * X[:, 1] + rng.normal(0, 0.3, 4000) > 0).astype(float)
        w, b = core.logistic_fit(X, y, iters=300, lr=0.05)
        p = core.sigmoid(X @ w + b)
        self.assertGreater(core.auc(y, p), 0.95)
        self.assertAlmostEqual(core.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)
        self.assertAlmostEqual(core.auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]), 0.5)

    def test_isotonic_is_monotone(self):
        rng = np.random.default_rng(2)
        x = rng.uniform(0, 100, 5000)
        y = 0.01 * x ** 2 + rng.normal(0, 10, 5000)
        kx, ky = core.isotonic(x, y, increasing=True)
        self.assertTrue(np.all(np.diff(ky) >= -1e-9))
        kx, ky = core.isotonic(x, -y, increasing=False)
        self.assertTrue(np.all(np.diff(ky) <= 1e-9))

    def test_psi(self):
        rng = np.random.default_rng(3)
        a = rng.normal(size=5000)
        self.assertLess(core.psi(a, rng.normal(size=5000)), 0.02)
        self.assertGreater(core.psi(a, rng.normal(1.0, 1, 5000)), 0.25)

    def test_mape_and_precision_recall(self):
        self.assertAlmostEqual(core.mape([100, 50], [110, 45]), 10.0)
        p, r, f1 = core.precision_recall([1, 1, 0, 0], [0.9, 0.2, 0.8, 0.1], 0.5)
        self.assertEqual((p, r), (0.5, 0.5))


if __name__ == "__main__":
    unittest.main()
