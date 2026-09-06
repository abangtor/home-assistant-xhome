from __future__ import annotations

import ast
import asyncio
import collections
import importlib.util
import sys
import time
import types
import unittest
from io import BytesIO
from pathlib import Path

IMAGE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "image.py"
COMPONENT_PATH = IMAGE_PATH.parent
CUSTOM_COMPONENTS_PATH = COMPONENT_PATH.parent


def _install_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
sys.modules["custom_components"].__path__ = [str(CUSTOM_COMPONENTS_PATH)]

package = types.ModuleType("custom_components.xhome")
package.__path__ = [str(COMPONENT_PATH)]
sys.modules["custom_components.xhome"] = package

ha_image = _install_module("homeassistant.components.image")


class FakeImageEntity:
    def __init__(self, hass, verify_ssl: bool = False) -> None:
        self.hass = hass
        self.verify_ssl = verify_ssl
        self.access_tokens = collections.deque(["token"], maxlen=2)


ha_image.ImageEntity = FakeImageEntity

ha_config_entries = _install_module("homeassistant.config_entries")
ha_config_entries.ConfigEntry = object

ha_core = _install_module("homeassistant.core")
ha_core.HomeAssistant = object

ha_entity_platform = _install_module("homeassistant.helpers.entity_platform")
ha_entity_platform.AddEntitiesCallback = object

xhome_const = _install_module("custom_components.xhome.const")
xhome_const.CONF_IMAGE_ROTATION = "image_rotation"
xhome_const.DEFAULT_IMAGE_ROTATION = 0
xhome_const.DOMAIN = "xhome"
xhome_const.IMAGE_ROTATIONS = [0, 90, 180, 270]

xhome_coordinator = _install_module("custom_components.xhome.coordinator")
xhome_coordinator.XHomeDataUpdateCoordinator = object
xhome_coordinator.XHomeLatestEventMedia = object

xhome_entity = _install_module("custom_components.xhome.entity")


class FakeXHomeEntity:
    def __init__(self, coordinator, uid: str, suffix: str) -> None:
        self.coordinator = coordinator
        self.uid = uid
        self._attr_unique_id = f"{uid}_{suffix}"

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict:
        return {}


xhome_entity.XHomeEntity = FakeXHomeEntity

spec = importlib.util.spec_from_file_location("custom_components.xhome.image", IMAGE_PATH)
image = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(image)


