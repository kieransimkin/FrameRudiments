"""Frame-addressed entrance/hold/exit mechanics extracted from lyric-plane rendering."""
from .timeline import half_up
from .io import number, integer


def lyric_placements(events, frame, *, fps, bpm, transition_ticks=8, width=1920, centre=960,
                     margin=650, programme_frames):
    """Enter by onset, hold to explicit end_frame, then leave; no copied song BPM."""
    fps = integer(fps, "fps", minimum=1)
    bpm = number(bpm, "bpm", minimum=.00001)
    transition = max(1, half_up(number(transition_ticks, "transition ticks", minimum=0) * 3.75 / bpm * fps))
    for index, event in enumerate(events):
        onset = event["frame"]
        if "end_frame" not in event:
            raise ValueError("Lyric-art choreography requires explicit end_frame")
        following = event["end_frame"]
        if following < onset:
            raise ValueError("Lyric-art end precedes onset")
        enter, end = max(0, onset - transition), min(programme_frames, following + transition)
        if not enter <= frame < end:
            continue
        if frame < onset:
            u = 1 - (1 - (frame - enter) / max(1, onset - enter)) ** 3
            x, phase = -margin + (centre + margin) * u, "enter"
        elif frame < following:
            x, phase = centre, "hold"
        else:
            u = 1 - (1 - min(1, (frame - following) / transition)) ** 3
            x, phase = centre + (width + margin - centre) * u, "exit"
        yield index, round(x), phase
