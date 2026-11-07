import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import pokemon_images


def png_header(width, height, colour_type, signature=None, chunk=b"IHDR"):
    """The first 26 bytes of a PNG: signature, IHDR length and type, size, depth, colour."""
    signature = pokemon_images.PNG_SIGNATURE if signature is None else signature
    return (
        signature + struct.pack(">I", 13) + chunk
        + struct.pack(">II", width, height) + bytes([8, colour_type])
    )


class FrameTests(unittest.TestCase):
    def test_small_sprite_gets_a_large_factor(self):
        self.assertEqual(pokemon_images.frame(22, 22), (22, 12))

    def test_wide_content_is_sized_by_its_longest_side(self):
        self.assertEqual(pokemon_images.frame(62, 50), (62, 5))

    def test_full_frame_sprite(self):
        self.assertEqual(pokemon_images.frame(96, 95), (96, 3))

    def test_converted_sprite_keeps_its_geometry(self):
        self.assertEqual(pokemon_images.frame(288, 200), (288, 1))

    def test_non_positive_input_raises(self):
        for size in ((0, 10), (10, 0), (-1, 5)):
            with self.subTest(size=size), self.assertRaises(pokemon_images.PokemonImageError):
                pokemon_images.frame(*size)


class PngSizeTests(unittest.TestCase):
    def write(self, data):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "sprite.png"
        path.write_bytes(data)
        return path

    def test_reads_width_height_and_colour_type(self):
        path = self.write(png_header(288, 288, 3))
        self.assertEqual(pokemon_images.png_size(path), (288, 288, 3))

    def test_wrong_signature_raises(self):
        path = self.write(png_header(1, 1, 3, signature=b"GIF89a\x00\x00"))
        with self.assertRaises(pokemon_images.PokemonImageError):
            pokemon_images.png_size(path)

    def test_wrong_chunk_type_raises(self):
        path = self.write(png_header(1, 1, 3, chunk=b"IDAT"))
        with self.assertRaises(pokemon_images.PokemonImageError):
            pokemon_images.png_size(path)

    def test_truncated_file_raises(self):
        path = self.write(pokemon_images.PNG_SIGNATURE)
        with self.assertRaises(pokemon_images.PokemonImageError):
            pokemon_images.png_size(path)


@unittest.skipUnless(shutil.which("magick"), "ImageMagick 7 is not installed")
class ConvertSpriteTests(unittest.TestCase):
    def convert(self, *draw):
        """Draw on a transparent 20x20 source and convert it; return (source, target, side)."""
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        source = Path(root.name) / "1.png"
        target = Path(root.name) / "out.png"
        subprocess.run(
            ["magick", "-size", "20x20", "xc:none", *draw, str(source)],
            check=True, capture_output=True,
        )
        side = pokemon_images.convert_sprite(source, target)
        self.assertEqual([p.name for p in Path(root.name).iterdir() if p.suffix == ".tmp"], [])
        return source, target, side

    def test_converts_a_trimmed_square_palette_sprite(self):
        source, target, side = self.convert("-fill", "red", "-draw", "rectangle 5,5 14,9")
        self.assertEqual(pokemon_images.content_size(source), (10, 5))
        self.assertEqual(side, 10 * 26)
        self.assertEqual(pokemon_images.png_size(target), (side, side, 3))
        # The longest side runs edge to edge.
        self.assertEqual(pokemon_images.content_size(target), (260, 130))

    def test_faint_pixels_leave_no_margin(self):
        source, target, side = self.convert(
            "-fill", "red", "-draw", "rectangle 5,5 14,9",
            "-fill", "rgba(255,0,0,0.25)", "-draw", "rectangle 15,5 16,9",
        )
        self.assertEqual(pokemon_images.content_size(source), (10, 5))
        self.assertEqual(side, 10 * 26)
        self.assertEqual(pokemon_images.content_size(target), (260, 130))


if __name__ == "__main__":
    unittest.main()
