## Andrea Michelotti

__version__ = "2.2.2"

import argparse
import os
import requests
import yaml
import logging
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

try:
    from p4p.client.thread import Context as PvaContext
    _has_p4p = True
except ImportError:
    _has_p4p = False

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

# IOC metadata keys to store as ChannelFinder properties
IOC_METADATA_KEYS = [
    "beamline", "devgroup", "devtype", "host", "ioc_version",
    "ca_server_port", "pva_server_port", "pva", "asset", "zone",
    "ioc_start_time", "iocprefix", "iocroot", "template", "zones", "server", "desc"
]


# ---------------------------------------------------------------------------
# ChannelFinder helpers
# ---------------------------------------------------------------------------

def ensure_property(cf_url, prop_name, owner, auth):
    """Create a ChannelFinder property if it does not already exist."""
    r = requests.get(f"{cf_url}/resources/properties/{prop_name}", auth=auth)
    if r.status_code != 200:
        data = {"name": prop_name, "owner": owner}
        r2 = requests.put(
            f"{cf_url}/resources/properties/{prop_name}",
            json=data, auth=auth)
        if r2.status_code == 200:
            logger.info(f"Created property: {prop_name}")
        else:
            logger.error(f"Failed to create property {prop_name}: {r2.status_code} {r2.text}")


def ensure_tag(cf_url, tag_name, owner, auth):
    """Create a ChannelFinder tag if it does not already exist."""
    r = requests.get(f"{cf_url}/resources/tags/{tag_name}", auth=auth)
    if r.status_code != 200:
        data = {"name": tag_name, "owner": owner}
        r2 = requests.put(
            f"{cf_url}/resources/tags/{tag_name}",
            json=data, auth=auth)
        if r2.status_code == 200:
            logger.info(f"Created tag: {tag_name}")
        else:
            logger.error(f"Failed to create tag {tag_name}: {r2.status_code} {r2.text}")


def metadata_properties(meta, owner):
    """ChannelFinder properties for the IOC metadata, leaving out empty values.

    ChannelFinder rejects a whole POST (HTTP 400) when any property value is empty, so an
    IOC whose values.yaml entry has e.g. `asset: ''` never reached ChannelFinder at all.
    """
    return [{"name": k, "owner": owner, "value": str(v)}
            for k, v in meta.items() if v is not None and str(v).strip() != ""]


def post_channels(cf_url, channels, auth):
    """Post a batch of channel entries to ChannelFinder using POST (merge semantics).
    POST merges properties and tags with existing data, unlike PUT which overwrites."""
    r = requests.post(
        f"{cf_url}/resources/channels",
        json=channels, auth=auth)
    if r.status_code == 200:
        logger.info(f"Successfully posted {len(channels)} channel(s)")
    else:
        logger.error(f"Failed to post channels: {r.status_code} {r.text}")


# ---------------------------------------------------------------------------
# PVA introspection via p4p
# ---------------------------------------------------------------------------

_pva_ctx = None

def _get_pva_context():
    """Lazy-init a shared PVA context."""
    global _pva_ctx
    if _pva_ctx is None and _has_p4p:
        _pva_ctx = PvaContext('pva')
    return _pva_ctx


def check_pva(pv_name, timeout=0.5):
    """Check if a PV is reachable via PVA using p4p.
    Returns True if the PV responds via PVA, False otherwise."""
    ctx = _get_pva_context()
    if ctx is None:
        return False
    try:
        ctx.get(pv_name, timeout=timeout)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# IOC metadata from config YAML
# ---------------------------------------------------------------------------

