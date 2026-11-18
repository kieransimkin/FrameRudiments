"""M15.3 minimal OpenFX Image Effect v1 host, used ONLY inside an owned child.

The C ABI and suite field order follow AcademySoftwareFoundation/openfx
include/ofxCore.h, ofxProperty.h and ofxImageEffect.h (BSD-3-Clause).
Supported: Filter context, Source and Output, RGBA byte, straight alpha,
full-frame, single render, no parameters, 1:1 PAR, CPU, no tiling.
All pointer-bearing code runs in a killable child; never import this into the
normal FrameRudiments compositor or the public descriptor scanner.
"""
from __future__ import annotations

import ctypes as C

from PIL import Image

OK, FAILED, UNSUPPORTED, BAD_HANDLE, BAD_INDEX, BAD_VALUE, REPLY_DEFAULT = 0, 1, 5, 9, 10, 11, 14
P = C.c_void_p
S = C.c_char_p
I = C.c_int
D = C.c_double
CP = C.CFUNCTYPE


class RectD(C.Structure):
    _fields_ = [(k, D) for k in ("x1", "y1", "x2", "y2")]


class Plugin(C.Structure):
    _fields_ = [("pluginApi", S), ("apiVersion", I), ("pluginIdentifier", S),
                ("major", C.c_uint), ("minor", C.c_uint),
                ("setHost", CP(None, P)), ("mainEntry", CP(I, S, P, P, P))]


class Host(C.Structure):
    _fields_ = [("host", P), ("fetchSuite", CP(P, P, S, I))]


# V1 ABI layout: four scalar setters, four array setters, four scalar getters,
# four array getters, reset, dimension. No private layouts or Python API flags.
_TYPES = [("Pointer", P), ("String", S), ("Double", D), ("Int", I)]
_PROP_FIELDS = (
    [("propSet" + name, CP(I, P, S, I, typ)) for name, typ in _TYPES]
    + [("propSet" + name + "N", CP(I, P, S, I, C.POINTER(typ))) for name, typ in _TYPES]
    + [("propGet" + name, CP(I, P, S, I, C.POINTER(P if name == "String" else typ)))
       for name, typ in _TYPES]
    + [("propGet" + name + "N", CP(I, P, S, I, C.POINTER(P if name == "String" else typ)))
       for name, typ in _TYPES]
    + [("propReset", CP(I, P, S)), ("propGetDimension", CP(I, P, S, C.POINTER(I)))]
)


class PropertySuite(C.Structure):
    _fields_ = _PROP_FIELDS


_IMG_FIELDS = [
    ("getPropertySet", CP(I, P, C.POINTER(P))),
    ("getParamSet", CP(I, P, C.POINTER(P))),
    ("clipDefine", CP(I, P, S, C.POINTER(P))),
    ("clipGetHandle", CP(I, P, S, C.POINTER(P), C.POINTER(P))),
    ("clipGetPropertySet", CP(I, P, C.POINTER(P))),
    ("clipGetImage", CP(I, P, D, C.POINTER(RectD), C.POINTER(P))),
    ("clipReleaseImage", CP(I, P)),
    ("clipGetRegionOfDefinition", CP(I, P, D, C.POINTER(RectD))),
    ("abort", CP(I, P)),
    ("imageMemoryAlloc", CP(I, P, C.c_size_t, C.POINTER(P))),
    ("imageMemoryFree", CP(I, P)),
    ("imageMemoryLock", CP(I, P, C.POINTER(P))),
    ("imageMemoryUnlock", CP(I, P)),
]


class ImageSuite(C.Structure):
    _fields_ = _IMG_FIELDS


def _ascii(value):
    if not value or len(value) > 128:
        raise ValueError("OpenFX property name is invalid")
    return value.decode("ascii")


