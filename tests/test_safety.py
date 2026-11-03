import pytest

from pa_edl.writer import Output, SafetyError, check_changes, normalise, validate_outputs, write_outputs

V = ("ipv4", "ipv6")


def test_normalise_accepts_public_ranges():
    assert normalise(["20.38.96.0/23", "20.38.98.0/23", "2603:1030:209::/48"], V) == {
        "ipv4": ["20.38.96.0/22"],
        "ipv6": ["2603:1030:209::/48"],
    }


def test_normalise_rejects_host_bits():
    with pytest.raises(SafetyError, match="invalid prefix"):
        normalise(["20.1.2.3/24"], V)


def test_normalise_rejects_garbage():
    with pytest.raises(SafetyError, match="invalid prefix"):
        normalise(["not-an-ip"], V)


@pytest.mark.parametrize("prefix", ["0.0.0.0/0", "20.0.0.0/7", "::/0", "2000::/15"])
def test_normalise_rejects_too_broad(prefix):
    with pytest.raises(SafetyError, match="broader than"):
        normalise([prefix], V)


def test_normalise_min_prefixlen_is_configurable():
    with pytest.raises(SafetyError, match="broader than"):
        normalise(["20.38.96.0/23"], V, {"ipv4": 24, "ipv6": 48})
    assert normalise(["20.38.96.0/24"], V, {"ipv4": 24, "ipv6": 48}) == {"ipv4": ["20.38.96.0/24"], "ipv6": []}


@pytest.mark.parametrize(
    "prefix",
    [
        "10.1.0.0/16",
        "192.168.1.0/24",
        "127.0.0.1/32",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "224.0.0.0/8",
        "240.0.0.0/8",
        "172.0.0.0/8",
    ],
)
def test_normalise_rejects_ipv4_special_purpose(prefix):
    with pytest.raises(SafetyError, match="special-purpose"):
        normalise([prefix], V)


@pytest.mark.parametrize("prefix", ["fd00::/16", "fe80::/64", "::1/128", "ff02::/16"])
def test_normalise_rejects_ipv6_outside_global_unicast(prefix):
    with pytest.raises(SafetyError, match="global unicast"):
        normalise([prefix], V)


def test_normalise_rejects_ipv6_documentation():
    with pytest.raises(SafetyError, match="special-purpose"):
        normalise(["2001:db8::/32"], V)


@pytest.mark.parametrize(
    "relpath",
    [
        "azure/public/storage/ipv4/../../../x.txt",
        "azure/public/storage/ipv4/EastUS.txt",
        "azure/public/storage/ipv4/a b.txt",
        "azure/public/storage/ipv4/x](https:.txt",
        "azure/public/storage/ipv4/.hidden.txt",
    ],
)
def test_validate_rejects_unsafe_segments(relpath, tmp_path):
    with pytest.raises(SafetyError, match="unsafe path segment"):
        validate_outputs([Output(relpath, [], "s")], tmp_path)


def test_validate_rejects_duplicates(tmp_path):
    with pytest.raises(SafetyError, match="both be written"):
        validate_outputs(
            [
                Output("a/b.txt", [], "s"),
                Output("a/b.txt", [], "s"),
            ],
            tmp_path,
        )


def test_validate_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "edls"
    out.mkdir()
    (out / "a").symlink_to(outside)
    with pytest.raises(SafetyError, match="resolves outside"):
        validate_outputs([Output("a/x.txt", [], "s")], out)


def test_write_outputs_validates_before_writing(tmp_path):
    out = tmp_path / "edls"
    with pytest.raises(SafetyError):
        write_outputs(
            [
                Output("ok/x.txt", ["20.0.0.0/24"], "s"),
                Output("bad/../../y.txt", [], "s"),
            ],
            out,
        )
    assert not out.exists()


def test_check_changes_growth(tmp_path):
    out = tmp_path / "a" / "b.txt"
    out.parent.mkdir(parents=True)
    out.write_text("20.0.0.0/24\n")
    assert check_changes([Output("a/b.txt", ["20.0.0.0/23"], "s")], out.parent.parent, 0.5, 2.0) == []
    problems = check_changes([Output("a/b.txt", ["20.0.0.0/22"], "s")], out.parent.parent, 0.5, 2.0)
    assert len(problems) == 1 and "grew" in problems[0]


def test_check_changes_shrink(tmp_path):
    out = tmp_path / "a" / "b.txt"
    out.parent.mkdir(parents=True)
    out.write_text("\n".join(f"20.0.{i}.0/24" for i in range(0, 20, 2)) + "\n")
    assert (
        check_changes(
            [Output("a/b.txt", [f"20.0.{i}.0/24" for i in range(0, 12, 2)], "s")], out.parent.parent, 0.5, 2.0
        )
        == []
    )
    problems = check_changes(
        [Output("a/b.txt", [f"20.0.{i}.0/24" for i in range(0, 8, 2)], "s")], out.parent.parent, 0.5, 2.0
    )
    assert problems == ["a/b.txt: shrank from 10 to 4 entries"]


def test_check_changes_skips_new_and_empty(tmp_path):
    out = tmp_path / "a" / "empty.txt"
    out.parent.mkdir(parents=True)
    out.write_text("")
    assert (
        check_changes(
            [
                Output("a/empty.txt", ["20.0.0.0/16"], "s"),
                Output("a/new.txt", ["20.0.0.0/16"], "s"),
            ],
            out.parent.parent,
            0.5,
            2.0,
        )
        == []
    )
