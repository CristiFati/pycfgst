import os
import tempfile
import unittest

import yaml

from pycfgst._pipeline_parser_config import (
    PipelineParserConfig,
    _apply_items,
    _classify_entries,
    _is_glob,
)


class ClassifyEntriesTestCase(unittest.TestCase):

    def test_empty(self):
        g, globs, exacts = _classify_entries({})
        self.assertIsNone(g)
        self.assertEqual(globs, [])
        self.assertEqual(exacts, {})

    def test_none(self):
        g, globs, exacts = _classify_entries(None)
        self.assertIsNone(g)
        self.assertEqual(globs, [])
        self.assertEqual(exacts, {})

    def test_global_only(self):
        g, globs, exacts = _classify_entries({"*": ["parent"]})
        self.assertEqual(g, ["parent"])
        self.assertEqual(globs, [])
        self.assertEqual(exacts, {})

    def test_mixed(self):
        config = {"*": ["a"], "te*": ["b"], "*ee": ["c"], "tee": ["d"]}
        g, globs, exacts = _classify_entries(config)
        self.assertEqual(g, ["a"])
        self.assertEqual(len(globs), 2)
        self.assertEqual(globs[0], ("te*", ["b"]))
        self.assertEqual(globs[1], ("*ee", ["c"]))
        self.assertEqual(exacts, {"tee": ["d"]})

    def test_is_glob(self):
        self.assertFalse(_is_glob("*"))
        self.assertTrue(_is_glob("te*"))
        self.assertTrue(_is_glob("*ee"))
        self.assertTrue(_is_glob("t?e"))
        self.assertTrue(_is_glob("[abc]"))
        self.assertFalse(_is_glob("tee"))


class ApplyItemsTestCase(unittest.TestCase):

    def test_add(self):
        ep, pp = set(), set()
        _apply_items(ep, pp, ["a", "b"])
        self.assertEqual(ep, {"a", "b"})
        self.assertEqual(pp, set())

    def test_negation(self):
        ep, pp = {"a", "b"}, set()
        _apply_items(ep, pp, ["!a"])
        self.assertEqual(ep, {"b"})

    def test_negation_missing(self):
        ep, pp = {"b"}, set()
        _apply_items(ep, pp, ["!a"])
        self.assertEqual(ep, {"b"})

    def test_discard_all(self):
        ep, pp = set(), set()
        _apply_items(ep, pp, ["a", "*"])
        self.assertIn("*", ep)

    def test_pad_properties(self):
        ep, pp = set(), set()
        _apply_items(ep, pp, [{"@pad": ["direction", "template"]}])
        self.assertEqual(ep, set())
        self.assertEqual(pp, {"direction", "template"})

    def test_pad_negation(self):
        ep, pp = set(), {"direction", "template"}
        _apply_items(ep, pp, [{"@pad": ["!direction"]}])
        self.assertEqual(pp, {"template"})

    def test_pad_discard_all(self):
        ep, pp = set(), {"direction"}
        _apply_items(ep, pp, [{"@pad": ["*"]}])
        self.assertIn("*", pp)


