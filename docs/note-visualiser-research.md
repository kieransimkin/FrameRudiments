# Note visualisers: research and next additions

Research checked 24 November 2026 against creators' documentation and published
project pages. The design ideas below are our implementation choices, not
claims that FrameRudiments already includes every referenced product feature.
No third-party code, artwork or game assets are incorporated.

![Actual first keyboard and highway implementations](media/note-visualisers-native.png)

*Actual synthetic FrameRudiments output. [Play the three native examples](https://kieransimkin.github.io/FrameRudiments/note-visualisers/).
The remaining shortlist is planned work, not demonstrated features.*

| View | Reader/player value | Decision for FrameRudiments |
| --- | --- | --- |
| Falling piano roll and bottom keyboard | Makes pitch, key position, chords and length immediately visible. | Implemented: aligned white/black geometry, channel/track colours, velocity and sustain-aware tails. |
| Rising keyboard history | Shows notes only after they occur, without claiming future knowledge during a performance. | Implemented as a second direction of the same renderer and timeline. |
| Perspective note highway | Gives an approaching rhythm-game presentation and clear held-note tails. | Implemented with 2..8 explicitly mapped lanes; no score/gameplay or inferred guitar fingering. |
| Drum/pad lanes | Makes kick, snare, hats and percussion parts visually separable. | Good next extension of the explicit lane map; add named pad identities and short-hit glyphs from a reviewed mapping. |
| Pitch-class/chord wheel | Makes simultaneous pitch classes and harmonic movement easy to compare. | Good next renderer from the same active-note query; retain octave/voicing separately and avoid claiming an inferred chord label. |
| Horizontal multi-part roll | Shows counterpoint or an arrangement over time without requiring a bottom keyboard. | Reuse immutable intervals and track colours; add a moving playhead and bounded history/lookahead. |
| String/fretboard view | Shows playable guitar/bass locations and techniques. | Needs authored strings, frets/tuning and optional techniques; ordinary MIDI pitch is insufficient to choose fingering. |
| Cinematic note impacts | Adds controllable light/particle response at note onset. | Compose bounded deterministic emitters/effects around the note timeline; keep per-note timing and reduced-motion behaviour inspectable. |

## References and what they informed

[Synthesia](https://www.synthesiagame.com/About) describes falling-note piano
learning. Its legible keyboard relationship informed the first view; our
renderer remains an offline compositor rather than a lesson/game system.

[SeeMusic's own feature guide](https://www.seemusicapp.com/features) covers
MIDI/live recording, note editing, falling-note videos and visual effects.
It supports the usefulness of preserving note length, velocity and performance
identity in a common input before choosing an art style.

[kosua20's MIDIVisualizer](https://github.com/kosua20/MIDIVisualizer) documents
OpenGL MIDI visualisation and image/video export; its
[release notes](https://github.com/kosua20/MIDIVisualizer/releases) describe
key outlines, colour sets, pedal/particle customisation and aligned falling
notes. Those are useful presentation references. Its code and visual assets
are not copied into this implementation.

[Clone Hero's official dictionary](https://wiki.clonehero.net/books/general-info/page/dictionary)
distinguishes the highway, strike line, sustains and visual scrolling speed.
That separation informs our decision to change view speed through lookahead
without changing the underlying note or audio clock. Our highway is a visual
presentation with an authored map and does not implement its scoring rules.

[Stephen Malinowski's Music Animation Machine player guide](https://www.musanim.com/Player/MAMPlayerUserGuide.pdf)
describes piano-roll, pitch-class wheel, interval and lattice displays, with
colour by part or pitch class. This supports the next wheel and multi-part
ideas; chord interpretation would need its own stated evidence rather than
being inferred from a decorative colour.

[Ubisoft's Rocksmith Workshop basics](https://www.ubisoft.com/en-us/game/rocksmith/plus/rocksmith-workshop/tutorials/basics)
uses explicit string/fret note entry. Its
[additional features guide](https://www.ubisoft.com/en-us/game/rocksmith/plus/rocksmith-workshop/tutorials/additional-features)
retains tuning/capo and arrangement details. These make a real fretboard view
a different data contract from a five-lane pitch projection.

[Keysight's creator-published Steam page](https://store.steampowered.com/app/1325730/Keysight/Official?l=english)
presents a real-time MIDI platform aimed at piano-focused creators. Cinematic
customisation is a useful later direction; first acceptance here remains
source-bound notes, clear keys, deterministic views and verified export.

## Suggested order

The keyboard and explicit highway share the shipped note primitive. Next,
prioritise drum/pad lanes and the pitch-class wheel: both can consume existing
note intervals without claiming guitar fingering or starting a new analysis
engine. Add the horizontal arrangement view after those, then a string/fretboard
view once its authored source is available. Particle/light styling can compose
around any of the views using existing FrameRudiments primitives.

This shortlist adds no model downloads, transcription job, controller session,
third-party account or purchase. Use reviewed source notes and retain platform
safe regions when designing an actual music video or Short.
