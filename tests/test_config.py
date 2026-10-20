from pathlib import Path

import pytest

from devcontrol import parse_version
from devcontrol.config import (
    Config,
    ConfigError,
    ensure_config_file,
    format_server_address,
    load_config,
    parse_server_address,
    set_values,
    validate_private_network,
)
from devcontrol.paths import Layout, default_layout, is_synced_folder


def test_parse_server_address():
    assert parse_server_address("100.64.1.2") == ("100.64.1.2", 8765)
    assert parse_server_address("pc-ana:9000") == ("pc-ana", 9000)
    assert parse_server_address("[fd7a:115c::1]:9001") == ("fd7a:115c::1", 9001)
    for bad in ("", "a b", "host:99999", "host:abc"):
        with pytest.raises(ConfigError):
            parse_server_address(bad)
    assert format_server_address("10.0.0.5", 8765) == "10.0.0.5"
    assert format_server_address("10.0.0.5", 9000) == "10.0.0.5:9000"


def test_only_private_networks():
    assert validate_private_network("100.64.0.0/10") == "100.64.0.0/10"
    assert validate_private_network("192.168.1.0/24") == "192.168.1.0/24"
    with pytest.raises(ConfigError):
        validate_private_network("8.8.8.0/24")
    cfg = Config()
    assert cfg.is_allowed_client("127.0.0.1")
    assert cfg.is_allowed_client("100.101.102.103")
    assert cfg.is_allowed_client("::ffff:192.168.1.20")
    assert not cfg.is_allowed_client("8.8.8.8")
    cfg.allowed_clients = ["100.1.1.1"]
    assert not cfg.is_allowed_client("100.1.1.2")


def test_data_lives_outside_program(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVCONTROL_HOME", str(tmp_path))
    layout = default_layout()
    assert layout.docs_root == tmp_path / "Documentos"
    layout.ensure()
    assert layout.backups_dir.is_dir() and layout.readme_file.exists()
    program = Path(__file__).resolve().parent.parent
    assert program not in layout.server_dir.parents
    assert is_synced_folder(Path("C:/Users/ana/OneDrive/Documentos"))


def test_installer_values_are_applied_once(tmp_path):
    layout = Layout(tmp_path / "D", tmp_path / "L")
    layout.local_root.mkdir(parents=True)
    layout.install_file.write_text("[instalacion]\nrol = miembro\nservidor = 100.64.0.9\naplicar = si\n", encoding="utf-8")
    cfg = load_config(layout)
    assert cfg.role == "miembro" and cfg.default_server == "100.64.0.9"
    assert cfg.default_server_address() == ("100.64.0.9", 8765)
    # Un cambio hecho a mano en config.ini no lo pisa el siguiente arranque
    set_values(layout.config_file, {("cliente", "servidor"): "100.64.0.10"})
    assert load_config(layout).default_server == "100.64.0.10"
    # Una reinstalación interactiva sí lo aplica
    layout.install_file.write_text("[instalacion]\nrol = anfitrion\nservidor =\naplicar = si\n", encoding="utf-8")
    ensure_config_file(layout)
    cfg = load_config(layout)
    assert cfg.is_host and cfg.default_server_address() == ("127.0.0.1", 8765)


def test_config_errors_are_clear(tmp_path):
    layout = Layout(tmp_path / "D", tmp_path / "L").ensure()
    ensure_config_file(layout)
    set_values(layout.config_file, {("servidor", "escuchar"): "8.8.8.8"})
    with pytest.raises(ConfigError, match="pública"):
        load_config(layout)


def test_parse_version():
    assert parse_version("2.10.0") > parse_version("2.9.9")
    assert parse_version("v2.0.1") == (2, 0, 1)
