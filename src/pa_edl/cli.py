import argparse
import sys
from pathlib import Path

from pa_edl.config import Config, load_config
from pa_edl.fetch import Fetcher
from pa_edl.providers import REGISTRY, ConfigError
from pa_edl.writer import Output, check_shrink, normalise, write_outputs


def build_outputs(config: Config, fetcher: Fetcher) -> list[Output]:
    providers = {}
    outputs: list[Output] = []
    for spec in config.edls:
        provider = providers.setdefault(spec.provider, REGISTRY[spec.provider](fetcher))
        lists = provider.build(spec.options)
        if not lists:
            raise ConfigError(f"{spec.path}: provider produced no lists")
        for lst in lists:
            by_version = normalise(lst.prefixes, spec.ip_versions)
            # min_entries applies per list across versions: a region with no IPv6 is fine.
            total = sum(len(e) for e in by_version.values())
            if total < spec.min_entries:
                raise ConfigError(f"{spec.path}/{lst.name}: {total} entries, below min_entries={spec.min_entries}")
            for version, entries in by_version.items():
                outputs.append(Output(f"{spec.path}/{version}/{lst.name}.txt", entries, lst.source))
    return outputs


def cmd_generate(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    outputs = build_outputs(config, Fetcher())

    problems = check_shrink(outputs, config.output_dir, config.max_shrink)
    if problems and not args.force:
        print(f"refusing to write: lists shrank by more than {config.max_shrink:.0%}:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("re-run with --force if this is expected", file=sys.stderr)
        return 2

    changes = write_outputs(outputs, config.output_dir, dry_run=args.dry_run)
    total = sum(len(o.entries) for o in outputs)
    print(f"{len(outputs)} lists, {total} entries{' (dry run)' if args.dry_run else ''}")
    for kind, paths in changes.items():
        for p in paths:
            print(f"  {kind}: {p}")
    if not any(changes.values()):
        print("  no changes")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    for pid, cls in sorted(REGISTRY.items()):
        print(f"{pid:12} {cls.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa-edl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="fetch feeds and write EDL files")
    gen.add_argument("-c", "--config", default="edls.toml", help="config file (default: edls.toml)")
    gen.add_argument("--force", action="store_true", help="ignore the max_shrink safety check")
    gen.add_argument("--dry-run", action="store_true", help="report changes without writing")
    gen.set_defaults(func=cmd_generate)

    prov = sub.add_parser("providers", help="list available providers")
    prov.set_defaults(func=cmd_providers)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
