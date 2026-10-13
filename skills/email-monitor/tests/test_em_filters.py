"""The filters are GENERATED from sender_map.json, never hand written. That is
the whole point: a hand written filter is a second copy of the standard, and two
copies drift. These tests pin the properties that make the generated set safe."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_filters  # noqa: E402

SENDER_MAP = {
    "version": 1,
    "by_address": {"a@example.com": "Receipt", "b@example.com": "Receipt",
                   "c@example.org": "Promo"},
    "by_domain": {"news.example.net": "Promo"},
}
NS = {"a": "http://www.w3.org/2005/Atom", "apps": "http://schemas.google.com/apps/2006"}


def entries(xml):
    return ET.fromstring(xml).findall("a:entry", NS)


def props(entry):
    return {p.get("name"): p.get("value")
            for p in entry.findall("apps:property", NS)}


def test_no_generated_filter_skips_the_inbox():
    """Generated filters label only. Hiding mail is a separate decision that is
    not the generator's to make."""
    for e in entries(em_filters.compile_filters(SENDER_MAP)):
        assert "shouldArchive" not in props(e)
