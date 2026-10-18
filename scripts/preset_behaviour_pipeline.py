"""Dense renderer captures -> cheap PixelCue motion -> selective PrismMoves prose.

Uses the existing pinned renderer/suite APIs; no neural/image analysis is copied.
All new results use a separate resumable directory. Legacy captures are untouched.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import full_preset_analysis as legacy

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "framerudiments.behaviour-experiment/1"
FAMILIES = ("cover-lyrics", "colour-bars", "grid-rings", "text-card")


def dense_intervals(seconds=3.0, capture_fps=15):
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ValueError("Duration must be finite and between 0 and 60 seconds")
    if type(capture_fps) is not int or not 5 <= capture_fps <= 60:
        raise ValueError("Capture rate must be an integer between 5 and 60 fps")
    count = math.floor(seconds*capture_fps + 1e-9) + 1
    if count > 512:
        raise ValueError("At most 512 captures per condition; reduce duration or capture fps")
    return [i/capture_fps for i in range(count)]


def signature(value):
    return hashlib.sha256(legacy.stable_json(value)).hexdigest()


def input_suite(work, font, artwork_directory=None, *, render_edge=384):
    """Use FrameRudiments' synthetic demo at a useful analysis resolution.

    Real covers are explicit inputs, never searched for outside the supplied folder.
    The default audio remains the disclosed four-second synthetic demo, not a song.
    """
    from framerudiments.demo import create_demo
    from framerudiments.io import write_json
    if not 160 <= render_edge <= 1024:
        raise ValueError("Fixture edge must be 160..1024")
    covers = []
    if artwork_directory:
        folder = Path(artwork_directory).resolve(strict=True)
        covers = sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in
                        {'.png', '.jpg', '.jpeg', '.webp', '.bmp'})
        if not covers or len(covers) > 48:
            raise ValueError("The explicit artwork folder must contain 1..48 supported images")
        if any(not p.resolve().is_relative_to(folder) or p.stat().st_size > 64*1024**2 for p in covers):
            raise ValueError("Artwork must stay inside the chosen folder and be at most 64 MiB")
    entries = []
    for cover in [None, *covers]:
        key = "synthetic" if cover is None else legacy.file_digest(cover)[:24]
        project = work/'fixtures'/f'{key}-{render_edge}'/'project.json'
        if not project.is_file():
            project.parent.parent.mkdir(parents=True, exist_ok=True)
            create_demo(project.parent, font, width=render_edge, height=render_edge)
            doc = legacy.read_json(project)
            image = cover or project.parent/'sprite.png'
            doc['background'] = {'image': {'path': str(image.resolve()), 'sha256': legacy.file_digest(image)}}
            doc['notes'] = 'Synthetic four-second audio and lyrics; explicitly supplied cover when selected. No real song response inferred.'
            write_json(project, doc)
        entries.append({'path': str(project.resolve()), 'sha256': legacy.file_digest(project)})
    suite = work/'fixtures'/'behaviour-suite.json'
    payload = {'schema': 'framerudiments.preset-suite.v1', 'projects': entries, 'input_mix': .35, 'seed': 7}
    if suite.exists() and legacy.read_json(suite) != payload:
        raise ValueError("Artwork selection or fixture resolution changed; use a new output directory")
    legacy.write_json(suite, payload)
    return suite


def capture_command(args, suite, catalogue, row, folder):
    command = [str(args.render_python), '-u', '-m', 'framerudiments', 'presets-profile', str(suite), str(catalogue),
               '--output', str(folder/'run'), '--id', row['id'], '--intervals',
               *[format(t, '.12g') for t in dense_intervals(args.seconds, args.capture_fps)]]
    for family in FAMILIES:
        command += ['--image', family]
    for mode in ('programme', 'silence'):
        command += ['--audio-mode', mode]
    if (folder/'run'/'experiment.json').is_file():
        command += ['--resume']
        if args.retry_failed:
            command += ['--retry-failed']
    return command


def analysis_command(args, manifest, output):
    return [str(args.analysis_python), '-u', '-m', 'prismmoves.preset_pipeline', 'analyse',
            '--captures', str(manifest), '--out', str(output), '--models-dir', str(ROOT/'models'),
            '--budget', str(args.semantic_budget), '--max-edge', '256', '--semantic-edge', str(args.semantic_edge),
            '--device', args.device, '--models', *args.models]


def stage(command, log, environment, timeout, *, runner=legacy.run_logged):
    print(f"  {log.stem}: started (log: {log.relative_to(ROOT) if log.is_relative_to(ROOT) else log.name})", flush=True)
    started = time.monotonic()
    last = [0.0]
    def heartbeat():
        elapsed = time.monotonic()-started
        if elapsed-last[0] < 20:
            return
        last[0] = elapsed
        print(f"  {log.stem}: {elapsed:.0f}s elapsed; worker is still running", flush=True)
    options = {'environment':environment, 'timeout':timeout}
    if 'on_poll' in inspect.signature(runner).parameters:
        options['on_poll'] = heartbeat
    code = runner(command, log, **options)
    if code not in (0, 2):
        print(f"  {log.stem}: failed, exit {code}; see the saved log", flush=True)
    return code


def _public(value):
    """Do not publish machine-local model paths or original input names."""
    if isinstance(value, dict):
        result = {}
        for key, v in value.items():
            if key in {'checkpoint', 'path', 'input_path', 'image_name', 'local_dir'}:
                continue
            result[key] = _public(v)
        return result
    if isinstance(value, list):
        return [_public(x) for x in value]
    return value


def publish_preset(staged, out, key, captures):
    """Compact catalogue receipt + compressed detailed evidence; raw PNGs stay local."""
    from PIL import Image, ImageDraw
    report = legacy.read_json(staged/'analysis.json')
    report = _public(report)
    target = out/key
    target.mkdir(parents=True, exist_ok=True)
    payload = gzip.compress(legacy.stable_json(report), compresslevel=8, mtime=0)
    legacy.write_atomic(target/'evidence.json.gz', payload)
    compact = {k: v for k, v in report.items() if k not in {'temporal', 'semantic_items'}}
    compact['evidence'] = {'file': 'evidence.json.gz', 'sha256': hashlib.sha256(payload).hexdigest(),
                           'format': 'gzip-compressed full PrismMoves report'}
    legacy.write_json(target/'analysis.json', compact)
    legacy.write_json(target/'semantic-plan.json', report['selection'])
    for name in ('report.md', 'report.html'):
        shutil.copyfile(staged/name, target/name)
    html_path = target/'report.html'
    html_path.write_text(html_path.read_text(encoding='utf-8').replace('</html>',
        '<p><a href="evidence.json.gz">Complete compressed frame/model evidence</a></p></html>'), encoding='utf-8')
    # Show selected frames rather than hundreds of nearly identical contact panels.
    refs = {c['id']+':'+str(f['frame']): f['output']
            for c in captures.get('conditions', []) for f in c.get('captures', [])}
    selected = report['selection']['selected'][:24]
    if not selected:
        # A blank renderer still deserves a preview: it is a finding, not evidence loss.
        selected = [{'id': identity, 'seconds': 0} for identity in list(refs)[:6]]
    if selected:
        cell = 144
        sheet = Image.new('RGB', (cell*4, (cell+24)*math.ceil(len(selected)/4)), '#0a1420')
        draw = ImageDraw.Draw(sheet)
        for i, frame in enumerate(selected):
            ref = refs.get(frame['id'])
            if not ref or not Path(ref['path']).is_file() or legacy.file_digest(Path(ref['path'])) != ref['sha256']:
                continue
            with Image.open(ref['path']) as image:
                image = image.convert('RGB')
                image.thumbnail((cell-6,cell-6))
                x, y = (i%4)*cell, (i//4)*(cell+24)
                sheet.paste(image, (x+(cell-image.width)//2,y+(cell-image.height)//2))
                draw.text((x+3,y+cell), f"{frame['seconds']:.2f}s  {i+1}",fill='white')
        sheet.save(target/'preview.webp', 'WEBP', quality=75, method=4)
        sheet.close()
    compact['completed_unix'] = time.time()
    legacy.write_json(target/'analysis.json', compact)
    return compact


def run(args):
    out = legacy.assert_local_directory(Path(args.output))
    if out.exists():
        config_file = out/'experiment.json'
        if config_file.exists():
            previous = legacy.read_json(config_file)
            if previous.get('settings', {}).get('schema') != SCHEMA:
                raise ValueError("Refusing to reuse another workflow's output directory")
        elif any(p.name not in {'_work', '.gitignore'} for p in out.iterdir()):
            raise ValueError('Output must be a new or recognised behaviour-analysis workspace')
    work = out/'_work'
    work.mkdir(parents=True, exist_ok=True)
    (out/'.gitignore').write_text('_work/\n', encoding='utf-8')
    environment = legacy.model_environment()
    with legacy.exclusive_run(work):
        # Fail before capture when the new analysis API has not been installed.
        subprocess.run([str(args.analysis_python), '-c',
            'from pixelcue.visual_sequence import analyse_sequences; from prismmoves.preset_pipeline import analyse_capture_manifest; import cv2'],
            env=environment, check=True)
        font = Path(args.font or os.environ.get('FRAMERUDIMENTS_TEST_FONT',
                          'C:/Windows/Fonts/arial.ttf' if os.name=='nt' else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        suite = Path(args.suite).resolve(strict=True) if args.suite else input_suite(
            work, font, args.artwork_directory, render_edge=args.render_edge)
        library = legacy.including_builtin_presets(legacy.get_preset_catalogue(Path(args.catalogue)))
        catalogue = work/'catalogue.json'
        legacy.write_json(catalogue, library)
        suite_doc = legacy.read_json(suite)
        frames = len(dense_intervals(args.seconds, args.capture_fps))*8*len(suite_doc.get('projects', []))
        if frames > 32768:
            raise ValueError('This suite exceeds the 32768-frame analysis bound; split the artwork suite')
        settings = {'schema': SCHEMA, 'suite_sha256': legacy.file_digest(suite),
                    'catalogue_sha256': legacy.file_digest(catalogue),
                    'capture_fps': args.capture_fps, 'seconds': args.seconds,
                    'families': FAMILIES}
        identity_program = (
            "import json,sys; from prismmoves.preset_pipeline import semantic_identity; "
            "identity,_=semantic_identity(sys.argv[1],json.loads(sys.argv[2]),[],"
            "{'max_edge':int(sys.argv[3]),'background':'#000000','device':sys.argv[4],"
            "'allow_download':False,'prompt_profile':'visualiser'}); print(json.dumps(identity,sort_keys=True))")
        engine_identity = subprocess.check_output([str(args.analysis_python), '-c', identity_program,
            str(ROOT/'models'), json.dumps(args.models), str(args.semantic_edge), args.device],
            env=environment, text=True, encoding='utf-8')
        analysis_settings = {'engine_identity_sha256': signature(json.loads(engine_identity)), 'budget': args.semantic_budget, 'edge': args.semantic_edge,
                             'models': args.models, 'device': args.device,
                             'driver_sha256': legacy.file_digest(Path(__file__))}
        analysis_signature = signature(analysis_settings)
        legacy.write_json(work/'analysis-settings.json', analysis_settings)
        config = out/'experiment.json'
        token = signature(settings)
        if config.exists() and legacy.read_json(config).get('signature') != token:
            raise ValueError('Behaviour experiment settings changed; select a new --output directory')
        legacy.write_json(config, {'signature': token, 'settings': settings})
        rows = [r for r in library['presets'] if (not args.ids or r['id'] in args.ids)
                and (not args.engine or r['engine'] == args.engine)]
        if args.ids and set(args.ids)-{r['id'] for r in rows}:
            raise ValueError('A requested preset id was not found in the chosen renderer/catalogue')
        pending = []
        for candidate in rows:
            report = out/legacy.name_key(candidate['id'])/'analysis.json'
            prior = legacy.read_json(report) if report.is_file() else {}
            same = prior.get('driver_analysis_signature') == analysis_signature
            if same and (not args.retry_failed or prior.get('analysis_state') == 'completed'):
                continue
            pending.append(candidate)
        queue = legacy.alternating_random_order(pending, args.order_seed if args.order_seed is not None else secrets.randbits(64))
        legacy.write_json(work/'schedule.json', {'signature': token, 'ids': [r['id'] for r in queue]})
        def reindex():
            subprocess.run([str(args.analysis_python), '-m', 'prismmoves.preset_pipeline',
                            'reindex', '--out', str(out)], env=environment, check=True)
        reindex()
        done, failed = 0, 0
        for row in queue:
            key = legacy.name_key(row['id'])
            complete = out/key/'analysis.json'
            if complete.is_file():
                prior = legacy.read_json(complete)
                if prior.get('driver_analysis_signature') == analysis_signature:
                    if not args.retry_failed or prior.get('analysis_state') == 'completed':
                        continue
            if args.limit and done >= args.limit:
                break
            print(f"[{done+1}] {row['engine']} — {row['name']} ({frames} dense captures; <= {args.semantic_budget} semantic images)", flush=True)
            folder = work/'jobs'/key
            folder.mkdir(parents=True, exist_ok=True)
            # Preserve an interrupted/invalid renderer folder rather than overwrite it.
            if (folder/'run').exists() and not (folder/'run'/'experiment.json').is_file():
                (folder/'run').rename(folder/f'orphan-render-{time.time_ns()}')
            capture_code = stage(capture_command(args,suite,catalogue,row,folder),folder/'capture.log',environment,args.capture_timeout)
            manifest = legacy.captured_manifest(work,row)
            if capture_code not in (0, 2) or not manifest.exists():
                legacy.write_json(folder/'failed-captures.json', {'preset': row, 'conditions': [],
                    'state':'failed','error':f'Renderer exited {capture_code}; see local capture.log'})
                manifest = folder/'failed-captures.json'
                failed += 1
            staged = folder/'prism'/f'{analysis_signature[:16]}-{time.time_ns()}'
            # A retry reuses matching temporal/model caches; a config mismatch is
            # explicit and never auto-published over previous report evidence.
            code = stage(analysis_command(args,manifest,staged),folder/'analysis.log',environment,args.analysis_timeout)
            if (staged/'analysis.json').is_file() and code in (0,2):
                public = publish_preset(staged,out,key,legacy.read_json(manifest))
                public['driver_analysis_signature'] = analysis_signature
                legacy.write_json(out/key/'analysis.json', public)
            else:
                failed += 1
                legacy.write_json(folder/'failure.json', {'capture_exit':capture_code,'analysis_exit':code})
            reindex()
            done += 1
        print(f"Processed {done} presets; {failed} analysis process failures. Gallery: {out/'index.html'}")
        print('A limited description is not a failed renderer. Read its evidence coverage before release.')
        return 2 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'visualiser-analysis'/'behaviour-catalogue')
    parser.add_argument('--catalogue',type=Path,default=ROOT/'models'/'preset-library'/'catalogue.json')
    parser.add_argument('--render-python',type=Path,default=Path(sys.executable))
    parser.add_argument('--analysis-python',type=Path,required=True)
    parser.add_argument('--suite',type=Path)
    parser.add_argument('--artwork-directory',type=Path)
    parser.add_argument('--font',type=Path)
    parser.add_argument('--render-edge',type=int,default=384)
    parser.add_argument('--seconds',type=float,default=3)
    parser.add_argument('--capture-fps',type=int,default=15)
    parser.add_argument('--semantic-budget',type=int,default=24)
    parser.add_argument('--semantic-edge',type=int,default=384)
    parser.add_argument('--models',nargs='*',choices=['clip','blip','dinov2','ocr'],default=['clip','blip'])
    parser.add_argument('--device',choices=['cpu','cuda','mps'],default='cpu')
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--id',dest='ids',action='append',default=[])
    parser.add_argument('--engine',choices=['butterchurn','projectm'])
    parser.add_argument('--order-seed',type=int)
    parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--capture-timeout',type=int,default=1800)
    parser.add_argument('--analysis-timeout',type=int,default=1800)
    args = parser.parse_args(argv)
    try:
        dense_intervals(args.seconds,args.capture_fps)
        if not 0 <= args.semantic_budget <= 64 or args.limit < 0:
            raise ValueError('Budget must be 0..64 and limit nonnegative')
        if args.suite and args.artwork_directory:
            raise ValueError('Choose an authored suite OR an artwork directory')
        if any(not 1 <= t <= 86400 for t in (args.capture_timeout,args.analysis_timeout)):
            raise ValueError('Timeouts must be between 1 and 86400 seconds')
        return run(args)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print(f'Preset behaviour analysis stopped: {exc}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
