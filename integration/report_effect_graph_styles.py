"""Export actual project style controls and the last evaluated graph sample.

No media is downloaded. Output is a fresh local folder; names and JSON omit
project file paths, lyrics and audio. Frame images may contain project artwork.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def export_report(project_path, output, frame=0):
    from framerudiments import Project
    from framerudiments.compositor import Compositor
    from framerudiments.effect_graph_integration import compositor_style_report
    from framerudiments.effect_graph_style_contract import style_catalogue

    output = Path(output)
    if output.exists():
        raise FileExistsError('Style evidence output must be a new directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='style-report-probe-', dir=output.parent) as temporary:
        path = Path(temporary) / 'before'
        path.write_bytes(b'probe')
        assert path.read_bytes() == b'probe'
        path.rename(path.with_name('after')).unlink()
    project = Project.load(project_path)
    if type(frame) is not int or not 0 <= frame < project.frame_count:
        raise ValueError('frame must identify an actual encoded project frame')
    comp = Compositor(project)
    try:
        if comp.effect_graph is None:
            raise ValueError('project has no effect_graph')
        image, _ = comp.frame(frame)
        report = compositor_style_report(comp)
        bundle = {
            'schema': 'framerudiments.style-evidence.v1', 'encoded_frame': frame,
            'catalogue': style_catalogue(), 'controls': comp.effect_graph.style_manifest(),
            'sample_report': report,
        }
        # Only publish evidence after the graph and requested encoded frame succeed.
        text = json.dumps(bundle, sort_keys=True, indent=2, allow_nan=False) + '\n'
        output.mkdir()
        image.save(output / 'frame.png')
        (output / 'styles.json').write_text(text, encoding='utf-8')
        return bundle
    finally:
        virtual = getattr(comp, '_temporal_virtual', None)
        if virtual is not None:
            virtual.close()
        comp.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frame', type=int, default=0)
    args = parser.parse_args()
    export_report(args.project, args.output, args.frame)
