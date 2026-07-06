import os
import platform
import sys
import unittest

from pycfutils import miscellaneous as cfmisc

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    HAS_GI = True
except (ImportError, ValueError):
    HAS_GI = False


HAS_NVDS = HAS_GI and (
    os.environ.get("DS_VERSION") is not None
    and os.path.isdir("/opt/nvidia/deepstream")
    and platform.architecture()[-1][:3].upper()
    == "ELF"  # Linux container on Mac not working
)

SAMPLE_VIDEO = os.environ.get("SAMPLE_VIDEO", "/media/videos/sample_1080p_h264.mp4")


def ts_str():
    return cfmisc.timestamp_string(
        human_readable=True, time_separator="-", separator="-"
    )


class DummyGStreamerTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if HAS_GI:
            raise unittest.SkipTest("PyGObject is installed; skipping dummy tests")

        from types import ModuleType

        class GstDummy:
            Bin = None
            Pipeline = None
            is_initialized = staticmethod(lambda: False)
            init = staticmethod(lambda argv=None: None)

        gi_mod = ModuleType("gi")
        gi_mod.repository = ModuleType("gi.repository")
        gi_mod.repository.Gst = GstDummy
        gi_mod.require_version = lambda ns, ver: None
        sys.modules["gi"] = gi_mod
        sys.modules["gi.repository"] = gi_mod.repository

    def test_registry_access_import(self):
        from pycfgst.registry_access import RegistryAccess

        self.assertIsNotNone(RegistryAccess())

    def test_pipeline_parser_import(self):
        from pycfgst.pipeline_parser import PipelineParser

        self.assertIsNotNone(PipelineParser())


class _GStreamerTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["GST_DEBUG"] = (
            "2,v4l2:1,qtdemux:1,basesrc:1,v4l2videodec:1"
            ",vadisplay:1,v4l2bufferpool:1,uridecodebin:1"
        )
        Gst.init(argv=None)

        from pycfgst.pipeline_parser import PipelineParser
        from pycfgst.registry_access import RegistryAccess

        cls.RegistryAccess = RegistryAccess
        cls.PipelineParser = PipelineParser

    def assertValidGstLaunch(self, output, quote_replace=""):
        # print("----- output\n", output)
        command = " ! ".join(self.split_output_string(output)).replace(
            "'", quote_replace
        )
        pipeline = Gst.parse_launch(command)
        self.assertIsNotNone(pipeline)
        pipeline.set_state(Gst.State.NULL)

    def factory_has_property(self, factory, property_):
        elem = Gst.ElementFactory.make(factory)
        if elem is None:
            return False
        return elem.find_property(property_) is not None

    def split_output_string(self, output):
        outls = output.split("\n")
        outls = [e.strip("\\").strip() for e in outls if e.strip()][1:]
        outls = tuple(e.strip() for e in " ".join(outls).split("!"))
        return outls

    def compare_pipeline_strings(self, input, output):  # Lamish
        inls = tuple(e.strip() for e in input.split("!"))
        outls = self.split_output_string(output)
        # print("-------In\n", inls)
        # print("-------Out\n", outls)

        if len(inls) != len(outls):
            print(f"Element count mismatch: {len(inls) != len(outls)}")
            return False
        for idx, inl in enumerate(inls):
            i = inl.find(",")
            i = inl.find(" ") if i == -1 else i
            ine = inl if i == -1 else inl[:i].strip('"')
            outl = outls[idx]
            if outl.startswith("'"):
                outl = outl[1:]
            i = outl.find(",")
            i = outl.find(" ") if i == -1 else i
            oute = outl if i == -1 else outl[:i].strip('"')
            if not ine or not oute or ine != oute:
                print(f"Element ({idx}) mismatch: '{ine}' != '{oute}'")
                return False
        return True


@unittest.skipUnless(HAS_GI, "PyGObject not installed")
class RegistryAccessTestCase(_GStreamerTestCase):

    def test_registry_access(self):
        ra = self.RegistryAccess()
        self.assertIsInstance(ra.contents(), dict)


@unittest.skipUnless(HAS_GI, "PyGObject not installed")
class PipelineParserGenericTestCase(_GStreamerTestCase):

    def test_double_properties(self):
        volume = 1.33
        pipeline_str = f"audiotestsrc ! volume volume={volume} ! fakesink"
        pipeline = Gst.parse_launch(pipeline_str)
        self.assertIsNotNone(pipeline)
        pipeline.set_state(Gst.State.PAUSED)
        pparser = self.PipelineParser()
        pstr = pparser.gst_launch(pipeline)
        # print(f"Output: {pstr}")
        self.assertValidGstLaunch(pstr)
        outls = self.split_output_string(pstr)
        self.assertIn(
            f"volume volume={self.PipelineParser.format_value(volume)}",
            " ".join(outls),
        )
        pipeline.set_state(Gst.State.NULL)


