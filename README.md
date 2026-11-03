# pa-edls

Palo Alto Networks [External Dynamic Lists](https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-admin/policy/use-an-external-dynamic-list-in-policy) (EDLs) generated from cloud provider IP range feeds. Hosted on GitHub and refreshed daily by GitHub Actions.

Currently supported:

| Provider | Source | Lists |
| --- | --- | --- |
| `azure` | [Azure service tags](https://learn.microsoft.com/azure/virtual-network/service-tags-overview) (Public, US Gov, China) | per-region and global lists for any service tag (configured: `Storage`) |

The current lists are browsable from [`edls/README.md`](edls/README.md). Every directory under `edls/` has a generated `README.md` listing its subdirectories and lists, with entry counts and raw URLs.

## Using the lists in PAN-OS

1. **Objects → External Dynamic Lists → Add**
2. Type: **IP List**
3. Source: the raw URL of the list, for example
   `https://raw.githubusercontent.com/<owner>/<repo>/main/edls/azure/public/storage/ipv4/eastus.txt`

   IPv4 and IPv6 are separate lists. Add a second EDL for `.../ipv6/eastus.txt` if you need both.
4. Repeat: **Daily** or **Hourly**. Upstream feeds change weekly at most, so daily is enough.
5. Reference the EDL as a source or destination address in security or PBF rules.

Each file has one CIDR per line, sorted, with no comments or headers. Overlapping and adjacent prefixes are collapsed, so a list can have fewer entries than the upstream tag. If a region has no ranges for an IP version, its file still exists but is empty, so the URL stays valid if ranges appear later.

## Layout

```
edls.toml                    # which EDLs to build
edls/                        # generated output (committed by the workflow; do not edit)
  manifest.json              # every list with its entry count and upstream source/version
  **/README.md               # per-directory index: subdirectories, lists, entry counts, raw URLs
  <provider>/<cloud>/<name>/<ipv4|ipv6>/<list>.txt   # e.g. azure/public/storage/ipv4/eastus.txt
src/pa_edl/
  cli.py                     # `pa-edl generate`, `pa-edl providers`
  config.py                  # edls.toml loading and validation
  writer.py                  # normalisation, safety checks, file writing
  fetch.py                   # HTTP with retries and per-run cache
  providers/
    base.py                  # Provider interface and registry
    azure.py
.github/workflows/
  generate.yml               # daily: test, generate, commit if changed
  ci.yml                     # PRs: tests + dry-run against live feeds
```

## Configuration (`edls.toml`)

```toml
output_dir = "edls"
max_shrink = 0.5        # refuse to write if any list loses more than 50% of its entries

[[edl]]
provider = "azure"
name = "storage"        # output folder: edls/azure/public/storage/
service = "Storage"
```

Keys every `[[edl]]` accepts:

| Key | Default | Description |
| --- | --- | --- |
| `provider` | required | provider id (`pa-edl providers` lists them) |
| `cloud` | `public` | provider cloud or partition. Used as a path segment; each provider checks which values it supports |
| `name` | required | output folder name. Lowercase `[a-z0-9._-]`, unique per provider and cloud |
| `ip_versions` | `["ipv4", "ipv6"]` | which per-version lists to write (`ipv4/`, `ipv6/` folders) |
| `min_entries` | `1` | fail the run if a list (IPv4 + IPv6 combined, per region) has fewer entries than this |

### `azure`

| Key | Default | Description |
| --- | --- | --- |
| `service` | required | service tag name, such as `Storage`, `Sql`, `AzureKeyVault`, `AzureCloud` |
| `cloud` | `public` | `public`, `usgov` or `china` (Azure Government, Azure China) |
| `split` | `region` | `region` writes one list per region. `none` writes a single `_all.txt` |
| `include_global` | `true` | with `split = "region"` and no `regions` filter, also writes the service-wide tag as `_all.txt` |
| `regions` | all | allow-list of region ids such as `["eastus", "westeurope"]`. An unknown region fails the run |
| `url` | discovered | pins a specific `ServiceTags_*.json` URL |

The ServiceTags JSON URL changes with every weekly release, so the provider discovers the current one from the Microsoft download page.

More examples:

```toml
# SQL in two regions only, IPv4 only
[[edl]]
provider = "azure"
name = "sql"
service = "Sql"
regions = ["eastus", "westeurope"]
ip_versions = ["ipv4"]

# Key Vault in Azure Government, as a single list (edls/azure/usgov/keyvault/ipv4/_all.txt, ...)
[[edl]]
provider = "azure"
name = "keyvault"
service = "AzureKeyVault"
cloud = "usgov"
split = "none"
```

## Safety

A bad upstream feed should not empty your firewall's allow lists. Each run:

- validates every prefix with Python's `ipaddress` module. Malformed input fails the run.
- fails if a list is below `min_entries`, if a service or region is missing, or if the feed format is unexpected.
- refuses to write if any existing list shrinks by more than `max_shrink`. To accept an expected large change, run the workflow manually with **force** checked, or run `pa-edl generate --force`.
- fails the workflow without committing on any error, so the previous lists stay in place and GitHub emails the repo owner about the failed run.

Unchanged runs produce no commit. Files and the manifest timestamp only change when list contents change.

## Local usage

Requires [uv](https://docs.astral.sh/uv/). Always run tools through `uv run`. Only the latest Python is supported (pinned in `.python-version`, currently 3.15). This only runs on GitHub-hosted runners, so there is no need to support older versions. uv downloads the pinned Python automatically.

```sh
uv sync                              # create .venv from uv.lock
uv run pa-edl generate --dry-run     # show what would change
uv run pa-edl generate
```

Checks (all run in CI):

```sh
uv run ruff check .                  # lint
uv run ruff format --check .         # formatting
uv run pyrefly check                 # types
uv run coverage run -m pytest && uv run coverage report   # tests, fails below 80% coverage
```

## Adding a provider

1. Create `src/pa_edl/providers/<id>.py`:

   ```python
   from pa_edl.providers.base import ConfigError, EdlList, Provider, register


   @register
   class AwsProvider(Provider):
       id = "aws"
       description = "AWS ip-ranges.json (per service and region)"

       def build(self, spec):
           service = spec.get("service") or "AMAZON"
           data = self.fetcher.json("https://ip-ranges.amazonaws.com/ip-ranges.json")
           by_region: dict[str, list[str]] = {}
           for p in data["prefixes"]:
               if p["service"] == service:
                   by_region.setdefault(p["region"].lower(), []).append(p["ip_prefix"])
           for p in data["ipv6_prefixes"]:
               if p["service"] == service:
                   by_region.setdefault(p["region"].lower(), []).append(p["ipv6_prefix"])
           if not by_region:
               raise ConfigError(f"aws: no prefixes for service {service!r}")
           src = f"AWS ip-ranges.json syncToken {data['syncToken']}"
           return [EdlList(region, prefixes, src) for region, prefixes in sorted(by_region.items())]
   ```

2. Import it in `src/pa_edl/providers/__init__.py`.
3. Add tests with a trimmed fixture of the upstream feed in `tests/fixtures/` (see `tests/test_azure.py`). Keep total coverage at 80% or higher.
4. Add `[[edl]]` entries to `edls.toml`.
5. Open a PR from a `feature/<description>` branch.

A provider only returns raw prefixes per list name. Validation, IP version filtering, collapsing, sorting, safety checks and writing are shared. `self.fetcher` caches by URL, so several EDLs that read the same feed download it once.

## Contributing

- Branches: `feature/<description>` or `fix/<description>`
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) (`feat: add aws provider`, `fix(azure): ...`). The daily workflow commits as `chore(edls): update EDLs <date>`.
- Merges go through a PR with 1 approval and passing CI.
- Dependencies: `uv add <pkg>` / `uv add --dev <pkg>`. Never `pip install`. Commit `uv.lock`.
- GitHub Actions: pin every action to a full commit SHA with the tag in a comment (`uses: actions/checkout@<sha> # v7.0.1`). Look up the SHA with `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`.

## Repository setup

- **Settings → Actions → General → Workflow permissions** must allow *Read and write* (the workflow also declares `contents: write`).
- Protect `main` with a ruleset: require a pull request with 1 approval and the CI checks. Add **GitHub Actions** (the `github-actions[bot]` integration) to the ruleset's bypass list. That lets the daily workflow push generated `edls/` changes directly, which keeps lists current without a daily review. All code changes still go through PRs.
- GitHub disables scheduled workflows after 60 days without repository activity. Data commits count as activity, but if upstream data stops changing, re-enable the workflow from the Actions tab.
