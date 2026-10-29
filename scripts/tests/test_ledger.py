"""Ledger rules: what an agent may claim and what the program grants."""

import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TMP = tempfile.mkdtemp(prefix="ddf-ledger-")
os.environ["CLAUDE_PLUGIN_DATA"] = TMP

from ddforge import evidence, ledger, machine, preflight, store  # noqa: E402

CONTRACT = ("1. Предмет договору\n1.1. Постачальник передає товар, а Покупець оплачує його у строки, визначені "
            "цим договором і додатками до нього.\n2. Відповідальність\n2.1. За прострочення оплати Покупець "
            "сплачує пеню у розмірі подвійної облікової ставки Національного банку України.\n"
            "3. Прикінцеві положення\n3.1. Договір набирає чинності з моменту підписання сторонами.\n") * 2


def garble(text: str, rate: float = 0.15) -> str:
    rnd = random.Random(7)
    table = str.maketrans("оасеір", "oaceip")
    return " ".join(w.translate(table) if rnd.random() < rate else w for w in text.split(" "))


def brief():
    return {"object": "Test", "depth": "full", "legislation": {"ref": "law"},
            "blocks": [{"id": "blk", "title": "B", "checklist": "custom", "notebook": {"ref": "nb"}, "custom_items": [
                {"id": "U1", "title": "u", "question": "q", "tier": "core", "scope": "unit", "doc_kinds": ["contract_commercial"]},
                {"id": "W1", "title": "w", "question": "q", "tier": "core", "scope": "whole_doc",
                 "doc_kinds": ["contract_commercial"], "probes": ["пен[яюі]"]},
                {"id": "W2", "title": "w2", "question": "q", "tier": "core", "scope": "whole_doc",
                 "doc_kinds": ["contract_commercial"], "probes": ["арбітраж"]},
                {"id": "S1", "title": "s", "question": "q", "tier": "extended", "scope": "unit", "doc_kinds": ["contract_commercial"]},
                {"id": "N1", "title": "n", "question": "q", "tier": "extended", "scope": "unit", "doc_kinds": ["contract_commercial"]},
                {"id": "R1", "title": "r", "question": "q", "tier": "extended", "scope": "unit", "doc_kinds": ["contract_commercial"]},
            ]}]}


class LedgerRules(unittest.TestCase):
    def setUp(self):
        self.work = Path(TMP) / f"w-{random.randrange(10**9)}"
        self.work.mkdir(parents=True)
        opts = {"scan_hit_cap": 50}
        from ddforge import config
        resolved = config.resolve_options()
        self.state, items = preflight.new_state(brief(), resolved["options"], resolved["source"], "t-x")
        ledger.save_items(self.work, items)
        machine.save(self.work, self.state)
        self.doc = evidence.register_text(self.work, origin="notebook", notebook_key="nb1", source_id="s1",
                                          title="Договір поставки", kind="contract_commercial", text=CONTRACT)
        ledger.init_block(self.work, self.state, "blk")

    def apply(self, result):
        ledger.apply_results(self.work, self.state, "blk", [result], 50)
        return ledger.load_ledger(self.work, "blk")["items"][result["item_id"]]

    def test_clear_without_basis_is_downgraded(self):
        row = self.apply({"item_id": "U1", "status": "clear"})
        self.assertEqual((row["status"], row["reason"]), ("unverified", "clear_not_supported"))
        self.assertEqual(row["downgraded_from"], "clear")

    def test_scan_unreviewed_hits_block_clear(self):
        did = self.doc["doc_id"]
        evidence.scan(self.work, [did], ["пен[яюі]"], item_id="blk:W1")
        row = self.apply({"item_id": "W1", "status": "clear", "basis": "scan"})
        self.assertEqual(row["status"], "unverified")
        self.assertIn("непереглянуті", row["detail"])

    def test_scan_reviewed_hits_grant_clear(self):
        did = self.doc["doc_id"]
        scan = evidence.scan(self.work, [did], ["пен[яюі]"], item_id="blk:W1")
        for hit in scan["docs"][did]["hits"]:
            evidence.cut(self.work, did, scan_id=scan["scan_id"], hit=hit["n"])
        row = self.apply({"item_id": "W1", "status": "clear", "basis": "scan"})
        self.assertEqual(row["status"], "clear")

    def test_scan_with_zero_hits_grants_clear(self):
        did = self.doc["doc_id"]
        evidence.scan(self.work, [did], ["арбітраж"], item_id="blk:W2")
        self.assertEqual(self.apply({"item_id": "W2", "status": "clear", "basis": "scan"})["status"], "clear")

    def test_whole_doc_item_needs_scan(self):
        row = self.apply({"item_id": "W2", "status": "clear", "basis": "units",
                          "units_read": {self.doc["doc_id"]: ["1"]}})
        self.assertEqual(row["status"], "unverified")

    def test_units_not_cut_are_refused(self):
        row = self.apply({"item_id": "U1", "status": "clear", "basis": "units",
                          "units_read": {self.doc["doc_id"]: ["1", "2", "3"]}})
        self.assertEqual(row["status"], "unverified")

    def test_units_cut_are_accepted(self):
        did = self.doc["doc_id"]
        index = evidence.read_index(self.work, did)
        for u in index["units"]:
            evidence.cut(self.work, did, unit=u["key"])
        keys = [u["key"] for u in index["units"]]
        row = self.apply({"item_id": "U1", "status": "clear", "basis": "units", "units_read": {did: keys}})
        self.assertEqual(row["status"], "clear", row)

    def test_degraded_text_never_gives_clear(self):
        text = garble(CONTRACT * 3)
        evidence.register_text(self.work, origin="notebook", notebook_key="nb1", source_id="s1",
                               title="Договір поставки", kind="contract_commercial", text=text)
        did = self.doc["doc_id"]
        doc = evidence.get_doc(self.work, did)
        self.assertIn(doc["text_quality"], ("degraded", "none"))
        row = self.apply({"item_id": "U1", "status": "clear", "basis": "chain"})
        self.assertEqual(row["status"], "unverified")
        self.assertIn(row["reason"], ("text_degraded", "scan_unreadable"))

    def test_risk_needs_valid_finding(self):
        row = self.apply({"item_id": "R1", "status": "risk", "findings": []})
        self.assertEqual((row["status"], row["reason"]), ("unverified", "agent_failed"))

    def test_screened_needs_a_query(self):
        row = self.apply({"item_id": "S1", "status": "screened", "queries": 0})
        self.assertEqual(row["status"], "unverified")
        row = self.apply({"item_id": "N1", "status": "screened", "queries": 2})
        self.assertEqual(row["status"], "screened")

    def test_na_needs_reason(self):
        self.assertEqual(self.apply({"item_id": "N1", "status": "na"})["status"], "unverified")

    def test_unknown_status_is_not_trusted(self):
        self.assertEqual(self.apply({"item_id": "U1", "status": "ok"})["reason"], "agent_failed")

    def test_settled_row_cannot_be_overwritten(self):
        self.apply({"item_id": "N1", "status": "screened", "queries": 2})
        out = ledger.apply_results(self.work, self.state, "blk", [{"item_id": "N1", "status": "clear"}], 50)
        self.assertEqual(out["applied"], 0)

    def test_chain_gap_is_detected(self):
        did = self.doc["doc_id"]
        row = self.apply({"item_id": "U1", "status": "clear", "basis": "chain",
                          "chain": [{"doc_id": did, "unit": "2", "quotes": ["За прострочення оплати Покупець сплачує пеню"]}]})
        self.assertEqual(row["status"], "unverified")
        self.assertEqual(row["reason"], "chain_gap")


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