@unittest.skipUnless(HAS_GI, "PyGObject not installed")
class PipelineParserGstLaunchTestCase(_GStreamerTestCase):

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

    def test_gst_launch_pipelines(self):
        pparser = self.PipelineParser()
        pipeline_strings = tuple(self.read_pipelines())
        for pipeline_string in pipeline_strings:
            pipeline = self.generate_pipeline(pipeline_string)
            self.assertIsNotNone(pipeline)
            pstr = pparser.gst_launch(pipeline)
            self.assertValidGstLaunch(pstr)
            self.assertTrue(self.compare_pipeline_strings(pipeline_string, pstr))


@unittest.skipUnless(HAS_GI, "PyGObject not installed")
class PipelineParserBinTestCase(_GStreamerTestCase):

    def save_dot(self, pipeline, file_name):
        with open(file_name, mode="w") as f:
            f.write(Gst.debug_bin_to_dot_data(pipeline, Gst.DebugGraphDetails.ALL))

    def create_element(self, factory, properties=None):
        elem = Gst.ElementFactory.make(factory)
        if properties:
            for prop, value in properties.items():
                elem.set_property(prop, value)
        return elem

    def create_bin(self, name, factory_data):
        b = Gst.Bin.new(name)
        elements = []
        for factory, properties in factory_data:
            elem = self.create_element(factory, properties)
            b.add(elem)
            elements.append(elem)
        for i in range(len(elements) - 1):
            elements[i].link(elements[i + 1])
        sink_pad = elements[0].get_static_pad("sink")
        if sink_pad:
            b.add_pad(Gst.GhostPad.new("sink", sink_pad))
        src_pad = elements[-1].get_static_pad("src")
        if src_pad:
            b.add_pad(Gst.GhostPad.new("src", src_pad))
        return b

    def test_one_bin(self):
        caps_str = "video/x-raw,width=960,height=540"
        pipeline_str = (
            f'videotestsrc pattern=18 ! videoscale ! "{caps_str}" ! fakevideosink'
        )
        pipeline = Gst.Pipeline.new("test-one-bin")
        src = self.create_element("videotestsrc", {"pattern": 18})
        scale_bin = self.create_bin(
            "scale-bin",
            [
                ("videoscale", {}),
                (
                    "capsfilter",
                    {"caps": Gst.Caps.from_string(caps_str)},
                ),
            ],
        )
        sink = self.create_element("fakevideosink")
        for elem in (src, scale_bin, sink):
            pipeline.add(elem)
        src.link(scale_bin)
        scale_bin.link(sink)
        self.assertIsNotNone(pipeline)
        pipeline.set_state(Gst.State.PAUSED)
        pparser = self.PipelineParser()
        pstr = pparser.gst_launch(pipeline)
        # print(f"Output: {pstr}")
        # self.save_dot(pipeline, f"test_one_bin_{ts_str()}.dot")
        self.assertValidGstLaunch(pstr)
        self.assertTrue(self.compare_pipeline_strings(pipeline_str, pstr))
        pipeline.set_state(Gst.State.NULL)

    @unittest.skipUnless(os.path.isfile(SAMPLE_VIDEO), "sample video not available")
    def test_playbin(self):
        pipeline = Gst.parse_launch(f"playbin uri=file://{SAMPLE_VIDEO}")
        self.assertIsNotNone(pipeline)
        pipeline.set_state(Gst.State.PAUSED)
        pparser = self.PipelineParser()
        pstr = pparser.gst_launch(pipeline)
        # print(f"Output: {pstr}")
        # self.save_dot(pipeline, f"test_playbin_{ts_str()}.dot")
        self.assertValidGstLaunch(pstr)
        self.assertTrue(self.split_output_string(pstr)[0].startswith("playbin"))
        pipeline.set_state(Gst.State.NULL)


