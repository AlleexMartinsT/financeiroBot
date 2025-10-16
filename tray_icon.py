# tray_icon.py
import threading
from PIL import Image, ImageDraw
import pystray

def _create_image():
    width, height = 32, 32
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.ellipse((4, 4, width - 4, height - 4), fill=(30, 144, 255, 255))
    return image

def run_tray(on_quit):
    def _quit(icon, item):
        icon.stop()
        on_quit()

    icon = pystray.Icon(
        "finance-bot",
        _create_image(),
        "Finance Bot",
        menu=pystray.Menu(pystray.MenuItem("Sair", _quit))
    )
    icon.run()