class MinimalHost:
    def __init__(self, width, height, rgba, time):
        self.width, self.height, self.time = width, height, time
        self.props = {}
        self.kinds = {}
        self.next_id = 100
        self.buffers = {}
        self.clips = {}
        self.clip_names = {}
        self.unsupported_requested = False
        # OFX canonical coordinates begin at bottom-left; one positive rowstride
        # advances upward. Pillow PNG input/output begins at top-left.
        raw = rgba.tobytes()
        span = width * 4
        bottom_up = b"".join(raw[y * span:(y + 1) * span]
                             for y in range(height - 1, -1, -1))
        self.src = C.create_string_buffer(bottom_up, len(bottom_up))
        self.dst = C.create_string_buffer(len(bottom_up))
        self.host_handle = self.new("host", {
            "OfxPropType": ("String", [b"OfxTypeImageEffectHost"]),
            "OfxPropName": ("String", [b"FrameRudimentsM15"]),
            "OfxPropAPIVersion": ("Int", [1, 4]),
            "OfxImageEffectPropSupportedContexts": ("String", [b"OfxImageEffectContextFilter"]),
            "OfxImageEffectPropSupportedComponents": ("String", [b"OfxImageComponentRGBA"]),
            "OfxImageEffectPropSupportedPixelDepths": ("String", [b"OfxBitDepthByte"]),
            "OfxImageEffectPropSupportsTiles": ("Int", [0]),
            "OfxImageEffectPropSupportsMultiResolution": ("Int", [0]),
            "OfxImageEffectPropTemporalClipAccess": ("Int", [0]),
            "OfxImageEffectPropSupportsMultipleClipPARs": ("Int", [0]),
            "OfxImageEffectPropMultipleClipDepths": ("Int", [0]),
        })
        self.descriptor = self.new("descriptor", {
            "OfxPropType": ("String", [b"OfxTypeImageEffect"]),
            "OfxImageEffectPropSupportedContexts": ("String", [b"OfxImageEffectContextFilter"]),
            "OfxImageEffectPropSupportedPixelDepths": ("String", [b"OfxBitDepthByte"]),
            "OfxImageEffectPropSupportsTiles": ("Int", [0]),
            "OfxImageEffectPropSupportsMultiResolution": ("Int", [0]),
        })
        self.instance = self.new("instance", {
            "OfxPropType": ("String", [b"OfxTypeImageEffectInstance"]),
            "OfxImageEffectPropContext": ("String", [b"OfxImageEffectContextFilter"]),
            "OfxImageEffectPropProjectSize": ("Double", [float(width), float(height)]),
            "OfxImageEffectPropProjectOffset": ("Double", [0.0, 0.0]),
            "OfxImageEffectPropProjectExtent": ("Double", [float(width), float(height)]),
            "OfxImageEffectPropFrameRate": ("Double", [24.0]),
            "OfxImageEffectPropPixelAspectRatio": ("Double", [1.0]),
        })
        self.in_args = self.new("args", {
            "OfxPropTime": ("Double", [time]),
            "OfxImageEffectPropRenderWindow": ("Int", [0, 0, width, height]),
            "OfxImageEffectPropRenderScale": ("Double", [1.0, 1.0]),
            "OfxImageEffectPropFieldToRender": ("String", [b"OfxImageFieldNone"]),
            "OfxImageEffectPropSequentialRenderStatus": ("Int", [0]),
        })
        self.context_args = self.new("args", {
            "OfxImageEffectPropContext": ("String", [b"OfxImageEffectContextFilter"]),
        })
        self.p_suite = PropertySuite(**self._property_callbacks())
        self.i_suite = ImageSuite(**self._image_callbacks())
        self._fetch = CP(P, P, S, I)(self._fetch_suite)
        self.host = Host(self.host_handle, self._fetch)

    def new(self, kind, initial=None):
        i = self.next_id
        self.next_id += 1
        self.kinds[i] = kind
        self.props[i] = initial or {}
        return i

    def _get(self, h, key, typ, index):
        if h not in self.props:
            return BAD_HANDLE, None
        try:
            t, values = self.props[h][_ascii(key)]
        except (KeyError, UnicodeError, ValueError):
            return 3, None
        if typ and typ != t:
            return BAD_VALUE, None
        if index < 0 or index >= len(values):
            return BAD_INDEX, None
        return OK, values[index]

    def _set(self, h, key, typ, index, value):
        if h not in self.props:
            return BAD_HANDLE
        if not 0 <= index < 128:
            return BAD_INDEX
        try:
            name = _ascii(key)
        except (ValueError, UnicodeError):
            return BAD_VALUE
        if typ == "String" and (not value or len(value) > 4096):
            return BAD_VALUE
        oldtype, items = self.props[h].get(name, (typ, []))
        if typ != oldtype:
            return BAD_VALUE
        while len(items) <= index:
            items.append(None)
        items[index] = value
        self.props[h][name] = (typ, items)
        return OK

    def _property_callbacks(self):
        cb = {}
        for name, typ in _TYPES:
            def single_set(h, k, i, v, name=name):
                return self._set(h, k, name, i, v)

            def many_set(h, k, count, array, name=name):
                if count < 0 or count > 128:
                    return BAD_INDEX
                for j in range(count):
                    result = self._set(h, k, name, j, array[j])
                    if result:
                        return result
                return OK

            def single_get(h, k, i, out, name=name):
                result, value = self._get(h, k, name, i)
                if result:
                    return result
                if name == "String":
                    buf = C.create_string_buffer(value)
                    self.buffers[(h, k, i)] = buf
                    out[0] = C.addressof(buf)
                else:
                    out[0] = value
                return OK

            def many_get(h, k, count, out, name=name):
                if not 0 <= count <= 128:
                    return BAD_INDEX
                for j in range(count):
                    result, value = self._get(h, k, name, j)
                    if result:
                        return result
                    if name == "String":
                        buf = C.create_string_buffer(value)
                        self.buffers[(h, k, j)] = buf
                        out[j] = C.addressof(buf)
                    else:
                        out[j] = value
                return OK

            cb["propSet" + name] = single_set
            cb["propSet" + name + "N"] = many_set
            cb["propGet" + name] = single_get
            cb["propGet" + name + "N"] = many_get
        cb["propReset"] = lambda h, k: self._reset(h, k)
        cb["propGetDimension"] = lambda h, k, result: self._dimension(h, k, result)
        return {name: func_type(cb[name]) for name, func_type in _PROP_FIELDS}

    def _reset(self, h, key):
        if h not in self.props:
            return BAD_HANDLE
        return OK if self.props[h].pop(_ascii(key), None) is not None else 3

    def _dimension(self, h, key, out):
        if h not in self.props:
            return BAD_HANDLE
        try:
            out[0] = len(self.props[h][_ascii(key)][1])
        except (KeyError, ValueError, UnicodeError):
            return 3
        return OK

    def _image_callbacks(self):
        def property_set(h, out):
            if h not in self.props:
                return BAD_HANDLE
            out[0] = h
            return OK

        def clip_define(h, name, out):
            if h != self.descriptor or name not in (b"Source", b"Output") or name in self.clips:
                return UNSUPPORTED
            cid = self.new("clip-descriptor", {
                "OfxPropName": ("String", [name]),
                "OfxImageClipPropSupportedComponents": ("String", [b"OfxImageComponentRGBA"]),
                "OfxImageClipPropOptional": ("Int", [0]),
                "OfxImageClipPropIsMask": ("Int", [0]),
            })
            self.clips[name] = cid
            out[0] = cid
            return OK

        def clip_get(h, name, clip_out, prop_out):
            if h != self.instance or name not in self.clips:
                return BAD_HANDLE
            cid = self.clip_names.get(name)
            if cid is None:
                cid = self.new("clip", {
                    "OfxPropName": ("String", [name]),
                    "OfxImageClipPropConnected": ("Int", [1]),
                    "OfxImageEffectPropComponents": ("String", [b"OfxImageComponentRGBA"]),
                    "OfxImageEffectPropPixelDepth": ("String", [b"OfxBitDepthByte"]),
                    "OfxImageEffectPropPreMultiplication": ("String", [b"OfxImageUnPreMultiplied"]),
                    "OfxImageEffectPropPixelAspectRatio": ("Double", [1.0]),
                })
                self.clip_names[name] = cid
            clip_out[0] = cid
            if prop_out:
                prop_out[0] = cid
            return OK

        def clip_image(clip, when, region, out):
            role = next((key for key, value in self.clip_names.items() if value == clip), None)
            if role is None:
                return BAD_HANDLE
            if abs(when - self.time) > 1e-5:
                return FAILED
            ptr = C.addressof(self.src if role == b"Source" else self.dst)
            img = self.new("image", {
                "OfxPropType": ("String", [b"OfxTypeImage"]),
                "OfxImagePropData": ("Pointer", [ptr]),
                "OfxImagePropBounds": ("Int", [0, 0, self.width, self.height]),
                "OfxImagePropRegionOfDefinition": ("Int", [0, 0, self.width, self.height]),
                "OfxImagePropRowBytes": ("Int", [self.width * 4]),
                "OfxImageEffectPropComponents": ("String", [b"OfxImageComponentRGBA"]),
                "OfxImageEffectPropPixelDepth": ("String", [b"OfxBitDepthByte"]),
                "OfxImageEffectPropPreMultiplication": ("String", [b"OfxImageUnPreMultiplied"]),
                "OfxImageEffectPropPixelAspectRatio": ("Double", [1.0]),
            })
            out[0] = img
            return OK

        def release_image(h):
            if self.kinds.get(h) != "image":
                return BAD_HANDLE
            self.props.pop(h)
            self.kinds.pop(h)
            return OK

        def rod(clip, when, out):
            if clip not in self.clip_names.values():
                return BAD_HANDLE
            out[0] = RectD(0.0, 0.0, float(self.width), float(self.height))
            return OK

        def unsupported(*_args):
            self.unsupported_requested = True
            return UNSUPPORTED

        functions = {
            "getPropertySet": property_set,
            "getParamSet": unsupported,
            "clipDefine": clip_define,
            "clipGetHandle": clip_get,
            "clipGetPropertySet": property_set,
            "clipGetImage": clip_image,
            "clipReleaseImage": release_image,
            "clipGetRegionOfDefinition": rod,
            "abort": lambda _h: 0,
            "imageMemoryAlloc": unsupported,
            "imageMemoryFree": unsupported,
            "imageMemoryLock": unsupported,
            "imageMemoryUnlock": unsupported,
        }
        return {name: tp(functions[name]) for name, tp in _IMG_FIELDS}

    def _fetch_suite(self, h, name, version):
        if h != self.host_handle or version != 1:
            self.unsupported_requested = True
            return None
        if name == b"OfxPropertySuite":
            return C.addressof(self.p_suite)
        if name == b"OfxImageEffectSuite":
            return C.addressof(self.i_suite)
        self.unsupported_requested = True
        return None

    def output_image(self):
        raw = self.dst.raw[:self.width * self.height * 4]
        stride = self.width * 4
        top_down = b"".join(raw[y * stride:(y + 1) * stride]
                            for y in range(self.height - 1, -1, -1))
        return Image.frombytes("RGBA", (self.width, self.height), top_down)


