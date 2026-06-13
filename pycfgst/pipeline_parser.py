import enum
import sys
import traceback

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


class PipelineParser:

    DEFAULT_ELEMENT_INDENT = " " * 2
    DEFAULT_PROPERTY_INDENT = " " * 4
    DEFAULT_GSTLAUNCH = "gst-launch-1.0 -e"  # v

    _SHELL_CHARACTERS = ("(", ")", " ", ";")
    _PARAMFLAG_WRITABLE = int(GObject.ParamFlags.WRITABLE)

    class Direction(enum.IntEnum):
        Unlinked = 0
        LeftRight = 1
        RightLeft = 2

    def __init__(self):
        self._config = PipelineParserConfig()

    def configure(
        self,
        user_config=None,
        merge=True,
        merge_policy=PipelineParserConfig.MERGE_POLICY_SPECIFICITY,
    ):
        self._config = PipelineParserConfig(
            user_config=user_config,
            merge=merge,
            merge_policy=merge_policy,
        )

    @classmethod
    def _resolve_peer(cls, pad, target_element, max_indirections):
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
    def _is_linked_pads(cls, pad0, pad1, max_indirections=-1):
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
    def element_direction(cls, left, right):
        for sink_pad in left.sinkpads:
            for src_pad in right.srcpads:
                if cls._is_linked_pads(src_pad, sink_pad):
                    return cls.Direction.RightLeft
        for sink_pad in right.sinkpads:
            for src_pad in left.srcpads:
                if cls._is_linked_pads(src_pad, sink_pad):
                    return cls.Direction.LeftRight
        return cls.Direction.Unlinked

    @classmethod
    def _shell_quote_item(cls, s):
        if not isinstance(s, str):
            return s
        if (s.startswith('"') and s.endswith('"')) or (
            s.startswith("'") and s.endswith("'")
        ):
            return s
        if not any(e in s for e in cls._SHELL_CHARACTERS):
            return s
        return f'"{s}"'

    @classmethod
    def _format_value(cls, val):
        return cls._shell_quote_item(str(val))

    @classmethod
    def _value(cls, val):
        if isinstance(val, int):
            ret = int(val)
        else:
            to_string = getattr(val, "to_string", None)
            if to_string:
                ret = val.to_string()
            else:
                ret = val
        return ret

    @classmethod
    def force_exclude_property(cls, prop, val=None):
        if not (prop.flags & cls._PARAMFLAG_WRITABLE):
            return True
        if val is not None and val == prop.default_value:
            return True
        return False

    @classmethod
    def _filtered_properties(cls, go, discard_props):
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
            val = cls._value(val)
            if val is None:
                continue
            if cls.force_exclude_property(prop, val):
                continue
            ret[prop.name] = val
        return ret

    @classmethod
    def _source_reference(cls, go, level, base_indent):
        ret = [f"{base_indent * level}{go.name}. \\"]
        return ret

    @classmethod
    def _sink_reference(cls, go, level, base_indent, pad_idx):
        ret = [f"{base_indent * level}! {go.name}.{go.sinkpads[pad_idx].name} \\"]
        return ret

    def _flatten_object(self, obj, out):
        if isinstance(obj, Gst.Bin):
            factory = obj.get_factory()
            if factory is not None and factory.name not in ("bin", "pipeline"):
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

    def _flatten_seq(self, seq, out):
        for obj in seq:
            self._flatten_object(obj, out)

    def _flatten(self, obj):
        ret = []
        if isinstance(obj, (list, tuple)):
            self._flatten_seq(obj, ret)
        elif isinstance(obj, Gst.Object):
            self._flatten_object(obj, ret)
        else:
            print(f"Illegal object: {obj}")
        return ret

    def _generate_graph(self, obj):
        ret = networkx.DiGraph()
        elements = self._flatten(obj)
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

    def is_capsfilter(self, element):
        return element.__class__.__name__ == "GstCapsFilter"

    def _format_element(self, element, level, base_indent, prop_indent, pre_link):
        indent = base_indent * level
        link_symbol = f"{'! ' if pre_link else ''}"
        if self.is_capsfilter(element):
            return [
                f"{indent}{link_symbol}"
                f"{self._shell_quote_item(element.get_property('caps').to_string())} \\"
            ]
        factory_name = element.get_factory().name
        resolved = self._config.resolve_filters(factory_name)
        pindent = indent + prop_indent
        ret = [f"{indent}{link_symbol}{factory_name} \\"]
        for k, v in self._filtered_properties(
            element, resolved.element_properties
        ).items():
            ret.append(f"{pindent}{k}={self._format_value(v)} \\")
        for pad in element.pads:
            pad_props = self._filtered_properties(pad, resolved.pad_properties)
            if pad_props:
                props_str = " ".join(
                    f"{pad.name}::{k}={self._format_value(v)}"
                    for k, v in pad_props.items()
                )
                ret.append(f"{pindent}{props_str} \\")
        return ret

    @classmethod
    def _sorted_successors(cls, node, succs):
        def src_pad_index(succ):
            for idx, src_pad in enumerate(node.srcpads):
                for sink_pad in succ.sinkpads:
                    if cls._is_linked_pads(src_pad, sink_pad):
                        return idx
            return len(node.srcpads)

        return sorted(succs, key=src_pad_index)

    def _format_node(
        self,
        node,
        graph,
        level,
        base_elem_indent,
        prop_indent,
        pre_link,
        multisinks,
    ):
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
            ret += self._format_element(
                node, level, base_elem_indent, prop_indent, pre_link
            )
            succs = tuple(graph.successors(node))
            if len(succs) > 1:
                for succ in self._sorted_successors(node, succs):
                    ret += self._source_reference(node, level, base_elem_indent)
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
                ret += self._format_node(
                    succs[0],
                    graph,
                    level,
                    base_elem_indent,
                    prop_indent,
                    True,
                    multisinks,
                )
        if 1 < len(preds) == len(levels):
            ret += self._sink_reference(node, level, base_elem_indent, len(levels) - 1)
            elem_level = min(levels)
            ret += self._format_element(
                node, elem_level, base_elem_indent, prop_indent, False
            )
            succs = tuple(graph.successors(node))
            if len(succs) > 1:
                for succ in self._sorted_successors(node, succs):
                    ret += self._source_reference(node, elem_level, base_elem_indent)
                    ret += self._format_node(
                        succ,
                        graph,
                        elem_level + 1,
                        base_elem_indent,
                        prop_indent,
                        True,
                        multisinks,
                    )
            elif len(succs) == 1:
                ret += self._format_node(
                    succs[0],
                    graph,
                    elem_level,
                    base_elem_indent,
                    prop_indent,
                    True,
                    multisinks,
                )
        return ret

    def gst_launch(
        self,
        gst_object_root,
        level=0,
        element_indent=None,
        property_indent=None,
        command=None,
    ):
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
