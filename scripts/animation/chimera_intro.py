from pathlib import Path
from manim import *
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]

def make_title_asset():
    path = ROOT / "assets" / "chimera_title.png"
    image = Image.new("RGBA", (1500, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(ROOT / "fonts" / "ec-dots-life-font" / "EcDotsLifeRegular-AydL.ttf"), 190)
    text = "Chimera"
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((1500 - box[2]) // 2, (300 - (box[3] - box[1])) // 2 - box[1]), text, font=font, fill=(242, 242, 242, 255))
    image.save(path)
    return path

class ChimeraIntro(Scene):
    def construct(self):
        self.camera.background_color = "#050505"
        word = ImageMobject(str(make_title_asset())).scale(0.55)
        mark = VGroup(*[Line(ORIGIN, direction, color="#A0A0A0", stroke_width=1.2) for direction in (UP, UR, RIGHT, DR, DOWN, DL, LEFT, UL)]).scale(0.8)
        ring = Circle(radius=1.05, color="#686868", stroke_width=1.1).rotate(PI / 8)
        loading = Text("LOADING", font="Arial", color="#A0A0A0", font_size=0.22).next_to(mark, DOWN, buff=0.35)
        self.play(FadeIn(word, shift=UP * 0.12, run_time=1.25), Create(ring, run_time=0.9), Create(mark, run_time=0.9))
        self.play(ring.animate.rotate(PI / 2), word.animate.scale(1.08), run_time=0.6)
        self.play(FadeIn(loading, run_time=0.35))
        self.play(ring.animate.rotate(PI / 2), word.animate.scale(1.04), run_time=0.55)
        self.wait(0.35)
        self.play(FadeOut(Group(word, mark, ring, loading), run_time=0.4))
