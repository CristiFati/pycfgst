# PyCFGst

[![PyPI version](https://img.shields.io/pypi/v/pycfgst)](https://pypi.org/project/pycfgst)
[![Python versions](https://img.shields.io/pypi/pyversions/pycfgst)](https://pypi.org/project/pycfgst)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

**PyCFGst** (**C**risti **F**ati's **G**Streamer utilities for **Py**thon) provides pipeline parsing and registry access for [GStreamer](https://gstreamer.freedesktop.org).

## Install

```shell
python -m pip install --upgrade pycfgst
```

### Requirements

- [PyGObject](https://gnome.pages.gitlab.gnome.org/pygobject) (GStreamer Python bindings)
- [PyYAML](https://pypi.org/project/PyYAML)
- [NetworkX](https://pypi.org/project/networkx)
- [PyCFUtils](https://pypi.org/project/pycfutils)

GStreamer and its plugins must be installed separately (system packages or platform-specific installers).

## Usage

### Registry Access

Inspect the GStreamer plugin registry:

```python
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

from pycfgst.registry_access import RegistryAccess

ra = RegistryAccess()
print(f"Plugins: {len(ra.contents())}")
print(f"Element classes: {len(ra.element_classes())}")
print(f"Failed: {ra.failed_classes}")
```

### Pipeline Parser

Convert a live GStreamer pipeline into a `gst-launch-1.0` command string:

```python
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

from pycfgst.pipeline_parser import PipelineParser

pipeline = Gst.parse_launch(
    "videotestsrc pattern=18 ! tee name=t0 "
    "! queue ! autovideosink "
    "t0. ! queue ! autovideosink"
)
pipeline.set_state(Gst.State.PAUSED)

pp = PipelineParser()
print(pp.gst_launch(pipeline))

pipeline.set_state(Gst.State.NULL)
```

Output:

```
gst-launch-1.0 -e \
  videotestsrc \
      pattern=18 \
  ! tee \
      name=t0 \
  t0. \
    ! queue \
    ! autovideosink \
  t0. \
    ! queue \
    ! autovideosink
```

## Property Filtering

When converting a GStreamer pipeline to a `gst-launch-1.0` command, `PipelineParser` decides which element and pad properties to include in the output. Properties are excluded (filtered out) based on a YAML configuration file using a 3-tier pattern matching system.

### Configuration File Format

The configuration file has three top-level keys:

```yaml
excluded_property_filter:
    # filter rules here

explicit_request_pads:
    # factories requiring explicit request pad names in output

traversed_bins:
    # experimental: bin factories to traverse into instead of treating as opaque elements
```

#### Pattern Matching (3 tiers)

Under `excluded_property_filter`, keys are element factory name patterns. They are matched against each element in the pipeline using three tiers of increasing specificity:

| Tier | Pattern | Example | Matches |
|------|---------|---------|---------|
| 1 | `*` (global) | `"*"` | All elements |
| 2 | Glob | `te*`, `*sink`, `video?rc` | Elements matching the glob via `fnmatch` |
| 3 | Exact | `tee`, `compositor` | Only that specific element |

Higher tiers override lower tiers. Within the glob tier, patterns are applied in order of appearance (last match wins).

#### Property Items

Each pattern maps to a list of property items:

```yaml
"*":
    - parent          # Exclude "parent" property
    - "!name"         # Re-include "name" (negation)
    - "*"             # Exclude ALL properties
    - "@pad":         # Pad properties section
        - direction   # Exclude pad "direction" property
        - "!caps"     # Re-include pad "caps"
        - "*"         # Exclude ALL pad properties
```

| Syntax | Meaning |
|--------|---------|
| `propname` | Exclude this property |
| `!propname` | Re-include this property (undo a previous exclusion; repeated negations are no-ops, not toggles) |
| `*` | Exclude all properties |
| `@pad` | Nested list of pad-specific property rules (same syntax) |

#### Resolution Example

For an element named `tee`, given:

```yaml
excluded_property_filter:
    "*":
        - name
        - parent
    "te*":
        - alloc-pad
    tee:
        - "!name"
```

Resolution order: `*` then `te*` then `tee`.

1. `*` excludes `name` and `parent`
2. `te*` matches, adds `alloc-pad` to exclusions
3. `tee` exact match, `!name` re-includes `name`

Result: `parent` and `alloc-pad` are excluded. `name` is included.

## Explicit Request Pads

Some GStreamer elements (particularly vendor-specific ones like Nvidia DeepStream or Qualcomm IM SDK) require request pads to be explicitly named in `gst-launch` syntax for linking to work. The `explicit_request_pads` configuration key lists factories where the parser should emit pad names on source and sink references.

For elements in this list, the parser output changes from:

```
tee name=t0 \
t0. \
  ! queue \
```

to:

```
tee name=t0 \
t0.src_0 \
  ! queue \
```

And for sink-side request pads (single predecessor):

```
! comp0.sink_0 \
compositor name=comp0 \
! autovideosink
```

Only request pads are affected — static pads are left unchanged.

Elements in `explicit_request_pads` automatically have their `name` property preserved in the output (regardless of the `excluded_property_filter` rules), since the name is required for pad references.

### Configuration

```yaml
explicit_request_pads:
    - nvcompositor
    - nvstreammux
    - nvtee
```

The default configuration includes Nvidia DS and Qualcomm IM SDK factories. Standard GStreamer factories (tee, compositor, etc.) handle request pad linking internally and do not need to be listed.

### User Configuration

Users can provide their own configuration file to customize filtering:

```python
pp = PipelineParser()
pp.configure(user_config="path/to/user_config.yaml")
output = pp.gst_launch(pipeline)
```

#### `configure()` Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `user_config` | `None` | Path to user YAML configuration file |
| `merge` | `True` | `True`: merge with defaults. `False`: use only user config |
| `merge_policy` | `PipelineParserConfig.MERGE_POLICY_SPECIFICITY` | Conflict resolution strategy. Supported values in `PipelineParserConfig.MERGE_POLICIES` |

#### User Config File Format

Same format as the defaults file:

```yaml
excluded_property_filter:
    "*":
        - some-global-prop
    "in-house-element":
        - "!parent"

explicit_request_pads:

traversed_bins:
```

#### Merge Behavior (`merge=True`)

When merging, default and user configurations are interleaved by specificity tier:

```
default *  ->  user *  ->  default globs  ->  user globs  ->  default exact  ->  user exact
```

At each step, additions and negations (`!prop`) apply to the accumulated set. This means:

- More specific patterns always beat less specific ones, regardless of source
- At the same specificity level, user entries override defaults
- A user glob cannot override a default exact match (use an exact match instead)

#### No Merge (`merge=False`)

Defaults are ignored entirely. Only the user configuration is used.

### Subclass Customization

Override `force_exclude_property` to add programmatic exclusion logic beyond the YAML configuration:

```python
class CustomPipelineParser(PipelineParser):
    @classmethod
    def force_exclude_property(cls, prop, val=None):
        if super().force_exclude_property(prop, val):
            return True
        # Custom logic here
        return prop.name.startswith("internal-")
```

This method is called twice per property: once before fetching the value (`val=None`, for early rejection based on property metadata) and once after (`val` is the actual value, for value-based filtering). The base implementation excludes non-writable properties and properties equal to their default value.

## Known Limitations

### Accumulating properties

Some GStreamer elements use properties where calling `set_property` multiple times accumulates internal state, but `get_property` only returns the last value set. Since the parser reads property values via `get_property`, it cannot reconstruct the full sequence of `set_property` calls. The generated `gst-launch-1.0` command will be missing earlier values, which may cause the pipeline to crash or behave differently from the original.

Known elements affected:

- **`nvdsvideotemplate`** and **`nvdsaudiotemplate`** (NVIDIA DeepStream): the `customlib-props` property accepts one `key:value` pair per call and pushes it to an internal vector, but `get_property` returns only the last string.

Example — Python sets three properties:

```python
element.set_property("customlib-props", "property0:value0")
element.set_property("customlib-props", "property1:value1")
element.set_property("customlib-props", "property2:value2")
```

The parser output will only contain:

```
customlib-props=property2:value2
```

The first two properties are silently lost.

## Notes

- Parts of code assisted by [Claude Code](https://claude.ai/code)

## Changelog

See [CHANGELOG](CHANGELOG) for a full list of changes.

## Contributing

Contributions are welcome. Please open an issue or submit a pull request on [GitHub](https://github.com/CristiFati/pycfgst).

## License

This project is licensed under the [MIT License](LICENSE).
