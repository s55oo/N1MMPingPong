"""Tiny QR code encoder (byte mode, error correction M, versions 1-6).

Enough for a phone lamp address such as http://192.168.178.123:8047/
(up to 106 bytes). No outside libraries, so nothing extra gets packed
into the exe. qr_matrix(text) -> list of rows, True = dark module.
"""

# version: (data codewords per block list, EC codewords per block) for level M
_BLOCKS_M = {
    1: ([16], 10),
    2: ([28], 16),
    3: ([44], 26),
    4: ([32, 32], 18),
    5: ([43, 43], 24),
    6: ([27, 27, 27, 27], 16),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
_EC_M_BITS = 0b00  # format bits for level M

# GF(256) with the QR polynomial 0x11d
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n):
    g = [1]
    for i in range(n):
        g = [a ^ _gmul(b, _EXP[i]) for a, b in zip(g + [0], [0] + g)]
    return g


def _rs_ec(data, n):
    gen = _rs_generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _gmul(gen[j], coef)
    return rem[len(data):]


def _codewords(text_bytes):
    for version in sorted(_BLOCKS_M):
        blocks, ec_len = _BLOCKS_M[version]
        capacity = sum(blocks)
        if len(text_bytes) + 2 <= capacity:  # 4 mode + 8 length bits + data + 4 term
            break
    else:
        raise ValueError("text too long for a QR code here")
    bits = [0, 1, 0, 0]  # byte mode
    bits += [(len(text_bytes) >> i) & 1 for i in range(7, -1, -1)]
    for byte in text_bytes:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    total_bits = capacity * 8
    bits += [0] * min(4, total_bits - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(data) < capacity:
        data.append((0xEC, 0x11)[pad % 2])
        pad += 1
    # split into blocks, add EC, interleave
    split, pos = [], 0
    for size in blocks:
        split.append(data[pos:pos + size])
        pos += size
    ecs = [_rs_ec(b, ec_len) for b in split]
    out = []
    for i in range(max(blocks)):
        out += [b[i] for b in split if i < len(b)]
    for i in range(ec_len):
        out += [e[i] for e in ecs]
    return version, out


def _format_bits(mask):
    data = (_EC_M_BITS << 3) | mask
    rem = data << 10
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= 0x537 << (i - 10)
    return ((data << 10) | rem) ^ 0x5412


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _base(version):
    size = 17 + 4 * version
    m = [[None] * size for _ in range(size)]

    def finder(r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < size and 0 <= cc < size:
                    dark = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) \
                        or (2 <= r <= 4 and 2 <= c <= 4)
                    m[rr][cc] = dark

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)
    for i in range(8, size - 8):
        m[6][i] = m[i][6] = i % 2 == 0
    pos = _ALIGN[version]
    for r in pos:
        for c in pos:
            if m[r][c] is not None:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1
    m[size - 8][8] = True  # dark module
    # reserve format areas
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = False
        if m[i][8] is None:
            m[i][8] = False
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = False
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = False
    return m


def _place(m, codewords, mask):
    size = len(m)
    fixed = [[v is not None for v in row] for row in m]
    out = [row[:] for row in m]
    bits = []
    for cw in codewords:
        bits += [(cw >> i) & 1 for i in range(7, -1, -1)]
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if fixed[r][c]:
                    continue
                bit = bits[idx] if idx < len(bits) else 0
                idx += 1
                if _MASKS[mask](r, c):
                    bit ^= 1
                out[r][c] = bool(bit)
        upward = not upward
        col -= 2
    fmt = _format_bits(mask)
    fbits = [(fmt >> i) & 1 for i in range(14, -1, -1)]  # bit 14 first
    # around the top-left finder
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    # split copy: bottom-left (bits 14..8), top-right (bits 7..0)
    coords2 = [(size - 1 - i, 8) for i in range(7)] + [(8, size - 8 + i) for i in range(8)]
    for (r, c), b in zip(coords1, fbits):
        out[r][c] = bool(b)
    for (r, c), b in zip(coords2, fbits):
        out[r][c] = bool(b)
    return out


def _penalty(m):
    size = len(m)
    score = 0
    for lines in (m, [list(col) for col in zip(*m)]):
        for row in lines:
            run, last = 0, None
            for v in row:
                if v == last:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, last = 1, v
            if run >= 5:
                score += run - 2
            text = "".join("1" if v else "0" for v in row)
            score += 40 * (text.count("10111010000") + text.count("00001011101"))
    for r in range(size - 1):
        for c in range(size - 1):
            v = m[r][c]
            if v == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(v for row in m for v in row)
    score += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
    return score


def qr_matrix(text):
    """QR code of `text` as a square list of rows (True = dark), no quiet zone."""
    version, codewords = _codewords(text.encode("utf-8"))
    base = _base(version)
    best = None
    for mask in range(8):
        m = _place(base, codewords, mask)
        p = _penalty(m)
        if best is None or p < best[0]:
            best = (p, m)
    return best[1]
