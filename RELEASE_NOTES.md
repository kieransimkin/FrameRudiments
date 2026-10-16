FrameRudiments 0.1.4 fixes portrait geometry in the pinned projectM legacy
VideoEcho path. A recorded square overscan canvas and exact crop preserve
source pixel proportions without modifying the upstream library or redrawing
lyrics/sprites after effects. Both engines now have neutral mapping acceptance
in portrait, landscape and square formats, alongside the full 0.1.3 suite.
projectM remains pinned to 349b7cc2db312d47cef6be24c6856ced6643a385.

The host also captures native shader errors/fallback warnings and aborts instead of silently accepting a fallback. Both warp and composite error paths have real-engine tests.
