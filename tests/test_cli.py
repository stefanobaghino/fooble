import json
from pathlib import Path

import pytest

from fooble.cli import main


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: fooble" in out
    assert "crawl" in out
    assert "extract" in out


def test_extract_writes_recipe_json(tmp_path: Path, fixtures: Path):
    (tmp_path / "html").mkdir()
    (tmp_path / "html" / "1001.html").write_bytes((fixtures / "1001.html").read_bytes())
    assert main(["--data-dir", str(tmp_path), "extract"]) == 0
    assert json.loads((tmp_path / "recipes" / "1001.json").read_text())["id"] == 1001


def test_extract_reports_failures(tmp_path: Path):
    (tmp_path / "html").mkdir()
    (tmp_path / "html" / "1.html").write_text("<html></html>")
    assert main(["--data-dir", str(tmp_path), "extract"]) == 1
