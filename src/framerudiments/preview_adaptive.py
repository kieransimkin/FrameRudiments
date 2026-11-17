"""S3 quality control. No render, wall-clock UTC or audio clock is created here.

Only fresh, current-epoch foreground samples train the controller. The caller
serialises access with the stream condition. Clock injection makes tests exact.
"""
from collections import deque
from math import ceil, isfinite
from time import monotonic

from .preview_stream_contract import _integer

SCHEMA = 'framerudiments.preview-adaptive.v1'


def _ms(value):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError('duration must be finite nonnegative milliseconds')
    return float(value)


def _percentile(values, fraction):
    return sorted(values)[ceil(len(values) * fraction) - 1] if values else None


class AdaptiveController:
    """One-rank changes; 1s cooldown; 12-frame pressure / 90-frame recovery.

    Only the contiguous supported suffix ending at Q5 is executable. Unknown
    lower-tier semantics are a visible capability floor, never a silent bypass.
    """
    def __init__(self, target_fps=30, *, supported_ranks=tuple(range(6)), clock=monotonic):
        _integer(target_fps, 'target_fps', 15, 120)
        if (not isinstance(supported_ranks, (tuple, list)) or not supported_ranks
                or any(type(r) is not int or not 0 <= r <= 5 for r in supported_ranks)
                or len(set(supported_ranks)) != len(supported_ranks) or 5 not in supported_ranks):
            raise ValueError('quality capabilities must include Q5 and unique integer ranks')
        ranks = set(supported_ranks)
        self.floor = 5
        while self.floor - 1 in ranks:
            self.floor -= 1
        self.rank, self.target_fps, self.clock = 5, target_fps, clock
        self.epoch = 0
        self.last_change = -float('inf')
        self.last_reason = 'initial_authored_quality'
        self.samples, self.pressure = deque(maxlen=12), deque(maxlen=12)
        self.ack_ms = deque(maxlen=120)
        self.total_service_ms = deque(maxlen=120)
        self.setup_ms_last = 0
        self.fresh_times, self.ack_times = deque(maxlen=512), deque(maxlen=512)
        self.fresh_total = self.cache_hits = self.ack_total = self.repeated_acks = self.stale_samples = 0
        self.rate_start = clock()
        self.reset('initial')

    @property
    def profile_id(self):
        return f'Q{self.rank}'

    @property
    def budget_ms(self):
        return 900.0 / self.target_fps  # Ten percent reserved for delivery/decode.

    def reset(self, reason='seek', *, target_fps=None):
        if target_fps is not None:
            self.target_fps = _integer(target_fps, 'target_fps', 15, 120)
        self.epoch += 1
        self.samples.clear()
        self.pressure.clear()
        self.ack_ms.clear()
        self.ema_ms = self.good_since = self.backlog_since = None
        self.good_frames = 0
        self.overloaded = False
        self.reset_reason = reason
        return self.epoch

    def hit(self):
        self.cache_hits += 1

    def acknowledge(self, latency_ms, *, repeated=False):
        latency = _ms(latency_ms)
        if type(repeated) is not bool:
            raise ValueError('repeated must be boolean')
        self.ack_total += 1
        self.ack_ms.append(latency)
        if repeated:
            self.repeated_acks += 1
        else:
            self.ack_times.append(self.clock())

    def _change(self, rank, reason, now):
        previous = self.rank
        self.rank, self.last_change, self.last_reason = rank, now, reason
        self.reset('profile_change')
        return {'from_profile': f'Q{previous}', 'to_profile': self.profile_id, 'reason': reason}

    def observe(self, render_ms, *, queue_ms=0, setup_ms=0, epoch, profile_id):
        total, queue, setup = _ms(render_ms), _ms(queue_ms), _ms(setup_ms)
        if setup > total:
            raise ValueError("setup time exceeds total service time")
        render = total - setup
        if epoch != self.epoch or profile_id != self.profile_id:
            self.stale_samples += 1
            return None
        now = self.clock()
        self.total_service_ms.append(total)
        self.setup_ms_last = setup
        self.fresh_total += 1
        self.fresh_times.append(now)
        self.samples.append(render)
        self.ema_ms = render if self.ema_ms is None else .2 * render + .8 * self.ema_ms
        p95, budget = _percentile(self.samples, .95), self.budget_ms
        self.pressure.append(p95 > budget)
        if queue > budget:
            if self.backlog_since is None:
                self.backlog_since = now
        else:
            self.backlog_since = None
        backlog = self.backlog_since is not None and now - self.backlog_since >= .25
        pressured = len(self.pressure) == 12 and sum(self.pressure) >= 8
        catastrophic = render >= max(250.0, budget * 8)
        self.overloaded = p95 > budget
        if (pressured or backlog or catastrophic) and now - self.last_change >= 1:
            if self.rank > self.floor:
                reason = ('catastrophic_service_time' if catastrophic else
                          'persistent_backlog' if backlog else 'p95_over_budget')
                return self._change(self.rank - 1, reason, now)
            self.last_reason = 'quality_floor_over_budget'
        if p95 < .75 * budget and queue <= budget:
            if self.good_since is None:
                self.good_since = now
            self.good_frames += 1
        else:
            self.good_since, self.good_frames = None, 0
        if (self.rank < 5 and self.good_since is not None and self.good_frames >= 90
                and now - self.good_since >= 2 and now - self.last_change >= 1):
            return self._change(self.rank + 1, 'sustained_headroom', now)
        return None

    def allocation_failure(self, *, epoch, profile_id):
        if epoch != self.epoch or profile_id != self.profile_id:
            self.stale_samples += 1
            return None
        now = self.clock()
        self.overloaded = True
        if self.rank > self.floor and now - self.last_change >= 1:
            return self._change(self.rank - 1, 'allocation_failure', now)
        self.last_reason = 'allocation_failure_at_floor_or_cooldown'
        return None

    def snapshot(self):
        now = self.clock()
        for rows in (self.fresh_times, self.ack_times):
            while rows and rows[0] < now - 2:
                rows.popleft()
        elapsed = min(2.0, now - self.rate_start)
        def rate(rows):
            return round(len(rows) / elapsed, 3) if elapsed >= .25 else None
        return {'schema': SCHEMA, 'epoch': self.epoch, 'profile': self.profile_id,
                'quality_floor': f'Q{self.floor}', 'target_fps': self.target_fps,
                'render_budget_ms': self.budget_ms, 'samples': len(self.samples),
                'render_ms_p50': _percentile(self.samples, .50),
                'render_ms_p95': _percentile(self.samples, .95), 'render_ms_ema': self.ema_ms,
                'ack_latency_ms_p95': _percentile(self.ack_ms, .95),
                'total_service_ms_p95': _percentile(self.total_service_ms, .95),
                'setup_ms_last': self.setup_ms_last,
                'render_latency_scope': 'service_excluding_explicit_native_setup',
                'fresh_fps': rate(self.fresh_times), 'acknowledged_fps': rate(self.ack_times),
                'fresh_rendered': self.fresh_total, 'cache_hits': self.cache_hits,
                'acknowledged': self.ack_total, 'repeated_acknowledgements': self.repeated_acks,
                'stale_samples': self.stale_samples, 'over_budget': self.overloaded,
                'target_unattainable': self.overloaded and self.rank == self.floor,
                'last_change_reason': self.last_reason, 'target_achievement_certified': False,
                'rate_scope': 'rolling_2s_server_completions_and_client_decode_acknowledgements'}
