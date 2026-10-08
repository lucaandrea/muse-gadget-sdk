# Muse UI polish proposal

Prepared October 8, 2026. Design study and implementation plan; firmware has not been changed.

**Implemented October 8, 2026:** see [the installation and validation notes](esp32/REFINED_UI.md). The text below records the original proposal.

Make Muse feel like a small, beautifully made voice companion: a calm black stage, a warm dimensional character, cream surfaces that appear when useful, and a precise orange response to interaction. The character remains central when desired; the same interface works with an abstract presence or with reading as its focus.

## What the repositories establish

The primary target is the Waveshare ESP32-S3-Touch-AMOLED-1.75C described in `esp32/ROUND_AMOLED.md`: a round 466 × 466 display, 8 MB PSRAM and 32 MB flash. Its board profile schedules frames every 40 ms, approximately 25 fps. Treat these as the documented configuration, not fresh hardware measurements. Retain the 412 × 412 Watcher and compact-board layouts as additional profiles.

I inspected Grep's saved light/dark screenshots, layered answer cards, compact watch screenshots, branding definitions, composer, dotted orb, voice orb and turn animation code. I compared these with Muse's saved device captures and eight-state simulator contact sheet. Grep's motion behavior below is established from source; I did not run its authenticated application.

| Grep detail | Evidence | Translation for Muse |
| --- | --- | --- |
| Orange `#FF3C00`, cream `#FAF6F1`, charcoal `#181818`, gray `#DBD4CF` | `grep-v2/artifacts/grep/src/pages/branding.css:1` | Keep these as the identity, with true black behind the character on AMOLED. |
| Neutral typography and icons; orange used sparingly | `grep-v2/artifacts/grep/src/pages/branding.tsx:55` | Orange identifies activity and the primary action. Text, state labels and ordinary controls remain neutral. |
| 24 px base radius and a layered card: border, inset edge, 3 px surrounding surface, crisp lower edge and soft shadow | `grep-v2/artifacts/grep/src/index.css:166` and `:382` | Build one reusable layered surface with cheap LVGL fills and hairlines. Use it for response cards, settings rows and pairing. |
| A slowly turning dotted sphere with a traveling orange highlight; a reserved layout slot | `grep-v2/artifacts/grep/src/components/search/dotted-orb.tsx` | An optional quiet presence, with a small bounded draw area and precomputed geometry. |
| Voice motion driven by input/output levels, with fast attack and slower release | `grep-v2/artifacts/grep/src/components/voice/voice-orb.tsx` | Animate the character's listening pose/mouth or an abstract pulse using the existing audio-level signal. |
| A one-time launch and answer reveal, interrupted when real content arrives; no replay on reopening | `grep-v2/artifacts/grep/src/components/search/turn-motion.tsx` | Animate each accepted voice turn once. Answers appear as soon as available; transitions never add latency. |
| 2.4 s thinking shimmer, 260 ms answer reveal and reduced-motion handling | `grep-v2/artifacts/grep/src/index.css:574`; `turn-motion.tsx` | Use a restrained highlight and approximately 240–320 ms layout transitions. Reduced motion keeps clear static labels. |

Muse already has useful foundations: separate listening/thinking/speaking states, input/output levels, reply pagination, a 300 ms character resize transition, partial avatar redraws, on-demand settings pages and a production-code simulator. Preserve them.

The strongest visual constraints are in `muse_pixel.h` and `muse_ui.c`: a 64 × 64 grid deliberately enlarged with nearest-neighbor scaling, pixel captions, and status colors taken from the avatar. These are why the present interface retains its toy-like appearance even after its settings became more rounded.

## Proposed experience

### Companion — default

On wake, a smooth cream character occupies roughly the middle 45–50% of the display diameter. Keep its friendly face and recognizable silhouette. Use soft upper-left lighting, subtle contact shading and a restrained orange detail. Remove surrounding pixel sparkles and the always-visible full progress ring.

The top contains one short state line. Show connection details only when actionable; battery can appear briefly on wake and remain available in Settings. A small lower capsule gives the context-appropriate prompt. Idle motion is an occasional blink, glance or small breath, with quiet intervals.

Holding the physical top button starts listening; releasing sends. Preserve the bottom button's sleep/wake behavior and swipe-left Settings access. The bezel hint can explain the buttons during onboarding, then recede. Touching the character retains its affectionate response.

When a reply arrives, the character eases upward and becomes smaller while a cream response card enters below. Keep a compact character visible during spoken replies if the user chooses. Tapping the response enters Reading. Separate “show character” from speaker on/off: the current heard/read layout follows the speaker setting, but visual preference and audio preference should be independent.

### Quiet — alternate home presentation

Replace the character with a small Grep-inspired dotted sphere, or a still mark under reduced motion. Keep the same state line, response card, settings and physical controls. It offers a more discreet desk presence without changing what the device can do.

