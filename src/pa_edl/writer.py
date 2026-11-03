"""Normalise lists and write them to the output directory.

The output directory is owned by the generator: files not produced by the
current run are removed, so renamed or deleted EDLs don't linger. Files are
only rewritten when their content changes, and the manifest timestamp only
moves when something else changed, so a no-op run yields a clean git tree.
"""

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = "manifest.json"
INDEX = "README.md"

# Shortest prefix length accepted by default. Upstream feeds publish /16 IPv4 and
# /47 IPv6 at their broadest; anything far shorter is almost certainly bad data
# and would open (or close) a huge range on the firewall.
DEFAULT_MIN_PREFIXLEN = {"ipv4": 8, "ipv6": 16}

# Ranges that never belong in a public cloud provider's address list: the IANA
# special-purpose registries (RFC 6890 and later: private, shared, documentation,
# benchmarking, 6to4 relay, AS112, AMT, reserved) plus multicast. Some of these
# are globally reachable (AS112, AMT) but are anycast services no cloud provider
# owns. A prefix that overlaps any of these is rejected, which also catches broad
# super-nets that merely contain one.
IPV4_BOGONS = [
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",        # "this network"
        "10.0.0.0/8",       # private
        "100.64.0.0/10",    # shared address space (CGNAT)
        "127.0.0.0/8",      # loopback
        "169.254.0.0/16",   # link-local
        "172.16.0.0/12",    # private
        "192.0.0.0/24",     # IETF protocol assignments
        "192.0.2.0/24",     # documentation (TEST-NET-1)
        "192.31.196.0/24",  # AS112-v4
        "192.52.193.0/24",  # AMT
        "192.88.99.0/24",   # deprecated 6to4 relay anycast
        "192.168.0.0/16",   # private
        "192.175.48.0/24",  # AS112 direct delegation
        "198.18.0.0/15",    # benchmarking
        "198.51.100.0/24",  # documentation (TEST-NET-2)
        "203.0.113.0/24",   # documentation (TEST-NET-3)
        "224.0.0.0/4",      # multicast
        "240.0.0.0/4",      # reserved, including broadcast
    )
]  # fmt: skip
# Only global unicast (2000::/3) is accepted for IPv6, minus these.
IPV6_GLOBAL_UNICAST = ipaddress.IPv6Network("2000::/3")
IPV6_BOGONS = [
    ipaddress.IPv6Network(n)
    for n in (
        "2001::/23",          # IETF protocol assignments: Teredo, benchmarking, AMT, AS112, ORCHID, DRIP
        "2001:db8::/32",      # documentation
        "2002::/16",          # 6to4
        "2620:4f:8000::/48",  # AS112 direct delegation
        "3fff::/20",          # documentation
    )
]  # fmt: skip

# Every path segment of an output: lowercase, no leading dot (so no "." or "..").
_SAFE_SEGMENT = re.compile(r"^[a-z0-9_][a-z0-9._-]*$")


class SafetyError(RuntimeError):
    pass


@dataclass
class Output:
    relpath: str  # e.g. "azure/storage/eastus.txt"
    entries: list[str]
    source: str


def _check_prefix(net: ipaddress.IPv4Network | ipaddress.IPv6Network, min_prefixlen: dict[str, int]) -> None:
    version = f"ipv{net.version}"
    if net.prefixlen < min_prefixlen[version]:
        raise SafetyError(f"{net} is broader than the /{min_prefixlen[version]} allowed for {version}")
    if isinstance(net, ipaddress.IPv4Network):
        bogon = next((b for b in IPV4_BOGONS if net.overlaps(b)), None)
    elif not net.subnet_of(IPV6_GLOBAL_UNICAST):
        raise SafetyError(f"{net} is outside IPv6 global unicast space {IPV6_GLOBAL_UNICAST}")
    else:
        bogon = next((b for b in IPV6_BOGONS if net.overlaps(b)), None)
    if bogon is not None:
        raise SafetyError(f"{net} overlaps special-purpose range {bogon}")


