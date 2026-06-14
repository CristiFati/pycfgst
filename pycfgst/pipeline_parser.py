"""GStreamer pipeline to gst-launch-1.0 command string conversion."""

from __future__ import annotations

import enum
import shlex
import sys
import traceback
from typing import Any

import networkx
from pycfutils.exceptions import ModuleException

try:
    import gi
except ImportError as ie:
    raise ModuleException(
        "This module requires PyGObject (https://gnome.pages.gitlab.gnome.org/pygobject)"
    ) from ie

gi.require_version("Gst", "1.0")
from gi.repository import GObject, Gst

from pycfgst._pipeline_parser_config import ALL_MARKER, PipelineParserConfig

__all__ = ("PipelineParser",)


class PipelineParser:
    """Convert a GStreamer pipeline into a gst-launch-1.0 command string."""

    DEFAULT_ELEMENT_INDENT = " " * 2
    DEFAULT_PROPERTY_INDENT = " " * 4
    DEFAULT_GSTLAUNCH = "gst-launch-1.0 -e"  # v

    _PARAMFLAG_READABLE = int(GObject.ParamFlags.READABLE)
    _PARAMFLAG_WRITABLE = int(GObject.ParamFlags.WRITABLE)
    _TRAVERSED_FACTORIES = ("bin", "pipeline")

    class Direction(enum.IntEnum):
        Unlinked = 0
        LeftRight = 1
        RightLeft = 2

    def __init__(self) -> None:
        self._config = PipelineParserConfig()

    def configure(
        self,
        user_config: str | None = None,
        merge: bool = True,
        merge_policy: str = PipelineParserConfig.MERGE_POLICY_SPECIFICITY,
    ) -> None:
        """Load configuration from a YAML file."""
        self._config = PipelineParserConfig(
            user_config=user_config,
            merge=merge,
            merge_policy=merge_policy,
        )

    @classmethod
    def _resolve_peer(
        cls, pad: Gst.Pad, target_element: Gst.Element, max_indirections: int
    ) -> Gst.Pad | None:
        cur = pad.peer
        level = 0
        while cur and isinstance(cur, Gst.ProxyPad):
            if cur.get_parent() == target_element:
                return cur
            internal = cur.get_internal()
            if not internal or not internal.peer:
                break
            cur = internal.peer
            level += 1
            if 0 < max_indirections < level:
                return None
        return cur

    @classmethod
    def _is_linked_pads(
        cls, pad0: Gst.Pad, pad1: Gst.Pad, max_indirections: int = -1
    ) -> bool:
        if pad0.peer == pad1 and pad1.peer == pad0:
            return True
        if max_indirections == 0:
            return False
        elem0 = pad0.get_parent()
        elem1 = pad1.get_parent()
        cur0 = cls._resolve_peer(pad0, elem1, max_indirections)
        cur1 = cls._resolve_peer(pad1, elem0, max_indirections)
        return cur0 == pad1 and cur1 == pad0

    @classmethod
    def element_direction(
        cls, left: Gst.Element, right: Gst.Element
    ) -> PipelineParser.Direction:
        """Determine the link direction between two elements."""
        for sink_pad in left.sinkpads:
            for src_pad in right.srcpads:
                if cls._is_linked_pads(src_pad, sink_pad):
                    return cls.Direction.RightLeft
        for sink_pad in right.sinkpads:
            for src_pad in left.srcpads:
                if cls._is_linked_pads(src_pad, sink_pad):
                    return cls.Direction.LeftRight
        return cls.Direction.Unlinked

    @staticmethod
    def _quote_value(val: Any) -> str:
        return shlex.quote(str(val))

    @classmethod
    def format_value(cls, val: Any) -> Any:
        """Normalize a GStreamer property value for gst-launch output."""
        if isinstance(val, int):
            ret = int(val)  # bools, enums
        elif isinstance(val, float):
            ret = val  # f"{val:.03f}"  # ?
        else:
            to_string = getattr(val, "to_string", None)
            if callable(to_string):
                ret = val.to_string()
            else:
                ret = val
        return ret

    @classmethod
    def force_exclude_property(cls, prop: GObject.ParamSpec, val: Any = None) -> bool:
        """Return True if the property should be excluded regardless of config."""
        if not (prop.flags & cls._PARAMFLAG_READABLE):
            return True
        if not (prop.flags & cls._PARAMFLAG_WRITABLE):
            return True
        if val is not None and val == prop.default_value:
            return True
        return False

    @classmethod
    def _filtered_properties(
        cls, go: Gst.Object, discard_props: set[str]
    ) -> dict[str, Any]:
        if ALL_MARKER in discard_props:
            return {}
        ret = {}
        for prop in go.list_properties():
            if prop.name in discard_props:
                continue
            if cls.force_exclude_property(prop):
                continue
            try:
                val = go.get_property(prop.name)
            except Exception:
                traceback.print_exc()
                continue
            if cls.force_exclude_property(prop, val):
                continue
            val = cls.format_value(val)
            if isinstance(val, int):
                val = int(val)
            if val is None:
                continue
            ret[prop.name] = val
        return ret

    @classmethod
    def _find_linked_src_pad(
        cls, node: Gst.Element, succ: Gst.Element
    ) -> Gst.Pad | None:
        for src_pad in node.srcpads:
            for sink_pad in succ.sinkpads:
                if cls._is_linked_pads(src_pad, sink_pad):
                    return src_pad
        return None

    @classmethod
    def _find_linked_sink_pad(
        cls, node: Gst.Element, pred: Gst.Element
    ) -> Gst.Pad | None:
        for sink_pad in node.sinkpads:
            for src_pad in pred.srcpads:
                if cls._is_linked_pads(src_pad, sink_pad):
                    return sink_pad
        return None

    def _source_reference(
        self,
        go: Gst.Element,
        level: int,
        base_indent: str,
        succ: Gst.Element | None = None,
    ) -> list[str]:
        factory = go.get_factory()
        if (
            succ is not None
            and factory
            and factory.name in self._config.explicit_request_pads
        ):
            pad = self._find_linked_src_pad(go, succ)
            if pad and pad.get_pad_template().presence == Gst.PadPresence.REQUEST:
                return [f"{base_indent * level}{go.name}.{pad.name} \\"]
        return [f"{base_indent * level}{go.name}. \\"]

    @classmethod
    def _sink_reference(
        cls, go: Gst.Element, level: int, base_indent: str, pad_idx: int
    ) -> list[str]:
        ret = [f"{base_indent * level}! {go.name}.{go.sinkpads[pad_idx].name} \\"]
        return ret

    def _flatten_object(self, obj: Gst.Object, out: list[Gst.Element]) -> None:
        if isinstance(obj, Gst.Bin):
            factory = obj.get_factory()
            if (
                factory is not None
                and factory.name not in self._TRAVERSED_FACTORIES
                and factory.name not in self._config.traverse_bins
            ):
                out.append(obj)
                return
            children = obj.children
            if children:
                self._flatten_seq(children, out)
                return
        elif obj.get_factory() is not None:
            out.append(obj)
            return
        out.append(obj)

    def _flatten_seq(self, seq: list | tuple, out: list[Gst.Element]) -> None:
        for obj in seq:
            self._flatten_object(obj, out)

    def _flatten(self, obj: Gst.Object | list | tuple) -> list[Gst.Element]:
        ret = []
        if isinstance(obj, (list, tuple)):
            self._flatten_seq(obj, ret)
        elif isinstance(obj, Gst.Object):
            self._flatten_object(obj, ret)
        else:
            raise TypeError(
                f"Expected Gst.Object or list/tuple, got {type(obj).__name__}"
            )
        return ret

    def _generate_graph(self, obj: Gst.Object) -> networkx.DiGraph:
        ret = networkx.DiGraph()
        elements = self._flatten(obj)
        ret.add_nodes_from(elements)
        for item0 in elements:
            for item1 in elements:
                if item0 == item1:
                    continue
                order = self.element_direction(item0, item1)
                if order == self.Direction.LeftRight:
                    ret.add_edge(item0, item1)
                elif order == self.Direction.RightLeft:
                    ret.add_edge(item1, item0)
        return ret

    def is_capsfilter(self, element: Gst.Element) -> bool:
        """Return True if the element is a capsfilter (emitted as inline caps)."""
        return element.__class__.__name__ == "GstCapsFilter"

    def _format_element(
        self,
        element: Gst.Element,
        level: int,
        base_indent: str,
        prop_indent: str,
        pre_link: bool,
    ) -> list[str]:
        indent = base_indent * level
        link_symbol = f"{'! ' if pre_link else ''}"
        if self.is_capsfilter(element):
            return [
                f"{indent}{link_symbol}"
                f"{self._quote_value(element.get_property('caps').to_string())} \\"
            ]
        factory_name = element.get_factory().name
        resolved = self._config.resolve_filters(factory_name)
        pindent = indent + prop_indent
        ret = [f"{indent}{link_symbol}{factory_name} \\"]
        for k, v in self._filtered_properties(
            element, resolved.element_properties
        ).items():
            ret.append(f"{pindent}{k}={self._quote_value(v)} \\")
        for pad in element.pads:
            pad_props = self._filtered_properties(pad, resolved.pad_properties)
            if pad_props:
                props_str = " ".join(
                    f"{pad.name}::{k}={self._quote_value(v)}"
                    for k, v in pad_props.items()
                )
                ret.append(f"{pindent}{props_str} \\")
        return ret

    @classmethod
    def _sorted_successors(
        cls, node: Gst.Element, succs: tuple[Gst.Element, ...]
    ) -> list[Gst.Element]:
        def src_pad_index(succ):
            for idx, src_pad in enumerate(node.srcpads):
                for sink_pad in succ.sinkpads:
                    if cls._is_linked_pads(src_pad, sink_pad):
                        return idx
            return len(node.srcpads)

        return sorted(succs, key=src_pad_index)

    def _format_successors(
        self,
        node: Gst.Element,
        graph: networkx.DiGraph,
        level: int,
        base_elem_indent: str,
        prop_indent: str,
        multisinks: dict[Gst.Element, list[int]],
    ) -> list[str]:
        ret = []
        succs = tuple(graph.successors(node))
        if len(succs) > 1:
            for succ in self._sorted_successors(node, succs):
                ret += self._source_reference(node, level, base_elem_indent, succ)
                ret += self._format_node(
                    succ,
                    graph,
                    level + 1,
                    base_elem_indent,
                    prop_indent,
                    True,
                    multisinks,
                )
        elif len(succs) == 1:
            factory = node.get_factory()
            if factory and factory.name in self._config.explicit_request_pads:
                ret += self._source_reference(node, level, base_elem_indent, succs[0])
            ret += self._format_node(
                succs[0],
                graph,
                level,
                base_elem_indent,
                prop_indent,
                True,
                multisinks,
            )
        return ret

    def _format_node(
        self,
        node: Gst.Element,
        graph: networkx.DiGraph,
        level: int,
        base_elem_indent: str,
        prop_indent: str,
        pre_link: bool,
        multisinks: dict[Gst.Element, list[int]],
    ) -> list[str]:
        ret = []
        levels = []
        preds = tuple(graph.predecessors(node))
        if len(preds) > 1:
            multisinks.setdefault(node, []).append(level)
            levels = multisinks[node]
            if len(levels) < len(preds):
                ret += self._sink_reference(
                    node, level, base_elem_indent, len(levels) - 1
                )
        else:
            factory = node.get_factory()
            if (
                pre_link
                and preds
                and factory
                and factory.name in self._config.explicit_request_pads
            ):
                sink_pad = self._find_linked_sink_pad(node, preds[0])
                if (
                    sink_pad
                    and sink_pad.get_pad_template().presence == Gst.PadPresence.REQUEST
                ):
                    indent = base_elem_indent * level
                    ret.append(f"{indent}! {node.name}.{sink_pad.name} \\")
                    pre_link = False
            ret += self._format_element(
                node, level, base_elem_indent, prop_indent, pre_link
            )
            ret += self._format_successors(
                node, graph, level, base_elem_indent, prop_indent, multisinks
            )
        if 1 < len(preds) == len(levels):
            ret += self._sink_reference(node, level, base_elem_indent, len(levels) - 1)
            elem_level = min(levels)
            ret += self._format_element(
                node, elem_level, base_elem_indent, prop_indent, False
            )
            ret += self._format_successors(
                node, graph, elem_level, base_elem_indent, prop_indent, multisinks
            )
        return ret

    def gst_launch(
        self,
        gst_object_root: Gst.Object,
        level: int = 0,
        element_indent: str | None = None,
        property_indent: str | None = None,
        command: str | None = None,
    ) -> str:
        """Generate a gst-launch-1.0 command string from a pipeline or bin."""
        if not isinstance(gst_object_root, Gst.Object):
            raise TypeError(
                f"Expected Gst.Object, got {type(gst_object_root).__name__}"
            )
        if element_indent is None:
            element_indent = self.DEFAULT_ELEMENT_INDENT
        if property_indent is None:
            property_indent = self.DEFAULT_PROPERTY_INDENT
        if command is None:
            command = self.DEFAULT_GSTLAUNCH
        graph = self._generate_graph(gst_object_root)
        ret = [f"\n{command} \\"] if command else []
        srcs = [e[0] for e in graph.in_degree if e[1] == 0]
        for src in srcs:
            ret += self._format_node(
                src,
                graph,
                level + 1,
                element_indent,
                property_indent,
                False,
                {},
            )
        return "\n".join(ret).rstrip(" \\")


if __name__ == "__main__":
    print("This module is not meant to be run directly.\n")
    sys.exit(-1)
