"""Create mbtool.ico (a small Modbus-style waveform badge) with Pillow. Run once; the .ico is committed."""
from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(n):
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(2, n // 6)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=r, fill=(26, 100, 214, 255))
    # square-wave "bus" signal
    pts, x, step, hi, lo = [], n * 0.14, n * 0.18, n * 0.34, n * 0.66
    y = lo
    pts.append((x, y))
    for i in range(4):
        y = hi if y == lo else lo
        pts.append((x, y))
        x += step
        pts.append((x, y))
    pts.append((n * 0.86, lo))
    d.line(pts, fill=(255, 255, 255, 255), width=max(1, n // 12), joint="curve")
    return img


if __name__ == "__main__":
    big = draw(256)
    big.save(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mbtool.ico"), sizes=[(s, s) for s in SIZES])
    print("mbtool.ico written")