class ImageEntityTests(unittest.TestCase):
    def test_latest_event_image_initializes_image_access_token(self):
        coordinator = types.SimpleNamespace(
            hass=object(),
            config_entry=types.SimpleNamespace(options={}),
            latest_event_media=lambda uid: None,
        )

        entity = image.XHomeLatestEventImage(coordinator, "abc")

        self.assertEqual(entity.uid, "abc")
        self.assertEqual(entity.access_tokens[-1], "token")

    def test_image_rotation_degrees_normalizes_options(self):
        self.assertEqual(image.image_rotation_degrees({"image_rotation": "90"}), 90)
        self.assertEqual(image.image_rotation_degrees({"image_rotation": "bad"}), 0)
        self.assertEqual(image.image_rotation_degrees({"image_rotation": "45"}), 0)

    def test_rotate_image_bytes_noops_without_rotation(self):
        self.assertEqual(image.rotate_image_bytes(b"not really an image", 0, "image/jpeg"), b"not really an image")

    def test_is_decodable_jpeg_rejects_non_jpeg(self):
        self.assertFalse(image.is_decodable_jpeg(b"not a jpeg"))

    def test_detect_live_rotation_edge_crop_finds_unstable_bottom_edge(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")

        frame = Image.new("L", (20, 24), 160)
        for y in range(16, 24):
            for x in range(20):
                frame.putpixel((x, y), 20)

        self.assertEqual(image.detect_live_rotation_edge_crop(frame, 90, max_crop_pixels=16), 8)

    def test_rotate_live_image_bytes_crops_source_edge_before_rotation(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")

        source = BytesIO()
        Image.new("RGB", (10, 6), "white").save(source, format="JPEG")

        rotated = image.rotate_live_image_bytes(
            source.getvalue(),
            90,
            "image/jpeg",
            edge_crop_pixels=2,
        )

        self.assertIsNotNone(rotated)
        with Image.open(BytesIO(rotated)) as rotated_image:
            self.assertEqual(rotated_image.size, (4, 10))

    def test_prepare_live_image_bytes_reports_auto_crop(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")

        source = BytesIO()
        Image.new("RGB", (10, 6), "white").save(source, format="JPEG")

        _rotated, crop_pixels = image.prepare_live_image_bytes(
            source.getvalue(),
            90,
            "image/jpeg",
            minimum_edge_crop_pixels=4,
        )

        self.assertGreaterEqual(crop_pixels, 4)


class LiveFrameRenderingTests(unittest.TestCase):
    """Exercise production frame methods without requiring a running HA."""

    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        self.Image = Image
        tree = ast.parse((COMPONENT_PATH / "camera.py").read_text())
        camera = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "XHomeLiveCamera")
        methods = {"_prepare_stream_jpeg", "async_camera_image", "_handle_live_jpeg"}
        selected = [node for node in camera.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in methods]
        namespace = {
            "prepare_live_image_bytes": image.prepare_live_image_bytes,
            "asyncio": asyncio,
            "time": time,
            "_replace_latest_frame": lambda queue, frame: queue.put_nowait(frame),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(COMPONENT_PATH / "camera.py"), "exec"), namespace)
        self.camera = type("FrameCamera", (), {name: namespace[name] for name in methods})()
        self.camera._attr_content_type = "image/jpeg"
        self.camera._image_rotation = lambda: 90

    def jpeg(self, edge):
        frame = self.Image.new("RGB", (640, 368), (160, 160, 160))
        # A real dark/light object along the edge must not be mistaken for padding.
        frame.paste((edge, edge, edge), (0, 336, 640, 368))
        output = BytesIO()
        frame.save(output, format="JPEG", quality=95)
        return output.getvalue()

    def test_changing_edge_brightness_preserves_all_pixels_and_dimensions(self):
        for rotation in (0, 90, 180, 270):
            self.camera._image_rotation = lambda rotation=rotation: rotation
            for edge in (160, 0, 255, 160):
                with self.subTest(rotation=rotation, edge=edge):
                    prepared, status, crop = self.camera._prepare_stream_jpeg(self.jpeg(edge))
                    self.assertEqual(crop, 0)
                    self.assertEqual(status, "rotated" if rotation else "original")
                    with self.Image.open(BytesIO(prepared)) as actual:
                        self.assertEqual(actual.size, (368, 640) if rotation in (90, 270) else (640, 368))
                        point = {0: (320, 352), 90: (15, 320), 180: (319, 15), 270: (352, 319)}[rotation]
                        self.assertAlmostEqual(actual.convert("L").getpixel(point), edge, delta=3)

    def test_truncated_and_non_jpeg_frames_are_dropped_in_every_rotation(self):
        valid = self.jpeg(160)
        png = BytesIO()
        self.Image.new("RGB", (640, 368)).save(png, format="PNG")
        for rotation in (0, 90, 180, 270):
            self.camera._image_rotation = lambda rotation=rotation: rotation
            for invalid in (valid[:-2], valid[:500], b"not a jpeg", png.getvalue()):
                with self.subTest(rotation=rotation, length=len(invalid)):
                    self.assertEqual(self.camera._prepare_stream_jpeg(invalid), (None, "invalid", 0))

    def test_raw_arrivals_do_not_replace_validated_snapshot_or_rotate_it_twice(self):
        prepared, _, _ = self.camera._prepare_stream_jpeg(self.jpeg(0))
        self.camera._last_live_jpeg = prepared
        self.camera._live_frames = 0
        self.camera._write_live_state = lambda: None
        queue = asyncio.Queue()
        self.camera._handle_live_jpeg(queue, b"broken next frame")
        self.assertEqual(queue.get_nowait(), b"broken next frame")
        self.assertEqual(asyncio.run(self.camera.async_camera_image()), prepared)
        with self.Image.open(BytesIO(asyncio.run(self.camera.async_camera_image()))) as snapshot:
            self.assertEqual(snapshot.size, (368, 640))

    def test_invalid_rotation_returns_a_pair_without_raising(self):
        self.assertEqual(image.prepare_live_image_bytes(self.jpeg(0), 45, "image/jpeg"), (None, 0))


if __name__ == "__main__":
    unittest.main()
