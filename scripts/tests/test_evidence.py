"""Evidence layer: structural index, text quality, quotation search, chain coverage, scans."""

import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TMP = tempfile.mkdtemp(prefix="ddf-evidence-")
os.environ["CLAUDE_PLUGIN_DATA"] = TMP

from ddforge import evidence, indexer, quality  # noqa: E402

LAW = """Закон України
Розділ I. Загальні положення
Стаття 1. Визначення термінів
У цьому Законі терміни вживаються в такому значенні: учасник — особа, яка має частку.
Стаття 2. Права учасників
Учасник має право:
а) брати участь в управлінні;
б) одержувати частину прибутку;
в) вийти з товариства.
Стаття 2-1. Особливі права
Статут може передбачати особливі права.
Розділ II. Відчуження
Стаття 3. Відчуження частки
Учасник має право відчужити частку іншому учаснику.
Додаток 1
Перелік форм.
"""

CONTRACT = """ДОГОВІР ПОСТАВКИ № 5
1. Предмет договору
1.1. Постачальник передає товар.
1.2. Покупець оплачує товар.
2. Відповідальність сторін
2.1. За прострочення Покупець сплачує пеню.
2.2. Загальна відповідальність обмежена сумою договору.
3. Прикінцеві положення
3.1. Договір набирає чинності з підписання.
"""


class Index(unittest.TestCase):
    def test_statute_units(self):
        idx = indexer.build_index(LAW)
        keys = [u["key"] for u in idx["units"]]
        for expected in ("ст.1", "ст.2", "ст.2-1", "ст.3", "розд.i", "дод.1"):
            self.assertIn(expected, keys, keys)

    def test_contract_clauses(self):
        idx = indexer.build_index(CONTRACT)
        keys = [u["key"] for u in idx["units"]]
        self.assertIn("2.1", keys)
        self.assertIn("3", keys)

    def test_find_unit_variants(self):
        idx = indexer.build_index(LAW)
        for ref in ("Стаття 2", "ст. 2", "статті 2", "ст.2", "Статтю 2", "стаття №2"):
            unit, _ = indexer.find_unit(idx, ref)
            self.assertIsNotNone(unit, ref)
            self.assertEqual(unit["key"], "ст.2")

    def test_unit_texts_do_not_overlap(self):
        idx = indexer.build_index(LAW)
        spans = sorted((u["start"], u["end"]) for u in idx["units"] if u["rank"] == idx["units"][0]["rank"])
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            self.assertLessEqual(e1, s2)


class Quality(unittest.TestCase):
    def test_clean_text_ok(self):
        self.assertEqual(quality.assess(CONTRACT * 3)["quality"], "ok")

    def test_short_text_is_not_trusted(self):
        self.assertEqual(quality.assess("Договір.")["quality"], "none")

    def test_homoglyph_noise_is_flagged(self):
        rnd = random.Random(3)
        table = str.maketrans("оасеір", "oaceip")
        noisy = " ".join(w.translate(table) if rnd.random() < 0.15 else w for w in (CONTRACT * 6).split(" "))
        self.assertIn(quality.assess(noisy)["quality"], ("degraded", "none"))

    def test_garbage_is_none(self):
        self.assertEqual(quality.assess("%$#@ 12 ^^ ~~ " * 80)["quality"], "none")


