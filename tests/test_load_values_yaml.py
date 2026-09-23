import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cfeeder  # noqa: E402


def load(text):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(textwrap.dedent(text))
        path = f.name
    try:
        return cfeeder.load_values_yaml(path)
    finally:
        os.unlink(path)


class LoadValuesYamlTest(unittest.TestCase):
    def test_iocs_as_mapping_keyed_by_name(self):
        # Current epik8s beamline format. Iterating this like a list yields
        # string keys and used to crash the whole run with AttributeError.
        defaults, iocs = load("""
            beamline: BTF
            iocDefaults:
              danfysik: {devgroup: mag, devtype: sys8x00}
            epicsConfiguration:
              iocs:
                danfysik-1:
                  name: danfysik-1
                  template: danfysik
                  zones: [BTF1, BTF2]
                  iocparam:
                    - {name: server, value: 10.0.0.1}
        """)
        self.assertEqual(list(iocs), ["danfysik-1"])
        entry = iocs["danfysik-1"]
        self.assertEqual(entry["devgroup"], "mag")  # merged from iocDefaults
        self.assertEqual(entry["zones"], "BTF1,BTF2")
        self.assertEqual(entry["beamline"], "BTF")
        self.assertEqual(entry["server"], "10.0.0.1")

    def test_mapping_key_used_when_name_missing(self):
        _, iocs = load("""
            epicsConfiguration:
              iocs:
                only-a-key: {devgroup: vac}
        """)
        self.assertEqual(iocs["only-a-key"]["name"], "only-a-key")

    def test_legacy_list_form_still_supported(self):
        _, iocs = load("""
            epicsConfiguration:
              iocs:
                - {name: a, devgroup: mag}
                - {name: b, devgroup: vac}
                - {devgroup: nameless-entry-is-skipped}
        """)
        self.assertEqual(sorted(iocs), ["a", "b"])

    def test_missing_or_empty_iocs(self):
        for text in ("beamline: X\n", "epicsConfiguration:\n", "epicsConfiguration:\n  iocs:\n"):
            defaults, iocs = load(text)
            self.assertEqual((defaults, iocs), ({}, {}))


if __name__ == "__main__":
    unittest.main()
