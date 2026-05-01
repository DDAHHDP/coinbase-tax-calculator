import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


class PackagingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        with PYPROJECT_PATH.open("rb") as pyproject_file:
            self.pyproject = tomllib.load(pyproject_file)

    def test_runtime_dependencies_support_python_312(self) -> None:
        poetry_config = self.pyproject["tool"]["poetry"]
        runtime_dependencies = poetry_config["dependencies"]
        self.assertFalse(poetry_config["package-mode"])
        self.assertEqual(runtime_dependencies["python"], ">=3.10,<4.0")
        self.assertEqual(set(runtime_dependencies), {"python"})

    def test_jupyterlab_is_not_a_runtime_dependency(self) -> None:
        runtime_dependencies = self.pyproject["tool"]["poetry"]["dependencies"]
        self.assertNotIn("jupyterlab", runtime_dependencies)

        dev_dependencies = (
            self.pyproject["tool"]["poetry"]["group"]["dev"]["dependencies"]
        )
        self.assertTrue(self.pyproject["tool"]["poetry"]["group"]["dev"]["optional"])
        self.assertEqual(dev_dependencies["jupyterlab"], ">=4.4.0,<5.0")


if __name__ == "__main__":
    unittest.main()