class Evidence(unittest.TestCase):
    def setUp(self):
        self.work = Path(TMP) / f"w-{random.randrange(10**9)}"
        self.work.mkdir(parents=True)
        info = evidence.register_text(self.work, origin="legislation", notebook_key="law", source_id="L1",
                                      title="Закон", kind="law", text=LAW * 2)
        self.doc = info["doc_id"]

    def test_register_is_idempotent_per_source(self):
        again = evidence.register_text(self.work, origin="legislation", notebook_key="law", source_id="L1",
                                       title="Закон", kind="law", text=LAW * 2)
        self.assertEqual(again["doc_id"], self.doc)
        self.assertTrue(again["reused"])

    def test_wrapped_connector_json_is_unwrapped(self):
        info = evidence.register_text(self.work, origin="notebook", notebook_key="nb1", source_id="x", title="x",
                                      kind="other", text='{"content": ' + __import__("json").dumps(CONTRACT * 3, ensure_ascii=False) + '}')
        self.assertTrue(evidence.read_text(self.work, info["doc_id"]).startswith("ДОГОВІР"))

    def test_quote_exact_and_normalized(self):
        ok = evidence.verify_quote(self.work, self.doc, "Учасник  має право відчужити частку іншому учаснику.")
        self.assertEqual(ok["verified"], "exact")
        bad = evidence.verify_quote(self.work, self.doc, "Учасник зобов'язаний відчужити частку державі.")
        self.assertEqual(bad["verified"], "not_found")

    def test_quote_with_typographic_variants(self):
        info = evidence.register_text(self.work, origin="notebook", notebook_key="nb1", source_id="q", title="q",
                                      kind="other", text=("Сторони домовились: «поставка» — це передача товару. " * 8))
        res = evidence.verify_quote(self.work, info["doc_id"], 'Сторони домовились: "поставка" - це передача товару.')
        self.assertEqual(res["verified"], "exact")

    def test_cut_logs_reads_and_reports_missing_units(self):
        cut = evidence.cut(self.work, self.doc, unit="Стаття 3")
        self.assertIn("Відчуження частки", cut["text"])
        self.assertIn("ст.3", evidence.cut_unit_keys(self.work, self.doc))
        missing = evidence.cut(self.work, self.doc, unit="Стаття 99")
        self.assertEqual(missing["error"], "unit_not_found")
        self.assertIn("available", missing)

    def test_cut_truncation_is_reported_and_continuable(self):
        big = "Стаття 1. Великий текст\n" + ("Речення про відчуження частки в товаристві. " * 600)
        info = evidence.register_text(self.work, origin="legislation", notebook_key="law", source_id="big", title="big",
                                      kind="law", text=big)
        cut = evidence.cut(self.work, info["doc_id"], unit="Стаття 1", max_chars=2000)
        self.assertTrue(cut["truncated"])
        more = evidence.cut_span(self.work, info["doc_id"], cut["continues_at"], cut["end"], max_chars=2000)
        self.assertTrue(more["text"])

    def test_chain_coverage_detects_gap(self):
        quotes_full = ["Учасник має право:", "а) брати участь в управлінні;", "б) одержувати частину прибутку;", "в) вийти з товариства."]
        res = evidence.chain_coverage(self.work, self.doc, "Стаття 2", quotes_full)
        self.assertTrue(res["ok"], res)
        gap = evidence.chain_coverage(self.work, self.doc, "Стаття 2", [quotes_full[0], quotes_full[3]])
        self.assertFalse(gap["ok"])

    def test_scan_tracks_review(self):
        scan = evidence.scan(self.work, [self.doc], [r"відчужи\w+"], item_id="t:1")
        entry = scan["docs"][self.doc]
        self.assertGreater(entry["hits_total"], 0)
        sha = evidence.probes_sha([r"відчужи\w+"])
        state = evidence.latest_scan_state(self.work, self.doc, sha)
        self.assertEqual(len(state["unreviewed"]), entry["hits_total"])
        for hit in entry["hits"]:
            evidence.cut(self.work, self.doc, scan_id=scan["scan_id"], hit=hit["n"])
        self.assertEqual(evidence.latest_scan_state(self.work, self.doc, sha)["unreviewed"], [])

    def test_scan_cap_marks_truncation(self):
        scan = evidence.scan(self.work, [self.doc], [r"Стаття"], cap=3)
        self.assertTrue(scan["docs"][self.doc]["truncated"])

    def test_scope_includes_undetermined(self):
        docs = [{"doc_id": "a", "kind": "statut"}, {"doc_id": "b", "kind": "undetermined"}, {"doc_id": "c", "kind": "tax_doc"},
                {"doc_id": "d", "kind": "other"}]
        self.assertEqual({d["doc_id"] for d in evidence.block_docs_in_scope(docs, ["statut"], "unit")}, {"a", "b"})
        self.assertEqual({d["doc_id"] for d in evidence.block_docs_in_scope(docs, ["statut"], "whole_doc")}, {"a", "b", "d"})


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