### Reading — content presentation

Use a cream sheet with charcoal text, a small identity mark or optional miniature character, and a clear page indicator. Left-align answer text. Start with about 5–7 comfortably spaced lines, then size pages by actual glyph measurement. Keep the current word/byte position when changing presentations so audio and text remain aligned.

Use explicit Previous/Next targets or a vertical reading gesture; reserve the existing horizontal home-to-settings gesture. Do not auto-scroll away from text someone is reading. Let the user return to following speech. A small bottom “Back” control returns to the prior presentation.

These are coordinated views, not three unrelated themes. The inline concept illustrates their hierarchy and transitions; its character is an illustrative dimensional stand-in, not a finished production model or an ESP32 performance demonstration.

## Visual specification for the round display

| Element | Initial specification |
| --- | --- |
| Canvas | `#000000` on the character/quiet view. Cream is concentrated in useful surfaces rather than lighting the entire idle display. |
| Light surfaces | Grep cream `#FAF6F1`; slightly lighter `#FFFCF8` for an inner paper surface. Charcoal `#181818` text. |
| Dark surfaces | `#181818` with a neutral `#343230` outer edge and a subtle lighter inner highlight. Cream text. |
| Accent | Grep orange `#FF3C00`; dark ink on an orange labeled button. Use neutral text on cream rather than orange body copy. |
| Geometry | 4 px spacing grid. Approximately 24 px card radius, 16 px inset controls and fully rounded capsules. One clear outer edge, 2–3 px reveal and one inner highlight. |
| Type | Start with the existing antialiased Montserrat at 16 px metadata, 20–22 px reading/body and 26–28 px primary status. Sentence case. Add an embedded Diatype subset only if its license covers the device. |
| Touch | Start with 52–60 physical pixels for primary targets on 466 px hardware; validate finger accuracy on the actual 1.75-inch panel. CSS touch sizes do not transfer directly to this pixel density. |
| Round safe area | Calculate each row's width from its vertical extent and the panel radius, preserving a 12–16 px bezel inset. Check the outer layer and hit target, not only the text. |

The existing settings code already computes a circular viewport; reuse that foundation. Avoid fitting a rectangular phone layout inside the circle. Keep card corners, content, touch bounds and page controls inside its usable chords.

Settings should use the same materials: three or four readable rows at once, quiet values aligned right, and orange only for a selected control. Put everyday Sound, Display and Connection choices ahead of diagnostics. Add Character/Quiet and Motion preferences under Display. Keep onboarding, pairing, network recovery and low-battery notices equally finished.

## Motion and state contract

| State or event | Visible behavior | Implementation rule |
| --- | --- | --- |
| Wake | Character or mark settles into place; concise status appears | About 240 ms. Never obscure a required pairing instruction. |
| Listening | Character leans in; 5–7 small rounded waveform bars respond to the microphone | Fast attack, slower decay. A visible listening label persists even in silence. |
| Release/send | Waveform settles into a small activity capsule | About 200 ms. Accepted input drives it; avoid implying a failed submission was sent. |
| Thinking | One orange dot travels through a short arc, or a subtle highlight crosses a neutral status | Indeterminate. Never invent a percentage or a “searching” stage the service has not reported. |
| First answer | Character makes space and a layered cream card rises by a few pixels | About 280 ms, once per turn. Interrupt the thinking animation immediately. |
| Speaking | Small mouth poses/soft pulse follow output audio; text stays stable | Use existing levels; do not imply phoneme-accurate lip sync. No typewriter effect delaying text. |
| Completed | Activity settles; answer remains readable | Do not automatically remove the result to show idle art. |
| Error/offline | Neutral surface with a restrained error mark, specific message and a useful action | Preserve the user's context; no repeated shake, angry loop or full-screen flashing. |
| Sleep/offscreen | No decorative drawing | Stop timers/render work, honoring existing sleep and settings behavior. |

Only one element should carry the main motion at a time. The character's expression, the activity cue and the reply transition must feel coordinated. Reduced motion uses immediate layout changes, still expressions and text state changes. A hardware mute must remain visually distinct from quiet/no-character mode.

## Character rendering and device budget

The current character is procedural pixel art, not a live 3D mesh. For this ESP32, produce a real 3D character offline and render a compact set of shaded images: neutral, blink, listening, thoughtful glance, a few speaking mouth poses and a short delighted reaction. This provides dimensional lighting and smooth contours with a predictable display cost. A later runtime-3D experiment would need its own benchmark and scope.

Start with approximately 192 × 192 assets at their intended display size, plus a separately prepared small reading pose. Prefer limited keyframes with lightweight eye/mouth overlays and gentle whole-character movement to full-screen video. Preserve the existing pixel renderer as a compact-board and missing-asset fallback behind a renderer interface.

