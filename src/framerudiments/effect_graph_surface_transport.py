"""M8.2 explicit host surface transport, alpha association and colour conversion.

Existing graph ports and the GL bridge remain RGBA8, straight-alpha and
*display-referred*. No implicit ICC profile, scene-linear rendering, HDR, GPU
residency, or shader conversion is asserted. Linear-sRGB float staging is an
explicit opt-in conversion of an input the caller certifies as sRGB-encoded.

A payload is an immutable byte snapshot. Rows are explicitly ordered and can
have zero-filled alignment padding; the current GPU bridge uses tight top-first
RGBA8 and does not flip image rows. Conversion never mutates caller images.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from PIL import Image

MAX_DIMENSION = 16384
MAX_PIXELS = 67_108_864
_BYTES_PER_PIXEL = {'rgba8': 4, 'r8': 1, 'rgba32f': 16}


def _dimension(number, label):
    if type(number) is not int or not 1 <= number <= MAX_DIMENSION:
        raise ValueError(f'{label} must be an integer in 1..{MAX_DIMENSION}')


@dataclass(frozen=True)
class TransportContract:
    """Physical bytes for one independent host transfer, not a graph port.

    `rgba8` can carry straight or premultiplied display-referred colours.
    `r8` is coverage data without RGB. `rgba32f` is *explicitly converted*
    linear-sRGB staging, always little-endian float32, not a GPU format offer.
    """

    width: int
    height: int
    pixel_format: str = 'rgba8'
    alpha: str = 'straight'
    colour_space: str = 'display-referred'
    row_order: str = 'top-first'
    alignment: int = 1

    def __post_init__(self):
        _dimension(self.width, 'surface width')
        _dimension(self.height, 'surface height')
        if self.width * self.height > MAX_PIXELS:
            raise ValueError('surface exceeds supported pixel budget')
        if type(self.pixel_format) is not str or self.pixel_format not in _BYTES_PER_PIXEL:
            raise ValueError('unknown transport pixel format')
        allowed = {
            'rgba8': ({'straight', 'premultiplied'}, 'display-referred'),
            'r8': ({'coverage'}, 'data'),
            'rgba32f': ({'straight', 'premultiplied'}, 'linear-srgb'),
        }
        associations, colour_space = allowed[self.pixel_format]
        if type(self.alpha) is not str or self.alpha not in associations or self.colour_space != colour_space:
            raise ValueError('invalid alpha association or colour space for transport format')
        if type(self.row_order) is not str or self.row_order not in {'top-first', 'bottom-first'}:
            raise ValueError('unsupported transport row order')
        if type(self.alignment) is not int or self.alignment not in {1, 2, 4, 8}:
            raise ValueError('transport row alignment must be 1, 2, 4 or 8')

    @property
    def size(self):
        return self.width, self.height

    @property
    def row_bytes(self):
        return self.width * _BYTES_PER_PIXEL[self.pixel_format]

    @property
    def row_stride(self):
        return (self.row_bytes + self.alignment - 1) // self.alignment * self.alignment

    @property
    def nbytes(self):
        return self.row_stride * self.height

    def describe(self):
        return {**asdict(self), 'bytes': self.nbytes,
                'row_bytes': self.row_bytes, 'row_stride': self.row_stride,
                'little_endian': self.pixel_format == 'rgba32f'}


@dataclass(frozen=True)
class TransportPayload:
    contract: TransportContract
    data: bytes

    def __post_init__(self):
        if not isinstance(self.contract, TransportContract):
            raise ValueError('transport payload needs a validated contract')
        if type(self.data) is not bytes or len(self.data) != self.contract.nbytes:
            raise ValueError('transport payload length does not match its declared stride and height')

    def describe(self):
        return self.contract.describe()


def bridge_contract(size):
    """Unmodified legacy GL texture/readback wire format (4 x 8-bit channels)."""
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        raise ValueError('bridge dimensions must be a width/height pair')
    return TransportContract(*size)


def _encode_rows(raw, contract):
    if len(raw) != contract.row_bytes * contract.height:
        raise ValueError('incorrect packed-pixel length')
    rows = [raw[i:i + contract.row_bytes]
            for i in range(0, len(raw), contract.row_bytes)]
    if contract.row_order == 'bottom-first':
        rows.reverse()
    if contract.row_stride == contract.row_bytes:
        return b''.join(rows)
    padding = b'\0' * (contract.row_stride - contract.row_bytes)
    return b''.join(row + padding for row in rows)


def _decode_rows(payload):
    contract = payload.contract
    rows = [payload.data[i:i + contract.row_bytes]
            for i in range(0, len(payload.data), contract.row_stride)]
    if contract.row_order == 'bottom-first':
        rows.reverse()
    return b''.join(rows)


def _srgb_to_linear(rgb):
    import numpy as np

    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb):
    import numpy as np

    return np.where(rgb <= 0.0031308, rgb * 12.92,
                    1.055 * np.maximum(rgb, 0) ** (1 / 2.4) - 0.055)


def pack_image(image, contract, *, source_encoding=None):
    """Encode one Pillow image without changing its pixels or project clock.

    For linear-sRGB staging the caller MUST explicitly assert `source_encoding`
    is `srgb`. This does not infer that a generic display-referred render is
    colour-managed. Quantisation destroys under-alpha RGB at tiny coverage.
    """
    if not isinstance(contract, TransportContract) or not isinstance(image, Image.Image):
        raise ValueError('pack_image requires a Pillow image and transport contract')
    if image.size != contract.size:
        raise ValueError('Pillow image dimensions disagree with transport contract')
    kind = contract.pixel_format
    if kind != 'rgba32f' and source_encoding is not None:
        raise ValueError('source_encoding is only valid for explicit linear-sRGB conversion')
    if image.mode != ('L' if kind == 'r8' else 'RGBA'):
        raise ValueError('transport needs L for coverage or RGBA for colour')
    if kind == 'rgba32f':
        if source_encoding != 'srgb':
            raise ValueError('linear-sRGB conversion needs explicit source_encoding=srgb')
        import numpy as np

        rgba = np.asarray(image, dtype=np.float32) / 255.0
        alpha = rgba[..., 3:4]
        linear = _srgb_to_linear(rgba[..., :3])
        if contract.alpha == 'premultiplied':
            linear *= alpha
        floats = np.concatenate([linear, alpha], axis=-1).astype('<f4')
        raw = floats.tobytes()
    elif kind == 'rgba8' and contract.alpha == 'premultiplied':
        import numpy as np

        rgba = np.asarray(image, dtype=np.uint16).copy()
        rgba[..., :3] = (rgba[..., :3] * rgba[..., 3:4] + 127) // 255
        raw = np.uint8(rgba).tobytes()
    else:
        raw = image.tobytes()
    return TransportPayload(contract, _encode_rows(raw, contract))


def unpack_image(payload, *, output_encoding=None):
    """Decode into Pillow's straight RGBA or L, with explicit float conversion.

    Alpha 0 is canonicalised to black after unassociation; RGB below alpha 255
    can lose precision. The untouched legacy straight-RGBA8 fast path remains
    byte-identical, including intentionally retained hidden RGB.
    """
    if not isinstance(payload, TransportPayload):
        raise ValueError('unpack_image requires a TransportPayload')
    contract = payload.contract
    if contract.pixel_format != 'rgba32f' and output_encoding is not None:
        raise ValueError('output_encoding is only valid for explicit linear-sRGB conversion')
    raw = _decode_rows(payload)
    if contract.pixel_format == 'r8':
        return Image.frombytes('L', contract.size, raw)
    if contract.pixel_format == 'rgba8' and contract.alpha == 'straight':
        return Image.frombytes('RGBA', contract.size, raw)
    import numpy as np

    if contract.pixel_format == 'rgba32f':
        if output_encoding != 'srgb':
            raise ValueError('linear-sRGB decode needs explicit output_encoding=srgb')
        floats = np.frombuffer(raw, dtype='<f4').reshape(contract.height, contract.width, 4)
        if not np.isfinite(floats).all() or np.any((floats < 0) | (floats > 1)):
            raise ValueError('float transport contains non-finite/out-of-range channel')
        rgb, alpha = floats[..., :3], floats[..., 3:4]
        if contract.alpha == 'premultiplied':
            rgb = np.divide(rgb, alpha, out=np.zeros_like(rgb), where=alpha > 0)
        srgb = _linear_to_srgb(np.clip(rgb, 0, 1))
        rgba = np.concatenate([srgb, alpha], axis=-1)
        result = np.uint8(np.rint(np.clip(rgba, 0, 1) * 255))
    else:
        rgba = np.frombuffer(raw, dtype=np.uint8).reshape(contract.height, contract.width, 4)
        alpha = rgba[..., 3:4].astype(np.float32)
        rgb = np.divide(rgba[..., :3].astype(np.float32) * 255, alpha,
                        out=np.zeros_like(rgba[..., :3], dtype=np.float32), where=alpha > 0)
        result = np.concatenate([np.uint8(np.rint(np.clip(rgb, 0, 255))),
                                 rgba[..., 3:4]], axis=-1)
    result[result[..., 3] == 0, :3] = 0
    return Image.fromarray(result, 'RGBA')


def transport_capabilities():
    """Data-only description. Device shaders still consume legacy RGBA8."""
    return {
        'schema': 'framerudiments.surface-transport.v1',
        'graph_port': {'pixel_format': 'rgba8', 'alpha': 'straight',
                       'colour_space': 'display-referred',
                       'row_order': 'top-first', 'row_alignment': 1, 'residency': 'host'},
        'host_formats': ['rgba8/straight', 'rgba8/premultiplied', 'r8/coverage',
                         'rgba32f/linear-srgb/straight', 'rgba32f/linear-srgb/premultiplied'],
        'device_upload': ['rgba8/straight/top-first/tight'],
        'device_readback': ['rgba8/straight/top-first/tight'],
        'conversion_policy': 'explicit; float conversions require caller-certified sRGB encoding',
        'gpu_conversion_shaders': False, 'gpu_r8_lineage': False,
        'row_order_conversion': 'explicit host copy; legacy GPU bridge unchanged',
        'cross_node_residency': False,
    }
