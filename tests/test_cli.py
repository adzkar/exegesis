"""CLI surface tests — flags and package version agreement."""
import pytest

import exegesis
from exegesis.cli import main


def test_version_flag(capsys):
    """--version prints the package __version__ and exits cleanly."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert exegesis.__version__ in capsys.readouterr().out
