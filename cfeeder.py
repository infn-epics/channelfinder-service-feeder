## Andrea Michelotti

__version__ = "2.0.0"

import argparse
import os
import subprocess
import requests
import yaml
import logging
import time
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

# IOC metadata keys to store as ChannelFinder properties
IOC_METADATA_KEYS = [
    "beamline", "devgroup", "devtype", "host", "ioc_version",
    "ca_server_port", "pva_server_port", "pva", "asset", "zone",
    "ioc_start_time", "iocprefix"
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
# PVA introspection via pvinfo (EPICS base CLI)
# ---------------------------------------------------------------------------

def pvinfo(pv_name, timeout=0.5):
    """Run pvinfo on a PV and return parsed structure info as a dict.
    Returns None if pvinfo fails or is unavailable."""
    try:
        result = subprocess.run(
            ["pvinfo", "-w", str(timeout), pv_name],
            capture_output=True, text=True, timeout=timeout + 2)
        if result.returncode == 0 and result.stdout.strip():
            return {"pvinfo": result.stdout.strip()}
    except FileNotFoundError:
        logger.debug("pvinfo command not found, skipping PVA introspection")
    except subprocess.TimeoutExpired:
        logger.debug(f"pvinfo timed out for {pv_name}")
    except Exception as e:
        logger.debug(f"pvinfo error for {pv_name}: {e}")
    return None


def pvget_structure(pv_name, timeout=3):
    """Run pvget -r '' to retrieve full PV structure via PVA.
    Returns structure string or None."""
    try:
        result = subprocess.run(
            ["pvget", "-r", "", "-w", str(timeout), pv_name],
            capture_output=True, text=True, timeout=timeout + 2)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return None


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
        return {k: str(data[k]) for k in IOC_METADATA_KEYS if k in data and data[k] is not None}
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

def load_ioc_names_from_values(values_yaml_path):
    """Extract IOC names from the iocDefaults section of values.yaml.
    Returns a list of IOC type names (keys under iocDefaults)."""
    with open(values_yaml_path, 'r') as f:
        data = yaml.safe_load(f) or {}
    ioc_defaults = data.get("iocDefaults", {})
    if not ioc_defaults:
        logger.warning("No iocDefaults found in values.yaml")
    return ioc_defaults


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
# Cleanup stale channels
# ---------------------------------------------------------------------------

def cleanup_stale_channels(cf_url, auth, max_age_days):
    """Delete channels whose lastUpdated property is older than max_age_days.
    Queries ChannelFinder for all channels that have a lastUpdated property,
    then deletes those with timestamps older than the cutoff."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    logger.info(f"Cleanup: removing channels with lastUpdated before {cutoff.isoformat()}")

    # Query all channels that have the lastUpdated property (wildcard value match)
    r = requests.get(f"{cf_url}/resources/channels", params={"lastUpdated": "*", "~size": 100000}, auth=auth)
    if r.status_code != 200:
        logger.error(f"Failed to query channels for cleanup: {r.status_code} {r.text}")
        return

    channels = r.json()
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

    # Get all channels (up to 100k)
    r = requests.get(f"{cf_url}/resources/channels", params={"~size": 100000}, auth=auth)
    if r.status_code != 200:
        logger.error(f"Failed to query channels: {r.status_code} {r.text}")
        return

    all_channels = r.json()

    # Get channels that DO have lastUpdated
    r2 = requests.get(f"{cf_url}/resources/channels", params={"lastUpdated": "*", "~size": 100000}, auth=auth)
    has_timestamp = set()
    if r2.status_code == 200:
        has_timestamp = {ch["name"] for ch in r2.json()}

    removed = 0
    for ch in all_channels:
        if ch["name"] not in has_timestamp:
            dr = requests.delete(f"{cf_url}/resources/channels/{ch['name']}", auth=auth)
            if dr.status_code == 200:
                removed += 1
                logger.debug(f"Deleted channel without lastUpdated: {ch['name']}")
            else:
                logger.error(f"Failed to delete {ch['name']}: {dr.status_code} {dr.text}")

    logger.info(f"Cleanup complete: deleted {removed} channel(s) without lastUpdated")


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_ioc(ioc_name, pvlist_dir, ioc_defaults, cf_url, owner, auth,
                use_pva=True, pva_timeout=3, batch_size=100):
    """Process a single IOC: read metadata, pvlist, attempt PVA introspection, feed CF."""
    logger.info(f"Processing IOC: {ioc_name}")

    # Load IOC metadata from the per-IOC config yaml
    ioc_meta = load_ioc_metadata(pvlist_dir, ioc_name)

    # Merge metadata from values.yaml iocDefaults if the IOC's devtype matches
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

    # Timestamp for staleness tracking
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Use devgroup as the channel owner (falls back to the service account)
    channel_owner = ioc_meta.get("devgroup", owner)

    # Load PV list from file
    pv_names = load_pvlist(pvlist_dir, ioc_name)
    if not pv_names:
        logger.warning(f"No PVs found for IOC {ioc_name}, skipping")
        return

    logger.info(f"  IOC {ioc_name}: {len(pv_names)} PVs, meta={ioc_meta}")

    # Ensure properties and tags exist in ChannelFinder
    all_prop_names = set(ioc_meta.keys()) | {"pvProtocol", "lastUpdated", "cfeederVersion"}
    for prop_name in all_prop_names:
        ensure_property(cf_url, prop_name, owner, auth)

    # Tag with IOC name, devgroup, beamline, and zone
    tags = [ioc_name]
    if "devgroup" in ioc_meta:
        tags.append(ioc_meta["devgroup"])
    if "beamline" in ioc_meta:
        tags.append(ioc_meta["beamline"])
    if "zone" in ioc_meta:
        tags.append(ioc_meta["zone"])
    for tag in tags:
        ensure_tag(cf_url, tag, owner, auth)

    # Build channel entries
    channels = []
    for pv_name in pv_names:
        properties = [{"name": k, "owner": channel_owner, "value": v} for k, v in ioc_meta.items()]
        properties.append({"name": "lastUpdated", "owner": channel_owner, "value": now_iso})
        properties.append({"name": "cfeederVersion", "owner": channel_owner, "value": __version__})

        # Try PVA introspection — protocol is pva if pvinfo succeeds, ca otherwise
        # Skip only when pva is explicitly set to false in IOC metadata
        pv_protocol = "ca"
        if use_pva and ioc_meta.get("pva", "").lower() != "false":
            info = pvinfo(pv_name, timeout=pva_timeout)
            if info:
                pv_protocol = "pva"
                properties.append({"name": "pvinfo", "owner": channel_owner, "value": info["pvinfo"][:500]})
                ensure_property(cf_url, "pvinfo", owner, auth)

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

    logger.info(f"  Done: {ioc_name} ({len(pv_names)} PVs)")


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
    parser.add_argument('--values-yaml', type=str, default=None,
                        help='Path to values.yaml for IOC metadata (optional)')
    parser.add_argument('--ioc', type=str, default=None,
                        help='Process only this IOC name (default: all IOCs with pvlist.txt)')
    parser.add_argument('--no-pva', action='store_true',
                        help='Skip PVA introspection even if IOC supports it')
    parser.add_argument('--pva-timeout', type=float, default=0.5,
                        help='Timeout in seconds for pvinfo (default: 0.5)')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Number of channels per POST batch (default: 100)')
    parser.add_argument('--cleanup-days', type=int, default=None,
                        help='Delete channels with lastUpdated older than N days')
    parser.add_argument('--remove-no-timestamp', action='store_true',
                        help='Delete channels that have no lastUpdated property')
    parser.add_argument('--cleanup-only', action='store_true',
                        help='Only run cleanup, do not feed channels')

    args = parser.parse_args()
    auth = (args.username, args.password)

    logger.info(f"cfeeder version {__version__}")

    # Cleanup passes
    if args.remove_no_timestamp:
        cleanup_channels_without_timestamp(args.cf_service_url, auth)
    if args.cleanup_days is not None:
        cleanup_stale_channels(args.cf_service_url, auth, args.cleanup_days)
    if args.cleanup_only:
        if not args.cleanup_days and not args.remove_no_timestamp:
            logger.warning("--cleanup-only requires --cleanup-days N or --remove-no-timestamp")
        return

    # Load iocDefaults from values.yaml if provided
    ioc_defaults = {}
    if args.values_yaml:
        ioc_defaults = load_ioc_names_from_values(args.values_yaml)
        logger.info(f"Loaded {len(ioc_defaults)} IOC defaults from {args.values_yaml}")

    # Determine which IOCs to process
    if args.ioc:
        ioc_names = [args.ioc]
    else:
        ioc_names = get_ioc_dirs(args.pvlist_dir)

    logger.info(f"Processing {len(ioc_names)} IOC(s) from {args.pvlist_dir}")

    for ioc_name in ioc_names:
        try:
            process_ioc(
                ioc_name=ioc_name,
                pvlist_dir=args.pvlist_dir,
                ioc_defaults=ioc_defaults,
                cf_url=args.cf_service_url,
                owner=args.username,
                auth=auth,
                use_pva=not args.no_pva,
                pva_timeout=args.pva_timeout,
                batch_size=args.batch_size
            )
        except Exception as e:
            logger.error(f"Error processing IOC {ioc_name}: {e}", exc_info=True)


if __name__ == "__main__":
    main()

