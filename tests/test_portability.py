import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestCorePackage(unittest.TestCase):
    def test_core_package_importable(self):
        # Load via file location: lib/core.py can shadow the core/ package
        # when lib/ precedes the repo root on sys.path (see test_contracts).
        import importlib.util
        pkg_init = REPO / "core" / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            "sundial_v3_core_pkg", pkg_init,
            submodule_search_locations=[str(REPO / "core")])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.__version__.startswith("3.")


if __name__ == "__main__":
    unittest.main()
