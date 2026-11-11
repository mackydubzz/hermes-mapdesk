"""Prevent test tooling from becoming an end-user install requirement."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_runtime_root_does_not_declare_a_node_sidecar(self):
        # Hermes treats ANY root package.json as a Node-sidecar declaration,
        # even when it contains only development dependencies.
        self.assertFalse((ROOT / 'package.json').exists(),
                         'Keep test-only Node tooling under dev/, not the install root')
        self.assertTrue((ROOT / 'dev/package.json').is_file())
        self.assertTrue((ROOT / 'dev/package-lock.json').is_file())


if __name__ == '__main__':
    unittest.main()
