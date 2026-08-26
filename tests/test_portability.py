import unittest


class TestCorePackage(unittest.TestCase):
    def test_core_package_importable(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import core
        assert core.__version__.startswith("3.")


if __name__ == "__main__":
    unittest.main()