def normalise(
    prefixes: list[str],
    ip_versions: tuple[str, ...],
    min_prefixlen: dict[str, int] = DEFAULT_MIN_PREFIXLEN,
) -> dict[str, list[str]]:
    """Validate, split by IP version, collapse overlaps, and sort.

    Each prefix must be an exact network (no host bits set), no broader than
    min_prefixlen, and outside special-purpose ranges; otherwise SafetyError.

    Returns {"ipv4": [...], "ipv6": [...]} restricted to ip_versions. A version
    with no prefixes maps to an empty list so its file (and URL) still exists.
    """
    v4: list[ipaddress.IPv4Network] = []
    v6: list[ipaddress.IPv6Network] = []
    for p in prefixes:
        try:
            net = ipaddress.ip_network(p.strip(), strict=True)
        except ValueError as exc:
            raise SafetyError(f"invalid prefix {p!r}: {exc}") from exc
        _check_prefix(net, min_prefixlen)
        if isinstance(net, ipaddress.IPv4Network):
            v4.append(net)
        else:
            v6.append(net)
    collapsed = {
        "ipv4": [str(n) for n in ipaddress.collapse_addresses(v4)],
        "ipv6": [str(n) for n in ipaddress.collapse_addresses(v6)],
    }
    return {v: collapsed[v] for v in ip_versions}


def _render(entries: list[str]) -> str:
    return "".join(f"{e}\n" for e in entries)


def _existing_lists(output_dir: Path) -> dict[str, Path]:
    if not output_dir.is_dir():
        return {}
    return {p.relative_to(output_dir).as_posix(): p for p in output_dir.rglob("*.txt") if p.is_file()}


def _address_count(entries: list[str]) -> int:
    return sum(ipaddress.ip_network(e).num_addresses for e in entries)


def check_changes(outputs: list[Output], output_dir: Path, max_shrink: float, max_growth: float) -> list[str]:
    """Return problems for existing lists that changed suspiciously in one run.

    A list may lose at most max_shrink of its entries, and its total address
    space may grow at most max_growth times. New or previously empty lists have
    no baseline; they are protected by the per-prefix checks in normalise().
    """
    problems = []
    existing = _existing_lists(output_dir)
    for out in outputs:
        old = existing.get(out.relpath)
        if old is None:
            continue
        old_entries = [line.strip() for line in old.read_text().splitlines() if line.strip()]
        if old_entries and len(out.entries) < len(old_entries) * (1 - max_shrink):
            problems.append(f"{out.relpath}: shrank from {len(old_entries)} to {len(out.entries)} entries")
        old_addrs = _address_count(old_entries)
        new_addrs = _address_count(out.entries)
        if old_addrs and new_addrs > old_addrs * max_growth:
            problems.append(
                f"{out.relpath}: address space grew {new_addrs / old_addrs:.1f}x ({old_addrs} -> {new_addrs})"
            )
    return problems


def _index_dirs(relpaths: list[str]) -> set[str]:
    """Every directory (relative to the output dir, "" is the root) that gets a README."""
    dirs = {""}
    for rel in relpaths:
        parts = rel.split("/")[:-1]
        dirs.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    return dirs


def validate_outputs(outputs: list[Output], output_dir: Path) -> None:
    """Refuse unsafe or colliding output paths before anything is written.

    List names can come from upstream feeds (e.g. Azure region ids), so every
    path segment is restricted to a safe character set. Every file the run
    writes (lists, directory READMEs and the manifest) must resolve inside
    output_dir, which rejects symlinks pointing elsewhere, and no two may
    resolve to the same file, which rejects duplicates and in-tree symlink
    aliases.
    """
    for out in outputs:
        bad = next((seg for seg in out.relpath.split("/") if not _SAFE_SEGMENT.match(seg)), None)
        if bad is not None:
            raise SafetyError(f"unsafe path segment {bad!r} in output {out.relpath!r}")

    relpaths = [o.relpath for o in outputs]
    targets = relpaths + [f"{d}/{INDEX}" if d else INDEX for d in sorted(_index_dirs(relpaths))] + [MANIFEST]
    root = output_dir.resolve()
    seen: dict[Path, str] = {}
    for rel in targets:
        resolved = (output_dir / rel).resolve()
        if not resolved.is_relative_to(root):
            raise SafetyError(f"output {rel!r} resolves outside {output_dir}")
        if resolved in seen:
            raise SafetyError(f"{seen[resolved]!r} and {rel!r} would both be written to {resolved}")
        seen[resolved] = rel


