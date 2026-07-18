from ivyea_translate.ocr import UPSCALE_THRESHOLD, compute_upscale


def test_small_screenshot_upscaled():
    assert compute_upscale(400, 120) == 2
    assert compute_upscale(1200, 300) == 2


def test_large_image_not_upscaled():
    assert compute_upscale(UPSCALE_THRESHOLD, 500) == 1
    assert compute_upscale(2560, 1440) == 1


def test_degenerate_sizes():
    assert compute_upscale(0, 100) == 1
    assert compute_upscale(-5, -5) == 1
