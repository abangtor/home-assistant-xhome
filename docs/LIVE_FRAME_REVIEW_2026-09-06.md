# Live camera frame review — 2026-09-06

## Initial observations (superseded where noted below)

- MainDoor camera in HA reports rotation 90 and auto edge crop 32 pixels.
- Saved July 29 app capture yields 882 assembled JPEGs, nominally 640x368.
- The existing brightness-based crop detector returns 0, 16 or 32 pixels.
  The MJPEG handler latches the maximum for each connection, changing output
  width from 368 to 352 or 336. A 32-pixel crop removes 8.7% of the width.
- Snapshot polling bypasses crop/validation and caches raw incoming frames;
  live playback follows a different processing path.
- Some assembled JPEGs contain visible colored/checkerboard artifacts before
  rotation. FFmpeg also decodes the first affected frame with zero decode
  errors. Successful decoding alone does not establish image integrity.
- User corrected the HA hostname to ha.my.track-point.de. HA tools subsequently
  worked and reported Core 2026.9.1; no connection configuration was changed.

## Local candidate change (not deployed)

- Preserve full decoded frame dimensions: camera explicitly disables auto crop.
- Decode/re-encode all live frames, including rotation 0; reject missing SOI/EOI,
  undecodable media and unsupported rotation values.
- Cache only prepared live frames; snapshot polling returns those same bytes
  without applying rotation again.
- Keep existing crop helpers available for offline diagnostic comparisons.

## Validation

- 151 unit tests pass, including edge-brightness changes at all four rotations,
  field-of-view preservation, rejected media and snapshot cache consistency.
- Integration/API/test syntax checks, scoped Ruff checks and git diff check pass.
- Capture replay: 872 frames accepted at exactly 368x640 with crop 0; 10 rejected.
  Previously Pillow load alone rejected 6; the EOI check rejects 4 additional
  incomplete frames. Capture/media stays outside the repository.

## Remaining investigation

This candidate fixes cropping and snapshot/live geometry inconsistencies. It
is not a demonstrated fix for artifacts already encoded in received JPEGs;
removing the masking crop can expose them. Browser rendering with this candidate
has not been verified, and it has not been installed in Home Assistant.

Need to identify the affected surface (HA card, expanded camera, or separate
XHome viewer) and compare its field of view/artifacts with the original phone
app. Reported loss of 17–20% exceeds the confirmed 8.7% crop, so do not attribute
all missing width to that workaround without further evidence.


## Follow-up: confirmed JPEG fragment metadata bug

User confirms the official app shows the full picture; both HA views exhibit
identical cropping/fragmented-edge symptoms across Android Chrome, Windows
Chrome/Firefox and the Android HA app. This points to the shared stream path.

The earlier claim of source-encoded corruption was not established: the saved
JPEGs had been reconstructed by our own parser. The final fragment carries a
28-byte metadata prefix. Its outer length includes that prefix, while bytes
16..19 give only the JPEG tail length. Taking the larger length from offset 20
inserted metadata directly into the JPEG entropy stream, near the bottom of
the unrotated frame (the left edge after rotation).

All 1,610 final JPEG packets in the July capture (including retransmissions)
have this 28-byte difference. The first affected frame contains the metadata at
JPEG offset 11,700, exactly the beginning of fragment 10. Removing those 28
bytes repairs the edge without changing JPEG dimensions or cropping pixels.

Source and vendored parsers now skip this prefix for command-8 final JPEG
fragments with the observed length difference, and reject truncated extended
fragments. Equal-length ordinary fragments remain unchanged. A regression test
splits a real generated JPEG at three tail lengths and requires byte-exact
reconstruction, plus rejection of truncated final fragments.

Validation:
- 151 unittest tests pass, including vendor synchronization.
- Ordered KCP replay: 1,344 JPEGs, all decode successfully at 640x368 with EOI.
- Inspected repaired samples (first frame, frame 100 and frame 1000): clean edge.
- Earlier packet-arrival-order extraction yielded only 882 frames and was not
  a reliable basis for assessing transport loss or source image integrity.
- Full-width camera output remains 368x640 after the configured rotation.

Not deployed; current live HA verification remains separate from capture replay.
