import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "calculate_taxes.py"


def load_module():
    spec = importlib.util.spec_from_file_location("calculate_taxes", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CalculateTaxesTests(unittest.TestCase):
    def test_module_can_be_imported_without_reading_input_file(self) -> None:
        module = load_module()
        self.assertTrue(callable(module.main))

    def test_module_exposes_package_cli_entrypoint(self) -> None:
        module = load_module()
        from coinbase_tax_calculator.cli import main as package_main

        self.assertIs(module.main, package_main)


if __name__ == "__main__":
    unittest.main()
