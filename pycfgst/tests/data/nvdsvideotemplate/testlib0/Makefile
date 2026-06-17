DS_ROOT := /opt/nvidia/deepstream/deepstream

PKGS := \
	gstreamer-1.0 \
	gstreamer-base-1.0 \
	gstreamer-video-1.0

TARGET_LIB := libvttest0.so

CFLAGS := -fPIC -std=c++14 -Wall -Werror \
	-I/usr/local/cuda/include \
	-I$(DS_ROOT)/sources/gst-plugins/gst-nvdsvideotemplate/includes \
	-I$(DS_ROOT)/sources/includes \
	$(shell pkg-config --cflags $(PKGS))

ifeq ($(shell uname -m),aarch64)
DS_LIB := /usr/lib/$(shell $(CXX) -dumpmachine)/tegra
else
DS_LIB := $(DS_ROOT)/lib
endif

LDFLAGS := -shared \
	-L$(DS_LIB) \
	-Wl,-rpath,$(DS_LIB) \
	-Wl,--no-as-needed -lnvdsbufferpool -Wl,--as-needed \
	$(shell pkg-config --libs $(PKGS))

all: $(TARGET_LIB)

$(TARGET_LIB): $(wildcard *.cpp)
	$(CXX) -o $@ $^ $(CFLAGS) $(LDFLAGS)

clean:
	rm -f $(TARGET_LIB)
