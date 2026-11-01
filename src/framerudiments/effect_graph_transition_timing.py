"""Stateless beat-authoritative timing for effect-graph transitions (M5.5).

The renderer supplies the same source/continuous beat readings as other
DanceRudiment controls; this module never creates or estimates its own clock.
"""

from bisect import bisect_right
import math

TIMING_CLOCKS = frozenset({'continuous', 'source'})
TIMING_REPEATS = frozenset({'once', 'loop', 'pingpong'})
TIMING_PRESETS = frozenset({
    'linear', 'smoothstep', 'ease_in', 'ease_out', 'ease_in_out',
    'triangle', 'pulse', 'bounce',
})
TIMING_ENUMS = {
    'clock': TIMING_CLOCKS,
    'repeat': TIMING_REPEATS,
    'preset': TIMING_PRESETS,
    'reverse': frozenset({False, True}),
}
TIMING_RANGES = {
    'start_beat': (-100_000.0, 100_000.0),
    'duration_beats': (1 / 64, 4096.0),
    'period_beats': (1 / 64, 8192.0),
    'phase_beats': (-4096.0, 4096.0),
    'steps': (0, 64),
}
TIMING_DEFAULTS = {
    'clock': 'continuous', 'repeat': 'once', 'preset': 'linear',
    'start_beat': 0.0, 'duration_beats': 4.0, 'phase_beats': 0.0,
    'reverse': False, 'steps': 0,
}
KEY_EASINGS = frozenset({'linear', 'smoothstep', 'hold'})


def _number(value, label, low, high):
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be finite and in [{low}, {high}]')


def validate_transition_timing(config):
    """Validate an authored timing object, including looping and keyframes."""
    if not isinstance(config, dict) or set(config) - {*TIMING_DEFAULTS, 'period_beats', 'keys'}:
        raise ValueError('timing must be an object with approved fields')
    for name, values in TIMING_ENUMS.items():
        if name in config and (type(config[name]) is not bool and not isinstance(config[name], str)
                               or config[name] not in values):
            raise ValueError(f'invalid transition timing {name}')
    for name, (low, high) in TIMING_RANGES.items():
        if name in config:
            _number(config[name], f'transition timing {name}', low, high)
    steps = config.get('steps', 0)
    if type(steps) is not int or steps == 1:
        raise ValueError('timing steps must be 0 or an integer from 2 to 64')
    repeat = config.get('repeat', 'once')
    period = config.get('period_beats')
    duration = config.get('duration_beats', 4.0)
    if repeat == 'once' and period is not None:
        raise ValueError('period_beats requires loop or pingpong timing')
    if period is not None and period < duration:
        raise ValueError('period_beats must be >= duration_beats')
    keys = config.get('keys')
    if keys is not None:
        if 'preset' in config:
            raise ValueError('timing keys cannot combine with preset')
        if not isinstance(keys, list) or not 2 <= len(keys) <= 32:
            raise ValueError('transition timing keys must contain 2..32 points')
        prior = -1.0
        for key in keys:
            if (not isinstance(key, dict) or not {'at', 'value'} <= set(key)
                    or set(key) - {'at', 'value', 'ease'}):
                raise ValueError('invalid transition timing key')
            _number(key.get('at'), 'timing key at', 0, 1)
            _number(key.get('value'), 'timing key value', 0, 1)
            if key.get('ease', 'linear') not in KEY_EASINGS:
                raise ValueError('invalid transition timing key easing')
            if key['at'] <= prior:
                raise ValueError('timing key positions must strictly increase')
            prior = key['at']
        if keys[0]['at'] != 0 or keys[-1]['at'] != 1:
            raise ValueError('timing keys must cover exactly 0..1')


def _preset(value, name):
    if name == 'smoothstep' or name == 'ease_in_out':
        return value * value * (3 - 2 * value)
    if name == 'ease_in':
        return value * value
    if name == 'ease_out':
        return 1 - (1 - value) ** 2
    if name == 'triangle':
        return 1 - abs(2 * value - 1)
    if name == 'pulse':
        return math.sin(math.pi * value)
    if name == 'bounce':
        return max(0.0, min(1.0, value + 0.17 * math.sin(4 * math.pi * value) * (1 - value)))
    return value


def _keyframe(value, keys):
    positions = [row['at'] for row in keys]
    index = max(0, min(len(keys) - 2, bisect_right(positions, value) - 1))
    start, end = keys[index:index + 2]
    t = (value - start['at']) / (end['at'] - start['at'])
    if start.get('ease') == 'hold' and value < end['at']:
        return start['value']
    if start.get('ease') == 'smoothstep':
        t = t * t * (3 - 2 * t)
    return start['value'] + (end['value'] - start['value']) * t


def transition_timing_progress(config, *, continuous_beat, source_beat=None):
    """Return deterministic transition progress at any authorized beat position.

    Repeated source audio is managed by the host clock: continuous_beat never
    restarts across source repeats; source_beat restarts with the media loop.
    """
    validate_transition_timing(config)
    clock = config.get('clock', 'continuous')
    beat = continuous_beat if clock == 'continuous' else source_beat
    if type(beat) not in (int, float) or not math.isfinite(beat):
        raise ValueError(f'timing requires a finite {clock} beat from the project clock')
    duration = config.get('duration_beats', 4.0)
    relative = beat - config.get('start_beat', 0.0) + config.get('phase_beats', 0.0)
    if relative < 0:
        fraction = 0.0
    elif config.get('repeat', 'once') == 'once':
        fraction = min(1.0, relative / duration)
    else:
        period = config.get('period_beats', duration)
        cycle = math.floor(relative / period)
        fraction = min(1.0, (relative - cycle * period) / duration)
        if config.get('repeat') == 'pingpong' and cycle % 2:
            fraction = 1.0 - fraction
    steps = config.get('steps', 0)
    if steps:
        fraction = round(fraction * (steps - 1)) / (steps - 1)
    if 'keys' in config:
        fraction = _keyframe(fraction, config['keys'])
    else:
        fraction = _preset(fraction, config.get('preset', 'linear'))
    if config.get('reverse', False):
        fraction = 1.0 - fraction
    return max(0.0, min(1.0, float(fraction)))