Budget example, before compression: one 192 × 192 RGB565 image is 72 KiB; a 4-bit alpha mask adds 18 KiB. Twelve such frames total 1,080 KiB. These are arithmetic estimates for a possible custom packed format, not a claim that LVGL natively decodes that exact packing. Prototype the decoder and measure its scratch space, copy cost and frame time.

The installation note records a 2,166,784-byte firmware image in a 4 MiB OTA slot, leaving about 1.93 MiB at that time. The working tree now contains additional audio changes, so remeasure the current build before reserving assets. The board's 32 MB physical flash does not enlarge its existing 4 MiB application slots. Avoid a partition migration for the first release; fit a modest asset pack alongside fonts and code. CJK font support already has a substantial footprint when enabled.

Retain partial invalidation and strip/tile decoding. Prefer precomputed highlights and flat nested fills over animated blur, full-screen translucent layers or repeated LVGL image transforms. Target the existing 25 fps active schedule initially, with slower or sparse idle updates. Measure CPU, DMA/internal heap, PSRAM, power and audio underruns on hardware; the simulator cannot establish those results.

## Implementation sequence

| Phase | Concrete changes | Completion gate |
| --- | --- | --- |
| 1. Capture baseline and establish tokens | Record current 466/412 screenshots and hardware timing. Add shared palette, radii, spacing and type styles. Decouple UI state accents from `muse_pixel_accent()`. Build reusable capsule, layered card and state-label helpers. | One consistent screen family; no change to voice, pairing or physical controls. |
| 2. Layout and typography | Introduce Companion, Quiet and Reading presentation preferences. Replace pixel captions on full-size displays. Add glyph-width-aware pagination and preserve byte/word progress across layouts. Apply the system to Settings and pairing. | Long answers, CJK/punctuation and every card fit both circles with usable hit targets. |
| 3. Turn choreography | Add a presentation controller keyed to accepted turns. Coordinate listen → think → answer, interruption, retry, reconnect and speech completion. Add reduced motion and stop decorative work when hidden/asleep. | Each arrival animates once; fast answers and cancellation never leave stale activity or delay content. |
| 4. Dimensional character | Develop the offline model/lighting and small asset pack. Add an asset renderer backend with bounded decode memory, stable frame pacing and pixel fallback. | Approved on-device silhouette, legible expressions, no audio underruns, app fits its OTA slot. |
| 5. Device polish | Tune actual brightness, orange rendering, edges, text sizes, touch behavior and sleep/wake transitions. Bring the real settings implementation into simulator coverage or exercise it separately. | Saved visual baselines, required firmware/host checks and an end-to-end real-device voice turn. |

Phases 1–2 deliver the first reviewable improvement. Approve the layout and reading experience before investing in a large animation library. Phase 4's renderer experiment should happen early enough to settle the asset budget, but the finished asset pack comes after the screen geometry is stable.

Likely code boundaries:

- New `muse_theme.[ch]` and `muse_ui_components.[ch]`: shared materials and primitives.
- `muse_ui.c`: compose the views and keep board-specific safe areas; gradually extract layout/presentation logic instead of growing this file further.
- New `muse_presentation.[ch]`: visual state, preference and interruptible turn transitions.
- `muse_settings_ui.c`, `muse_settings.[ch]`: consistent controls and saved presentation/motion preferences.
- `muse_chat_text.c`, `muse_state.[ch]`: glyph-aware page contracts with safe threading; do not call LVGL measurement from an audio/network task without an appropriate boundary.
- New renderer interface around the existing `muse_pixel` contract, with an optional asset backend. Keep user-specific avatar files out of tracked source.
- `esp32/simulator`: more presentation scenarios and settings coverage. Its current Settings view is a placeholder.

## Validation and acceptance

Capture boot, unpaired, pairing confirmation, ready, listening, thinking, speaking, reading, muted, offline, error, low battery, settings and sleep/wake at 466 and 412. Include long text, large glyph widths, CJK fallback, no-character preference and reduced motion. Verify compact layouts are still usable rather than inheriting oversized cards.

Add meaningful tests for glyph wrapping/page continuity, one-shot transitions, interruption/retry and persisted preferences. Keep the existing deterministic simulator checks; add visual baselines and circular-bound checks since deterministic screenshots alone do not prove good layout. The real settings UI needs additional coverage because its current simulator stub cannot validate it.

On the board, verify hold-to-talk, release-to-send, interrupt, playback, mute, touch gestures, pairing, Wi-Fi recovery and sleep/wake with the new visuals running. Record active frame times and missed deadlines against the 40 ms schedule, internal-memory/PSRAM high-water marks and sustained audio stability. Run the repository's required firmware builds, size checks and host tests during implementation. No firmware build or flash is needed for this design-only proposal.

Success means the screen reads as one considered object: clear type, disciplined orange, tactile edges, a character with believable volume, and motion that explains what Muse is doing. The user can choose how present that character should be without sacrificing the quality of the rest of the interface.
