import os
import unittest

try:
    pcfgst = True
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    from pycfutils.gstreamer.pipeline_parser import PipelineParser
    from pycfutils.gstreamer.registry_access import RegistryAccess
except:
    pcfgst = None


# @TODO - cfati: Dummy
class _GStreamerBaseTestCase(unittest.TestCase):
    if pcfgst is None:  # Mock (PyGObject not installed)

        def setUp(self):
            import sys
            from types import ModuleType

            class GstDummy:
                Bin = None
                Pipeline = None

                @staticmethod
                def is_initialized():
                    return False

                @staticmethod
                def init(argv=None):
                    pass

            print("PyGObject not installed. Run dummy test")

            gi = ModuleType("gi")
            sys.modules["gi"] = gi
            gi.repository = ModuleType("gi.repository")
            gi.repository.Gst = GstDummy
            gi.require_version = lambda ns, ver: None
            sys.modules["gi.repository"] = gi.repository

    else:

        def setUp(self):
            Gst.init(argv=None)


class GStreamerTestCase(_GStreamerBaseTestCase):

    @classmethod
    def generate_pipeline(cls, command):
        return Gst.parse_launch(cls.normalize_command(command))

    @classmethod
    def normalize_command(cls, command):
        return command.replace('"', "")

    @classmethod
    def read_pipelines(cls):
        pipelines = []
        data_file = os.path.join(
            os.path.dirname(__file__), "data", "gst_launch_pipelines.txt"
        )
        with open(data_file) as f:
            lines = f.readlines()
        current = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                if current:
                    pipelines.append(" ".join(current))
                    current = []
                continue
            if stripped.endswith("\\"):
                stripped = stripped[:-1].rstrip()
            if stripped.startswith("gst-launch"):
                continue
            current.append(stripped)
        if current:
            pipelines.append(" ".join(current))
        return pipelines

    def test_registry_access(self):
        global RegistryAccess
        if pcfgst is None:

            from pycfutils.gstreamer.registry_access import RegistryAccess

            ra = RegistryAccess()
            self.assertIsNotNone(ra)
        else:

            ra = RegistryAccess()
            self.assertIsInstance(ra.contents(), dict)

    def compare_pipeline_strings(self, input, output):  # Lame
        inls = tuple(e.strip() for e in input.split("!"))
        outls = output.split("\n")
        start = 0
        while start < len(outls) and not outls[start].strip():
            start += 1
        start += 1
        outls = tuple(e.strip() for e in "\n".join(outls[start:]).split("!"))
        if len(inls) != len(outls):
            print(f"Element count mismatch: {len(inls) != len(outls)}")
            return False
        for idx, inl in enumerate(inls):
            i = inl.find(",")
            i = inl.find(" ") if i == -1 else i
            ine = inl if i == -1 else inl[:i].strip('"')
            outl = outls[idx]
            i = outl.find(",")
            i = outl.find(" ") if i == -1 else i
            oute = outl if i == -1 else outl[:i].strip('"')
            if not ine or not oute or ine != oute:
                print(f"Element ({idx}) mismatch: '{ine}' != '{oute}'")
                return False
        return True

    def test_pipeline_parser(self):
        global PipelineParser
        if pcfgst is None:

            from pycfutils.gstreamer.pipeline_parser import PipelineParser

            pparser = PipelineParser()
            self.assertIsNotNone(pparser)
        else:

            pparser = PipelineParser()
            pipeline_strings = tuple(self.read_pipelines())
            for idx, pipeline_string in enumerate(pipeline_strings):
                # print(f"----- Pipeline {idx}:\n--- Input:\n{pipeline_string}\n")
                pipeline = self.generate_pipeline(pipeline_string)
                output = pparser.gst_launch(pipeline)
                # print(f"--- Output:\n{output}\n")
                self.assertTrue(self.compare_pipeline_strings(pipeline_string, output))
