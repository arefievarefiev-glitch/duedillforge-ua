"""Static checks of the shipped checklists."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddforge import checklists  # noqa: E402


class ShippedChecklists(unittest.TestCase):
    def setUp(self):
        checklists.load_all.cache_clear()
        self.catalog = checklists.load_all()

    def test_catalog_not_empty(self):
        for cid in ("corporate", "contracts", "real-estate", "land", "labor", "finance", "litigation", "tax",
                    "permits", "ip-it", "agro-territory", "agro-support", "agro-assets"):
            self.assertIn(cid, self.catalog)

    def test_every_standalone_checklist_resolves_in_all_modes(self):
        for cid, data in self.catalog.items():
            if "items" not in data:
                continue
            for depth in ("full", "redflag"):
                for modules in (["general"], ["general", "agro"]):
                    items, norms = checklists.resolve_block_items({"id": "x", "checklist": cid}, depth, modules)
                    self.assertTrue(items, f"{cid}/{depth}: no items")
                    ids = {i["id"] for i in items}
                    self.assertEqual(len(ids), len(items))

    def test_every_norm_reference_resolves(self):
        for cid, data in self.catalog.items():
            known = {n["id"] for n in data.get("norms", [])}
            for item in data.get("items", []):
                for nid in item.get("norm_ids", []):
                    self.assertIn(nid, known, f"{cid}/{item['id']}: unknown norm {nid}")
            for target, overlay in data.get("overlays", {}).items():
                base = {n["id"] for n in self.catalog[target].get("norms", [])}
                known = base | {n["id"] for n in overlay.get("norms", [])}
                for item in overlay["items"]:
                    for nid in item.get("norm_ids", []):
                        self.assertIn(nid, known, f"agro/{target}/{item['id']}: unknown norm {nid}")

    def test_every_block_has_core_items_for_redflag(self):
        for cid, data in self.catalog.items():
            if "items" in data:
                self.assertTrue([i for i in data["items"] if i["tier"] == "core"], cid)

    def test_norms_are_named_at_act_level_only(self):
        # a checklist names the act, never an article: the articles are found in the legislation notebook
        pattern = re.compile(r"\bст(?:\.|атт\w+)\s*\d", re.I)
        for cid, data in self.catalog.items():
            for norm in data.get("norms", []):
                self.assertFalse(pattern.search(norm["act"]), norm["act"])
            for item in data.get("items", []):
                self.assertFalse(pattern.search(item["question"]), f"{cid}/{item['id']}")

    def test_whole_doc_items_have_probes_that_find_real_text(self):
        sample = "У разі зміни контролю контрагент має право в односторонньому порядку розірвати договір. Арбітраж. Застава."
        item = next(i for i in self.catalog["contracts"]["items"] if i["id"] == "CON-01")
        self.assertTrue(any(re.search(p, sample, re.I) for p in item["probes"]))
        item = next(i for i in self.catalog["contracts"]["items"] if i["id"] == "CON-02")
        self.assertTrue(any(re.search(p, sample, re.I) for p in item["probes"]))


if __name__ == "__main__":
    unittest.main()
