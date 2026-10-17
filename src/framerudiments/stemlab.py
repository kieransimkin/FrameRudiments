"""Read saved StemLab evidence without re-running analysis or copying DSP."""

from .io import read_json, integer


def selected_loop(path, loop_id, source_sha256):
    report = read_json(path)
    if report.get("schema") != "stemlab.loops.v1":
        raise ValueError("Expected stemlab.loops.v1")
    if report.get("source_sha256") != source_sha256:
        raise ValueError("StemLab loop belongs to a different audio source")
    pool = [x for x in report["loops"] if x["id"] == loop_id]
    if len(pool) != 1:
        raise ValueError("Select one accepted loop id; exploratory candidates are not accepted loops")
    item = pool[0]
    start = integer(item["start_sample"], "loop start")
    end = integer(item["end_sample"], "loop end", minimum=start + 1)
    return dict(
        start_sample=start,
        end_sample=end,
        sample_rate=report["sample_rate"],
        evidence=item,
        listening_approved=False,
    )


def selected_match(path, source_index, match_index):
    report = read_json(path)
    if report.get("schema") != "stemlab.dancerudiments.candidates.v1":
        raise ValueError("Expected stemlab.dancerudiments.candidates.v1")
    source = report["sources"][integer(source_index, "source index")]
    match = source["matches"][integer(match_index, "match index")]
    return {
        "selection": match,
        "source": {k: v for k, v in source.items() if k != "matches"},
        "note": "Ranked fit in this source window; no creative or whole-song approval implied.",
    }


def beat_grid(path):
    data = read_json(path)
    beats = data.get("beats")
    if not isinstance(beats, list):
        raise ValueError("Expected a saved beat report with beats[]")
    return beats