def load_ioc_metadata(pvlist_dir, ioc_name):
    """Load IOC metadata from <pvlist_dir>/<ioc_name>/<ioc_name>-config.yaml."""
    config_path = os.path.join(pvlist_dir, ioc_name, f"{ioc_name}-config.yaml")
    if not os.path.isfile(config_path):
        logger.debug(f"No config yaml found at {config_path}")
        return {}
    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) or {}
        result = {}
        for k in IOC_METADATA_KEYS:
            if k in data and data[k] is not None:
                v = data[k]
                if isinstance(v, list):
                    result[k] = ",".join(str(x) for x in v)
                else:
                    result[k] = str(v)
        return result
    except Exception as e:
        logger.warning(f"Error reading {config_path}: {e}")
        return {}


def parse_start_log(pvlist_dir, ioc_name):
    """Parse <pvlist_dir>/<ioc_name>/start.log to extract metadata.

    Expected format (key: value lines):
        Start Date: Sun Mar 15 02:15:35 UTC 2026
        TAG "runtime-v26.3.14b1"
        IOC Name: myioc
        IOC Prefix: SPARC:VAC
        IOC Version: latest

    When IOC Version is 'latest', the real version is extracted from the TAG line.
    Returns a dict with zero or more of: ioc_version, ioc_start_time, iocprefix.
    """
    log_path = os.path.join(pvlist_dir, ioc_name, "start.log")
    result = {}
    if not os.path.isfile(log_path):
        return result
    tag_version = None
    try:
        with open(log_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith("IOC Version:"):
                    result["ioc_version"] = line.split(":", 1)[1].strip()
                elif line.startswith("Start Date:"):
                    result["ioc_start_time"] = line.split(":", 1)[1].strip()
                elif line.startswith("IOC Prefix:"):
                    result["iocprefix"] = line.split(":", 1)[1].strip()
                elif line.startswith('TAG '):
                    # TAG "runtime-v26.3.14b1" → extract version after "runtime-"
                    tag_raw = line.split(None, 1)[1].strip('"\' ')
                    if tag_raw.startswith("runtime-"):
                        tag_version = tag_raw[len("runtime-"):]
                    else:
                        tag_version = tag_raw
    except Exception as e:
        logger.warning(f"Error reading {log_path}: {e}")
    # Use TAG version when IOC Version is missing or 'latest'
    if tag_version and result.get("ioc_version", "latest").lower() == "latest":
        result["ioc_version"] = tag_version
    return result


def load_pvlist(pvlist_dir, ioc_name):
    """Load PV names from <pvlist_dir>/<ioc_name>/pvlist.txt."""
    pvlist_path = os.path.join(pvlist_dir, ioc_name, "pvlist.txt")
    if not os.path.isfile(pvlist_path):
        logger.warning(f"No pvlist.txt found for IOC {ioc_name} at {pvlist_path}")
        return []
    with open(pvlist_path, 'r') as f:
        return [line.strip().strip(',') for line in f if line.strip() and not line.startswith('#')]


# ---------------------------------------------------------------------------
# Values.yaml parsing — extract IOC names
# ---------------------------------------------------------------------------

def iter_ioc_entries(iocs):
    """Yield (name, entry) for every IOC in epicsConfiguration.iocs.

    epik8s beamline repos now key `iocs` by IOC name (a mapping); the legacy
    form was a list of entries each carrying its own `name`. Both are accepted
    so an old values.yaml keeps working. Iterating a mapping directly yields
    only its string keys, which used to crash the whole run at startup.
    """
    if isinstance(iocs, dict):
        for key, entry in iocs.items():
            if isinstance(entry, dict):
                yield (entry.get("name") or key), entry
    elif isinstance(iocs, list):
        for entry in iocs:
            if isinstance(entry, dict) and entry.get("name"):
                yield entry["name"], entry


def _merge_values(base, override):
    """Merge Helm values: maps combine recursively; later values replace others."""
    for key, value in override.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _merge_values(base[key], value)
        else:
            base[key] = value
    return base


def load_values_yaml(values_yaml_paths):
    """Parse one or more Helm values files in order and return IOC metadata.

    ioc_defaults: dict keyed by template/devtype name with default metadata.
    iocs_by_name: dict keyed by IOC name with the per-IOC entry from `iocs`
                  (mapping or legacy list), already merged with its matching
                  iocDefault.
    """
    if isinstance(values_yaml_paths, (str, os.PathLike)):
        values_yaml_paths = [values_yaml_paths]
    data = {}
    for path in values_yaml_paths:
        with open(path, 'r') as f:
            _merge_values(data, yaml.safe_load(f) or {})

    ioc_defaults = data.get("iocDefaults") or {}
    epics_config = data.get("epicsConfiguration") or {}
    iocs_raw = epics_config.get("iocs") if isinstance(epics_config, dict) else None
    beamline = data.get("beamline", "")

    iocs_by_name = {}
    for name, ioc_entry in iter_ioc_entries(iocs_raw):
        # Start with a copy of matching iocDefault (by template or devtype)
        merged = {}
        template = ioc_entry.get("template", "")
        devtype = ioc_entry.get("devtype", "")
        for key in [template, devtype]:
            if key and key in ioc_defaults:
                merged.update(ioc_defaults[key])
                break
        # Overlay the per-IOC entry (takes precedence)
        merged.update(ioc_entry)
        merged.setdefault("name", name)
        # Normalize zones to comma-separated string
        zones_val = merged.get("zones")
        if isinstance(zones_val, list):
            merged["zones"] = ",".join(str(z) for z in zones_val)
        elif zones_val is not None:
            merged["zones"] = str(zones_val)
        # Propagate beamline from top level if not set
        if beamline and "beamline" not in merged:
            merged["beamline"] = beamline
        # Extract server from iocparam if not a direct key
        if "server" not in merged:
            for p in merged.get("iocparam", []):
                if isinstance(p, dict) and p.get("name") == "server" and p.get("value"):
                    merged["server"] = p["value"]
                    break
        iocs_by_name[name] = merged

    # Standalone soft IOCs (top-level `softiocs`, deployed by epik8s-chart next to
    # epicsConfiguration.iocs) are configured IOCs too: --only-configured must feed them.
    softioc_defaults = data.get("softiocDefaults") or {}
    for name, entry in iter_ioc_entries(data.get("softiocs")):
        if name in iocs_by_name:
            continue
        merged = {**softioc_defaults, **entry, "name": name}
        if beamline and "beamline" not in merged:
            merged["beamline"] = beamline
        iocs_by_name[name] = merged

    logger.info(f"Loaded {len(ioc_defaults)} IOC defaults, {len(iocs_by_name)} IOC entries from {values_yaml_paths}")
    return ioc_defaults, iocs_by_name


def get_ioc_dirs(pvlist_dir):
    """Get all IOC directories that contain a pvlist.txt file."""
    ioc_names = []
    if not os.path.isdir(pvlist_dir):
        logger.error(f"pvlist directory does not exist: {pvlist_dir}")
        return ioc_names
    for entry in sorted(os.listdir(pvlist_dir)):
        entry_path = os.path.join(pvlist_dir, entry)
        if os.path.isdir(entry_path) and os.path.isfile(os.path.join(entry_path, "pvlist.txt")):
            ioc_names.append(entry)
    return ioc_names


# ---------------------------------------------------------------------------
# Paginated channel query helper
# ---------------------------------------------------------------------------

PAGE_SIZE = 10000

def fetch_all_channels(cf_url, auth, extra_params=None):
    """Fetch every channel matching extra_params (all channels by default).

    Reads through ChannelFinder's scroll API: ~from paging stops at 10000
    channels (Elasticsearch refuses from+size beyond that), which left every
    channel after the first 10000 unseen by the cleanup passes. Older servers
    without /resources/scroll fall back to ~from paging.
    """
    params = {"~name": "*", **(extra_params or {}), "~size": PAGE_SIZE}
    r = requests.get(f"{cf_url}/resources/scroll", params=params, auth=auth)
    if r.status_code == 200:
        all_channels = []
        page = r.json()
        while page.get("channels"):
            all_channels.extend(page["channels"])
            if not page.get("id"):
                break
            r = requests.get(f"{cf_url}/resources/scroll/{page['id']}", params=params, auth=auth)
            if r.status_code != 200:
                raise RuntimeError(f"ChannelFinder scroll failed after {len(all_channels)} channels: "
                                   f"{r.status_code} {r.text}")
            page = r.json()
        return all_channels
    logger.warning(f"Scroll API unavailable ({r.status_code}); paging with ~from (at most {PAGE_SIZE} channels)")
    return _fetch_channels_by_offset(cf_url, auth, extra_params)


def _fetch_channels_by_offset(cf_url, auth, extra_params=None):
    all_channels = []
    offset = 0
    while True:
        params = {"~size": PAGE_SIZE}
        if offset > 0:
            params["~from"] = offset
        if extra_params:
            params.update(extra_params)
        r = requests.get(f"{cf_url}/resources/channels", params=params, auth=auth)
        if r.status_code != 200:
            if offset > 0:
                logger.warning(f"Pagination stopped at offset {offset} (server returned {r.status_code})")
                break
            logger.error(f"Failed to query channels: {r.status_code} {r.text}")
            break
        page = r.json()
        if not page:
            break
        all_channels.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_channels


# ---------------------------------------------------------------------------
# Cleanup stale channels
# ---------------------------------------------------------------------------

def cleanup_stale_channels(cf_url, auth, max_age_days):
    """Delete channels whose lastUpdated property is older than max_age_days.
    Queries ChannelFinder for all channels that have a lastUpdated property,
    then deletes those with timestamps older than the cutoff."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    logger.info(f"Cleanup: removing channels with lastUpdated before {cutoff.isoformat()}")

    channels = fetch_all_channels(cf_url, auth, {"lastUpdated": "*"})
    stale_count = 0
    for ch in channels:
        last_updated_val = None
        for prop in ch.get("properties", []):
            if prop["name"] == "lastUpdated":
                last_updated_val = prop.get("value")
                break
        if not last_updated_val:
            continue
        try:
            ts = datetime.strptime(last_updated_val, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug(f"Could not parse lastUpdated '{last_updated_val}' for channel {ch['name']}")
            continue
        if ts < cutoff:
            ch_name = ch["name"]
            dr = requests.delete(f"{cf_url}/resources/channels/{ch_name}", auth=auth)
            if dr.status_code == 200:
                stale_count += 1
                logger.debug(f"Deleted stale channel: {ch_name} (lastUpdated={last_updated_val})")
            else:
                logger.error(f"Failed to delete channel {ch_name}: {dr.status_code} {dr.text}")

    logger.info(f"Cleanup complete: deleted {stale_count} stale channel(s)")


def cleanup_channels_without_timestamp(cf_url, auth):
    """Delete channels that do not have a lastUpdated property.
    These are legacy entries created before cfeeder v2.0.0."""
    logger.info("Cleanup: removing channels without lastUpdated property")

    all_channels = fetch_all_channels(cf_url, auth)

    # Get channels that DO have lastUpdated
    has_timestamp = {ch["name"] for ch in fetch_all_channels(cf_url, auth, {"lastUpdated": "*"})}

    to_remove = [ch for ch in all_channels if ch["name"] not in has_timestamp]
    logger.info(f"Found {len(all_channels)} total channels, {len(has_timestamp)} with lastUpdated, "
                f"{len(to_remove)} to remove")

    removed = 0
    for ch in to_remove:
        dr = requests.delete(f"{cf_url}/resources/channels/{ch['name']}", auth=auth)
        if dr.status_code == 200:
            removed += 1
            if removed % 500 == 0:
                logger.info(f"  ...deleted {removed}/{len(to_remove)} channels")
        else:
            logger.error(f"Failed to delete {ch['name']}: {dr.status_code} {dr.text}")

    logger.info(f"Cleanup complete: deleted {removed} channel(s) without lastUpdated")


# ---------------------------------------------------------------------------
# Configured IOCs and pruning
# ---------------------------------------------------------------------------

def _is_disabled(entry):
    return bool(entry.get("disable") or entry.get("disabled")) or entry.get("enabled") is False


def configured_ioc_names(iocs_by_name):
    """Lower-case names of the IOCs the values files deploy (disabled ones left out)."""
    return {name.lower() for name, entry in iocs_by_name.items() if not _is_disabled(entry)}


def select_configured(ioc_dirs, iocs_by_name):
    """The IOC directories that belong to a configured IOC, and the configured IOCs with no directory.

    Directories of removed or renamed IOCs stay on the config share for good; feeding them
    every night keeps their PVs in ChannelFinder with a fresh lastUpdated, and - fed in
    alphabetical order - lets them overwrite the properties of a live IOC serving the same PVs.
    """
    configured = configured_ioc_names(iocs_by_name)
    selected = [d for d in ioc_dirs if d.lower() in configured]
    missing = sorted(configured - {d.lower() for d in selected})
    return selected, missing


def prune_channels(cf_url, auth, owner, keep_names, keep_iocs=(), max_fraction=0.5, dry_run=False):
    """Delete this feeder's channels that no fed IOC publishes any more.

    keep_names: every PV name in the pvlist.txt of the IOCs fed in this run.
    keep_iocs:  IOCs whose channels are kept as they are, because this run could not
                read their PV list (configured but no pvlist.txt).
    A channel goes when its name is in no fed pvlist - by name, not by iocName, because
    a stale IOC may have relabelled a live PV. Only channels owned by `owner` are touched.
    Refuses (deletes nothing) when it would remove more than max_fraction of the channels,
    so an empty config share or a wrong values file cannot wipe ChannelFinder.
    Returns the names of the channels it deleted (or, with dry_run, would delete).
    """
    if not keep_names:
        logger.error("Prune skipped: no PV was read from any pvlist.txt, so nothing can be told stale")
        return []
    keep_iocs = {i.lower() for i in keep_iocs}
    channels = fetch_all_channels(cf_url, auth)
    stale, by_ioc = [], {}
    for ch in channels:
        if ch.get("owner") != owner or ch["name"] in keep_names:
            continue
        ioc = next((p.get("value", "") for p in ch.get("properties", []) if p["name"] == "iocName"), "")
        if ioc.lower() in keep_iocs:
            continue
        stale.append(ch["name"])
        by_ioc[ioc or "?"] = by_ioc.get(ioc or "?", 0) + 1

    logger.info(f"Prune: {len(stale)} of {len(channels)} channel(s) are in no fed pvlist")
    for ioc, count in sorted(by_ioc.items()):
        logger.info(f"  {ioc}: {count}")
    if not stale:
        return []
    fraction = len(stale) / len(channels)
    if fraction > max_fraction and not dry_run:
        logger.error(f"Prune refused: it would delete {fraction:.0%} of the channels, more than "
                     f"--prune-max-fraction {max_fraction:.0%}. Check the values files and the config "
                     f"share, then rerun with a higher --prune-max-fraction if this is intended.")
        return []
    if dry_run:
        logger.info("Dry run: nothing deleted")
        return stale

    session = requests.Session()
    session.auth = auth
    removed = []
    for name in stale:
        dr = session.delete(f"{cf_url}/resources/channels/{quote(name, safe=':')}")
        if dr.status_code == 200:
            removed.append(name)
            if len(removed) % 1000 == 0:
                logger.info(f"  ...deleted {len(removed)}/{len(stale)} channels")
        else:
            logger.error(f"Failed to delete {name}: {dr.status_code} {dr.text}")
    logger.info(f"Prune complete: deleted {len(removed)} channel(s)")
    return removed


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_ioc(ioc_name, pvlist_dir, ioc_defaults, iocs_by_name, cf_url, owner, auth,
                use_pva=True, pva_timeout=0.5, batch_size=100):
    """Process a single IOC: read metadata, pvlist, attempt PVA introspection, feed CF."""
    logger.info(f"Processing IOC: {ioc_name}")

    # Load IOC metadata from the per-IOC config yaml
    ioc_meta = load_ioc_metadata(pvlist_dir, ioc_name)

    # Merge metadata from values.yaml iocs entry (already merged with iocDefaults)
    values_entry = iocs_by_name.get(ioc_name, {})
    for k in IOC_METADATA_KEYS:
        if k not in ioc_meta and k in values_entry and values_entry[k] is not None:
            ioc_meta[k] = str(values_entry[k])

    # Fallback: try matching by devtype in iocDefaults (for IOCs not in iocs list)
    if not values_entry:
        devtype = ioc_meta.get("devtype", "")
        for default_key, default_vals in ioc_defaults.items():
            if default_key == devtype or default_key == ioc_name:
                for k in IOC_METADATA_KEYS:
                    if k not in ioc_meta and k in default_vals and default_vals[k] is not None:
                        ioc_meta[k] = str(default_vals[k])

    # Merge start.log data — overrides yaml for ioc_version, adds ioc_start_time
    start_log = parse_start_log(pvlist_dir, ioc_name)
    ioc_meta.update(start_log)

    # Always set iocName property
    ioc_meta["iocName"] = ioc_name

    # Use desc from values.yaml if present
    if "desc" not in ioc_meta and "description" in values_entry:
        ioc_meta["desc"] = str(values_entry["description"])

    # Compute 'device' property from iocprefix[:iocroot][:device_name].
    # iocprefix and iocroot are popped here — not stored as CF properties directly.
    iocprefix = ioc_meta.pop("iocprefix", "") or values_entry.get("iocprefix", "")
    iocroot = ioc_meta.pop("iocroot", "") or values_entry.get("iocroot", "")
    devices_list = values_entry.get("devices", [])
    default_device = f"{iocprefix}:{iocroot}" if (iocprefix and iocroot) else iocprefix
    if devices_list:
        device_prefix_map = []
        for dev in devices_list:
            if isinstance(dev, dict):
                dev_name = dev.get("name", "")
                dev_devtype = dev.get("devtype")
            else:
                dev_name = str(dev)
                dev_devtype = None
            if dev_name and iocprefix:
                device_prefix_map.append((
                    f"{iocprefix}:{dev_name}:",
                    f"{iocprefix}:{dev_name}",
                    dev_devtype,
                ))
        # Sort longest prefix first for most-specific match
        device_prefix_map.sort(key=lambda x: len(x[0]), reverse=True)
    else:
        device_prefix_map = []

    # Timestamp for staleness tracking
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Channel owner must match the authenticated user for authorization
    channel_owner = owner

    # Load PV list from file
    pv_names = load_pvlist(pvlist_dir, ioc_name)
    if not pv_names:
        logger.warning(f"No PVs found for IOC {ioc_name}, skipping")
        return

    logger.info(f"  IOC {ioc_name}: {len(pv_names)} PVs, meta={ioc_meta}")

    # Ensure properties and tags exist in ChannelFinder
    all_prop_names = {p["name"] for p in metadata_properties(ioc_meta, owner)} | {"pvProtocol", "lastUpdated", "cfeederVersion", "device"}
    for prop_name in all_prop_names:
        ensure_property(cf_url, prop_name, owner, auth)

    # Tag with IOC name, devgroup, beamline, zone, and zones
    tags = [ioc_name]
    if "devgroup" in ioc_meta:
        tags.append(ioc_meta["devgroup"])
    if "beamline" in ioc_meta:
        tags.append(ioc_meta["beamline"])
    if "zone" in ioc_meta:
        tags.append(ioc_meta["zone"])
    if "zones" in ioc_meta:
        for z in ioc_meta["zones"].split(","):
            z = z.strip()
            if z and z not in tags:
                tags.append(z)
    if "template" in ioc_meta and ioc_meta["template"] not in tags:
        tags.append(ioc_meta["template"])
    tags = [t for t in tags if t and str(t).strip()]
    for tag in tags:
        ensure_tag(cf_url, tag, owner, auth)

    # Check PVA once for first PV of the IOC to determine protocol
    ioc_is_pva = False
    if use_pva and ioc_meta.get("pva", "").lower() != "false":
        # Test first PV to determine if the IOC supports PVA
        if pv_names:
            ioc_is_pva = check_pva(pv_names[0], timeout=pva_timeout)
            if ioc_is_pva:
                logger.info(f"  IOC {ioc_name}: PVA detected (tested {pv_names[0]})")

    # Build channel entries
    channels = []
    for pv_name in pv_names:
        # Determine device: match PV name against device-prefix map, else use IOC default
        pv_device = default_device
        pv_devtype_override = None
        for pv_prefix, device_str, device_devtype in device_prefix_map:
            if pv_name.startswith(pv_prefix):
                pv_device = device_str
                pv_devtype_override = device_devtype
                break

        pv_meta = dict(ioc_meta)
        if pv_devtype_override not in (None, ""):
            pv_meta["devtype"] = str(pv_devtype_override)

        properties = metadata_properties(pv_meta, channel_owner)
        if pv_device:
            properties.append({"name": "device", "owner": channel_owner, "value": pv_device})
        properties.append({"name": "lastUpdated", "owner": channel_owner, "value": now_iso})
        properties.append({"name": "cfeederVersion", "owner": channel_owner, "value": __version__})

        pv_protocol = "pva" if ioc_is_pva else "ca"
        properties.append({"name": "pvProtocol", "owner": channel_owner, "value": pv_protocol})

        channel = {
            "name": pv_name,
            "owner": channel_owner,
            "properties": properties,
            "tags": [{"name": t, "owner": channel_owner} for t in tags]
        }
        channels.append(channel)

        # Post in batches
        if len(channels) >= batch_size:
            post_channels(cf_url, channels, auth)
            channels = []

    # Post remaining
    if channels:
        post_channels(cf_url, channels, auth)

    logger.info(f"  Done: {ioc_name} ({len(pv_names)} PVs, protocol={'pva' if ioc_is_pva else 'ca'})")


def main():
    parser = argparse.ArgumentParser(
        description='Feed ChannelFinder from IOC pvlist.txt and values.yaml metadata.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('pvlist_dir', type=str,
                        help='Directory containing IOC subdirectories with pvlist.txt files '
                             '(e.g. /Volumes/data_epik8s/config)')
    parser.add_argument('cf_service_url', type=str,
                        help='ChannelFinder service URL (e.g. http://localhost:8080/ChannelFinder)')
    parser.add_argument('username', type=str,
                        help='ChannelFinder username')
    parser.add_argument('password', type=str,
                        help='ChannelFinder password')
    parser.add_argument('--values-yaml', type=str, nargs='+', default=None,
                        help='Helm values files in deployment order for IOC metadata (optional)')
    parser.add_argument('--ioc', type=str, default=None,
                        help='Process only this IOC name (default: all IOCs with pvlist.txt)')
    parser.add_argument('--no-pva', action='store_true',
                        help='Skip PVA introspection even if IOC supports it')
    parser.add_argument('--pva-timeout', type=float, default=0.5,
                        help='Timeout in seconds for PVA check (default: 0.5)')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Number of channels per POST batch (default: 100)')
    parser.add_argument('--cleanup-days', type=int, default=None,
                        help='Delete channels with lastUpdated older than N days')
    parser.add_argument('--remove-no-timestamp', action='store_true',
                        help='Delete channels that have no lastUpdated property')
    parser.add_argument('--cleanup-only', action='store_true',
                        help='Only run cleanup, do not feed channels')
    parser.add_argument('--only-configured', action='store_true',
                        help='Feed only the IOCs listed (and not disabled) in --values-yaml; '
                             'directories of removed IOCs on the config share are ignored')
    parser.add_argument('--prune', action='store_true',
                        help='After feeding, delete the channels that are in no fed IOC pvlist.txt')
    parser.add_argument('--prune-max-fraction', type=float, default=0.5,
                        help='Refuse to prune when it would delete more than this fraction of the '
                             'channels (default: 0.5)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Feed and delete nothing: report what --prune would delete')

    args = parser.parse_args()
    auth = (args.username, args.password)

    logger.info(f"cfeeder version {__version__}")

    if args.only_configured and not args.values_yaml:
        parser.error("--only-configured needs --values-yaml")
    if args.prune and args.ioc:
        parser.error("--prune needs every IOC's PV list; it cannot be used with --ioc")
    if args.dry_run and not args.prune:
        parser.error("--dry-run reports what --prune would delete; add --prune")

    # Cleanup passes
    if args.dry_run and (args.remove_no_timestamp or args.cleanup_days is not None):
        logger.warning("Dry run: the --remove-no-timestamp and --cleanup-days passes are skipped")
    elif args.remove_no_timestamp:
        cleanup_channels_without_timestamp(args.cf_service_url, auth)
    if args.cleanup_days is not None and not args.dry_run:
        cleanup_stale_channels(args.cf_service_url, auth, args.cleanup_days)
    if args.cleanup_only:
        if not args.cleanup_days and not args.remove_no_timestamp:
            logger.warning("--cleanup-only requires --cleanup-days N or --remove-no-timestamp")
        return

    # Load values.yaml if provided
    ioc_defaults = {}
    iocs_by_name = {}
    if args.values_yaml:
        ioc_defaults, iocs_by_name = load_values_yaml(args.values_yaml)

    # Determine which IOCs to process
    if args.ioc:
        ioc_names = [args.ioc]
    else:
        ioc_names = get_ioc_dirs(args.pvlist_dir)

    missing = []
    if args.only_configured:
        all_dirs = ioc_names
        ioc_names, missing = select_configured(ioc_names, iocs_by_name)
        ignored = sorted(set(all_dirs) - set(ioc_names))
        if ignored:
            logger.info(f"Ignoring {len(ignored)} IOC directory(ies) not in the values files: {', '.join(ignored)}")
        if missing and not args.ioc:
            logger.warning(f"Configured IOC(s) with no pvlist.txt (channels kept as they are): {', '.join(missing)}")

    if args.dry_run:
        logger.info(f"Dry run: not feeding {len(ioc_names)} IOC(s)")
        ioc_names_to_feed = []
    else:
        ioc_names_to_feed = ioc_names
        logger.info(f"Processing {len(ioc_names)} IOC(s) from {args.pvlist_dir}")

    for ioc_name in ioc_names_to_feed:
        try:
            process_ioc(
                ioc_name=ioc_name,
                pvlist_dir=args.pvlist_dir,
                ioc_defaults=ioc_defaults,
                iocs_by_name=iocs_by_name,
                cf_url=args.cf_service_url,
                owner=args.username,
                auth=auth,
                use_pva=not args.no_pva,
                pva_timeout=args.pva_timeout,
                batch_size=args.batch_size
            )
        except Exception as e:
            logger.error(f"Error processing IOC {ioc_name}: {e}", exc_info=True)

    if args.prune:
        keep_names = set()
        keep_iocs = list(missing)
        for ioc_name in ioc_names:
            pvs = load_pvlist(args.pvlist_dir, ioc_name)
            if pvs:
                keep_names.update(pvs)
            else:
                keep_iocs.append(ioc_name)
        prune_channels(args.cf_service_url, auth, args.username, keep_names, keep_iocs,
                       max_fraction=args.prune_max_fraction, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

