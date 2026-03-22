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
| `--values-yaml` | Path to `values.yaml` for `iocDefaults` metadata (optional) |
| `--ioc` | Process only this IOC (default: all with pvlist.txt) |
| `--no-pva` | Skip PVA introspection |
| `--pva-timeout` | Timeout for pvinfo in seconds (default: 3) |
| `--batch-size` | Channels per POST batch (default: 100) |

## Channel Properties

Each PV channel is created with these properties (when available):

- `iocName` — IOC directory name
- `beamline`, `devgroup`, `devtype` — from IOC config
- `host` — IOC hostname
- `ioc_version` — IOC software version
- `ca_server_port`, `pva_server_port` — EPICS ports
- `pva` — whether IOC supports PV Access
- `asset` — asset documentation link
- `pvProtocol` — resolved protocol (`ca` or `pva`)
- `pvinfo` — PVA structure info (if available)

```