"""Optional projectM 4.2 C API host; no movement or MilkDrop implementation here."""
import ctypes as C
import json
import os
from pathlib import Path

from PIL import Image

from .io import sha256

PROJECTM_COMMIT = "349b7cc2db312d47cef6be24c6856ced6643a385"
_users = 0


class ProjectMBackend:
    def __init__(self, size, fps, preset, mix, seed, work):
        del seed, work  # projectM has no public seed API; do not claim seeded engine noise.
        global _users
        try:
            import glfw
            from OpenGL import GL
        except ImportError as exc:
            raise ValueError("projectM needs pip install 'framerudiments[projectm]' and the pinned native runtime") from exc
        self.glfw, self.gl = glfw, GL
        self.window = self.handle = None
        self.texture = self.fbo = self.output_texture = None
        self.dll_dirs = []
        self.registered = False
        path = Path(os.environ.get("FRAMERUDIMENTS_PROJECTM_LIBRARY", ""))
        if not path.is_file():
            raise ValueError("Set FRAMERUDIMENTS_PROJECTM_LIBRARY to the pinned projectM shared library")
        receipt = path.parent / "framerudiments-projectm.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        if data.get("source_commit") != PROJECTM_COMMIT or data.get("library_sha256") != sha256(path):
            raise ValueError("projectM runtime receipt does not match the required source pin and library bytes")
        if os.name == "nt":
            for folder in [str(path.parent), *os.environ.get("FRAMERUDIMENTS_PROJECTM_DLL_DIRS", "").split(os.pathsep)]:
                if folder:
                    self.dll_dirs.append(os.add_dll_directory(folder))
        try:
            self.lib = C.CDLL(str(path.resolve()))
            self._bind()
            components = [C.c_int() for _ in range(3)]
            self.lib.projectm_get_version_components(*(C.byref(item) for item in components))
            actual_version = tuple(item.value for item in components)
            if actual_version != (4, 2, 0):
                raise ValueError(f"Unexpected projectM native version: {actual_version}")
            if not _users and not glfw.init():
                raise RuntimeError("GLFW could not initialise OpenGL; use a supported GPU or Mesa/Xvfb")
            _users += 1
            self.registered = True
            glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
            glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
            glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
            glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
            glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
            self.window = glfw.create_window(*size, "FrameRudiments projectM offscreen", None, None)
            if not self.window:
                raise RuntimeError("projectM requires an OpenGL 3.3 context")
            glfw.make_context_current(self.window)
            glfw.swap_interval(0)
            self.frame_size, self.mix = size, mix
            # Pinned projectM legacy VideoEcho stretches portrait output. A square
            # overscan canvas avoids that upstream path without patching the library.
            self.size = (max(size), size[1]) if size[1] > size[0] else size
            self.crop_left = (self.size[0] - size[0]) // 2
            self.handle = self.lib.projectm_create()
            if not self.handle:
                raise RuntimeError("projectM could not create its renderer")
            self.failures = []
            log_type = C.CFUNCTYPE(None, C.c_char_p, C.c_int, C.c_void_p)
            def collect_log(message, level, user):
                text = (message or b"").decode("utf-8", "replace")
                if level >= 5 or (level >= 4 and "fallback" in text.lower()):
                    self.failures.append(text)
            self.log_callback = log_type(collect_log)
            self.lib.projectm_set_log_callback(self.log_callback, True, None)
            callback = C.CFUNCTYPE(None, C.c_char_p, C.c_char_p, C.c_void_p)
            self.failure_callback = callback(lambda name, message, user: self.failures.append(
                (message or b"Unknown preset error").decode("utf-8", "replace")))
            self.lib.projectm_set_preset_switch_failed_event_callback(self.handle, self.failure_callback, None)
            self.lib.projectm_set_window_size(self.handle, *self.size)
            self.lib.projectm_set_mesh_size(self.handle, 64, 48)
            self.lib.projectm_set_fps(self.handle, fps)
            self.lib.projectm_set_frame_time(self.handle, 0.0)
            self.lib.projectm_set_preset_locked(self.handle, True)
            self.lib.projectm_set_hard_cut_enabled(self.handle, False)
            self.lib.projectm_set_preset_start_clean(self.handle, True)
            self.lib.projectm_load_preset_file(self.handle, os.fsencode(preset), False)
            self._check()
            self.texture = self._texture()
            self.output_texture = self._texture()
            self.fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                                      GL.GL_TEXTURE_2D, self.output_texture, 0)
            if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("projectM output framebuffer is incomplete")
            # Allocate the preset feedback buffers without consuming output frame zero.
            self.lib.projectm_opengl_render_frame_fbo(self.handle, int(self.fbo))
            self._check()
            self.evidence = dict(engine="projectm", version="4.2.0-dev", source_commit=PROJECTM_COMMIT,
                                 library_sha256=sha256(path), runtime_receipt=data, native_version=list(actual_version),
                                 glfw_version=__import__("importlib.metadata", fromlist=["version"]).version("glfw"),
                                 pyopengl_version=__import__("importlib.metadata", fromlist=["version"]).version("PyOpenGL"),
                                 opengl=GL.glGetString(GL.GL_VERSION).decode(),
                                 renderer=GL.glGetString(GL.GL_RENDERER).decode(),
                                 input="full composed RGBA before MilkDrop warp and composite",
                                 frame_size=list(self.frame_size), engine_canvas_size=list(self.size),
                                 portrait_mapping="centred square overscan, then exact pixel crop" if self.crop_left else "native viewport",
                                 clock="projectm_set_frame_time(output frame / fps)",
                                 warmup_frames=1, random_seed_supported=False)
        except BaseException:
            self.close()
            raise

    def _bind(self):
        h = C.c_void_p
        specs = {
            "create": (h, []), "destroy": (None, [h]),
            "get_version_components": (None, [C.POINTER(C.c_int)] * 3),
            "set_log_callback": (None, [C.c_void_p, C.c_bool, C.c_void_p]),
            "set_window_size": (None, [h, C.c_size_t, C.c_size_t]),
            "set_mesh_size": (None, [h, C.c_size_t, C.c_size_t]),
            "set_fps": (None, [h, C.c_int32]),
            "set_frame_time": (None, [h, C.c_double]),
            "get_last_frame_time": (C.c_double, [h]),
            "set_preset_locked": (None, [h, C.c_bool]),
            "set_hard_cut_enabled": (None, [h, C.c_bool]),
            "set_preset_start_clean": (None, [h, C.c_bool]),
            "load_preset_file": (None, [h, C.c_char_p, C.c_bool]),
            "set_preset_switch_failed_event_callback": (None, [h, C.c_void_p, h]),
            "pcm_add_float": (None, [h, C.POINTER(C.c_float), C.c_uint, C.c_int]),
            "opengl_burn_texture": (None, [h, C.c_uint32, C.c_int, C.c_int, C.c_int, C.c_int]),
            "opengl_render_frame_fbo": (None, [h, C.c_uint32]),
        }
        for name, (result, args) in specs.items():
            try:
                fn = getattr(self.lib, "projectm_" + name)
            except AttributeError as exc:
                raise ValueError(f"Pinned projectM 4.2 API missing: {name}") from exc
            fn.restype, fn.argtypes = result, args

    def _texture(self):
        gl = self.gl
        texture = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        for parameter in (gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, parameter, gl.GL_LINEAR)
        for parameter in (gl.GL_TEXTURE_WRAP_S, gl.GL_TEXTURE_WRAP_T):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, parameter, gl.GL_CLAMP_TO_EDGE)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, *self.size, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
        return texture

    def _check(self):
        if self.failures:
            raise RuntimeError("projectM preset failed: " + "; ".join(self.failures))

    def process(self, image, context, pcm):
        gl = self.gl
        self.glfw.make_context_current(self.window)
        self.lib.projectm_set_log_callback(self.log_callback, True, None)
        frame = image.convert("RGBA")
        if self.size != self.frame_size:
            canvas = Image.new("RGBA", self.size, "black")
            canvas.paste(frame, (self.crop_left, 0))
            frame = canvas
        # Uniform source contribution preserves previous processed pixels in feedback.
        frame.putalpha(round(self.mix * 255))
        pixels = frame.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, *self.size, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, pixels)
        gl.glViewport(0, 0, *self.size)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendEquation(gl.GL_FUNC_ADD)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        self.lib.projectm_set_frame_time(self.handle, context.output_seconds)
        self.lib.projectm_opengl_burn_texture(self.handle, int(self.texture), 0, 0, *self.size)
        audio = (C.c_float * len(pcm)).from_buffer_copy(pcm)
        self.lib.projectm_pcm_add_float(self.handle, audio, len(pcm) // 2, 2)
        self.lib.projectm_opengl_render_frame_fbo(self.handle, int(self.fbo))
        self._check()
        actual = self.lib.projectm_get_last_frame_time(self.handle)
        if abs(actual - context.output_seconds) > 1e-9:
            raise RuntimeError("projectM did not honour the offline frame clock")
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo)
        gl.glPixelStorei(gl.GL_PACK_ALIGNMENT, 1)
        raw = gl.glReadPixels(0, 0, *self.size, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE)
        frame = Image.frombytes("RGBA", self.size, raw).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return frame.crop((self.crop_left, 0, self.crop_left + self.frame_size[0], self.frame_size[1]))

    def close(self):
        global _users
        if self.window:
            self.glfw.make_context_current(self.window)
            if self.handle:
                self.lib.projectm_destroy(self.handle)
                self.handle = None
            if self.texture:
                self.gl.glDeleteTextures([self.texture])
            if self.output_texture:
                self.gl.glDeleteTextures([self.output_texture])
            if self.fbo:
                self.gl.glDeleteFramebuffers(1, [self.fbo])
            self.glfw.destroy_window(self.window)
            self.window = None
        if hasattr(self, "log_callback"):
            self.lib.projectm_set_log_callback(None, True, None)
        if self.registered:
            _users -= 1
            if not _users:
                self.glfw.terminate()
            self.registered = False
        if hasattr(self, "evidence"):
            self.evidence["cleanup"] = dict(instance_destroyed=self.handle is None, window_destroyed=self.window is None)
        for directory in self.dll_dirs:
            directory.close()
        self.dll_dirs = []
