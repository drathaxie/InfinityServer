"""Validate custom portrait-frame PNGs against the vanilla geometry rules.

Usage:  python validate_portrait.py <portraits_dir> <key>
e.g.:   python validate_portrait.py "C:/.../UserData/Beyond/portraits" potato

Checks each <key>_*.png layer against the rules in README.md and prints
PASS / WARN / FAIL per rule, so art can be fixed without a client restart cycle.
"""
import sys
import numpy as np
from PIL import Image


def load(path):
    try:
        return np.array(Image.open(path).convert('RGBA'))
    except FileNotFoundError:
        return None


def report(level, msg):
    print(f'  [{level}] {msg}')
    return level == 'FAIL'


def check_plate(a):
    bad = False
    H, W = a.shape[:2]
    A = a[:, :, 3]
    ar = W / H
    if not (1.70 <= ar <= 1.95):
        bad |= report('WARN', f'canvas {W}x{H} aspect {ar:.2f}; UI rect is 430x240 (1.79) '
                              '- art will stretch to fit')
    else:
        report('PASS', f'canvas {W}x{H} aspect ok')

    # normalize zone coords to this canvas (rules defined on 584x322)
    sx, sy = W / 584.0, H / 322.0
    opaque = A > 128

    # Rule 1: transparency on the right (solid mass ends by x=545)
    right_zone = opaque[:, int(552 * sx):]
    frac = right_zone.mean()
    if frac > 0.35:
        bad |= report('FAIL', f'right edge zone (x>{int(552*sx)}) is {frac:.0%} opaque - '
                              'must be transparent/thin flourishes only (rule 1)')
    else:
        report('PASS', f'right transparency zone ok ({frac:.0%} opaque)')

    total = opaque.mean()
    if total > 0.90:
        bad |= report('FAIL', f'plate is {total:.0%} opaque (full-bleed). Vanilla plates are '
                              '73-82% - add transparent surroundings (rule 1)')
    else:
        report('PASS', f'overall opacity {total:.0%} (vanilla range 73-82%)')

    # Rule 2: grey field position - darkest low-saturation region should match the box
    R, G, B = (a[:, :, i].astype(int) for i in range(3))
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    ch = opaque & (mx < 75) & ((mx - mn) < 16)
    gx0, gy0, gx1, gy1 = int(70*sx), int(57*sy), int(527*sx), int(248*sy)
    box = ch[gy0:gy1, gx0:gx1]
    inside = box.mean() if box.size else 0
    out_mask = ch.copy()
    out_mask[gy0:gy1, gx0:gx1] = False
    outside = out_mask.sum() / max(ch.sum(), 1)
    if inside < 0.75:
        bad |= report('WARN', f'grey-field box only {inside:.0%} charcoal - interior may be '
                              'misplaced (expect x70-527 y57-248 on 584x322)')
    else:
        report('PASS', f'grey field fills its box ({inside:.0%})')
    if outside > 0.30:
        bad |= report('WARN', f'{outside:.0%} of charcoal lies OUTSIDE the grey-field box - '
                              'grey area will read bigger than vanilla (rule 2)')
    else:
        report('PASS', f'charcoal contained ({outside:.0%} outside box)')

    # Rule 3: airy top/bottom bands outside the ring-hidden zone
    for name, band in (('top', opaque[:int(20*sy), int(80*sx):int(545*sx)]),
                       ('bottom', opaque[int(284*sy):, int(80*sx):int(545*sx)])):
        f = band.mean() if band.size else 0
        if f > 0.60:
            bad |= report('WARN', f'{name} decoration band is {f:.0%} solid - needs '
                                  'transparent gaps or it reads as a slab (rule 3)')
        else:
            report('PASS', f'{name} band airy ({f:.0%} solid)')
    return bad


def check_ring(a):
    bad = False
    H, W = a.shape[:2]
    if abs(W - H) > max(W, H) * 0.02:
        bad |= report('WARN', f'canvas {W}x{H} not square - preserveAspect letterboxes it')
    else:
        report('PASS', f'canvas {W}x{H} square')
    A = a[:, :, 3]
    cy, cx = H // 2, W // 2
    # hole size varies by style (vanilla 29.5-34% radius, potato smaller); just require
    # the very center to be transparent so the character head shows through
    r = int(min(W, H) * 0.12)
    disc = A[cy-r:cy+r, cx-r:cx+r]
    yy, xx = np.ogrid[-r:r, -r:r]
    inside = (xx*xx + yy*yy) < r*r
    frac = (disc[inside] > 32).mean()
    if frac > 0.02:
        bad |= report('FAIL', f'canvas center {frac:.0%} opaque - the character head must '
                              'show through the ring hole')
    else:
        report('PASS', 'center hole transparent')
    return bad


def check_circle(a, label_):
    H, W = a.shape[:2]
    if abs(W - H) > max(W, H) * 0.03:
        return report('WARN', f'{label_} canvas {W}x{H} not square (rect is square, '
                              'preserveAspect)')
    report('PASS', f'{label_} canvas {W}x{H} ok')
    return False


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    d, key = sys.argv[1], sys.argv[2]
    failed = False
    for suffix, checker in (('plate', check_plate), ('frame', check_ring),
                            ('lvlcircle', lambda a: check_circle(a, 'lvlcircle')),
                            ('background', lambda a: check_circle(a, 'background'))):
        path = f'{d}/{key}_{suffix}.png'
        a = load(path)
        print(f'{key}_{suffix}.png:')
        if a is None:
            print('  [SKIP] not present (falls back to Default art)')
            continue
        failed |= checker(a)
    print()
    print('RESULT:', 'FAIL - fix items above' if failed else 'OK - restart the client to see it')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
