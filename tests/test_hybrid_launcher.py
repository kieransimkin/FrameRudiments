from pathlib import Path
import ast
import importlib.util

ROOT=Path(__file__).resolve().parents[1]


def test_full_launcher_routes_before_old_sweep_and_retains_legacy():
    text=(ROOT/'scripts/Run-FullPresetAnalysis.ps1').read_text()
    guard=text.index('if (-not $Legacy -and -not $InstallOnly)')
    route=text.index("Run-HybridPresetAnalysis.ps1",guard)
    assert guard<route<text.index("$pythonScript =")
    assert '    return\n}' in text[route:]
    assert '[switch]$Legacy' in text and '[switch]$ReanalyseExisting' in text


def test_hybrid_setup_cannot_recurse_or_start_old_analysis():
    text=(ROOT/'scripts/Run-HybridPresetAnalysis.ps1').read_text()
    assert '-Legacy -SkipSetup -InstallOnly' in text
    assert '.venv-hybrid-analysis' in text
    assert 'prismmoves.hybrid_response' in text
    assert "full-catalogue\\hybrid" in text


def test_reanalyse_existing_branch_runs_before_native_preflight():
    s=(ROOT/'scripts/Run-HybridPresetAnalysis.ps1').read_text()
    a=s.index('if ($ReanalyseExisting -or $Status -or $Verify)')
    b=s.index('-Legacy -SkipSetup -InstallOnly')
    assert a<b and 'return' in s[a:b]
    assert "--allow-download" in s[a:b]


def test_legacy_options_are_not_silently_reinterpreted():
    s=(ROOT/'scripts/Run-FullPresetAnalysis.ps1').read_text()
    assert "'Suite','ArtworkDirectory'" in s and 'require -Legacy' in s


def test_driver_uses_explicit_supported_analysis_module():
    p=ROOT/'scripts/controlled_preset_analysis.py'
    s=p.read_text()
    ast.parse(s)
    assert 'default="prismmoves.hybrid_response"' in s
    assert '--semantic-budget' in s and '--models-dir' in s
    assert "args.analysis_module, \"index\"" in s


def test_alternating_random_order_survives_new_pipeline():
    p=ROOT/'scripts/controlled_preset_analysis.py'
    spec=importlib.util.spec_from_file_location('hybrid_driver',p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    rows=[{'id':str(i)+e,'engine':e} for e in ('butterchurn','projectm') for i in range(5)]
    a=m.ordered_presets(rows,12)
    b=m.ordered_presets(rows,13)
    assert [r['engine'] for r in a]==['projectm','butterchurn']*5
    assert [r['id'] for r in a]!=[r['id'] for r in b]
