from pathlib import Path
import tomllib


def test_projectm_extra_includes_numpy_for_pyopengl_array_handler():
    metadata = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    dependencies = metadata["project"]["optional-dependencies"]["projectm"]
    assert any(item.startswith("numpy") for item in dependencies)
