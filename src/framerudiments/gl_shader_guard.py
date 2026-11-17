"""Conservative, fail-closed GL Transitions *subset* preflight (M9.4).

No GL context or shader compiler is opened. This is not a sandbox: only
explicitly reviewed/pinned source may execute, and a GPU process isolation
boundary is still necessary for content from untrusted parties.

A single, straight-line transition(vec2) body is admitted. There are no
user-defined functions, loops, conditionals (except the bounded ternary
operator), new samplers, image/buffer operations or preprocessor directives.
"""

from dataclasses import dataclass
from math import isfinite
import re

POLICY_VERSION = "framerudiments.gl-transition-restricted.v1"
MAX_TOKENS = 768
MAX_STATEMENTS = 48
MAX_CALLS = 64
MAX_EXPRESSION_DEPTH = 12

HOST_UNIFORMS = {
    "progress": "float", "ratio": "float", "resolution": "vec2", "RENDERSIZE": "vec2",
    "TIME": "float", "TIMEDELTA": "float", "FRAMEINDEX": "int",
}
# Pure functions in GLSL 3.30 only. The two host helpers are the only image
# access points; there are deliberately no texture/texelFetch calls.
PURE_CALLS = frozenset({
    "radians", "degrees", "sin", "cos", "tan", "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh", "pow", "exp", "log",
    "exp2", "log2", "sqrt", "inversesqrt", "abs", "sign", "floor", "trunc",
    "round", "roundEven", "ceil", "fract", "mod", "min", "max", "clamp",
    "mix", "step", "smoothstep", "length", "distance", "dot", "cross",
    "normalize", "reflect", "refract", "faceforward", "lessThan", "lessThanEqual",
    "greaterThan", "greaterThanEqual", "equal", "notEqual", "any", "all", "not",
    "float", "int", "bool", "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4",
    "bvec2", "bvec3", "bvec4", "getFromColor", "getToColor",
})
VALUE_TYPES = frozenset({"float", "int", "bool", "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4", "bvec2", "bvec3", "bvec4"})
SWIZZLES = set("xyzwrgbastpq")
HOST_PRIVATE = frozenset({"from_tex", "to_tex", "out_color", "main"})
FORBIDDEN_STATEMENTS = frozenset({
    "for", "while", "do", "if", "else", "switch", "case", "default",
    "continue", "break", "discard", "layout", "buffer", "shared",
    "subroutine", "in", "out", "inout", "precision", "highp", "mediump", "lowp",
})
TOKEN = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<id>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<symbol>==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|[+\-*/%<>=!?:.,;(){}\[\]])"
    r"|(?P<bad>.)", re.DOTALL,
)
ALLOWED_EXPRESSION_SIGNS = frozenset({
    "+", "-", "*", "/", "<", ">", "<=", ">=", "==", "!=", "&&", "||", "!", "?", ":", ",", ".", "(", ")",
})


@dataclass(frozen=True)
class _Token:
    value: str
    category: str
    start: int
    end: int


def _strip_comments(source):
    """Replace comments with spaces, retaining offsets and every newline.

    Strings, backslash line splicing and non-ASCII *code* are not part of the
    supported GLSL subset. UTF-8 text inside comments remains harmless.
    """
    if not isinstance(source, str):
        raise ValueError("GL shader must be UTF-8 text")
    out = list(source)
    i = 0
    while i < len(source):
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = len(source) if end == -1 else end
            for p in range(i, end):
                out[p] = " "
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValueError("GL shader has an unterminated block comment")
            end += 2
            for p in range(i, end):
                if source[p] not in "\r\n":
                    out[p] = " "
            i = end
        else:
            ch = source[i]
            if ch == "#":
                raise ValueError("GL shader preprocessor directives/includes are not permitted")
            if ch in "\"'\\":
                raise ValueError("GL shader strings, quoting and line splicing are unsupported")
            if ord(ch) > 127 or (ord(ch) < 32 and ch not in "\r\n\t"):
                raise ValueError("GL shader code must be printable ASCII")
            i += 1
    return "".join(out)


