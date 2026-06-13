import dataclasses
import fnmatch
import sys
import traceback

try:
    from importlib.resources import files
except ImportError:
    from importlib_resources import files

import yaml

ALL_MARKER = "*"
NEGATION_PREFIX = "!"
PAD_PREFIX = "@"
PAD_KEY = f"{PAD_PREFIX}pad"

_GLOB_CHARS = frozenset("*?[]")


@dataclasses.dataclass
class ResolvedFilter:
    element_properties: set
    pad_properties: set


def _is_glob(key):
    return key != ALL_MARKER and any(c in key for c in _GLOB_CHARS)


def _classify_entries(config):
    """Split config keys into (global_entry, globs_in_order, exact_entries).

    Returns:
        tuple: (global_items: list|None, globs: list[(pattern, items)], exacts: dict[name, items])
    """
    global_items = None
    globs = []
    exacts = {}
    if not config:
        return global_items, globs, exacts
    for key, items in config.items():
        if key == ALL_MARKER:
            global_items = items or []
        elif _is_glob(key):
            globs.append((key, items or []))
        else:
            exacts[key] = items or []
    return global_items, globs, exacts


def _apply_items(element_props, pad_props, items):
    for item in items:
        if isinstance(item, dict):
            if PAD_KEY in item:
                pad_items = item[PAD_KEY]
                if not pad_items:
                    continue
                for pad_item in pad_items:
                    if isinstance(pad_item, str):
                        if pad_item == ALL_MARKER:
                            pad_props.clear()
                            pad_props.add(ALL_MARKER)
                            continue
                        if pad_item.startswith(NEGATION_PREFIX):
                            prop_name = pad_item[len(NEGATION_PREFIX) :]
                            pad_props.discard(prop_name)
                        else:
                            pad_props.add(pad_item)
            continue
        if not isinstance(item, str):
            continue
        if item.startswith(NEGATION_PREFIX):
            prop_name = item[len(NEGATION_PREFIX) :]
            element_props.discard(prop_name)
        else:
            element_props.add(item)


def _resolve_single_source(classified, element_name):
    global_items, globs, exacts = classified
    element_props = set()
    pad_props = set()

    if global_items is not None:
        _apply_items(element_props, pad_props, global_items)

    for pattern, items in globs:
        if fnmatch.fnmatch(element_name, pattern):
            _apply_items(element_props, pad_props, items)

    if element_name in exacts:
        _apply_items(element_props, pad_props, exacts[element_name])

    return element_props, pad_props


def _resolve_interleaved(default_classified, user_classified, element_name):
    d_global, d_globs, d_exacts = default_classified
    u_global, u_globs, u_exacts = user_classified
    element_props = set()
    pad_props = set()

    # Tier 1: globals
    for items in (d_global, u_global):
        if items is not None:
            _apply_items(element_props, pad_props, items)

    # Tier 2: globs (in appearance order within each source)
    for source_globs in (d_globs, u_globs):
        for pattern, items in source_globs:
            if fnmatch.fnmatch(element_name, pattern):
                _apply_items(element_props, pad_props, items)

    # Tier 3: exact matches
    for source_exacts in (d_exacts, u_exacts):
        if element_name in source_exacts:
            _apply_items(element_props, pad_props, source_exacts[element_name])

    return element_props, pad_props


def _extract_section(raw, key, default=None):
    if isinstance(raw, dict):
        return raw.get(key, default)
    return default


class PipelineParserConfig:

    MERGE_POLICY_SPECIFICITY = "specificity"
    MERGE_POLICIES = (MERGE_POLICY_SPECIFICITY,)

    _CONFIG_KEY_PROPERTIES = "excluded_property_filter"
    _CONFIG_KEY_TRAVERSE_BINS = "traversed_bins"

    def __init__(
        self, user_config=None, merge=True, merge_policy=MERGE_POLICY_SPECIFICITY
    ):
        if merge_policy not in self.MERGE_POLICIES:
            raise ValueError(
                f"Unsupported merge_policy: {merge_policy!r}. Supported: {self.MERGE_POLICIES}"
            )
        self._merge = merge
        self._merge_policy = merge_policy

        defaults_raw = self._load_defaults()
        self._defaults_classified = _classify_entries(
            _extract_section(defaults_raw, self._CONFIG_KEY_PROPERTIES, {})
        )
        self._default_traverse_bins = list(
            _extract_section(defaults_raw, self._CONFIG_KEY_TRAVERSE_BINS, []) or []
        )

        if user_config:
            user_raw = self._load(user_config)
            self._user_classified = _classify_entries(
                _extract_section(user_raw, self._CONFIG_KEY_PROPERTIES, {})
            )
            self._user_traverse_bins = list(
                _extract_section(user_raw, self._CONFIG_KEY_TRAVERSE_BINS, []) or []
            )
        else:
            self._user_classified = None
            self._user_traverse_bins = []

    @staticmethod
    def _load_defaults():
        try:
            # Might be moved into a different (pycfgst_config) package / repository
            data = files("pycfgst") / "pipeline_parser_defaults.yaml"
            return yaml.safe_load(data.read_text()) or {}
        except Exception:
            traceback.print_exc()
            return {}

    @staticmethod
    def _load(path):
        with open(str(path)) as f:
            return yaml.safe_load(f) or {}

    def resolve_filters(self, element_name):
        if self._user_classified is not None and not self._merge:
            ep, pp = _resolve_single_source(self._user_classified, element_name)
        elif self._user_classified is not None and self._merge:
            ep, pp = _resolve_interleaved(
                self._defaults_classified, self._user_classified, element_name
            )
        else:
            ep, pp = _resolve_single_source(self._defaults_classified, element_name)
        return ResolvedFilter(
            element_properties=ep,
            pad_properties=pp,
        )

    @property
    def traverse_bins(self):
        if self._user_traverse_bins:
            return self._user_traverse_bins
        return self._default_traverse_bins


if __name__ == "__main__":
    print("This module is not meant to be run directly.\n")
    sys.exit(-1)
