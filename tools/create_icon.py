from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    output = Path("assets") / "jarvis.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (256, 256), (0, 5, 9, 255))
    draw = ImageDraw.Draw(image)
    cyan = (0, 220, 255, 255)
    pale = (180, 248, 255, 255)
    for inset, width in ((18, 5), (34, 2), (54, 4), (76, 2)):
        draw.ellipse((inset, inset, 255 - inset, 255 - inset), outline=cyan, width=width)
    draw.arc((28, 28, 227, 227), 205, 330, fill=pale, width=9)
    draw.arc((48, 48, 207, 207), 15, 145, fill=pale, width=7)
    draw.polygon([(128, 70), (160, 128), (128, 186), (96, 128)], outline=pale, fill=(0, 70, 90, 255))
    image.save(output, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()

