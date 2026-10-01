from typer.testing import CliRunner

from app import __version__
from app.cli.main import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_outputs_json():
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert '"env": "test"' in result.output