def _tokens(source):
    result = []
    for item in TOKEN.finditer(source):
        kind = item.lastgroup
        if kind == "space":
            continue
        if kind == "bad":
            raise ValueError(f"GL shader unsupported token {item.group()!r}")
        result.append(_Token(item.group(), kind, item.start(), item.end()))
        if len(result) > MAX_TOKENS:
            raise ValueError(f"GL shader exceeds {MAX_TOKENS} token limit")
    return result


class _Checker:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0
        self.removals = []
        self.calls = 0
        self.statements = 0
        self.global_constants = set()
        self.declared_uniforms = set()

    def peek(self):
        return self.tokens[self.index].value if self.index < len(self.tokens) else None

    def take(self, wanted=None):
        if self.index >= len(self.tokens):
            raise ValueError("GL shader has an incomplete declaration or expression")
        token = self.tokens[self.index]
        if wanted is not None and token.value != wanted:
            raise ValueError(f"GL shader expected {wanted!r}, got {token.value!r}")
        self.index += 1
        return token

    @staticmethod
    def _variable(token, current):
        name = token.value
        if token.category != "id" or name.startswith("gl_") or name in PURE_CALLS or name in HOST_UNIFORMS or name in HOST_PRIVATE or name in current or name in {"transition", "true", "false"}:
            raise ValueError(f"GL shader invalid, duplicate or reserved local name: {name}")
        return name

    def expression(self, visible, *, stop=";"):
        start = self.index
        depth = 0
        ternaries = 0
        while self.peek() is not None:
            tok = self.tokens[self.index]
            val = tok.value
            if val == stop and depth == 0:
                break
            if val in {";", "{", "}", "[", "]", "=", "++", "--", "+=", "-=", "*=", "/=", "%"}:
                raise ValueError(f"GL shader unsupported expression operation: {val}")
            if tok.category == "number":
                try:
                    value = float(val)
                except (ValueError, OverflowError) as exc:
                    raise ValueError("GL shader numeric literal is invalid") from exc
                if not isfinite(value) or value > 1e9:
                    raise ValueError("GL shader numeric literal is outside the supported range")
            elif tok.category == "id":
                before = self.tokens[self.index - 1].value if self.index > start else None
                after = self.tokens[self.index + 1].value if self.index + 1 < len(self.tokens) else None
                if before == ".":
                    if not 1 <= len(val) <= 4 or any(ch not in SWIZZLES for ch in val):
                        raise ValueError("GL shader supports only vector swizzles")
                elif after == "(":
                    if val not in PURE_CALLS:
                        raise ValueError(f"GL shader unsupported function or resource access: {val}")
                    self.calls += 1
                    if self.calls > MAX_CALLS:
                        raise ValueError("GL shader function call budget exceeded")
                elif val not in visible and val not in {"true", "false"}:
                    raise ValueError(f"GL shader unknown or inaccessible identifier: {val}")
            elif val not in ALLOWED_EXPRESSION_SIGNS:
                raise ValueError(f"GL shader unsupported expression token: {val}")
            if val == "(":
                depth += 1
                if depth > MAX_EXPRESSION_DEPTH:
                    raise ValueError("GL shader expression nesting budget exceeded")
            elif val == ")":
                depth -= 1
                if depth < 0:
                    raise ValueError("GL shader has an unbalanced expression")
            elif val == "?":
                ternaries += 1
            elif val == ":":
                ternaries -= 1
                if ternaries < 0:
                    raise ValueError("GL shader has an unmatched conditional colon")
            self.index += 1
        if self.index == start or depth != 0 or ternaries != 0:
            raise ValueError("GL shader has an empty or unbalanced expression")

    def parse(self):
        # Explicit host uniforms only. No buffers, samplers, images, layout
        # qualifiers or user-defined GLSL types are accepted from shader source.
        # GLSL permits host uniform declarations and const globals in either
        # order. Their initializer expressions can refer only to earlier consts.
        while self.peek() in {"uniform", "const"}:
            if self.peek() == "uniform":
                first = self.take("uniform")
                kind = self.take().value
                name = self.take().value
                end = self.take(";")
                if name not in HOST_UNIFORMS:
                    raise ValueError(f"GL transition unsupported shader uniform: {name}")
                if HOST_UNIFORMS[name] != kind:
                    raise ValueError(f"GL transition host uniform {name} must have type {HOST_UNIFORMS[name]}")
                if name in self.declared_uniforms:
                    raise ValueError(f"GL shader duplicate host uniform: {name}")
                self.declared_uniforms.add(name)
                self.removals.append((first.start, end.end))
            else:
                self.take("const")
                ty = self.take().value
                if ty not in VALUE_TYPES:
                    raise ValueError("GL shader unsupported global constant type")
                name = self._variable(self.take(), self.global_constants)
                self.take("=")
                self.expression(self.global_constants)
                self.take(";")
                self.global_constants.add(name)
        if self.peek() == "void":
            raise ValueError("GL transition must expose transition(vec2), not main()")
        if self.peek() != "vec4":
            raise ValueError("GL transition needs a vec4 transition(vec2 uv) function")
        self.take("vec4")
        if self.peek() != "transition":
            raise ValueError("GL transition needs a vec4 transition(vec2 uv) function")
        self.take("transition")
        self.take("(")
        self.take("vec2")
        argument = self.take()
        if argument.category != "id" or argument.value in HOST_UNIFORMS or argument.value in PURE_CALLS or argument.value in HOST_PRIVATE or argument.value.startswith("gl_"):
            raise ValueError("GL shader invalid transition argument")
        self.take(")")
        self.take("{")
        visible = set(HOST_UNIFORMS) | self.global_constants | {argument.value}
        locals_const = set()
        returned = False
        while self.peek() not in (None, "}"):
            self.statements += 1
            if self.statements > MAX_STATEMENTS:
                raise ValueError("GL shader statement budget exceeded")
            if self.peek() in FORBIDDEN_STATEMENTS:
                raise ValueError(f"GL shader unsupported control flow or storage: {self.peek()}")
            if self.peek() == "return":
                self.take("return")
                self.expression(visible)
                self.take(";")
                returned = True
                break
            immutable = self.peek() == "const"
            if immutable:
                self.take("const")
            if self.peek() in VALUE_TYPES:
                self.take()
                name = self._variable(self.take(), visible)
                self.take("=")
                self.expression(visible)
                self.take(";")
                visible.add(name)
                if immutable:
                    locals_const.add(name)
            else:
                if immutable:
                    raise ValueError("GL shader const requires an initialized local declaration")
                token = self.take()
                if self.peek() == "(":
                    raise ValueError(f"GL shader unsupported standalone call: {token.value}")
                if token.value not in visible or token.value in HOST_UNIFORMS or token.value in locals_const or token.value == argument.value or token.value in self.global_constants:
                    raise ValueError("GL shader can assign only initialized mutable locals")
                self.take("=")
                self.expression(visible)
                self.take(";")
        if not returned:
            raise ValueError("GL shader transition must return a vec4 expression")
        self.take("}")
        if self.peek() is not None:
            raise ValueError("GL shader does not permit extra functions or trailing statements")


def inspect_restricted_gl_transition(source):
    """Return sanitized GLSL and an explanatory bounded-subset receipt.

    Never use this as an untrusted-code execution sandbox. The caller must
    independently verify an exact full-file pin and explicit trust opt-in.
    """
    cleaned = _strip_comments(source)
    checker = _Checker(_tokens(cleaned))
    checker.parse()
    text = list(cleaned)
    for a, b in checker.removals:
        for i in range(a, b):
            if text[i] not in "\r\n":
                text[i] = " "
    receipt = {
        "policy": POLICY_VERSION, "tokens": len(checker.tokens),
        "statements": checker.statements, "calls": checker.calls,
        "uniforms": sorted(checker.declared_uniforms),
        "limits": {"tokens": MAX_TOKENS, "statements": MAX_STATEMENTS,
                   "calls": MAX_CALLS, "expression_depth": MAX_EXPRESSION_DEPTH},
        "scope": "restricted_source_review_only_not_a_gpu_sandbox",
    }
    return "".join(text), receipt
