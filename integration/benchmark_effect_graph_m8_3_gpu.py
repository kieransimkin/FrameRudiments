"""Measure real EGL node-bridge timings; includes uploads/readbacks, not encoding."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from framerudiments.effect_graph import _chromatic_displacement
from framerudiments.effect_graph_filter import render_filter
from framerudiments.effect_graph_gpu import GLMatteBackend
from framerudiments.effect_graph_transitions import render_transition

SIZES = {'small':(320,180),'hd':(1920,1080),'4k':(3840,2160)}


def sample(size):
    width, height = size
    y, x = np.indices((height,width),dtype=np.int32)
    array = np.stack(((x*7+y*3)%256, (x+y*5)%256, (x*2+y*11)%256,
                      (x*13+y*17)%256), -1).astype('uint8')
    return Image.fromarray(array,'RGBA')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--size', choices=SIZES,default='small')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    size=SIZES[args.size]
    a=sample(size)
    b=Image.new('RGBA',size,(55,140,220,120))
    cases=[
        ('radial-transition',lambda gpu:gpu.render_transition(a,b,{'transition_style':'radial','progress':.4,'feather':.2}),
         lambda:render_transition(a,b,{'transition_style':'radial','progress':.4,'feather':.2})),
        ('rgb-channel-displacement',lambda gpu:gpu.chromatic_displacement(a,{'red_offset':[2.5,-3]}),
         lambda:_chromatic_displacement(a,{'red_offset':[2.5,-3]})),
        ('blur',lambda gpu:gpu.render_filter(a,{'filter_radius':1.5}),
         lambda:render_filter(a,{'filter_radius':1.5})),
        ('bloom',lambda gpu:gpu.render_filter(a,{'filter_type':'bloom','filter_radius':1.5}),
         lambda:render_filter(a,{'filter_type':'bloom','filter_radius':1.5})),
    ]
    with GLMatteBackend(backend='egl') as gpu:
        rows=[]
        for name,gpu_func,cpu_func in cases:
            before=perf_counter()
            cpu=cpu_func()
            cpu_seconds=perf_counter()-before
            gpu.begin_transfer_sample()
            before=perf_counter()
            image=gpu_func(gpu)
            gpu_seconds=perf_counter()-before
            delta=np.abs(np.asarray(image,dtype='int16')-np.asarray(cpu,dtype='int16'))
            rows.append({'effect':name,'cpu_seconds':cpu_seconds,'gpu_bridge_seconds':gpu_seconds,
                         'maximum_channel_difference':int(delta.max()),
                         'transfers':dict(gpu.last_transport)})
        result={'schema':'framerudiments.m8-3-benchmark.v1','resolution':list(size),
                'renderer':gpu.capabilities.renderer,'graphics_backend':'egl',
                'scope':'per-node upload+execute+readback; no decoder, encoder or full DAG',
                'one_sample_only':True,'data':rows}
    path=Path(args.output)
    path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