class ResolveDefaultsOnlyTestCase(unittest.TestCase):

    def _config_from_properties(self, properties, explicit_request_pads=()):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries(properties)
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = set(explicit_request_pads)
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = set()
        return cfg

    def test_global_match(self):
        r = self._config_from_properties({"*": ["parent"]})
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"parent"})
        self.assertNotIn("*", result.element_properties)

    def test_exact_match(self):
        r = self._config_from_properties({"tee": ["alloc-pad"]})
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"alloc-pad"})

    def test_no_match(self):
        r = self._config_from_properties({"tee": ["alloc-pad"]})
        result = r.resolve_filters("queue")
        self.assertEqual(result.element_properties, set())

    def test_glob_match(self):
        r = self._config_from_properties({"te*": ["alloc-pad"]})
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"alloc-pad"})
        result2 = r.resolve_filters("queue")
        self.assertEqual(result2.element_properties, set())

    def test_glob_suffix(self):
        r = self._config_from_properties({"*ee": ["alloc-pad"]})
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"alloc-pad"})
        result2 = r.resolve_filters("tea")
        self.assertEqual(result2.element_properties, set())

    def test_specificity_exact_beats_glob(self):
        r = self._config_from_properties(
            {
                "te*": ["a", "b"],
                "tee": ["!a", "c"],
            }
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("a", result.element_properties)
        self.assertIn("b", result.element_properties)
        self.assertIn("c", result.element_properties)

    def test_specificity_glob_beats_global(self):
        r = self._config_from_properties(
            {
                "*": ["parent", "name"],
                "te*": ["!parent"],
            }
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("parent", result.element_properties)
        self.assertIn("name", result.element_properties)

    def test_discard_all(self):
        r = self._config_from_properties(
            {
                "*": ["*"],
            }
        )
        result = r.resolve_filters("anything")
        self.assertIn("*", result.element_properties)

    def test_pad_properties(self):
        r = self._config_from_properties(
            {
                "tee": [{"@pad": ["direction", "template"]}],
            }
        )
        result = r.resolve_filters("tee")
        self.assertEqual(result.pad_properties, {"direction", "template"})
        self.assertEqual(result.element_properties, set())

    def test_combined_element_and_pad(self):
        r = self._config_from_properties(
            {
                "*": ["parent", {"@pad": ["direction"]}],
                "tee": ["alloc-pad", {"@pad": ["!direction", "caps"]}],
            }
        )
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"parent", "alloc-pad"})
        self.assertNotIn("direction", result.pad_properties)
        self.assertIn("caps", result.pad_properties)

    def test_redundant_negation_across_tiers(self):
        """!name at glob and exact tiers — second negation is a no-op."""
        r = self._config_from_properties(
            {
                "*": ["name"],
                "te*": ["!name"],
                "tee": ["!name"],
            }
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("name", result.element_properties)

    def test_multiple_globs_order(self):
        r = self._config_from_properties(
            {
                "t*": ["a"],
                "te*": ["!a", "b"],
            }
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("a", result.element_properties)
        self.assertIn("b", result.element_properties)

    def test_empty_config(self):
        r = self._config_from_properties({})
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, set())
        self.assertEqual(result.pad_properties, set())


class ResolveMergeTestCase(unittest.TestCase):

    def _config_with_both(self, defaults, user, merge=True, explicit_request_pads=()):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = merge
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries(defaults)
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = set(explicit_request_pads)
        cfg._user_classified = _classify_entries(user)
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = set()
        return cfg

    def test_merge_additive(self):
        r = self._config_with_both(
            {"*": ["parent"]},
            {"*": ["name"]},
        )
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"parent", "name"})

    def test_merge_user_negates_default(self):
        r = self._config_with_both(
            {"*": ["parent", "name"]},
            {"*": ["!parent"]},
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("parent", result.element_properties)
        self.assertIn("name", result.element_properties)

    def test_merge_specificity_interleaving(self):
        """default * adds parent, user te* removes it, default tee re-adds it."""
        r = self._config_with_both(
            {"*": ["parent"], "tee": ["parent"]},
            {"te*": ["!parent"]},
        )
        result = r.resolve_filters("tee")
        self.assertIn("parent", result.element_properties)

    def test_merge_user_glob_vs_default_global(self):
        """user te* should beat default * (higher specificity)."""
        r = self._config_with_both(
            {"*": ["parent"]},
            {"te*": ["!parent"]},
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("parent", result.element_properties)

    def test_merge_default_exact_beats_user_glob(self):
        """default exact tee should beat user glob te* (higher specificity)."""
        r = self._config_with_both(
            {"tee": ["parent"]},
            {"te*": ["!parent"]},
        )
        result = r.resolve_filters("tee")
        self.assertIn("parent", result.element_properties)

    def test_no_merge_user_only(self):
        r = self._config_with_both(
            {"*": ["parent", "name"]},
            {"*": ["caps"]},
            merge=False,
        )
        result = r.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"caps"})

    def test_merge_pad_properties(self):
        r = self._config_with_both(
            {"*": [{"@pad": ["direction"]}]},
            {"tee": [{"@pad": ["template", "!direction"]}]},
        )
        result = r.resolve_filters("tee")
        self.assertNotIn("direction", result.pad_properties)
        self.assertIn("template", result.pad_properties)

    def test_no_user_falls_back_to_defaults(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({"*": ["parent"]})
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = set()
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = set()
        result = cfg.resolve_filters("tee")
        self.assertEqual(result.element_properties, {"parent"})


class MergePolicyTestCase(unittest.TestCase):

    def test_invalid_merge_policy(self):
        with self.assertRaises(ValueError):
            PipelineParserConfig(merge_policy="source")


class TraversedBinsTestCase(unittest.TestCase):

    def test_default_empty(self):
        cfg = PipelineParserConfig()
        self.assertEqual(cfg.traverse_bins, set())

    def test_from_user_config(self):
        user_config = {
            "excluded_property_filter": {"*": ["parent"]},
            "traversed_bins": ["bin0", "bin1"],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(user_config, f)
            tmp_path = f.name
        try:
            cfg = PipelineParserConfig(user_config=tmp_path)
            self.assertEqual(cfg.traverse_bins, {"bin0", "bin1"})
        finally:
            os.unlink(tmp_path)

    def test_user_merges_with_default(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({})
        cfg._default_traverse_bins = {"default_bin"}
        cfg._default_explicit_request_pads = set()
        cfg._user_classified = None
        cfg._user_traverse_bins = {"user_bin"}
        cfg._user_explicit_request_pads = set()
        self.assertEqual(cfg.traverse_bins, {"default_bin", "user_bin"})

    def test_user_overrides_default_no_merge(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = False
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({})
        cfg._default_traverse_bins = {"default_bin"}
        cfg._default_explicit_request_pads = set()
        cfg._user_classified = None
        cfg._user_traverse_bins = {"user_bin"}
        cfg._user_explicit_request_pads = set()
        self.assertEqual(cfg.traverse_bins, {"user_bin"})

    def test_user_empty_falls_back_to_default(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({})
        cfg._default_traverse_bins = {"default_bin"}
        cfg._default_explicit_request_pads = set()
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = set()
        self.assertEqual(cfg.traverse_bins, {"default_bin"})


class ExplicitRequestPadsTestCase(unittest.TestCase):

    def test_defaults_loaded(self):
        cfg = PipelineParserConfig()
        self.assertIn("nvcompositor", cfg.explicit_request_pads)
        self.assertIn("nvtee", cfg.explicit_request_pads)

    def test_name_not_excluded(self):
        cfg = PipelineParserConfig()
        result = cfg.resolve_filters("nvcompositor")
        self.assertNotIn("name", result.element_properties)

    def test_name_excluded_without_explicit(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({"*": ["name"]})
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = set()
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = set()
        result = cfg.resolve_filters("compositor")
        self.assertIn("name", result.element_properties)

    def test_name_forced_kept_with_explicit(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({"*": ["name"]})
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = {"compositor"}
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = set()
        result = cfg.resolve_filters("compositor")
        self.assertNotIn("name", result.element_properties)

    def test_user_explicit_merges_with_default(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = True
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({})
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = {"tee"}
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = {"compositor", "funnel"}
        self.assertEqual(cfg.explicit_request_pads, {"tee", "compositor", "funnel"})

    def test_user_explicit_overrides_default_no_merge(self):
        cfg = PipelineParserConfig.__new__(PipelineParserConfig)
        cfg._merge = False
        cfg._merge_policy = PipelineParserConfig.MERGE_POLICY_SPECIFICITY
        cfg._defaults_classified = _classify_entries({})
        cfg._default_traverse_bins = set()
        cfg._default_explicit_request_pads = {"tee"}
        cfg._user_classified = None
        cfg._user_traverse_bins = set()
        cfg._user_explicit_request_pads = {"compositor", "funnel"}
        self.assertEqual(cfg.explicit_request_pads, {"compositor", "funnel"})


class PipelineParserConfigLoadTestCase(unittest.TestCase):

    def test_user_file_loading(self):
        user_config = {
            "excluded_property_filter": {"*": ["custom-prop"], "tee": ["alloc-pad"]},
            "traversed_bins": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(user_config, f)
            tmp_path = f.name
        try:
            cfg = PipelineParserConfig(user_config=tmp_path)
            result = cfg.resolve_filters("tee")
            self.assertIn("custom-prop", result.element_properties)
            self.assertIn("alloc-pad", result.element_properties)
            self.assertIn("parent", result.element_properties)
        finally:
            os.unlink(tmp_path)

    def test_user_file_no_merge(self):
        user_config = {
            "excluded_property_filter": {"*": ["custom-prop"]},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(user_config, f)
            tmp_path = f.name
        try:
            cfg = PipelineParserConfig(user_config=tmp_path, merge=False)
            result = cfg.resolve_filters("tee")
            self.assertIn("custom-prop", result.element_properties)
            self.assertNotIn("parent", result.element_properties)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
