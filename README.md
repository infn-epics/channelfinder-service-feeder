# Channelfinder Service Feeder

Feeds the [ChannelFinder](https://github.com/ChannelFinder/ChannelFinderService) service with PV data extracted from IOC configuration directories.

For each IOC it:
1. Reads `<pvlist_dir>/<ioc_name>/pvlist.txt` for the list of PV names
2. Reads `<pvlist_dir>/<ioc_name>/<ioc_name>-config.yaml` for IOC metadata (beamline, devgroup, devtype, host, etc.)
3. Optionally reads `values.yaml` for `iocDefaults` metadata
4. If the IOC supports PVA (`pva: true` in its config), attempts `pvinfo` to extract PV structure information
5. Posts channels to ChannelFinder with properties (metadata) and tags (ioc name, devgroup, beamline)


## Install

```
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Process all IOCs in the pvlist directory
python cfeeder.py /Volumes/data_epik8s/config http://localhost:8080/ChannelFinder admin adminPass

# With values.yaml for extra IOC metadata
python cfeeder.py /Volumes/data_epik8s/config http://localhost:8080/ChannelFinder admin adminPass \
    --values-yaml /path/to/deploy/values.yaml

# With all Helm values files, in the same order as the deployment
python cfeeder.py /Volumes/data_epik8s/config http://localhost:8080/ChannelFinder admin adminPass \
    --values-yaml /path/to/deploy/values.yaml /path/to/deploy/values-linac.yaml \
    /path/to/deploy/values-tl.yaml /path/to/deploy/values-accumulator.yaml \
    /path/to/deploy/values-vac.yaml

# Process a single IOC
python cfeeder.py /Volumes/data_epik8s/config http://localhost:8080/ChannelFinder admin adminPass \
    --ioc ac1bpm01

# Skip PVA introspection
python cfeeder.py /Volumes/data_epik8s/config http://localhost:8080/ChannelFinder admin adminPass \
    --no-pva
```

## Options

| Argument | Description |
|---|---|
| `pvlist_dir` | Directory with IOC subdirectories containing `pvlist.txt` |
| `cf_service_url` | ChannelFinder service URL |
| `username` | ChannelFinder username |
| `password` | ChannelFinder password |
| `--values-yaml` | One or more Helm values files in deployment order for IOC metadata (optional) |
| `--ioc` | Process only this IOC (default: all with pvlist.txt) |
| `--no-pva` | Skip PVA introspection |
| `--pva-timeout` | Timeout for pvinfo in seconds (default: 3) |
| `--batch-size` | Channels per POST batch (default: 100) |
| `--cleanup-days` | Delete channels with `lastUpdated` older than N days |
| `--remove-no-timestamp` | Delete channels that have no `lastUpdated` property |
| `--cleanup-only` | Only run the cleanup passes, do not feed |
| `--only-configured` | Feed only the IOCs listed (and not disabled) in `--values-yaml`; directories of removed IOCs on the config share are ignored |
| `--prune` | After feeding, delete the feeder's channels that are in no fed IOC's `pvlist.txt` |
| `--prune-max-fraction` | Refuse to prune when it would delete more than this fraction of the channels (default: 0.5) |
| `--dry-run` | With `--prune`: feed and delete nothing, log what would be deleted per IOC |

## Keeping ChannelFinder free of stale PVs

The config share keeps the directory of every IOC that ever ran, so feeding every
directory re-posts removed and renamed IOCs each night with a fresh `lastUpdated`;
and since IOCs are fed in alphabetical order, an old directory can overwrite the
properties of the live IOC that now serves the same PVs. Run the feeder with

```bash
python cfeeder.py /nfs/config http://channelfinder:8080/ChannelFinder epics pass \
    --values-yaml deploy/values.yaml --only-configured --prune
```

to feed only the configured IOCs and then delete every channel no configured IOC
publishes (by PV name, so a live PV relabelled by an old IOC is kept). IOCs configured
without a `pvlist.txt` keep their channels; channels owned by another user are never
touched; a prune that would delete more than `--prune-max-fraction` of the channels is
refused. Add `--dry-run` first to see what would go. Channels are read through the
scroll API, so the cleanup passes see all channels, not only the first 10000.

## Channel Properties

Each PV channel is created with these properties (when available):

- `iocName` — IOC directory name
- `beamline`, `devgroup`, `devtype` — from IOC config (`devices[].devtype` overrides IOC `devtype` for matching device PVs)
- `host` — IOC hostname
- `ioc_version` — IOC software version
- `ca_server_port`, `pva_server_port` — EPICS ports
- `pva` — whether IOC supports PV Access
- `asset` — asset documentation link
- `pvProtocol` — resolved protocol (`ca` or `pva`)
- `pvinfo` — PVA structure info (if available)

```
