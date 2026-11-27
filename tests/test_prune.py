import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cfeeder  # noqa: E402

CF = "http://cf/ChannelFinder"
AUTH = ("epics", "pw")


def channel(name, ioc, owner="epics"):
    return {"name": name, "owner": owner, "properties": [{"name": "iocName", "value": ioc}]}


class Response:
    def __init__(self, status, payload=None):
        self.status_code, self._payload, self.text = status, payload, ""

    def json(self):
        return self._payload


class FakeCF:
    """ChannelFinder serving `channels` through the scroll API, `page` at a time."""

    def __init__(self, channels, page=2, scroll=True):
        self.channels, self.page, self.scroll = channels, page, scroll
        self.deleted = []

    def get(self, url, params=None, auth=None):
        if "/resources/scroll" in url:
            if not self.scroll:
                return Response(404)
            start = int(url.rsplit("/", 1)[1]) if url.rstrip("/").split("/")[-1].isdigit() else 0
            chunk = self.channels[start:start + self.page]
            return Response(200, {"id": str(start + self.page), "channels": chunk})
        start = int((params or {}).get("~from", 0))
        if start >= cfeeder.PAGE_SIZE:
            return Response(400)
        return Response(200, self.channels[start:start + cfeeder.PAGE_SIZE])

    def delete(self, url, auth=None):
        self.deleted.append(url.rsplit("/", 1)[1])
        return Response(200)


class Session:
    def __init__(self, cf):
        self.cf, self.auth = cf, None

    def delete(self, url):
        return self.cf.delete(url)


def run_prune(cf, **kwargs):
    with mock.patch.object(cfeeder.requests, "get", cf.get), \
         mock.patch.object(cfeeder.requests, "Session", lambda: Session(cf)):
        return cfeeder.prune_channels(CF, AUTH, "epics", **kwargs)


class FetchAllChannelsTest(unittest.TestCase):
    def test_reads_every_scroll_page(self):
        cf = FakeCF([channel(f"PV{i}", "ioc") for i in range(7)], page=3)
        with mock.patch.object(cfeeder.requests, "get", cf.get):
            names = [c["name"] for c in cfeeder.fetch_all_channels(CF, AUTH)]
        self.assertEqual(names, [f"PV{i}" for i in range(7)])

    def test_falls_back_to_offset_paging_without_scroll(self):
        cf = FakeCF([channel("A", "ioc")], scroll=False)
        with mock.patch.object(cfeeder.requests, "get", cf.get):
            self.assertEqual([c["name"] for c in cfeeder.fetch_all_channels(CF, AUTH)], ["A"])


class SelectConfiguredTest(unittest.TestCase):
    def test_ignores_directories_of_removed_iocs_and_reports_missing_ones(self):
        iocs = {
            "fp1-cam-vis-01": {},
            "FI8-CAM-VIS-001": {},          # names match case-insensitively
            "old-ioc": {"disable": True},   # disabled: not fed
            "no-dir-ioc": {},
        }
        dirs = ["fp1-cam-vis-01", "fp1-cam-vis-1", "fi8-cam-vis-001", "old-ioc"]
        selected, missing = cfeeder.select_configured(dirs, iocs)
        self.assertEqual(selected, ["fp1-cam-vis-01", "fi8-cam-vis-001"])
        self.assertEqual(missing, ["no-dir-ioc"])

    def test_enabled_false_counts_as_disabled(self):
        self.assertEqual(cfeeder.configured_ioc_names({"a": {"enabled": False}, "b": {}}), {"b"})


class PruneTest(unittest.TestCase):
    def setUp(self):
        self.cf = FakeCF([
            channel("CAM:1:Rate", "fp1-cam-vis-1"),   # live PV relabelled by a stale IOC: kept by name
            channel("CAM:OLD:Rate", "fp1-cam-vis-1"),  # stale
            channel("CAM:2:Rate", "fi8-cam-vis-001"),
            channel("CAM:2:Gone", "fi8-cam-vis-001"),  # dropped from a live IOC's pvlist
            channel("VAC:1", "no-dir-ioc"),            # IOC with no pvlist this run: kept
            channel("OTHER:1", "x", owner="admin"),    # not ours: never touched
        ])
        self.keep = {"CAM:1:Rate", "CAM:2:Rate"}

    def test_deletes_channels_in_no_fed_pvlist(self):
        removed = run_prune(self.cf, keep_names=self.keep, keep_iocs=["no-dir-ioc"], max_fraction=1.0)
        self.assertEqual(removed, ["CAM:OLD:Rate", "CAM:2:Gone"])
        self.assertEqual(self.cf.deleted, ["CAM:OLD:Rate", "CAM:2:Gone"])

    def test_dry_run_deletes_nothing(self):
        stale = run_prune(self.cf, keep_names=self.keep, keep_iocs=["no-dir-ioc"], max_fraction=1.0, dry_run=True)
        self.assertEqual(stale, ["CAM:OLD:Rate", "CAM:2:Gone"])
        self.assertEqual(self.cf.deleted, [])

    def test_refuses_above_max_fraction(self):
        # 2 of 6 channels = 33% > 25%
        self.assertEqual(run_prune(self.cf, keep_names=self.keep, keep_iocs=["no-dir-ioc"], max_fraction=0.25), [])
        self.assertEqual(self.cf.deleted, [])

    def test_refuses_with_no_pv_list_at_all(self):
        self.assertEqual(run_prune(self.cf, keep_names=set(), max_fraction=1.0), [])
        self.assertEqual(self.cf.deleted, [])


if __name__ == "__main__":
    unittest.main()
