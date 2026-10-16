FrameRudiments 0.2.0 adds source-bound StemLab BPM and first-beat alignment to
the existing procedural clock. Exact master/hash, rational offset and source
frame checks preserve authority; excerpt/repeat mapping applies phase once.
Lyrics, cues and captured recordings retain original source time. A reproducible
synthetic example, actual encoded screenshot/video and regression coverage show
the contract. Existing projects keep their configured clocks.

Includes the 0.1.4 portrait geometry fix in the pinned projectM legacy
VideoEcho path. A recorded square overscan canvas and exact crop preserve
source pixel proportions without modifying the upstream library or redrawing
lyrics/sprites after effects. Both engines now have neutral mapping acceptance
in portrait, landscape and square formats, alongside the full 0.1.3 suite.
projectM remains pinned to 349b7cc2db312d47cef6be24c6856ced6643a385.

The host also captures native shader errors/fallback warnings and aborts instead of silently accepting a fallback. Both warp and composite error paths have real-engine tests.
