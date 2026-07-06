#!/usr/bin/env python
"""GStreamer plugin registry inspection and element class discovery."""

from __future__ import annotations

import sys

from pycfutils.exceptions import ModuleException
from pycfutils.miscellaneous import pretty_print as pprint

try:
    import gi
except ImportError as ie:
    raise ModuleException(
        "This module requires PyGObject (https://gnome.pages.gitlab.gnome.org/pygobject)"
    ) from ie

gi.require_version("Gst", "1.0")
from gi.repository import Gst

__all__ = ("RegistryAccess",)


class RegistryAccess:
    """Access to the GStreamer plugin registry: element factories and their classes."""

    def __init__(self) -> None:
        if not Gst.is_initialized():
            print("Gst engine is not initialized. Initializing.")
            Gst.init(argv=None)
        self.__contents = None
        self.__element_classes_dict = None
        self.__element_classes = None
        self.__container_classes = (Gst.Bin, Gst.Pipeline)
        self.__failed_classes = ()
        self.invalidate_caches()

    def contents(self, force: bool = False) -> dict[str, dict[str, Gst.PluginFeature]]:
        """Return plugin features grouped by plugin name."""
        if self.__contents is None or force:
            registry = Gst.Registry.get()
            plugin_names = sorted(e.get_name() for e in registry.get_plugin_list())
            self.__contents = {
                e0: dict(
                    sorted(
                        (
                            (e1.name, e1)
                            for e1 in registry.get_feature_list_by_plugin(e0)
                        ),
                        key=lambda arg: arg[0],
                    )
                )
                for e0 in plugin_names
            }
        return self.__contents

    def element_classes_dict(self, force: bool = False) -> dict[str, type]:
        """Return a mapping of element factory names to their Python classes."""
        if self.__element_classes_dict is None or force:
            failed_classes = []
            items = []
            self.__element_classes_dict = {}
            for e in self.contents(force=force).values():
                items.extend(e.items())
            items.sort(key=lambda arg: arg[0])
            for name, obj in items:
                if isinstance(obj, Gst.ElementFactory):
                    try:
                        pytype = obj.get_element_type().pytype
                        if pytype is None:
                            raise TypeError
                        self.__element_classes_dict[name] = pytype
                    except Exception:
                        try:
                            self.__element_classes_dict[name] = obj.make(name).__class__
                        except Exception:
                            failed_classes.append(name)
            self.__failed_classes = tuple(failed_classes)
        return self.__element_classes_dict

    @property
    def failed_classes(self) -> tuple[str, ...]:
        """Factory names whose Python class could not be resolved."""
        return self.__failed_classes

    def element_classes(
        self, force: bool = False, exclude_containers: bool = True
    ) -> tuple[type, ...]:
        """Return element Python classes, optionally excluding Bin and Pipeline."""
        if self.__element_classes is None or force:
            self.__element_classes = tuple(
                item for item in self.element_classes_dict(force=force).values()
            )
        return (
            tuple(
                item
                for item in self.__element_classes
                if item not in self.__container_classes
            )
            if exclude_containers
            else self.__element_classes
        )

    def invalidate_caches(self) -> None:
        """Clear all cached data, forcing a fresh registry scan on next access."""
        self.__contents = None
        self.__element_classes_dict = None
        self.__element_classes = None


if __name__ == "__main__":
    print(
        "Python {:s} {:03d}bit on {:s}\n".format(
            " ".join(elem.strip() for elem in sys.version.split("\n")),
            64 if sys.maxsize > 0x100000000 else 32,
            sys.platform,
        )
    )
    Gst.init(argv=None)
    ra = RegistryAccess()
    contents = ra.contents()
    pprint(contents, head="\nGst registry contents:", sort_dicts=False)
    pprint(ra.element_classes_dict(), head="\nGst registry features (with classes):")
    print("\nDone.\n")
