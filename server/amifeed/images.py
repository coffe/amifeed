"""Pictures for terminals: any image a web page has, made small enough for a
slow link and sent as sixel graphics (DEC VT340, xterm, UP-Term's XCON)."""

import io
import re

from PIL import Image, ImageOps

# A decompression bomb is refused before it is decoded.
Image.MAX_IMAGE_PIXELS = 40_000_000

COLOURS = (64, 32, 16, 8)   # tried in this order until the picture fits the budget
MIN_WIDTH = 48
_GROWTH = {64: 3.0, 32: 2.1, 16: 1.5, 8: 1.0}

_SIXEL = bytes(range(63, 127))
_TO_SIXEL = bytes.maketrans(bytes(range(64)), _SIXEL)
_RUN = re.compile(rb"(.)\1{3,}")


_tables = {}


def _mask_table(c, k):
    t = _tables.get((c, k))
    if t is None:
        t = _tables[(c, k)] = bytes(1 << k if i == c else 0 for i in range(256))
    return t


class ImageError(Exception):
    pass


def encode(pixels, w, h, palette):
    """Sixel for w x h palette indices (one byte each).

    The DCS starts with P2=1, so bits that are 0 leave the screen as it is:
    with P2=0 some terminals (UP-Term's XCON) paint the background over the
    colours already drawn in the same band."""
    out = [b'\x1bP0;1;0q"1;1;%d;%d' % (w, h)]
    for c in sorted(set(pixels)):
        r, g, b = palette[3 * c:3 * c + 3]
        out.append(b"#%d;2;%d;%d;%d" % (c, (r * 100 + 127) // 255, (g * 100 + 127) // 255,
                                        (b * 100 + 127) // 255))
    for y0 in range(0, h, 6):
        rows = [pixels[y * w:(y + 1) * w] for y in range(y0, min(y0 + 6, h))]
        parts = []
        for c in sorted(set(b"".join(rows))):
            # Row k as bytes that are 1<<k where the pixel has colour c, else 0.
            # The rows never share a bit, so adding them as big integers is a
            # bytewise OR - done in C instead of a Python loop per pixel.
            m = sum(int.from_bytes(row.translate(_mask_table(c, k)), "big")
                    for k, row in enumerate(rows))
            # Cut the empty end before the run-length coding, never after it:
            # stripping '?' from "!16?" would leave a bare repeat count.
            sx = m.to_bytes(w, "big").rstrip(b"\0").translate(_TO_SIXEL)
            if sx:
                parts.append(b"#%d" % c + _RUN.sub(lambda r: b"!%d%s" % (len(r[0]), r[1]), sx))
        out.append(b"$".join(parts) + b"-")
    out.append(b"\x1b\\")
    return b"".join(out)


def _open(data):
    try:
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (1024, 1024))       # JPEG: decode at a smaller size, much faster
        im.seek(0)                          # the first frame of an animation
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (0, 0, 0, 255))   # terminals are dark
            im = Image.alpha_composite(bg, im)
        return im.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise ImageError("not a picture I can read (%s)" % e.__class__.__name__)


# Median cut is slow on a whole picture; a palette from a copy this wide is as
# good and the remapping is fast.
PALETTE_WIDTH = 160


def _sixel(im, w, h, n):
    im = im.resize((w, h), Image.Resampling.LANCZOS)
    small = im.resize((min(w, PALETTE_WIDTH), max(1, h * min(w, PALETTE_WIDTH) // w)))
    pal = small.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    q = im.quantize(palette=pal, dither=Image.Dither.NONE)
    return encode(q.tobytes(), w, h, q.getpalette())


def to_sixel(data, max_w, max_h, budget):
    """Returns (sixel bytes, width, height): as big as max_w x max_h allows
    (never bigger than the original) and at most budget bytes, giving up
    colours first and then size."""
    im = _open(data)
    scale = min(1.0, max_w / im.width, max_h / im.height)
    w, h = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    # One try with the fewest colours tells how far to shrink before the others.
    probe = _sixel(im, w, h, COLOURS[-1])
    if len(probe) > budget:
        f = max(0.1, (budget / len(probe)) ** 0.5 * 0.95)
        w, h = max(MIN_WIDTH, int(w * f)), max(1, int(h * f))
    # About how much more each colour count costs than the fewest (measured on
    # photos); counts that clearly will not fit are not tried.
    est = min(len(probe), budget)        # after a shrink: about the budget
    while True:
        for n in COLOURS:
            if est * _GROWTH[n] > budget * 1.2 and n != COLOURS[-1]:
                continue
            six = _sixel(im, w, h, n)
            if len(six) <= budget:
                return six, w, h
        if w <= MIN_WIDTH:
            raise ImageError("the picture does not fit in %d bytes" % budget)
        # The size goes with the area: shrink by about what is needed, in one go.
        f = min(0.9, max(0.3, (budget / len(six)) ** 0.5 * 0.95))
        est = budget
        w, h = max(MIN_WIDTH, int(w * f)), max(1, int(h * f))
