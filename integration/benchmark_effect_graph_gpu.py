"""M3.3 optional end-to-end CPU/GPU RGBA benchmarking, no private assets.

This benchmark includes Pillow->GPU upload and framebuffer readback. Timings
are not representative of a future texture-resident DAG. Large image runs are
opt-in and guarded by the backend's configurable GPU memory budget.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
from statistics import median
from time import perf_counter

from PIL import Image, ImageDraw

from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_mattes import apply_matte, chroma_key

SIZES = {'hd': (1920,1080), '4k': (3840,2160), '8k': (7680,4320)}


def run(args):
    sizes = SIZES if args.all else {args.size: SIZES[args.size]}
    device = None
    try:
        device = GLMatteBackend(backend=args.backend, max_working_set_bytes=args.gpu_budget_mib*1024**2)
    except (RuntimeError, ImportError) as exc:
        if args.require_gpu:
            raise
        unavailable = str(exc)
    else:
        unavailable = None
    output = {'schema': 'framerudiments.gpu-matte-benchmark.v1',
              'gpu': dict(device.capabilities.__dict__) if device else None,
              'gpu_error': unavailable, 'platform': platform.platform(), 'results': []}
    try:
        for label,size in sizes.items():
            ink = Image.new('RGBA',size,(35,180,60,250))
            matte = Image.new('RGBA',size)
            ImageDraw.Draw(matte).ellipse((size[0]//5,size[1]//5,4*size[0]//5,4*size[1]//5),fill=(220,60,20,200))
            actions = [
                ('matte', lambda: apply_matte([ink,matte], {'mask_opacity':.75,'invert':True}),
                 lambda: device.apply_matte([ink,matte], {'mask_opacity':.75,'invert':True})),
                ('chroma_key', lambda: chroma_key(ink, {'tolerance':.09,'softness':.16}),
                 lambda: device.chroma_key(ink, {'tolerance':.09,'softness':.16}))
            ]
            for name,cpu,gpu in actions:
                row = dict(size=label, width=size[0],height=size[1],kind=name)
                for backend,func in [('cpu',cpu),('gpu',gpu)]:
                    if backend == 'cpu' and not args.cpu:
                        row[backend] = {'skipped':'pass --cpu to benchmark reference implementation'}
                        continue
                    if backend == 'gpu' and device is None:
                        row[backend] = {'skipped':unavailable}
                        continue
                    samples=[]
                    try:
                        func()  # warm up shaders/context and buffers
                        for _ in range(args.repeats):
                            t0=perf_counter()
                            result=func()
                            samples.append((perf_counter()-t0)*1000)
                            del result
                    except (GPUUnsupported, MemoryError) as exc:
                        row[backend] = {'skipped':str(exc)}
                    else:
                        row[backend] = {'median_ms':round(median(samples),3),
                                        'samples_ms':[round(x,3) for x in samples]}
                output['results'].append(row)
    finally:
        if device is not None:
            device.close()
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size',choices=SIZES,default='hd')
    parser.add_argument('--all',action='store_true',help='HD, 4K and 8K (very high resource usage)')
    parser.add_argument('--cpu',action='store_true',help='Include CPU baseline; expensive at 8K')
    parser.add_argument('--backend',default=None,help='ModernGL backend; egl recommended for Linux CI')
    parser.add_argument('--gpu-budget-mib',type=int,default=768)
    parser.add_argument('--require-gpu',action='store_true')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',type=Path,default=Path('effect-graph-gpu-benchmarks.json'))
    args=parser.parse_args()
    if not 1<=args.repeats<=20 or not 128<=args.gpu_budget_mib<=16384:
        parser.error('repeats must be 1..20; GPU budget 128..16384 MiB')
    report=run(args)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
