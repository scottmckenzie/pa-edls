# pa-edls

Palo Alto Networks [External Dynamic Lists](https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-admin/policy/use-an-external-dynamic-list-in-policy) (EDLs) generated from cloud provider IP range feeds. Hosted on GitHub and refreshed daily by GitHub Actions.

Currently supported:

| Provider | Source | Lists |
| --- | --- | --- |
| `azure` | [Azure service tags](https://learn.microsoft.com/azure/virtual-network/service-tags-overview) (Public, US Gov, China) | per-region and global lists for any service tag (configured: `Storage`) |

The current lists are browsable from [`generated/README.md`](generated/README.md). Every directory under `generated/` has a generated `README.md` listing its subdirectories and lists, with entry counts, upstream change numbers and raw URLs.

## Using the lists in PAN-OS

1. **Objects → External Dynamic Lists → Add**
2. Type: **IP List**
3. Source: the raw URL of the list, for example
   `https://raw.githubusercontent.com/<owner>/<repo>/main/generated/azure/public/storage/ipv4/eastus.txt`

   IPv4 and IPv6 are separate lists. Add a second EDL for `.../ipv6/eastus.txt` if you need both.
4. Repeat: **Daily** or **Hourly**. Upstream feeds change weekly at most, so daily is enough.
5. Reference the EDL as a source or destination address in security or PBF rules.

Each file has one CIDR per line, sorted, with no comments or headers. Overlapping and adjacent prefixes are collapsed, so a list can have fewer entries than the upstream tag. If a region has no ranges for an IP version, its file still exists but is empty, so the URL stays valid if ranges appear later.

## Layout

```
edls.toml                    # which EDLs to build
generated/                   # generated output (committed by the workflow; do not edit)
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
output_dir = "generated"
max_shrink = 0.5        # refuse to write if any list loses more than 50% of its entries
max_growth = 2.0        # ...or if its total address space more than doubles

[[edl]]
provider = "azure"
name = "storage"        # output folder: generated/azure/public/storage/
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
| `min_prefixlen_ipv4` | `8` | reject IPv4 prefixes broader than this (e.g. `/7`, `/0`) |
| `min_prefixlen_ipv6` | `16` | reject IPv6 prefixes broader than this |

### `azure`

| Key | Default | Description |
| --- | --- | --- |
| `service` | required | service tag name, such as `Storage`, `Sql`, `AzureKeyVault`, `AzureCloud` |
| `cloud` | `public` | `public`, `usgov` or `china` (Azure Government, Azure China) |
| `split` | `region` | `region` writes one list per region. `none` writes a single `_all.txt` |
| `include_global` | `true` | with `split = "region"` and no `regions` filter, also writes the service-wide tag as `_all.txt` |
| `regions` | all | allow-list of list names such as `["eastus", "westeurope"]` (case-insensitive). An unknown name fails the run |
| `url` | discovered | pins a specific `ServiceTags_*.json` URL |

The ServiceTags JSON URL changes with every weekly release, so the provider discovers the current one from the Microsoft download page.

Each regional tag becomes its own list, named after the tag in lowercase: `Storage.FranceCentral` becomes `francecentral.txt`, and `Storage.EastUS2Stage` becomes `eastus2stage.txt`. The tag's `region` property isn't used for naming. It is sometimes a legacy id (`centralfrance`), and staging tags share it with the main tag (`Storage.EastUS2` and `Storage.EastUS2Stage` are both `eastus2`).

The **Change number** column in each list README is the tag's own `changeNumber`, which Microsoft increments only when that tag's prefixes change. A combined list built from a `regions` filter shows each tag's number, comma-separated. The file-wide `changeNumber`, which goes up with every weekly release, is recorded in `manifest.json` as part of each list's `source`.

More examples:

```toml
# SQL in two regions only, IPv4 only
[[edl]]
provider = "azure"
name = "sql"
service = "Sql"
regions = ["eastus", "westeurope"]
ip_versions = ["ipv4"]

# Key Vault in Azure Government, as a single list (generated/azure/usgov/keyvault/ipv4/_all.txt, ...)
[[edl]]
provider = "azure"
name = "keyvault"
service = "AzureKeyVault"
cloud = "usgov"
split = "none"
```

## Safety

These lists decide firewall policy, so a bad or tampered upstream feed must not empty them or open them up. Each run:

- **Downloads over HTTPS only.** Redirects to any other scheme are refused, and responses over 50 MB are rejected.
- **Validates every prefix.** Each must be an exact network (no host bits set). It must be no broader than `min_prefixlen_ipv4`/`min_prefixlen_ipv6` (`/8` and `/16` by default, so `0.0.0.0/0` and `::/0` can never appear). It must not overlap private, loopback, link-local, documentation, multicast or reserved ranges, and IPv6 must be global unicast (`2000::/3`).
- **Validates every output path.** List names come from the upstream feed, so each path segment is restricted to `[a-z0-9._-]` (no leading dot), two lists can't share a file, and nothing is written outside `generated/`.
- **Fails on bad input:** a list below `min_entries`, a missing service or region, or an unexpected feed format.
- **Refuses large changes to existing lists:** more than `max_shrink` of the entries lost, or total address space grown more than `max_growth` times. To accept an expected large change, run the workflow manually with **force** checked, or run `pa-edl generate --force`. `--force` skips only these two checks, never the per-prefix or path checks.
- **Commits nothing on any error.** The previous lists stay in place, and GitHub emails the repo owner about the failed run.

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

   `EdlList` also takes an optional `change` (a string), shown in the list README's **Change number** column. Set it if the feed versions each list; the column is left out of a directory where no list has one.
5. Open a PR from a `feature/<description>` branch.

A provider only returns raw prefixes per list name. Validation, IP version filtering, collapsing, sorting, safety checks and writing are shared. `self.fetcher` caches by URL, so several EDLs that read the same feed download it once.

## Contributing

- Branches: `feature/<description>` or `fix/<description>`
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) (`feat: add aws provider`, `fix(azure): ...`). The daily workflow commits as `chore(edls): update EDLs <date>`.
- Merges go through a PR with 1 approval and passing CI.
- Dependencies: `uv add <pkg>` / `uv add --dev <pkg>`. Never `pip install`. Commit `uv.lock`.
- GitHub Actions: pin every action to a full commit SHA with the tag in a comment (`uses: actions/checkout@<sha> # v7.0.1`). Look up the SHA with `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`.

## Repository setup

The daily workflow pushes changes under `generated/` straight to `main`, while all code changes go through PRs. GitHub doesn't allow the built-in `GITHUB_TOKEN` (`github-actions[bot]`) on a ruleset bypass list, so the workflow pushes as a small GitHub App that you own and add to the bypass list.

1. **Create the app:** go to **Settings → Developer settings → GitHub Apps → New GitHub App**.
   - Use any name, such as `pa-edl-bot`, and any homepage URL.
   - Untick **Webhook → Active**.
   - Under **Repository permissions**, set **Contents** to *Read and write*. Leave everything else at *No access*.
   - Set **Where can this app be installed** to *Only on this account*.
   - After creating it, note the **Client ID** and use **Generate a private key**, which downloads a `.pem` file.
2. **Install the app** on this repository only: on the app's page, click **Install App**.
3. **Store its credentials in a protected environment:** go to **Settings → Environments → New environment** and name it `edl-publish`.
   - Under **Deployment branches and tags**, choose **Selected branches and tags** and add `main` only.
   - Add an environment variable `EDL_APP_CLIENT_ID` containing the Client ID.
   - Add an environment secret `EDL_APP_PRIVATE_KEY` containing the whole `.pem` file.
   - Don't also store them as repository-level secrets or variables, which any branch could read. If they're there from an earlier setup, delete them.
4. **Protect `main`:** go to **Settings → Rules → Rulesets → New branch ruleset**, targeting the default branch.
   - Require a pull request with 1 approval, and require the `test` status check (the job in `ci.yml`). It only appears in the picker after CI has run once.
   - Under **Bypass list → Add bypass**, add the app and leave it on **Always allow**. "For pull requests only" would block the direct push.
5. **Run it once:** run **Actions → Generate EDLs → Run workflow**. This fills in the raw URLs in the `generated/**/README.md` indexes.

Commits from the workflow appear as `<app-name>[bot]`. How the app's push access is contained:

- **Only `main` can publish.** The job only runs on `main`, and the `edl-publish` environment only releases the app credentials to `main`. Someone with write access can't run a modified workflow from a branch to get them.
- **The token exists only for the push.** It's minted just before the commit step and only when lists changed. It's scoped to `contents: write` on this repository and expires within an hour.
- **Nothing else runs with write access.** The checkout leaves no credentials in `.git/config`, only runtime dependencies are installed (tests and dev tools run in CI), and git hooks are disabled for the commit and push.
- **`GITHUB_TOKEN` is read-only.**

GitHub disables scheduled workflows after 60 days without repository activity. Data commits count as activity, but if upstream data stops changing, re-enable the workflow from the Actions tab.
