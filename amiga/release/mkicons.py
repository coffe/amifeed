#!/usr/bin/env python3
"""Write the release's Workbench icons (classic 4-colour .info, OS 2.x+).
   mkicons.py OUTDIR  ->  OUTDIR/amifeed.info (drawer), OUTDIR/amifeed/{amifeed,README.txt,INSTALL.txt}.info
The pictures are drawn here, so the release has no third-party icon art."""
import os, struct, sys

W, H = 40, 20  # hires pixels are twice as tall as wide, so 40x20 looks square-ish

def canvas():
    return [[0] * W for _ in range(H)]

def rect(c, x0, y0, x1, y1, col, fill=None):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            edge = x in (x0, x1) or y in (y0, y1)
            if edge or fill is not None:
                c[y][x] = col if edge else fill

def tool_art():  # a screen showing the RSS symbol
    c = canvas()
    rect(c, 3, 0, 36, 15, 1, fill=3)
    rect(c, 14, 16, 25, 17, 1, fill=1)
    rect(c, 8, 18, 31, 19, 1, fill=1)
    ox, oy = 9, 13
    for y in range(1, 15):
        for x in range(4, 36):
            dx, dy = (x - ox) / 2.0, oy - y  # halve x for the pixel aspect
            d = (dx * dx + dy * dy) ** 0.5
            if dx >= -0.5 and dy >= -0.5 and (d < 1.3 or 3.2 < d < 4.6 or 6.4 < d < 7.8 or 9.6 < d < 11):
                c[y][x] = 2
    return c

def drawer_art():  # a drawer front with a handle
    c = canvas()
    rect(c, 2, 1, 37, 18, 1, fill=2)
    rect(c, 4, 3, 35, 16, 1, fill=0)
    rect(c, 14, 8, 25, 10, 1, fill=3)
    return c

def project_art():  # a page with a folded corner and lines of text
    c = canvas()
    rect(c, 9, 0, 30, 19, 1, fill=2)
    fx, fy, x0 = 8, 4, 22  # dog-ear: cut the corner above the diagonal, outline the flap
    for y in range(fy + 1):
        for x in range(x0, 31):
            t = (x - x0) / fx - y / fy
            if t > 1.0 / fx:
                c[y][x] = 0
            elif t > -1.0 / fx:
                c[y][x] = 1
        c[y][x0] = 1
    for x in range(x0, 31):
        c[fy][x] = 1
    for y in (4, 7, 10, 13, 16):
        for x in range(12, 27 if y != 4 else 21):
            c[y][x] = 3 if y % 2 == 0 else 1
    return c

def planes(c):
    row = (W + 15) // 16 * 2
    out = b''
    for plane in (0, 1):
        for y in range(H):
            bits = bytearray(row)
            for x in range(W):
                if (c[y][x] >> plane) & 1:
                    bits[x // 8] |= 0x80 >> (x % 8)
            out += bytes(bits)
    return out

def text(s):
    s = s.encode('latin-1') + b'\0'
    return struct.pack('>I', len(s)) + s

def icon(kind, art, deftool=None, stack=0):
    is_drawer = kind == 2
    gadget = struct.pack('>IhhHHHHHIIIIIHI', 0, 0, 0, W, H, 4, 3, 1,
                         1, 0, 0, 0, 0, 0, 1)  # GADGIMAGE + complement; UserData 1 = OS 2.x icon
    b = struct.pack('>HH', 0xE310, 1) + gadget
    b += struct.pack('>BBIIIIIII', kind, 0, 1 if deftool else 0, 0,
                     0x80000000, 0x80000000,  # NO_ICON_POSITION: Workbench places it
                     1 if is_drawer else 0, 0, stack)
    if is_drawer:  # NewWindow + CurrentX/Y
        b += struct.pack('>hhhhBBIIIIIIIhhHHH', 60, 40, 420, 140, 255, 255, 0, 0,
                         0, 0, 0, 0, 0, 90, 40, 0xFFFF, 0xFFFF, 1) + struct.pack('>ii', 0, 0)
    b += struct.pack('>hhhhhIBBI', 0, 0, W, H, 2, 1, 3, 0, 0) + planes(art)
    if deftool:
        b += text(deftool)
    if is_drawer:
        b += struct.pack('>IH', 2, 2)  # DrawerData2: show all files, view by name
    return b

def write(path, data):
    with open(path, 'wb') as f:
        f.write(data)

if __name__ == '__main__':
    out = sys.argv[1]
    assert len(tool_art()) == H and all(len(r) == W for r in tool_art())
    write(os.path.join(out, 'amifeed.info'), icon(2, drawer_art()))
    write(os.path.join(out, 'amifeed', 'amifeed.info'), icon(3, tool_art(), stack=16384))
    for name in ('README.txt', 'INSTALL.txt'):
        write(os.path.join(out, 'amifeed', name + '.info'),
              icon(4, project_art(), deftool='SYS:Utilities/MultiView'))