def run_plugin(path, plugin_id, input_rgba, frame, fps):
    """Load and execute a selected native OpenFX plugin in the isolated child."""
    width, height = input_rgba.size
    context = MinimalHost(width, height, input_rgba, float(frame / fps))
    native = C.CDLL(str(path))  # MUST be called ONLY in the owned child process.
    # OFX 1.5 introduced an optional early bootstrap. Check for it explicitly:
    # older OpenFX binaries only use OfxPlugin::setHost after GetPlugin.
    if hasattr(native, "OfxSetHost"):
        native.OfxSetHost.argtypes = [C.POINTER(Host)]
        native.OfxSetHost.restype = I
        if native.OfxSetHost(C.byref(context.host)) not in (OK, REPLY_DEFAULT):
            raise ValueError("OpenFX binary rejected the pre-discovery host")
    native.OfxGetNumberOfPlugins.restype = I
    native.OfxGetNumberOfPlugins.argtypes = []
    native.OfxGetPlugin.restype = C.POINTER(Plugin)
    native.OfxGetPlugin.argtypes = [I]
    count = native.OfxGetNumberOfPlugins()
    if not 1 <= count <= 32:
        raise ValueError("OpenFX binary plugin count outside supported range")
    selected = None
    for index in range(count):
        plugin_ptr = native.OfxGetPlugin(index)
        if not plugin_ptr:
            raise ValueError("OpenFX binary returned a null plugin descriptor")
        item = plugin_ptr.contents
        if item.pluginIdentifier and item.pluginIdentifier.decode("ascii") == plugin_id:
            selected = item
            break
    if selected is None:
        raise ValueError("Expected OpenFX plugin identifier not found in binary")
    if (selected.pluginApi != b"OfxImageEffectPluginAPI" or selected.apiVersion != 1
            or selected.major > 1000 or selected.minor > 1000):
        raise ValueError("Unsupported OpenFX plugin API or version")
    selected.setHost(C.byref(context.host))

    def call(action, handle=0, args=0):
        result = selected.mainEntry(action, handle, args, None)
        if result not in (OK, REPLY_DEFAULT):
            raise ValueError(f"OpenFX {action.decode()} failed with status {result}")

    loaded = created = False
    try:
        call(b"OfxActionLoad")
        loaded = True
        call(b"OfxActionDescribe", context.descriptor)
        supported = context.props[context.descriptor].get("OfxImageEffectPropSupportedContexts", ("String", []))[1]
        depths = context.props[context.descriptor].get("OfxImageEffectPropSupportedPixelDepths", ("String", []))[1]
        if (b"OfxImageEffectContextFilter" not in supported or
                b"OfxBitDepthByte" not in depths):
            raise ValueError("OpenFX effect does not declare the exact filter/byte contract")
        call(b"OfxImageEffectActionDescribeInContext", context.descriptor, context.context_args)
        if set(context.clips) != {b"Source", b"Output"}:
            raise ValueError("OpenFX filter requires exactly Source and Output RGBA clips")
        for name, descriptor in context.clips.items():
            values = context.props[descriptor].get("OfxImageClipPropSupportedComponents", ("String", []))[1]
            if b"OfxImageComponentRGBA" not in values:
                raise ValueError("OpenFX declared clip cannot produce RGBA8: " + name.decode())
        properties = context.props[context.descriptor]
        for unsupported_prop in ("OfxImageEffectPropSupportsTiles", "OfxImageEffectPropSupportsMultiResolution",
                                 "OfxImageEffectPropTemporalClipAccess"):
            if properties.get(unsupported_prop, ("Int", [0]))[1][0]:
                raise ValueError("OpenFX plugin needs unsupported temporal/tiled/multiresolution output")
        if context.unsupported_requested:
            raise ValueError("OpenFX plugin requested an unsupported host suite")
        call(b"OfxActionCreateInstance", context.instance)
        created = True
        call(b"OfxImageEffectActionRender", context.instance, context.in_args)
        if context.unsupported_requested:
            raise ValueError("OpenFX plugin requested an unsupported host operation")
        return context.output_image(), {"identifier": plugin_id,
                                        "api": "OfxImageEffectPluginAPI", "api_version": 1,
                                        "plugin_version": [selected.major, selected.minor],
                                        "context": "OfxImageEffectContextFilter",
                                        "host_suites": ["OfxPropertySuiteV1", "OfxImageEffectSuiteV1"]}
    finally:
        if created:
            try:
                selected.mainEntry(b"OfxActionDestroyInstance", context.instance, None, None)
            except Exception:
                pass
        if loaded:
            try:
                selected.mainEntry(b"OfxActionUnload", None, None, None)
            except Exception:
                pass
