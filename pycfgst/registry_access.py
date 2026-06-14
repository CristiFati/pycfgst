#!/usr/bin/env python

from __future__ import annotations

import sys
from pprint import pprint

from pycfutils.exceptions import ModuleException

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
        return self.__failed_classes

    def element_classes(
        self, force: bool = False, exclude_containers: bool = True
    ) -> tuple[type, ...]:
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
    print("\nGst registry contents:")
    contents = ra.contents()
    pprint(contents, sort_dicts=False)
    print("\nGst registry features (with classes):")
    pprint(ra.element_classes_dict())
    print("\nDone.\n")