@unittest.skipUnless(HAS_NVDS, "NVidia DeepStream not available")
class PipelineParserNVidiaTestCase(_GStreamerTestCase):

    _vttest_lib = os.path.join(
        os.path.dirname(__file__),
        "data",
        "nvdsvideotemplate",
        "testlib0",
        "libvttest0.so",
    )
    _vttest_lib_props = {
        "prop0": "val0",
        "prop1": "val1[{]},<.>/?|;:",
        "prop2": "val 2 ",
    }
    _vttest_lib_sep = ";;;"

    def test_nvvideoconvert(self):
        pipeline_str = "videotestsrc pattern=18 ! nvvideoconvert ! fakevideosink"
        pipeline = Gst.parse_launch(pipeline_str)
        self.assertIsNotNone(pipeline)
        pipeline.set_state(Gst.State.PAUSED)
        pparser = self.PipelineParser()
        pstr = pparser.gst_launch(pipeline)
        # print(f"Output: {pstr}")
        self.assertValidGstLaunch(pstr)
        self.assertTrue(self.compare_pipeline_strings(pipeline_str, pstr))
        pipeline.set_state(Gst.State.NULL)

    @unittest.skipUnless(os.path.isfile(SAMPLE_VIDEO), "sample video not available")
    def test_nvv4ldecoder(self):
        pipeline_str = (
            f'filesrc location="{SAMPLE_VIDEO}" ! qtdemux ! h264parse'
            " ! nvv4l2decoder ! nvvideoconvert ! fakevideosink"
        )
        pipeline = Gst.parse_launch(pipeline_str)
        self.assertIsNotNone(pipeline)
        pipeline.set_state(Gst.State.PAUSED)
        pparser = self.PipelineParser()
        pstr = pparser.gst_launch(pipeline)
        # print(f"Output: {pstr}")
        self.assertValidGstLaunch(pstr)
        self.assertTrue(self.compare_pipeline_strings(pipeline_str, pstr))
        pipeline.set_state(Gst.State.NULL)

    def _test_videotemplate(self, pipeline_str):
        pipeline = Gst.parse_launch(pipeline_str)
        self.assertIsNotNone(pipeline)
        ret = pipeline.set_state(Gst.State.PLAYING)
        self.assertNotEqual(ret, Gst.StateChangeReturn.FAILURE)
        bus = pipeline.get_bus()
        msg = bus.timed_pop_filtered(
            5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        self.assertIsNotNone(msg)
        self.assertEqual(msg.type, Gst.MessageType.EOS)
        pparser = self.PipelineParser()
        pstr = pparser.gst_launch(pipeline)
        # print(f"Output: {pstr}")
        self.assertValidGstLaunch(pstr, quote_replace='"')
        self.assertEqual(pstr.count("customlib-props="), 1)
        pipeline.set_state(Gst.State.NULL)
        return pstr

    def _customlib_props_str(self, props):
        return " ".join(f'customlib-props="{k}:{v}"' for k, v in props.items())

    @unittest.skipUnless(os.path.isfile(_vttest_lib), "libvttest0.so not built")
    def test_videotemplate_original_joined(self):
        props_str = self._vttest_lib_sep.join(
            f"{k}:{v}" for k, v in self._vttest_lib_props.items()
        )
        prop_sep_str = f' customlib-props-sep="{self._vttest_lib_sep}"'
        pipeline_str = (
            "videotestsrc pattern=18 num-buffers=1"
            " ! nvvideoconvert"
            f' ! nvdsvideotemplate customlib-name="{self._vttest_lib}"'
        )
        has_sep = self.factory_has_property("nvdsvideotemplate", "customlib-props-sep")
        if has_sep:
            pipeline_str += prop_sep_str
        pipeline_str += f' customlib-props="{props_str}"'
        pipeline_str += " ! fakevideosink"
        # print(pipeline_str)
        print(
            f"videotemplate {'DOES' if has_sep else 'does NOT'} support customlib-props-sep,"
            f" {len(self._vttest_lib_props) if has_sep else 1} line(s) from custom library should be displayed"
        )
        pstr = self._test_videotemplate(pipeline_str)
        k0 = tuple(self._vttest_lib_props.keys())[0]
        self.assertTrue(f"{k0}:{self._vttest_lib_props[k0]}" in pstr)
        self.assertTrue(props_str in pstr)

    @unittest.skipUnless(os.path.isfile(_vttest_lib), "libvttest0.so not built")
    def test_videotemplate_original_separate(self):
        pipeline_str = (
            "videotestsrc pattern=18 num-buffers=1"
            " ! nvvideoconvert"
            f' ! nvdsvideotemplate customlib-name="{self._vttest_lib}"'
            f" {self._customlib_props_str(self._vttest_lib_props)}"
            " ! fakevideosink"
        )
        # print(pipeline_str)
        pstr = self._test_videotemplate(pipeline_str)
        k0 = tuple(self._vttest_lib_props.keys())[0]
        self.assertFalse(f"{k0}:{self._vttest_lib_props[k0]}" in pstr)
