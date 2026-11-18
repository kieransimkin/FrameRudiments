"""M11.3: bounded, frame-addressed native temporal effects.

Each source is a completed pre-AA native RGB frame. Time remapping samples the
existing exact encoded-frame ruler (with optional linear interpolation);
echoes, temporally sampled directional blur and slit scan inspect actual native
frames, never predicted motion. No global state, DSP or external engine replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import ceil, cos, floor, isfinite, radians, sin

from PIL import Image

from .io import fields, integer, number
from .quality_effects import linear_shutter_composite

SCHEMA = 'framerudiments.native-temporal-fx.v1'
MAX_SOURCE_FRAMES = 48
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_HISTORY = 32
MAX_STRIPS = 24
MAX_ECHOES = 12
MAX_BLUR_SAMPLES = 12
_ALLOWED = {'boundary', 'time_remap', 'motion_echoes', 'directional_blur', 'slit_scan', 'modulations'}
_ORDER = ('time_remap', 'motion_echoes', 'directional_blur', 'slit_scan')
_STRENGTHS = ('motion_echoes', 'directional_blur', 'slit_scan')


def _rational(value):
    value = Fraction(value)
    return {'numerator': value.numerator, 'denominator': value.denominator}


def _frame(frame, count):
    if type(frame) is not int or not 0 <= frame < count:
        raise ValueError('native temporal FX frame must be an integer inside the programme')
    return frame


def _expected_fields(value, keys, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    fields(value, set(keys), label)


def _bool(value, label):
    if type(value) is not bool:
        raise ValueError(f'{label} must be boolean')
    return value


def _settings(project, config):
    _expected_fields(config, _ALLOWED, 'post_effects.temporal_fx')
    if not any(k in config for k in _ORDER):
        raise ValueError('temporal_fx requires at least one declared effect')
    boundary = config.get('boundary', 'repeat')
    if boundary not in ('repeat', 'programme'):
        raise ValueError('temporal_fx boundary must be repeat or programme')
    result = {'boundary': boundary}
    if 'time_remap' in config:
        cfg = config['time_remap']
        _expected_fields(cfg, ('output_start', 'output_end', 'source_start', 'rate', 'interpolation'),
                         'temporal_fx.time_remap')
        start = integer(cfg.get('output_start', 0), 'remap output_start', minimum=0,
                        maximum=project.frame_count - 1)
        end = integer(cfg.get('output_end', project.frame_count), 'remap output_end',
                      minimum=start + 1, maximum=project.frame_count)
        if 'output_end' not in cfg:
            raise ValueError('temporal_fx.time_remap requires explicit output_end for bounded reach')
        source_start = integer(cfg.get('source_start', start), 'remap source_start',
                               minimum=0, maximum=project.frame_count - 1)
        rate = cfg.get('rate', [1, 1])
        if not isinstance(rate, (list, tuple)) or len(rate) != 2:
            raise ValueError('time_remap.rate must be [signed numerator, positive denominator]')
        num = integer(rate[0], 'remap rate numerator', minimum=-32, maximum=32)
        den = integer(rate[1], 'remap rate denominator', minimum=1, maximum=32)
        interpolation = cfg.get('interpolation', 'linear')
        if interpolation not in ('linear', 'nearest'):
            raise ValueError('time_remap interpolation must be linear or nearest')
        result['time_remap'] = {'output_start': start, 'output_end': end,
            'source_start': source_start, 'rate': (num, den), 'interpolation': interpolation}
    if 'motion_echoes' in config:
        cfg = config['motion_echoes']
        _expected_fields(cfg, ('count', 'delay_frames', 'decay', 'strength', 'shift_x', 'shift_y'),
                         'temporal_fx.motion_echoes')
        result['motion_echoes'] = {
            'count': integer(cfg.get('count', 3), 'echo count', minimum=1, maximum=MAX_ECHOES),
            'delay_frames': integer(cfg.get('delay_frames', 2), 'echo delay', minimum=1, maximum=MAX_HISTORY),
            'decay': number(cfg.get('decay', .6), 'echo decay', minimum=0, maximum=1),
            'strength': number(cfg.get('strength', .5), 'echo strength', minimum=0, maximum=1),
            'shift_x': number(cfg.get('shift_x', 0), 'echo shift_x', minimum=-96, maximum=96),
            'shift_y': number(cfg.get('shift_y', 0), 'echo shift_y', minimum=-96, maximum=96),
        }
        if result['motion_echoes']['count'] * result['motion_echoes']['delay_frames'] > MAX_HISTORY:
            raise ValueError('motion echoes exceed the 32-frame history window')
    if 'directional_blur' in config:
        cfg = config['directional_blur']
        _expected_fields(cfg, ('samples', 'history_frames', 'angle_degrees', 'distance_px',
                               'falloff', 'strength'), 'temporal_fx.directional_blur')
        result['directional_blur'] = {
            'samples': integer(cfg.get('samples', 5), 'blur samples', minimum=2, maximum=MAX_BLUR_SAMPLES),
            'history_frames': integer(cfg.get('history_frames', 4), 'blur history_frames',
                                      minimum=1, maximum=MAX_HISTORY),
            'angle_degrees': number(cfg.get('angle_degrees', 0), 'blur angle', minimum=-360, maximum=360),
            'distance_px': number(cfg.get('distance_px', 8), 'blur distance', minimum=0, maximum=96),
            'falloff': number(cfg.get('falloff', 1), 'blur falloff', minimum=0, maximum=8),
            'strength': number(cfg.get('strength', .5), 'blur strength', minimum=0, maximum=1),
        }
    if 'slit_scan' in config:
        cfg = config['slit_scan']
        _expected_fields(cfg, ('axis', 'strips', 'history_frames', 'reverse', 'strength'),
                         'temporal_fx.slit_scan')
        axis = cfg.get('axis', 'x')
        if axis not in ('x', 'y'):
            raise ValueError('slit_scan.axis must be x or y')
        result['slit_scan'] = {
            'axis': axis,
            'strips': integer(cfg.get('strips', 12), 'slit strips', minimum=2, maximum=MAX_STRIPS),
            'history_frames': integer(cfg.get('history_frames', 8), 'slit history_frames',
                                      minimum=1, maximum=MAX_HISTORY),
            'reverse': _bool(cfg.get('reverse', False), 'slit_scan.reverse'),
            'strength': number(cfg.get('strength', 1), 'slit strength', minimum=0, maximum=1),
        }
    return result


@dataclass(frozen=True)
class TemporalFXPlan:
    frame: int
    # Stable roles map to exact, de-duplicated encoded source pixels and weights.
    roles: tuple[tuple[str, tuple[tuple[int, Fraction], ...]], ...]
    source_frames: tuple[int, ...]
    limits: dict

    def document(self):
        return {'schema': SCHEMA, 'frame': self.frame,
                'roles': [{'role': role, 'sources': [
                    {'frame': index, 'weight': _rational(weight)} for index, weight in refs]}
                    for role, refs in self.roles],
                'source_frames': list(self.source_frames), 'limits': dict(self.limits),
                'coordinate': 'exact_encoded_frames_pre_feedback',
                'frame_addressed': True}

    def by_role(self):
        return dict(self.roles)


class NativeTemporalFX:
    """Immutable configuration, deterministic tap planning, and frame composition."""

    def __init__(self, project, config, context_at):
        self.frame_count = project.frame_count
        self.size = (project.width, project.height)
        self.span = Fraction((project.end_sample - project.start_sample) * project.fps,
                             project.sample_rate)
        if self.span <= 0:
            raise ValueError('temporal_fx requires a positive source repeat span')
        self.config = _settings(project, config)
        rows = config.get('modulations', [])
        if not isinstance(rows, list) or len(rows) > len(_STRENGTHS):
            raise ValueError('temporal_fx modulations must be a list of at most three strength drivers')
        self.drivers = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('temporal_fx modulation must be an object')
            target = row.get('target')
            if target not in {f'temporal_fx.{key}.strength' for key in _STRENGTHS
                              if key in self.config} or target in self.drivers:
                raise ValueError('temporal_fx driver target must name one unique configured strength')
            proxy = {**row, 'target': 'visual.opacity'}
            proxy.setdefault('range', [0, 1])
            from .modulation import _Modulator
            self.drivers[target] = _Modulator(proxy, project, context_at)
        self.files = [ref for driver in self.drivers.values() for ref in driver.motion.files]
        self.evidence = {'schema': SCHEMA, 'order': list(_ORDER),
            'settings': {key: ({**value, 'rate': list(value['rate'])} if key == 'time_remap' else value)
                         if isinstance(value, dict) else value for key, value in self.config.items()},
            'modulations': [{**driver.evidence, 'target': target}
                            for target, driver in self.drivers.items()],
            'sampling': 'observed_native_RGB8_frames_with_exact_rational_interpolation',
            'feedback': 'feed_temporal_output_to_existing_native_recurrence_if_configured',
            'not_optical_flow': True}

    def _cycle(self, frame):
        if self.config['boundary'] == 'programme':
            return (0, self.frame_count - 1)
        cycle = floor(Fraction(frame, 1) / self.span)
        return (max(0, ceil(cycle * self.span)),
                min(self.frame_count - 1, ceil((cycle + 1) * self.span) - 1))

    def _position(self, frame):
        cfg = self.config.get('time_remap')
        if cfg is None or not cfg['output_start'] <= frame < cfg['output_end']:
            return Fraction(frame)
        elapsed = frame - cfg['output_start']
        return Fraction(cfg['source_start']) + Fraction(elapsed * cfg['rate'][0], cfg['rate'][1])

    def _samples(self, anchor, logical_frame):
        lo, hi = self._cycle(anchor)
        # Non-present history cannot leak backward across a source reset.
        if not lo <= logical_frame <= hi:
            return ()
        value = max(Fraction(lo), min(Fraction(hi), self._position(logical_frame)))
        interpolation = self.config.get('time_remap', {}).get('interpolation', 'linear')
        if interpolation == 'nearest':
            return ((max(lo, min(hi, floor(value + Fraction(1, 2)))), Fraction(1)),)
        low, high = floor(value), ceil(value)
        if low == high:
            return ((low, Fraction(1)),)
        weight = value - low
        return ((low, 1 - weight), (high, weight))

    def plan(self, frame):
        _frame(frame, self.frame_count)
        roles = [('base', self._samples(frame, frame))]
        echo = self.config.get('motion_echoes')
        if echo:
            roles.extend((f'echo:{i}', self._samples(frame, frame - i * echo['delay_frames']))
                         for i in range(1, echo['count'] + 1))
        blur = self.config.get('directional_blur')
        if blur:
            roles.extend((f'blur:{i}', self._samples(frame,
                          frame - round(Fraction(i * blur['history_frames'], blur['samples'] - 1))))
                         for i in range(1, blur['samples']))
        slit = self.config.get('slit_scan')
        if slit:
            roles.extend((f'slit:{i}', self._samples(frame, frame - round(Fraction(
                (slit['strips'] - 1 - i if slit['reverse'] else i) * slit['history_frames'],
                slit['strips'] - 1)))) for i in range(slit['strips']))
        unique = tuple(sorted({index for _name, refs in roles for index, _weight in refs}))
        if not unique or len(unique) > MAX_SOURCE_FRAMES:
            raise ValueError('temporal_fx exceeds 48 independent native frame taps')
        # Count source raster plus its potential raw-byte counterpart, each
        # interpolation output, spatially translated echoes/blur taps and the
        # live composite/temporary work surfaces. These are conservative native
        # RGB payload estimates, not a bound on optional NumPy/GPU scratch.
        interpolated = sum(len(refs) > 1 for _name, refs in roles)
        echo_copies = (sum(bool(refs) for name, refs in roles if name.startswith('echo:'))
                       if echo and (echo['shift_x'] or echo['shift_y']) else 0)
        blur_copies = sum(bool(refs) for name, refs in roles if name.startswith('blur:'))
        retained_buffers = 2 * len(unique) + 2 * interpolated + echo_copies + blur_copies + 10
        size_bytes = retained_buffers * self.size[0] * self.size[1] * 3
        if size_bytes > MAX_SOURCE_BYTES:
            raise ValueError('temporal_fx exceeds the 256 MiB retained native RGB budget')
        return TemporalFXPlan(frame, tuple(roles), unique,
            {'unique_frame_taps': len(unique), 'retained_rgb_bytes_upper_bound': size_bytes,
             'estimated_rgb_buffers': retained_buffers,
             'limit_frame_taps': MAX_SOURCE_FRAMES, 'limit_rgb_bytes': MAX_SOURCE_BYTES,
             'boundary': self.config['boundary']})

    def _strengths(self, context):
        values = {stage: self.config[stage]['strength'] for stage in _STRENGTHS
                  if stage in self.config}
        reports = {}
        for target, driver in self.drivers.items():
            result = driver.output(context)
            if result is None:
                continue
            from .modulation import _combine
            value, native_value = result
            stage = target.split('.')[1]
            values[stage] = max(0, min(1, _combine(
                values[stage], value, driver.combine, driver.weight)))
            reports[target] = {'value': values[stage], 'native_value': native_value}
        return values, reports

    def apply(self, frame_images, plan, context):
        """Compose detached, identical-sized native frames without mutating inputs."""
        if not isinstance(plan, TemporalFXPlan):
            raise ValueError('temporal_fx requires its exact admitted plan')
        if set(frame_images) != set(plan.source_frames):
            raise ValueError('temporal_fx received mismatched native frame taps')
        if any(image.mode != 'RGB' or image.size != self.size for image in frame_images.values()):
            raise ValueError('temporal_fx native input must be same-size RGB8')
        roles = plan.by_role()
        memo = {}

        def sample(role):
            if role not in roles or not roles[role]:
                return None
            if role not in memo:
                refs = roles[role]
                if len(refs) == 1:
                    memo[role] = frame_images[refs[0][0]]
                else:
                    memo[role] = linear_shutter_composite(
                        [frame_images[index] for index, _ in refs],
                        [float(weight) for _index, weight in refs], strength=1)
            return memo[role]

        output = sample('base').copy()
        strengths, native_reports = self._strengths(context)
        applied = []
        echo = self.config.get('motion_echoes')
        if echo:
            weight = strengths['motion_echoes']
            prior = []
            weights = []
            for k in range(1, echo['count'] + 1):
                source = sample(f'echo:{k}')
                if source is None:
                    continue
                if echo['shift_x'] or echo['shift_y']:
                    source = _translate(source, -echo['shift_x'] * k, -echo['shift_y'] * k)
                prior.append(source)
                weights.append(echo['decay'] ** (k - 1))
            if weight and prior and sum(weights):
                # The current frame's original weight is explicit, irrespective
                # of the count/decay of past frames admitted this cycle.
                sw = sum(weights)
                output = linear_shutter_composite([output, *prior],
                    [1 - weight, *(weight * w / sw for w in weights)], strength=1)
            applied.append({'effect': 'motion_echoes', 'count': len(prior),
                            'delay_frames': echo['delay_frames'], 'strength': weight})
        blur = self.config.get('directional_blur')
        if blur:
            weight = strengths['directional_blur']
            taps = []
            weights = []
            for k in range(1, blur['samples']):
                source = sample(f'blur:{k}')
                if source is None:
                    continue
                offset = blur['distance_px'] * k / (blur['samples'] - 1)
                angle = radians(blur['angle_degrees'])
                taps.append(_translate(source, cos(angle) * offset, sin(angle) * offset))
                weights.append((k + 1) ** -blur['falloff'])
            if weight and taps:
                exposure = linear_shutter_composite([output, *taps], [1, *weights], strength=1)
                output = linear_shutter_composite([output, exposure], [1 - weight, weight], strength=1)
            applied.append({'effect': 'directional_blur', 'observed_taps': len(taps),
                            'history_frames': blur['history_frames'], 'strength': weight})
        slit = self.config.get('slit_scan')
        if slit:
            weight = strengths['slit_scan']
            if weight:
                mosaic = output.copy()
                limit = self.size[0 if slit['axis'] == 'x' else 1]
                for i in range(slit['strips']):
                    source = sample(f'slit:{i}') or output
                    lo = i * limit // slit['strips']
                    hi = (i + 1) * limit // slit['strips']
                    if hi <= lo:
                        continue
                    area = (lo, 0, hi, self.size[1]) if slit['axis'] == 'x' else (
                        0, lo, self.size[0], hi)
                    mosaic.paste(source.crop(area), area)
                output = linear_shutter_composite([output, mosaic], [1 - weight, weight], strength=1)
            applied.append({'effect': 'slit_scan', 'strips': slit['strips'],
                            'axis': slit['axis'], 'strength': weight})
        return output, {'schema': SCHEMA, 'frame': plan.frame,
            'ordered_stages': [key for key in _ORDER if key in self.config],
            'sources': list(plan.source_frames), 'roles': plan.document()['roles'],
            'stages': applied, 'modulations': native_reports, 'source_seconds': context.source_seconds,
            'scope': 'after_native_shutter_before_feedback_and_output_AA'}


def _translate(image, dx, dy):
    if not isfinite(dx) or not isfinite(dy):
        raise ValueError('nonfinite temporal translation')
    # Pillow maps output coordinates to source coordinates, so the negative
    # coefficients move the observed image by the positive displacement.
    return image.transform(image.size, Image.Transform.AFFINE,
        (1, 0, -dx, 0, 1, -dy), resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0))


def temporal_fx_bounds(config):
    """Conservative source reach for authored temporal_fx cache invalidation.

    A remap window is finite; derive its maximal displacement exactly rather
    than declaring a false independent operation or a constant small window.
    """
    if not isinstance(config, dict):
        raise ValueError('post_effects.temporal_fx must be an object')
    past = future = 0
    echo = config.get('motion_echoes') or {}
    blur = config.get('directional_blur') or {}
    slit = config.get('slit_scan') or {}
    if echo:
        past = max(past, echo.get('count', 3) * echo.get('delay_frames', 2))
    if blur:
        past = max(past, blur.get('history_frames', 4))
    if slit:
        past = max(past, slit.get('history_frames', 8))
    remap = config.get('time_remap')
    if remap:
        start = remap.get('output_start', 0)
        end = remap.get('output_end')
        source = remap.get('source_start', start)
        rate = remap.get('rate', [1, 1])
        if end is None:
            raise ValueError('time_remap requires output_end for bounded invalidation')
        if (type(start) is not int or type(end) is not int or type(source) is not int
                or not isinstance(rate, (list, tuple)) or len(rate) != 2
                or type(rate[0]) is not int or type(rate[1]) is not int or rate[1] <= 0):
            raise ValueError('invalid temporal_fx remap bounds')
        diff = [Fraction(source - start),
                Fraction(source - (end - 1)) + Fraction((end - 1 - start) * rate[0], rate[1])]
        # Temporal taps themselves may move across the beginning of a remap
        # window, adding at most the authored history lookback.
        past += max(0, ceil(-min(diff))) + 1
        future += max(0, ceil(max(diff))) + 1
    return int(past), int(future)
