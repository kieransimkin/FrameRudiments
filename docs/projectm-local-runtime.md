# A project-local projectM runtime bundle

The successful Windows repair showed missing GNU runtime imports, not a Visual
C++ Redistributable problem. `scripts/ensure_projectm_runtime.py --repository .`
now checks the pinned installed projectM DLL and collects the needed MinGW DLLs
when appropriate. The full-analysis setup hook runs it before its normal native
preflight. The behaviour-analysis launcher also invokes it explicitly.

Collection uses the recorded CMake compiler, a byte-matched built projectM DLL
(or checked active install manifest), compiler version and architecture, and
GCC's own library lookup. The helper checks transitive imports, refuses
conflicting existing files and probes staged then installed dependencies in fresh
processes. All copied files and receipts remain below
`models/runtimes/projectm/install/` and `models/runtimes/projectm/diagnostics/`.
Already loadable installations are left alone. A missing/mismatched compiler,
non-MinGW load error or bad dependency is an explicit stop, not an arbitrary
DLL download. A library-load check is not an OpenGL render test.

The update bundles the collection/verification code, **not unverified DLL bytes
in a platform-independent Python wheel**. The resulting local project runtime
contains its required compiler DLLs. No global PATH or administrator changes are
required. Models and captures are not deleted. Keep the generated runtime out
of Git. Before publicly redistributing a Windows binary archive, preserve the
exact compiler/library notices and applicable source/licence obligations; this
local repair is not a blanket licence clearance for the Espressif toolchain.

Synthetic PE and subprocess-receipt tests never execute their fake binaries.
Real Windows DLL loading was verified by the earlier repair on the user's
machine; the newly integrated launcher still needs a Windows end-to-end check.
