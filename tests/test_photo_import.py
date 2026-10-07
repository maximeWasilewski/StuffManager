from io import BytesIO

import pytest
from PIL import Image

from app.images import PhotoError, process_photo


def encoded(fmt):
    buffer = BytesIO()
    image = Image.new("RGB", (80, 40), (230, 20, 10))
    if fmt == "MPO":
        image.save(buffer, format=fmt, save_all=True,
                   append_images=[Image.new("RGB", image.size, (10, 20, 230))])
    else:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "MPO", "PNG", "WEBP", "HEIF", "AVIF", "TIFF"])
def test_gallery_formats_convert_to_primary_jpeg(fmt):
    data = encoded(fmt)
    assert Image.open(BytesIO(data)).format == fmt
    image = Image.open(BytesIO(process_photo(data)))
    assert image.format == "JPEG" and image.size == (80, 40)
    # The red primary photo must be kept, not the blue extra MPO frame.
    red, green, blue = image.getpixel((30, 20))
    assert red > 180 and blue < 70


def test_gallery_mpo_accepted_even_with_jpg_name_and_generic_mime(client):
    response = client.post("/ajout-rapide", data={"provider": "manual"},
                           files={"galerie": ("IMG_0001.JPG", encoded("MPO"), "application/octet-stream")},
                           follow_redirects=False)
    assert response.status_code == 303
    photo = client.get(response.headers["location"] + "/photo")
    assert Image.open(BytesIO(photo.content)).format == "JPEG"


def test_gallery_jpeg_exif_orientation_and_metadata_are_normalized():
    buffer = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "Private photo description"
    Image.new("RGB", (80, 40), "red").save(buffer, format="JPEG", exif=exif)
    image = Image.open(BytesIO(process_photo(buffer.getvalue())))
    assert image.size == (40, 80)
    assert not image.getexif()


def test_mislabeled_non_image_and_unsupported_image_still_rejected():
    with pytest.raises(PhotoError, match="illisible"):
        process_photo(b"not an image")
    with pytest.raises(PhotoError, match="format de photo"):
        process_photo(encoded("BMP"))
