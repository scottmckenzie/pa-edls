import json

import pytest

from pa_edl.writer import MANIFEST, Output, SafetyError, normalise, write_outputs


def test_normalise_collapses_sorts_and_dedupes():
    raw = ["20.0.1.0/24", "20.0.0.0/24", "20.0.0.0/24", "2603:1000::/33", "2603:1000:8000::/33", "20.0.0.5/32"]
    assert normalise(raw, ("ipv4", "ipv6")) == {"ipv4": ["20.0.0.0/23"], "ipv6": ["2603:1000::/32"]}


def test_normalise_filters_versions():
    raw = ["20.0.0.0/24", "2603:1000::/32"]
    assert normalise(raw, ("ipv4",)) == {"ipv4": ["20.0.0.0/24"]}
    assert normalise(raw, ("ipv6",)) == {"ipv6": ["2603:1000::/32"]}
    assert normalise(["20.0.0.0/24"], ("ipv4", "ipv6")) == {"ipv4": ["20.0.0.0/24"], "ipv6": []}


def test_normalise_rejects_garbage():
    with pytest.raises(SafetyError):
        normalise(["not-an-ip"], ("ipv4",))


def test_write_add_update_remove_and_stable_manifest(tmp_path):
    out = tmp_path / "edls"
    first = [
        Output("azure/storage/eastus.txt", ["10.0.0.0/24"], "s"),
        Output("azure/storage/old.txt", ["10.1.0.0/24"], "s"),
    ]
    changes = write_outputs(first, out)
    assert changes["added"] == ["azure/storage/eastus.txt", "azure/storage/old.txt"]
    assert (out / "azure/storage/eastus.txt").read_text() == "10.0.0.0/24\n"
    stamp = json.loads((out / MANIFEST).read_text())["generated_at"]

    # Identical rerun: nothing changes, including the manifest timestamp.
    assert not any(write_outputs(first, out).values())
    assert json.loads((out / MANIFEST).read_text())["generated_at"] == stamp

    second = [Output("azure/storage/eastus.txt", ["10.0.0.0/23"], "s")]
    changes = write_outputs(second, out)
    assert changes == {"added": [], "updated": ["azure/storage/eastus.txt"], "removed": ["azure/storage/old.txt"]}
    assert not (out / "azure/storage/old.txt").exists()


def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "edls"
    changes = write_outputs([Output("a/b/c.txt", ["10.0.0.0/24"], "s")], out, dry_run=True)
    assert changes["added"] == ["a/b/c.txt"]
    assert not out.exists()


def test_directory_readmes(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    out = tmp_path / "edls"
    write_outputs(
        [
            Output("azure/public/storage/ipv4/eastus.txt", ["10.0.0.0/24"], "s"),
            Output("azure/public/storage/ipv6/eastus.txt", [], "s"),
        ],
        out,
    )
    root = (out / "README.md").read_text()
    assert "[azure/](azure/README.md)" in root
    assert "[Up]" not in root
    storage = (out / "azure/public/storage/README.md").read_text()
    assert "[ipv4/](ipv4/README.md)" in storage and "[ipv6/](ipv6/README.md)" in storage
    assert "## Lists" not in storage
    ipv4 = (out / "azure/public/storage/ipv4/README.md").read_text()
    assert "| [eastus.txt](eastus.txt) | 1 |" in ipv4
    assert "Raw URL" not in ipv4
    assert "| [eastus.txt](eastus.txt) | 0 |" in (out / "azure/public/storage/ipv6/README.md").read_text()

    # Dropping a directory removes its README and the directory itself.
    write_outputs([Output("azure/public/storage/ipv4/eastus.txt", ["10.0.0.0/24"], "s")], out)
    assert not (out / "azure/public/storage/ipv6").exists()
    assert "ipv6" not in (out / "azure/public/storage/README.md").read_text()


def test_directory_readme_raw_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/edls")
    monkeypatch.setenv("EDL_BRANCH", "main")
    out = tmp_path / "edls"
    write_outputs([Output("azure/public/storage/ipv4/eastus.txt", ["10.0.0.0/24"], "s")], out)
    ipv4 = (out / "azure/public/storage/ipv4/README.md").read_text()
    url = "https://raw.githubusercontent.com/acme/edls/main/edls/azure/public/storage/ipv4/eastus.txt"
    assert f"| [eastus.txt](eastus.txt) | 1 | {url} |" in ipv4


def test_directory_readme_change_column(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/edls")
    monkeypatch.setenv("EDL_BRANCH", "main")
    out = tmp_path / "edls"
    write_outputs(
        [
            Output("a/ipv4/eastus.txt", ["20.0.0.0/24"], "s", "61"),
            Output("a/ipv4/westus.txt", ["20.0.1.0/24"], "s"),  # provider gave no change number
            Output("b/ipv4/x.txt", ["20.0.2.0/24"], "s"),
        ],
        out,
    )
    a = (out / "a/ipv4/README.md").read_text()
    assert "| List | Entries | Change number | Raw URL |" in a
    assert "| [eastus.txt](eastus.txt) | 1 | 61 | https://" in a
    assert "| [westus.txt](westus.txt) | 1 |  | https://" in a
    # No list in the directory has a change number: no column.
    assert "Change number" not in (out / "b/ipv4/README.md").read_text()