def _raw_base_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("EDL_BRANCH") or os.environ.get("GITHUB_REF_NAME")
    if repo and ref:
        return f"https://raw.githubusercontent.com/{repo}/{ref}"
    return None


def _render_indexes(output_dir_name: str, outputs: list[Output]) -> dict[str, str]:
    """Render a README.md for every directory: its subdirectories and its lists.

    Keys are directory paths relative to the output directory ("" is the root).
    """
    base = _raw_base_url()
    dirs: dict[str, set[str]] = {"": set()}
    files: dict[str, list[Output]] = {}
    for out in outputs:
        parent = out.relpath.rsplit("/", 1)[0]
        files.setdefault(parent, []).append(out)
        parts = parent.split("/")
        for i in range(len(parts)):
            dirs.setdefault("/".join(parts[: i + 1]), set())
            dirs["/".join(parts[:i])].add(parts[i])

    indexes = {}
    for d, subdirs in dirs.items():
        title = "Generated EDLs" if not d else f"`{d}`"
        lines = [f"# {title}", "", "Generated by `pa-edl generate`. Do not edit by hand."]
        if d:
            lines += ["", "[Up](../README.md)"]
        if subdirs:
            lines += ["", "## Directories", ""]
            lines += [f"- [{sub}/]({sub}/README.md)" for sub in sorted(subdirs)]
        if d in files:
            header, sep = "| List | Entries |", "| --- | ---: |"
            if base:
                header, sep = header + " Raw URL |", sep + " --- |"
            lines += ["", "## Lists", "", header, sep]
            for out in sorted(files[d], key=lambda o: o.relpath):
                filename = out.relpath.rsplit("/", 1)[-1]
                row = f"| [{filename}]({filename}) | {len(out.entries)} |"
                if base:
                    row += f" {base}/{output_dir_name}/{out.relpath} |"
                lines.append(row)
        indexes[d] = "\n".join(lines) + "\n"
    return indexes


def write_outputs(outputs: list[Output], output_dir: Path, dry_run: bool = False) -> dict[str, list[str]]:
    """Write lists and per-directory READMEs, prune stale ones, update the manifest.

    Returns a change summary of the lists.
    """
    validate_outputs(outputs, output_dir)
    changes: dict[str, list[str]] = {"added": [], "updated": [], "removed": []}
    existing = _existing_lists(output_dir)

    for out in outputs:
        target = output_dir / out.relpath
        content = _render(out.entries)
        if out.relpath not in existing:
            changes["added"].append(out.relpath)
        elif existing[out.relpath].read_text() != content:
            changes["updated"].append(out.relpath)
        else:
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    indexes = _render_indexes(output_dir.name, outputs)
    index_paths = {output_dir / d / INDEX for d in indexes}
    if not dry_run:
        for d, content in indexes.items():
            target = output_dir / d / INDEX
            if not target.exists() or target.read_text() != content:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)

    produced = {o.relpath for o in outputs}
    for rel, path in sorted(existing.items()):
        if rel not in produced:
            changes["removed"].append(rel)
            if not dry_run:
                path.unlink()
    if not dry_run and output_dir.is_dir():
        for stale in output_dir.rglob(INDEX):
            if stale not in index_paths:
                stale.unlink()
        for d in sorted(output_dir.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    lists_meta = [{"path": o.relpath, "entries": len(o.entries), "source": o.source} for o in outputs]
    manifest_path = output_dir / MANIFEST
    old_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if old_manifest.get("lists") == lists_meta and "generated_at" in old_manifest:
        generated_at = old_manifest["generated_at"]
    else:
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    manifest = {"generated_at": generated_at, "lists": lists_meta}

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return changes
