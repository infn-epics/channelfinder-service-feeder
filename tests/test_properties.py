import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cfeeder  # noqa: E402


class MetadataPropertiesTest(unittest.TestCase):
    def test_empty_values_are_left_out(self):
        # ChannelFinder refuses a whole POST with an empty property value; an
        # `asset: ''` in values.yaml kept every PV of the IOC out of ChannelFinder.
        props = cfeeder.metadata_properties(
            {"asset": "", "desc": "  ", "server": None, "devgroup": "mot", "pva": True}, "epics")
        self.assertEqual(props, [
            {"name": "devgroup", "owner": "epics", "value": "mot"},
            {"name": "pva", "owner": "epics", "value": "True"},
        ])


if __name__ == "__main__":
    unittest.main()
