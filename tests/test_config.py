"""User settings (F1, D34): the file over the defaults, and a bad file never
stops the app."""

from pathlib import Path

import pytest

from quant_hub.config import (
    SETTINGS_ENV,
    TEMPLATE,
    AppConfig,
    default_settings_path,
    load_config,
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_missing_file_is_written_from_the_template_and_means_the_defaults(tmp_path):
    path = tmp_path / "new" / "settings.toml"
    config, problems = load_config(path)
    assert config == AppConfig() and problems == []
    assert path.read_text(encoding="utf-8") == TEMPLATE
    # The template is itself a valid file of the defaults.
    assert load_config(path) == (AppConfig(), [])


def test_settings_override_the_defaults(tmp_path):
    path = write(tmp_path, """
[store]
path = "data/store.db"

[ibkr]
enabled = false
host = "10.0.0.5"
port = 7497
client_id = 3
""")
    config, problems = load_config(path)
    assert problems == []
    assert config.db_path == (tmp_path / "data" / "store.db").resolve()   # relative to the file
    assert (config.ibkr_enabled, config.ibkr_host, config.ibkr_port, config.ibkr_client_id) == (
        False, "10.0.0.5", 7497, 3)
    assert config.quote_timeout_s == AppConfig().quote_timeout_s           # untouched defaults


def test_home_is_expanded_in_the_store_path(tmp_path):
    config, _ = load_config(write(tmp_path, '[store]\npath = "~/qh/x.db"\n'))
    assert config.db_path == Path.home() / "qh" / "x.db"


@pytest.mark.parametrize("text, fragment", [
    ('[ibkr]\nport = "7496"\n', "port must be a whole number"),
    ("[ibkr]\nport = 70000\n", "port must be a whole number"),
    ("[ibkr]\nport = true\n", "port must be a whole number"),
    ('[ibkr]\nenabled = "yes"\n', "enabled must be true or false"),
    ("[ibkr]\nclient_id = -1\n", "client_id must be a whole number"),
    ('[ibkr]\nhost = ""\n', "host must be a host name"),
    ("[store]\npath = 5\n", "path must be a path"),
    ("[ibkr]\nprot = 7497\n", "unknown setting 'prot'"),
    ("[brokers]\nport = 7497\n", "unknown section [brokers]"),
])
def test_each_problem_is_reported_and_keeps_the_default(tmp_path, text, fragment):
    config, problems = load_config(write(tmp_path, text))
    assert config == AppConfig()
    assert len(problems) == 1 and fragment in problems[0]


def test_good_settings_survive_a_bad_neighbour(tmp_path):
    config, problems = load_config(write(tmp_path, "[ibkr]\nport = 7497\nclient_id = -2\n"))
    assert config.ibkr_port == 7497 and config.ibkr_client_id == AppConfig().ibkr_client_id
    assert len(problems) == 1


def test_a_file_that_isnt_toml_means_every_default(tmp_path):
    config, problems = load_config(write(tmp_path, "[ibkr\nport = \n"))
    assert config == AppConfig()
    assert len(problems) == 1 and "using every default" in problems[0]


def test_the_environment_can_name_another_file(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ENV, str(tmp_path / "paper.toml"))
    assert default_settings_path() == tmp_path / "paper.toml"
    monkeypatch.delenv(SETTINGS_ENV)
    assert default_settings_path() == Path.home() / ".quant_hub" / "settings.toml"
