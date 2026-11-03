import pytest

from pa_edl.cli import build_outputs, main
from pa_edl.config import load_config
from pa_edl.providers import ConfigError


def write_config(tmp_path, body):
    path = tmp_path / "edls.toml"
    path.write_text('output_dir = "out"\n' + body)
    return path


def test_build_outputs_end_to_end(tmp_path, azure_fetcher):
    cfg = load_config(
        write_config(
            tmp_path, '[[edl]]\nprovider = "azure"\nname = "storage"\nservice = "Storage"\nip_versions = ["ipv4"]\n'
        )
    )
    outputs = {o.relpath: o.entries for o in build_outputs(cfg, azure_fetcher)}
    assert outputs["azure/public/storage/ipv4/eastus.txt"] == ["20.38.96.0/22", "40.71.10.16/28"]
    assert cfg.output_dir == tmp_path / "out"


def test_min_entries_enforced(tmp_path, azure_fetcher):
    cfg = load_config(
        write_config(tmp_path, '[[edl]]\nprovider = "azure"\nname = "s"\nservice = "Storage"\nmin_entries = 100\n')
    )
    with pytest.raises(ConfigError, match="min_entries"):
        build_outputs(cfg, azure_fetcher)


@pytest.mark.parametrize(
    "body, match",
    [
        ('[[edl]]\nprovider = "nope"\nname = "x"\n', "unknown provider"),
        ('[[edl]]\nprovider = "azure"\nname = "Bad Name"\n', "name"),
        ('[[edl]]\nprovider = "azure"\nname = "x"\nip_versions = ["ipv5"]\n', "ip_versions"),
        ('[[edl]]\nprovider = "azure"\nname = "x"\n[[edl]]\nprovider = "azure"\nname = "x"\n', "duplicate"),
        ("", "no \\[\\[edl\\]\\]"),
    ],
)
def test_config_validation(tmp_path, body, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write_config(tmp_path, body))


@pytest.fixture
def cli(monkeypatch, tmp_path, azure_fetcher):
    """Run `pa-edl` against the fixture feed, with output under tmp_path/out."""
    monkeypatch.setattr("pa_edl.cli.Fetcher", lambda: azure_fetcher)
    config = write_config(tmp_path, '[[edl]]\nprovider = "azure"\nname = "storage"\nservice = "Storage"\n')

    def run(*args):
        return main(["generate", "-c", str(config), *args])

    return run


def test_generate_writes_and_is_idempotent(cli, tmp_path, capsys):
    assert cli("--dry-run") == 0
    assert not (tmp_path / "out").exists()
    assert cli() == 0
    assert (tmp_path / "out/azure/public/storage/ipv4/eastus.txt").exists()
    assert cli() == 0
    assert "no changes" in capsys.readouterr().out


def test_generate_refuses_large_shrink(cli, tmp_path, capsys):
    assert cli() == 0
    eastus = tmp_path / "out/azure/public/storage/ipv4/eastus.txt"
    eastus.write_text("".join(f"10.{i}.0.0/16\n" for i in range(50)))
    assert cli() == 2
    assert "refusing to write" in capsys.readouterr().err
    assert cli("--force") == 0
    assert "10.0.0.0/16" not in eastus.read_text()


def test_config_error_exit_code(tmp_path, capsys):
    assert main(["generate", "-c", str(write_config(tmp_path, ""))]) == 1
    assert "config error" in capsys.readouterr().err


def test_providers_command(capsys):
    assert main(["providers"]) == 0
    assert "azure" in capsys.readouterr().out


def test_lists_split_by_ip_version(tmp_path, azure_fetcher):
    cfg = load_config(write_config(tmp_path, '[[edl]]\nprovider = "azure"\nname = "storage"\nservice = "Storage"\n'))
    outputs = {o.relpath: o.entries for o in build_outputs(cfg, azure_fetcher)}
    base = "azure/public/storage"
    assert outputs[f"{base}/ipv4/eastus.txt"] == ["20.38.96.0/22", "40.71.10.16/28"]
    assert outputs[f"{base}/ipv6/eastus.txt"] == ["2603:1030:209::/48"]
    # westeurope has no IPv6 prefixes; its ipv6 list still exists (empty) so the URL is stable.
    assert outputs[f"{base}/ipv6/westeurope.txt"] == []
    assert f"{base}/ipv4/_all.txt" in outputs


def test_cloud_in_path(tmp_path, azure_fetcher):
    cfg = load_config(write_config(tmp_path, '[[edl]]\nprovider = "azure"\nname = "x"\ncloud = "usgov"\n'))
    assert cfg.edls[0].path == "azure/usgov/x"
