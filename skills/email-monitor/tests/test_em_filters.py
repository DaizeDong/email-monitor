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


def test_addresses_sharing_a_label_are_merged_into_one_filter():
    got = entries(em_filters.compile_filters(SENDER_MAP))
    receipt = [props(e) for e in got if props(e).get("label") == "Receipt"]
    assert len(receipt) == 1
    assert "a@example.com" in receipt[0]["from"]
    assert "b@example.com" in receipt[0]["from"]


def test_no_generated_filter_skips_the_inbox():
    """Generated filters label only. Hiding mail is a separate decision that is
    not the generator's to make."""
    for e in entries(em_filters.compile_filters(SENDER_MAP)):
        assert "shouldArchive" not in props(e)


def test_no_generated_filter_matches_on_free_text():
    """Every generated criterion is a from: clause. The keyword rules are the
    exact thing being retired, so the generator must be incapable of emitting
    one."""
    for e in entries(em_filters.compile_filters(SENDER_MAP)):
        p = props(e)
        assert "hasTheWord" not in p and "subject" not in p
        assert p.get("from")


def test_empty_map_yields_no_entries():
    assert entries(em_filters.compile_filters({})) == []


def test_output_parses_as_xml():
    ET.fromstring(em_filters.compile_filters(SENDER_MAP))


def test_list_rules_are_reported_rather_than_silently_dropped():
    sm = {"version": 1, "by_address": {"a@example.com": "Receipt"},
          "by_list_id": {"users.lists.example.net": "Promo"}}
    assert em_filters.uncompilable(sm)["by_list_id"] == 1
    xml = em_filters.compile_filters(sm)
    assert "users.lists.example.net" not in xml, "a list rule must not leak into the XML"
    assert "a@example.com" in xml, "address rules must still compile"
