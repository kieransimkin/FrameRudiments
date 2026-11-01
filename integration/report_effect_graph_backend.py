"""Inspect an authored graph without rendering media or evaluating native drivers.

--probe-gpu is the only option that may create a context. A successful probe
still does not qualify shaders or prove hardware acceleration. No files are
written unless --output is explicitly supplied; existing files are never replaced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_backend_contract import PlanLimits
from framerudiments.effect_graph_integration import create_gpu_backend


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Graph JSON or project containing effect_graph')
    parser.add_argument('--size', type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--mode', choices=['cpu', 'auto', 'gpu'], default='cpu')
    parser.add_argument('--probe-gpu', action='store_true')
    parser.add_argument('--max-retained-host-bytes', type=int)
    parser.add_argument('--max-transfer-bytes', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.probe_gpu and args.mode == 'cpu':
        parser.error('--probe-gpu needs --mode auto or gpu')
    if args.output is not None and args.output.exists():
        parser.error('output already exists; choose a new file')
    data = json.loads(args.input.read_text(encoding='utf-8-sig'))
    size = args.size or [data.get('width'), data.get('height')]
    if None in size:
        parser.error('supply --size for a graph without project dimensions')
    graph = EffectGraph(data.get('effect_graph', data))
    limits = PlanLimits(args.max_retained_host_bytes, args.max_transfer_bytes)
    backend, initialization = None, None
    try:
        if args.probe_gpu:
            initialization = {}
            # Probe in auto mode to retain failure diagnostics in the JSON.
            backend = create_gpu_backend('auto', report=initialization)
        plan = graph.backend_plan({key: size for key in graph.source_ids}, mode=args.mode,
                                  gpu_backend=backend, limits=limits)
        plan['input_scope'] = {
            'parameters': 'authored values only; native drivers were not evaluated',
            'native_bindings_present': bool(data.get('effect_graph_controls')),
            'source_dimensions': 'one supplied canvas for all providers; use Python API for mixed sizes',
            'media_assets_validated': False,
        }
        plan['initialization'] = initialization
        text = json.dumps(plan, indent=2, sort_keys=True, allow_nan=False) + '\n'
        if args.output:
            # Exclusive creation prevents replacement of a competing writer's result.
            with args.output.open('x', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
        else:
            print(text, end='')
        return 0 if plan['eligible'] else 2
    finally:
        if backend is not None:
            backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
