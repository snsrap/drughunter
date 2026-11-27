import unittest

from proteinviewer.errors import BadRequest
from proteinviewer.schemas import (
    BufferRequest,
    DatasetIdRequest,
    DatasetResolveRequest,
    DatasetRowsRequest,
    DatasetUploadRequest,
    GenerateRequest,
    JobQuery,
    PocketRequest,
    PrepRequest,
)


class SchemaTest(unittest.TestCase):
    def test_buffer_defaults(self):
        r = BufferRequest.parse({"data": "ATOM"})
        self.assertEqual((r.token, r.fmt), ("structure", None))
        self.assertEqual(BufferRequest.parse({"data": "A", "token": "t"}).token, "t")
        with self.assertRaises(BadRequest):
            BufferRequest.parse({"data": "   "})

    def test_pocket_defaults_and_normalisation(self):
        r = PocketRequest.parse(
            {"token": "t", "method": " LaCuNa ", "ligand": ["mk1", " ", "hem"], "cutoff": ""}
        )
        self.assertEqual(r.method, "lacuna")
        self.assertEqual(r.cutoff, 6.0)
        self.assertEqual(r.ligands, frozenset({"MK1", "HEM"}))
        self.assertEqual(r.raw["method"], " LaCuNa ")

    def test_pocket_rejects_bad_values(self):
        for bad in (
            {"cutoff": "abc"},
            {"cutoff": 0.1},
            {"cutoff": True},
            {"cutoff": float("nan")},
            {"ligand": "MK1"},
        ):
            with self.assertRaises(BadRequest, msg=bad):
                PocketRequest.parse(bad)

    def test_prep(self):
        r = PrepRequest.parse({"token": "t", "noRebuild": {"A": [1, "2"]}})
        self.assertEqual((r.ph, dict(r.no_rebuild)), (7.0, {"A": (1, 2)}))
        for bad in (
            {"noRebuild": [1]},
            {"noRebuild": {"A": "12"}},
            {"noRebuild": {"A": ["x"]}},
            {"ph": 15},
        ):
            with self.assertRaises(BadRequest, msg=bad):
                PrepRequest.parse(bad)

    def test_generate(self):
        r = GenerateRequest.parse({"token": "t", "ranks": [2, "1", 2]})
        self.assertEqual((r.ranks, r.n, r.cfg, r.batch_size), (frozenset({1, 2}), 50, 0.5, 50))
        for bad in (
            {"ranks": []},
            {"ranks": "1"},
            {"ranks": [1], "n": 0},
            {"ranks": [1], "n": 2.5},
            {"ranks": ["x"]},
        ):
            with self.assertRaises(BadRequest, msg=bad):
                GenerateRequest.parse(bad)

    def test_job_query(self):
        q = JobQuery.parse({"id": "j", "mols": "1"})
        self.assertEqual((q.job_id, q.molecules, q.table), ("j", True, False))


class DatasetSchemaTest(unittest.TestCase):
    def test_upload_keeps_the_text_and_only_the_files_own_name(self):
        r = DatasetUploadRequest.parse({"name": "C:\\data\\../ligands.csv", "data": "a\n1\n"})
        self.assertEqual((r.name, r.data), ("ligands.csv", "a\n1\n"))
        self.assertEqual(DatasetUploadRequest.parse({"data": "a"}).name, "dataset.csv")
        for bad in ({}, {"data": "  \n"}, {"data": 5}):
            with self.assertRaises(BadRequest, msg=bad):
                DatasetUploadRequest.parse(bad)

    def test_resolve_without_a_target_is_prediction_only(self):
        r = DatasetResolveRequest.parse(
            {"dataset_id": "ds-1", "structure_column": "SMILES", "target_column": ""}
        )
        self.assertEqual(r.config, {"structure_column": "SMILES", "target": None})
        r = DatasetResolveRequest.parse({"dataset_id": "ds-1", "structure_column": "SMILES"})
        self.assertIsNone(r.config["target"])

    def test_resolve_a_regression_target_in_full(self):
        r = DatasetResolveRequest.parse(
            {
                "dataset_id": "ds-1",
                "structure_column": "SMILES",
                "target_column": "Ki",
                "target_kind": "regression",
                "qualifier_column": None,
                "source_units": "nM",
                "transform": "pki",
            }
        )
        self.assertEqual(
            r.config["target"],
            {
                "column": "Ki",
                "kind": "regression",
                "qualifier_column": None,
                "positive_class": None,
                "transform": "pki",
                "source_units": "nM",
            },
        )

    def test_resolve_makes_the_user_confirm_what_cannot_be_inferred(self):
        base = {"dataset_id": "ds-1", "structure_column": "S", "target_column": "y"}
        for bad, match in (
            ({}, "regression or classification"),
            ({"target_kind": "ranking"}, "regression or classification"),
            ({"target_kind": "regression"}, "confirm the qualifier"),
            ({"target_kind": "regression", "qualifier_column": ""}, "column name or null"),
            ({"target_kind": "regression", "qualifier_column": None}, "confirm the units"),
            (
                {
                    "target_kind": "regression",
                    "qualifier_column": None,
                    "source_units": "nM",
                    "transform": "log",
                },
                "unknown transform",
            ),
        ):
            with self.assertRaisesRegex(BadRequest, match):
                DatasetResolveRequest.parse({**base, **bad})
        for bad in ({"structure_column": ""}, {"dataset_id": ""}):
            with self.assertRaises(BadRequest):
                DatasetResolveRequest.parse({**base, **bad})

    def test_resolve_classification_needs_no_units(self):
        r = DatasetResolveRequest.parse(
            {
                "dataset_id": "ds-1",
                "structure_column": "S",
                "target_column": "y",
                "target_kind": "classification",
                "qualifier_column": None,
                "positive_class": "1",
            }
        )
        self.assertEqual(r.config["target"]["positive_class"], "1")
        self.assertNotIn("source_units", r.config["target"])

    def test_id_comes_from_a_body_or_a_query(self):
        self.assertEqual(DatasetIdRequest.parse({"dataset_id": "ds-1"}).dataset_id, "ds-1")
        self.assertEqual(DatasetIdRequest.parse({"id": "ds-2"}).dataset_id, "ds-2")
        with self.assertRaises(BadRequest):
            DatasetIdRequest.parse({})

    def test_rows_defaults_and_the_page_ceiling(self):
        r = DatasetRowsRequest.parse({"dataset_id": "ds-1"})
        self.assertEqual((r.offset, r.limit, r.sort, r.filters, r.depict), (0, 50, None, (), True))
        self.assertEqual(DatasetRowsRequest.parse({"dataset_id": "d", "limit": 100}).limit, 100)
        for bad in (
            {"limit": 101},
            {"limit": 0},
            {"offset": -1},
            {"offset": 1.5},
            {"sort": "name"},
            {"filters": {"key": "a"}},
            {"filters": ["a"]},
            {},
        ):
            with self.assertRaises(BadRequest, msg=bad):
                DatasetRowsRequest.parse({"dataset_id": "ds-1", **bad} if bad else {})
        r = DatasetRowsRequest.parse(
            {"dataset_id": "d", "depict": False, "filters": [{"key": "a"}]}
        )
        self.assertFalse(r.depict)
        self.assertEqual(r.filters[0]["key"], "a")


if __name__ == "__main__":
    unittest.main()
