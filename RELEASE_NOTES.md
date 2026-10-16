FrameRudiments 0.1.3 adds optional full-frame Butterchurn and projectM stages.
Captions, lyric artwork, native DanceRudiments sprites and animated atlases enter
the same real MilkDrop feedback/warp/composite path as the rest of the frame.
Choose either engine or an ordered chain with original feedback, kaleidoscope
and Julia-distortion presets.

projectM is pinned to unreleased 4.2 source commit
349b7cc2db312d47cef6be24c6856ced6643a385 (including its exact evaluator submodule).
Butterchurn 2.6.7 and Puppeteer Core 25.14.0 use a complete npm lock. Explicit
setup commands provision optional runtimes; the base renderer needs neither.

Offline frame clocks, causal PCM from the shared exact trim/repeat/fade chain,
source/runtime hashes, pre/post-effect QA images, sequential-state validation
and verified owned-resource cleanup preserve traceable rendering. Mandatory
real-engine CI covers shaders, glyph feedback, orientation, animated sprites,
chained encoding and failure cleanup. GPU output can vary by driver; inspect
processed text and final safe zones. Audio/creative approval remains separate.

Website: https://kieransimkin.co.uk/danceflow/
