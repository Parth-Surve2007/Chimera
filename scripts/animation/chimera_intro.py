from pathlib import Path
from manim import *
from manimpango import register_font

FONT_PATH = Path(__file__).resolve().parents[2] / "fonts" / "ec-dots-life-font" / "EcDotsLifeRegular-AydL.ttf"
register_font(str(FONT_PATH))

class ChimeraIntro(Scene):
    def construct(self):
        self.camera.background_color = "#050505"
        font = "EC Dots Life"
        word = Text("CHIMERA", font=font, color="#F2F2F2", font_size=1.0).set_opacity(0.08).scale(1.7)
        mark = VGroup(*[Line(ORIGIN, direction, color="#A0A0A0", stroke_width=1.2) for direction in (UP, UR, RIGHT, DR, DOWN, DL, LEFT, UL)]).scale(0.8)
        ring = Circle(radius=1.05, color="#686868", stroke_width=1.1).rotate(PI / 8)
        subtitle = Text("LOCAL-FIRST CAPABILITY ENGINE", font=font, color="#A0A0A0", font_size=0.22).next_to(mark, DOWN, buff=0.38)
        self.play(FadeIn(word, run_time=0.8), Create(ring, run_time=0.8), Create(mark, run_time=0.8))
        self.play(ring.animate.rotate(PI / 2), word.animate.set_opacity(0.16), run_time=0.6)
        self.play(FadeIn(subtitle, shift=UP * 0.08, run_time=0.45))
        self.wait(0.35)
        self.play(FadeOut(VGroup(word, mark, ring, subtitle), run_time=0.35))
