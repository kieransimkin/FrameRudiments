## FrameRudiments visual examples

![Actual before/after lyric and native sprite frames](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/full-frame-comparison.png)

Actual synthetic FrameRudiments 0.1.4 candidate output: lyrics, native moving
sprites and a beat-animated atlas all pass through both real visualisers.
[Play the four-second demo](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/full-frame-demo.mp4)
with [transcript](https://github.com/kieransimkin/FrameRudiments/blob/v0.1.4/docs/media/demo-transcript.md).

![Generated effects-pipeline illustration, not screenshot evidence](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/effects-pipeline.svg)

The diagram illustrates composition order; the still and video are captured
renderer output. Use the exact runtime pins and project format in the
[visualiser guide](https://github.com/kieransimkin/FrameRudiments/blob/v0.1.4/docs/visualisers.md).
Inspect encoded glyphs/safe zones; pre-effect bounds do not prove final fit.
