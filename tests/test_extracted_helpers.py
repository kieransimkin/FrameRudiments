from PIL import Image, ImageDraw
import pytest
from framerudiments.atlas import isolate_one_object, components
from framerudiments.choreography import lyric_placements


def test_alpha_cleaner_preserves_largest_object():
    image = Image.new("RGBA", (30, 30))
    d = ImageDraw.Draw(image)
    d.rectangle((5, 5, 15, 15), fill="white")
    d.rectangle((23, 23, 24, 24), fill="white")
    clean, count, removed = isolate_one_object(image)
    assert count == 2 and removed == 4
    assert len(components(clean.getchannel("A"))) == 1
    assert clean.getpixel((23, 23))[3] == 0


def test_lyric_choreography_hits_onset_and_explicit_end():
    events = [dict(frame=60, end_frame=120)]
    options = dict(fps=60, bpm=100, transition_ticks=8, programme_frames=180)
    assert list(lyric_placements(events, 60, **options)) == [(0, 960, "hold")]
    assert list(lyric_placements(events, 120, **options)) == [(0, 960, "exit")]
    assert list(lyric_placements(events, 138, **options)) == []
    assert list(lyric_placements(events, 42, **options)) == [(0, -650, "enter")]
    with pytest.raises(ValueError):
        list(lyric_placements([dict(frame=60)], 60, **options))
